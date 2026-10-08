"""BARC 中文投資人須知（docs/templates/barc-zh-iis.md）：段落標題＋點號＋標籤定位，價格表依座標拆欄。

依 8 份真實樣本建立（單一／多標的、有無 KI、記憶式與否、USD／CNH）：價格表出現樣本以外的欄頭或文字時
整張表轉人工覆核，不猜欄位。記憶式商品的 KO 欄頭不寫百分比（「為最初價格乘以自動提前出場觸發百分比」），
那份就沒有 KO %；有 KI 的商品多一欄「觸及生效價格」，那份另有 KI %。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import PriceRow
from ..text import squash
from . import iis
from .layout import TextIndex, parse_date

TEMPLATE_ID = "barc-zh-iis"
PARSER_VERSION = "1"
SECTIONS = ("警語：", "相關機構事業概況：", "商品簡介：", "收益分配事項：", "本商品之費用明細表：")
PRICE_HEADERS = (  # 欄、欄頭第一行開頭的寫法、是否必有（最初價格欄頭須整行相等，避免和 KI 欄頭的續行混淆）
    ("initial", ("最初價格",), True),
    ("strike", ("執行價格",), True),
    ("ko", ("觸發水準", "自動提前出場觸發"), True),
    ("ki", ("觸及生效價",), False),
)
PCT_FIELDS = (("strike", "strike_pct"), ("ko", "ko_pct"), ("ki", "ki_pct"))
KO_BY_PERIOD = "乘以自動提前出場觸發百分比"  # 記憶式商品 KO 欄頭：各期百分比不同，沒有單一 KO %
NUMBER = re.compile(r"[\d,]+\.\d+")
TABLE_CELL_WIDTH = 300  # 表格儲存格的寬度上限（pt）；更寬的是表格外的段落
COLUMN_REACH = 70  # 欄頭或數字離欄中心超過此距離（pt）視為範本以外的欄位

PROVIDED = frozenset(
    {
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "currency_zh",
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


def _ordered(text: str, anchors: Sequence[str]) -> bool:
    pos = -1
    for a in anchors:
        pos = text.find(a, pos + 1)
        if pos < 0:
            return False
    return True


def detect(lines: Sequence[Line]) -> DetectionResult:
    ti = TextIndex(lines)
    failed = []
    for ok, message in (
        ("中文投資人須知" in ti.text, "封面缺少「中文投資人須知」"),
        ("1.發行機構：英商巴克萊銀行股份有限公司" in ti.text, "發行機構不是BARC"),
        (_ordered(ti.text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _underlyings(ti: TextIndex) -> ParsedField:
    found = iis.raw(ti, r"10\.連結標的資產[:：].+?11\.商品年期")
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


def _price_table(lines: Sequence[Line]) -> dict[str, ParsedField]:
    """收益分配事項的價格表：欄頭（最初價格、執行價格、觸發水準）依座標拆欄，每列一檔標的。"""
    start = next((i for i, ln in enumerate(lines) if squash(ln.text) == "標的資產"), None)
    if start is None:
        return {
            n: ParsedField.missing(n, "找不到價格表")
            for n in ("underlying_prices", "underlying_names", "strike_pct", "ko_pct")
        }
    head = lines[start]
    region = [head]
    for ln in lines[start + 1 :]:
        if ln.page != head.page or ln.x1 - ln.x0 > TABLE_CELL_WIDTH or re.match(r"\(\d+\)", squash(ln.text)):
            break  # 表格下方的整行段落或下一個子項
        region.append(ln)
    nums = [ln for ln in region if NUMBER.fullmatch(squash(ln.text))]
    if not nums:
        return _bad("價格表沒有價格", region)
    top = min(ln.y0 for ln in nums)
    header = [ln for ln in region[1:] if ln.y0 < top - 2]
    body = [ln for ln in region if ln.y0 >= top - 2]
    centers: dict[str, float] = {}
    for key, labels, required in PRICE_HEADERS:
        if key == "initial":
            hits = [ln for ln in header if squash(ln.text) == labels[0]]
        else:
            hits = [ln for ln in header if squash(ln.text).startswith(labels)]
        hits.sort(key=lambda ln: ln.y0)  # 欄頭換行後的續行也可能以同樣的字開頭（例：自動提前出場觸發百分比）
        if (required and not hits) or any(abs(ln.xc - hits[0].xc) > COLUMN_REACH for ln in hits):
            return _bad(f"價格表欄頭找不到或有多個「{labels[0]}」", region)
        if hits:
            centers[key] = hits[0].xc
    name_edge = min(centers.values()) - COLUMN_REACH

    def column(ln: Line) -> str | None:
        key = min(centers, key=lambda k: abs(centers[k] - ln.xc))
        return key if abs(centers[key] - ln.xc) <= COLUMN_REACH else None

    heads = {k: "" for k in centers}
    for ln in header:
        key = column(ln)
        if key is None:
            return _bad(f"價格表有範本以外的欄頭「{ln.text}」", region)
        heads[key] += squash(ln.text)
    rows: list[tuple[float, dict[str, Decimal], list[Line], list[str]]] = []  # 列首 y、價格、原文行、名稱
    for ln in sorted((x for x in body if NUMBER.fullmatch(squash(x.text))), key=lambda x: (x.y0, x.x0)):
        key = column(ln)
        if key is None:
            return _bad(f"價格表有範本以外的欄位「{ln.text}」", region)
        if not rows or abs(rows[-1][0] - ln.y0) > 3:
            rows.append((ln.y0, {}, [], []))
        if key in rows[-1][1]:
            return _bad("價格表同一列同一欄有兩個價格", region)
        rows[-1][1][key] = iis.number(squash(ln.text))
        rows[-1][2].append(ln)
    for ln in sorted((x for x in body if not NUMBER.fullmatch(squash(x.text))), key=lambda x: (x.y0, x.x0)):
        row = next((r for r in reversed(rows) if r[0] <= ln.y0 + 3), None)  # 標的名稱可能換行，屬於上方最近的一列
        if ln.xc >= name_edge or row is None:
            return _bad(f"價格表有範本以外的文字「{ln.text}」", region)
        row[3].append(squash(ln.text))
        row[2].append(ln)
    if any(set(prices) != set(centers) for _, prices, _, _ in rows):
        return _bad("價格表有一列缺少價格", region)
    out: dict[str, ParsedField] = {}
    for key, field in PCT_FIELDS:
        if key not in heads or (key == "ko" and KO_BY_PERIOD in heads[key]):
            continue  # 這份沒有 KI 欄，或記憶式商品 KO 欄頭沒有單一百分比：不核對該百分比
        m = re.search(r"為最初價格的([\d.]+)%", heads[key])
        out[field] = (
            ParsedField.present(field, Decimal(m[1]), header)
            if m
            else ParsedField.invalid(field, header, f"價格表欄頭沒有百分比：{heads[key]}")
        )
    price_rows = tuple(
        PriceRow(None, prices, tuple(Evidence.of(x) for x in lns), "".join(names) or None)
        for _, prices, lns, names in rows
    )
    out["underlying_prices"] = ParsedField(
        "underlying_prices", FieldStatus.PRESENT, price_rows, [Evidence.of(x) for x in region]
    )
    names = [r.name for r in price_rows]
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
            ("product_code_cover", "封面商品代號", "封面「商品代號」", r"(?<!機構)商品代號[:：](\d{12})"),
            (
                "product_code_distributor",
                "封面受託或銷售機構商品代號",
                "封面「受託或銷售機構商品代號」",
                r"受託或銷售機構商品代號[:：](\d{12})",
            ),
        ),
    )
    put("isin", r"ISIN[:：]([A-Z]{2}[A-Z0-9]{10})")
    put("name_zh", r"(英商巴克萊銀行.+?結構型商品(?:[（(][^（）()]*[）)])*)\(\d")  # 括號全形半形混用
    put("name_en", r"結構型商品(?:[（(][^（）()]*[）)])*\((\d.+?)\)（下稱")
    put("currency_zh", r"計價幣別[:：]([^，。]+)")  # 例：人民幣，於香港銀行同業市場進行交易之貨幣
    put("denomination", r"每單位商品面額為([\d,]+)", iis.integer)
    put("min_subscription", r"最低申購金額為([\d,]+)", iis.integer)
    put("issue_price_pct", r"發行價格為商品面額之([\d.]+)%", Decimal)
    fields["underlyings"] = _underlyings(ti)
    put("tenor_months", r"11\.商品年期[:：](\d+)個月", int)
    put("issue_date", r"12\.發行日[:：]" + iis.DATE, parse_date)
    put("maturity_date", r"13\.到期日或最終實物贖回日[:：]" + iis.DATE, parse_date)
    put("monthly_coupon_pct", r"每月之配息率（為([\d.]+)%", Decimal)
    put("coupon_pa_pct", r"即年利率為([\d.]+)%", Decimal)
    fields.update(_price_table(lines))
    provided = (PROVIDED - {"ko_pct"}) | {f for f in ("ko_pct", "ki_pct") if f in fields}
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (("issuer_name_org", "發行機構名稱", "相關機構事業概況第 1 點", r"1\.發行機構[:：](.+?)；營業所在地"),),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (
            ("distributor_name_w4a", "受託機構名稱（警語 4）", "警語第 4 點", r"4\.本商品雖經(.+?)審查"),
            ("distributor_name_w4b", "受託機構名稱（警語 4）", "警語第 4 點", r"之價值，且(.+?)不負本商品投資盈虧之責"),
            ("distributor_name_w4c", "受託機構名稱（警語 4）", "警語第 4 點", r"盈虧之責。(.+?)依法不得承諾"),
            ("distributor_name_w5", "受託機構名稱（警語 5）", "警語第 5 點", r"5\.本商品持有期間.+?而非由(.+?)保證。"),
            ("distributor_name_w6", "受託機構名稱（警語 6）", "警語第 6 點", r"另行訂定者，係由(.+?)負責外"),
            ("distributor_name_w9", "受託機構名稱（警語 9）", "警語第 9 點", r"9\.(.{1,30}?)應提供專業投資人"),
            (
                "distributor_name_org",
                "受託機構名稱（相關機構）",
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
                "受託機構地址（相關機構）",
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
