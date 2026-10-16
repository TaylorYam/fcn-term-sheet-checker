"""BARC 中文投資人須知（docs/templates/barc-zh-iis.md）：段落標題＋點號＋標籤定位，價格表以 parsers/price_table.py 讀。

依 8 份真實樣本建立（單一／多標的、有無 KI、記憶式與否、USD／CNH）：價格表只讀認得的欄（樣本以外的欄頭或文字
不再整張轉人工覆核，Issue #288）；價格個數不符、必要欄缺漏仍轉人工覆核。記憶式商品的 KO 欄頭不寫百分比（「為最初價格乘以自動提前出場觸發百分比」），
那份就沒有 KO %；有 KI 的商品多一欄「觸及生效價格」，那份另有 KI %。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import barc, iis, money, price_table
from .common import derived
from .layout import TextIndex, parse_date

TEMPLATE_ID = "barc-zh-iis"
PARSER_VERSION = "2"  # 2：價格表改由 parsers/price_table.py 讀（Issue #288）
SECTIONS = ("警語：", "相關機構事業概況：", "商品簡介：", "收益分配事項：", "本商品之費用明細表：")
PRICE_TABLE = replace(barc.PRICE_TABLE, where="收益分配事項價格表")  # 同說明書第 15 條價格表
PCT_FIELDS = (("strike", "strike_pct"), ("ko", "ko_pct"), ("ki", "ki_pct"))
KO_BY_PERIOD = "乘以自動提前出場觸發百分比"  # 記憶式商品 KO 欄頭：各期百分比不同，沒有單一 KO %
TABLE_CELL_WIDTH = 300  # 表格儲存格的寬度上限（pt）；更寬的是表格外的段落

PROVIDED = frozenset(
    {
        "product_codes",
        "risk_level_summary",
        "isin",
        "name_zh",
        "name_en",
        "currency_zh",
        "currency_others",
        "denomination",
        "min_subscription",
        "issue_price_pct",
        "underlyings",
        "underlying_names",
        "tenor_months",
        "issue_date",
        "maturity_date",
        "coupon_pa_pct",
        "monthly_coupon_pct",
        "underlying_prices",
        "strike_pct",
        "ko_pct",
        "issuer_names",
        "distributor_names",
        "distributor_addresses",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def detect(lines: Sequence[Line]) -> DetectionResult:
    ti = TextIndex(lines)
    failed = []
    for ok, message in (
        ("中文投資人須知" in ti.text, "封面缺少「中文投資人須知」"),
        ("1.發行機構：英商巴克萊銀行股份有限公司" in ti.text, "發行機構不是BARC"),
        (iis.in_order(ti.text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _underlyings(ti: TextIndex) -> ParsedField:
    found = iis.raw(ti, r"連結標的資產[:：].+?商品年期")
    if found is None:
        return ParsedField.missing("underlyings", "找不到「連結標的資產」")
    text, lns = found
    tickers = re.findall(r"([A-Z0-9]+ [A-Z]{2})\s*Equity", text)
    if not tickers:
        return ParsedField.invalid("underlyings", lns, "連結標的資產沒有已知寫法的彭博代號（例：ABC UN Equity）")
    return ParsedField.present("underlyings", tickers, lns)


def _bad(note: str, lns: Sequence[Line]) -> dict[str, ParsedField]:
    return {
        name: ParsedField.invalid(name, list(lns), note)
        for name in ("underlying_prices", "underlying_names", "strike_pct", "ko_pct")
    }


def _region(lines: Sequence[Line]) -> list[Line] | None:
    """收益分配事項的價格表範圍：「標的資產」欄頭那一行起，到表格下方的整行段落、下一個子項「(N)」或下一段「N.」之前
    （可跨到下一頁）。"""
    start = next((i for i, ln in enumerate(lines) if squash(ln.text) == "標的資產"), None)
    if start is None:
        return None
    region = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.x1 - ln.x0 > TABLE_CELL_WIDTH or re.match(r"\(\d+\)|\d+\.(?!\d)", squash(ln.text)):
            break
        region.append(ln)
    return region


def _price_table(lines: Sequence[Line]) -> dict[str, ParsedField]:
    """收益分配事項的價格表（`parsers/price_table.py` 讀，以執行價格欄的價格切列）：每列一檔標的、名稱在左欄。
    記憶式商品 KO 欄頭不寫百分比，那份就沒有 KO %；有 KI 欄才有 KI %。"""
    region = _region(lines)
    if region is None:
        return {
            n: ParsedField.missing(n, "找不到價格表")
            for n in ("underlying_prices", "underlying_names", "strike_pct", "ko_pct")
        }
    table = price_table.read("underlying_prices", region, barc.table_spec(region, PRICE_TABLE.where))
    if not table.ok:
        return {n: derived(n, table) for n in ("underlying_prices", "underlying_names", "strike_pct", "ko_pct")}
    t: price_table.PriceTable = table.value
    out: dict[str, ParsedField] = {}
    for key, field in PCT_FIELDS:
        if key not in t.columns or (key == "ko" and KO_BY_PERIOD in t.heads[key]):
            continue  # 這份沒有 KI 欄，或記憶式商品 KO 欄頭沒有單一百分比：不核對該百分比
        head = list(t.head_lines[key])
        out[field] = (
            ParsedField.present(field, t.pcts[key], head)
            if key in t.pcts
            else ParsedField.invalid(field, head, f"價格表欄頭沒有百分比：{t.heads[key]}")
        )
    out["underlying_prices"] = price_table.price_rows(table)
    names = [squash(n) for n in t.names]
    body = [ln for r in t.rows for ln in r.lines]
    out["underlying_names"] = (
        ParsedField.present("underlying_names", names, body)
        if all(names)
        else ParsedField.invalid("underlying_names", body, "價格表有一列沒有標的名稱")
    )
    return out


def read(lines: Sequence[Line]) -> iis.IisSheet:
    ti = TextIndex(lines)
    fields: dict[str, ParsedField] = {}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (
            ("product_code_cover", "product_code", "封面「商品代號」", r"(?<!機構)商品代號[:：](\d{12})"),
            (
                "product_code_distributor",
                "trustee_product_code",
                "封面「受託或銷售機構商品代號」",
                r"受託或銷售機構商品代號[:：](\d{12})",
            ),
        ),
    )
    put("isin", r"ISIN[:：]([A-Z]{2}[A-Z0-9]{10})")
    put("risk_level_summary", r"本商品風險程度[:：](RR\d)")
    put("name_zh", r"(英商巴克萊銀行.+?結構型商品(?:[（(][^（）()]*[）)])*)\(\d")  # 括號全形半形混用
    put("name_en", r"結構型商品(?:[（(][^（）()]*[）)])*\((\d.+?)\)（下稱")
    put("currency_zh", r"計價幣別[:：]([^，。]+)")  # 例：人民幣，於香港銀行同業市場進行交易之貨幣
    put("denomination", r"每單位商品面額為([\d,]+)", iis.integer)
    put("min_subscription", r"最低申購金額為([\d,]+)", iis.integer)
    fields["currency_others"] = iis.occurrences(  # 金額旁的幣別字 = 參考條件表承作幣別（Issue #170）
        "currency_others",
        ti,
        (
            (
                "denomination_currency",
                "denomination_currency",
                "商品簡介每單位商品面額",
                rf"每單位商品面額為[\d,]+\s*({money.CURRENCY})",
            ),
            (
                "min_subscription_currency",
                "minimum_subscription_currency",
                "商品簡介最低申購金額",
                rf"最低申購金額為[\d,]+\s*({money.CURRENCY})",
            ),
        ),
    )
    put("issue_price_pct", r"發行價格為商品面額之([\d.]+)%", Decimal)
    fields["underlyings"] = _underlyings(ti)
    put("tenor_months", r"商品年期[:：](\d+)個月", int)
    put("issue_date", r"發行日[:：]" + iis.DATE, parse_date)
    put("maturity_date", r"到期日或最終實物贖回日[:：]" + iis.DATE, parse_date)
    put("monthly_coupon_pct", r"每月之配息率（為([\d.]+)%", Decimal)
    put("coupon_pa_pct", r"即年利率為([\d.]+)%", Decimal)
    fields.update(_price_table(lines))
    provided = (PROVIDED - {"ko_pct"}) | {f for f in ("ko_pct", "ki_pct") if f in fields}
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (("issuer_name_org", "issuer_name", "相關機構事業概況第 1 點", r"1\.發行機構[:：](.+?)；營業所在地"),),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (
            ("distributor_name_w4a", "distributor_name_warning", "警語第 4 點", r"4\.本商品雖經(.+?)審查"),
            (
                "distributor_name_w4b",
                "distributor_name_warning",
                "警語第 4 點",
                r"之價值，且(.+?)不負本商品投資盈虧之責",
            ),
            ("distributor_name_w4c", "distributor_name_warning", "警語第 4 點", r"盈虧之責。(.+?)依法不得承諾"),
            (
                "distributor_name_w5",
                "distributor_name_warning",
                "警語第 5 點",
                r"5\.本商品持有期間.+?而非由(.+?)保證。",
            ),
            ("distributor_name_w6", "distributor_name_warning", "警語第 6 點", r"另行訂定者，係由(.+?)負責外"),
            ("distributor_name_w9", "distributor_name_warning", "警語第 9 點", r"9\.(.{1,30}?)應提供專業投資人"),
            (
                "distributor_name_org",
                "distributor_name_org",
                "相關機構事業概況第 3 點",
                r"3\.受託或銷售機構[:：](.+?)；營業所在地",
            ),
        ),
    )
    fields["distributor_addresses"] = iis.occurrences(
        "distributor_addresses",
        ti,
        (
            (
                "distributor_address_org",
                "distributor_address_org",
                "相關機構事業概況第 3 點",
                r"3\.受託或銷售機構[:：].{1,30}?；營業所在地[:：](.+?)。商品簡介",
            ),
        ),
    )
    for label, pattern in (
        ("申購費用", r"申購費用[^%]{0,20}?"),
        ("提前贖回費用", r"提前贖回費用[^%]{0,20}?"),
        ("分銷費用", r"分銷費用[^%]{0,60}?"),
    ):
        put(f"fee_{label}", pattern + iis.RATE)
    return iis.IisSheet(fields, ti, frozenset(provided))
