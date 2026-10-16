"""DBS 中文投資人須知（docs/templates/dbs-zh-iis.md）：段落標題＋標籤定位，句型在去空白的全文上讀。

依 4 份真實樣本建立（同說明書 DB1–DB4）。A4 版面；每頁頁首「中文投資人須知刊印日期：…」、頁底「第N 頁/共4 頁」，
兩者都不算內文（刊印日期另讀成 4 個出處）。第 1 頁中文名稱後接「(下稱「本商品」) (商品代號：…) ( 受託或銷售機構商品代號：…)
( 國際證券編碼ISIN：…)」，再來是英文名稱與警語 1.～11.。段落標題沒有編號（整行）。商品簡介有價格表（同說明書表1，表頭可能
跨頁）、面額、發行價格、交易日；收益分配事項與贖回價金之計算的寫法同說明書（記憶式、下限觸發事件）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import full_brackets, squash
from . import dbs, iis, price_table
from .common import RawText, capture_group, derived, fee_rates, number
from .layout import TextIndex, parse_date

TEMPLATE_ID = "dbs-zh-iis"
PARSER_VERSION = "2"  # 2：價格表改由 parsers/price_table.py 讀（Issue #287）
TITLE = "中文投資人須知(最終版本)"
SECTIONS = (
    "※投資風險警語：",
    "相關機構事業概況",
    "商品簡介",
    "收益分配事項",
    "贖回價金之計算",
    "發行或受託不成立之處理",
    "商品主要風險揭露",
    "投資人應負擔的各項費用及金額或計算基準之表列",
    "相關機構之權利、義務及責任",
)
PAGE_OF = r"第\s*(\d+)\s*頁\s*/\s*共\s*(\d+)\s*頁"
PRINT_HEAD = re.compile(r"^中文投資人須知刊印日期：")
TERMS = ("收益分配事項", "發行或受託不成立之處理")  # KO／KI 寫法所在的段落
D = dbs.D
PCT = dbs.PCT
PROVIDED = frozenset(
    {
        "page_totals",
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "print_dates",
        "currency_zh",
        "currency_others",
        "denomination",
        "issue_price_pct",
        "underlyings",
        "underlying_prices",
        "strike_pct",
        "ko_pct",
        "tenor_months",
        "trade_date",
        "issue_date",
        "final_valuation_date",
        "maturity_date",
        "coupon_pa_pct",
        "monthly_coupon_ceiling_pct",
        "first_callable_period",
        "ko_memory",
        "ki_type",
        "issuer_names",
        "distributor_names",
        "distributor_addresses",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def _noise(ln: Line) -> bool:
    t = ln.text.strip()
    return bool(re.fullmatch(PAGE_OF, t) or PRINT_HEAD.match(t))


def body(lines: Sequence[Line]) -> list[Line]:
    """頁碼與每頁頁首刊印日期以外的行（兩者另由 `page_totals`、`print_dates` 讀）。"""
    return [ln for ln in lines if not _noise(ln)]


def detect(lines: Sequence[Line]) -> DetectionResult:
    text_lines = body(lines)
    text = TextIndex(text_lines).text
    head = squash(text_lines[0].text) if text_lines else ""
    m = re.search(r"^中文投資人須知\(最終版本\)(.+?)\(下稱「本商品」\)", text)
    zh = m[1] if m else ""
    en = re.search(r"國際證券編碼ISIN：[A-Z0-9]{12}\)(.+?)※投資風險警語：", text)
    failed = []
    for ok, message in (
        (head == TITLE, f"第一行不是「{TITLE}」"),
        (
            zh.startswith(dbs.ISSUER_ZH) and full_brackets(squash(dbs.NAME_ZH_MARK)) in full_brackets(zh),
            f"中文名稱不是以「{dbs.ISSUER_ZH}」開頭或沒有「{dbs.NAME_ZH_MARK}」",
        ),
        (
            en is not None and en[1].startswith(squash(dbs.ISSUER_EN)) and squash(dbs.NAME_EN_MARK) in en[1],
            f"英文名稱不是以「{dbs.ISSUER_EN}」開頭或沒有「{dbs.NAME_EN_MARK}」",
        ),
        (
            all(x in text for x in ("(商品代號：", "受託或銷售機構商品代號：", "國際證券編碼ISIN：")),
            "找不到「(商品代號：…)」「受託或銷售機構商品代號：」「國際證券編碼ISIN：」",
        ),
        (
            "相關機構事業概況1.發行機構：新加坡商星展銀行" in text and "2.發行人：" in text,
            "相關機構第 1 點的發行機構不是新加坡商星展銀行或第 2 點不是發行人",
        ),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in text_lines[:2]])


def print_dates(lines: Sequence[Line]) -> ParsedField:
    """每頁頁首的刊印日期（每頁一個出處，`print_date_p{n}`）。"""
    items = []
    for ln in lines:
        if PRINT_HEAD.match(ln.text.strip()):
            value = parse_date(ln.text)
            pf = (
                ParsedField.present(f"print_date_p{ln.page}", value, [ln])
                if value
                else ParsedField.invalid(f"print_date_p{ln.page}", [ln], f"「{ln.text.strip()}」無法辨識")
            )
            items.append(Occurrence(pf.name, "刊印日期", f"第 {ln.page} 頁頁首中文投資人須知刊印日期", pf))
    if not items:
        return ParsedField.missing("print_dates", "找不到頁首「中文投資人須知刊印日期：」")
    return standard_fields.occurrences("print_dates", items)


def underlyings(raw: RawText) -> ParsedField:
    """商品簡介「連結標的資產：甲公司 (Alpha Inc)，彭博代碼為ABC UW、…。」的彭博代碼，依順序。"""
    a = raw.text.find("連結標的資產：")
    b = raw.text.find("。", a)
    if a < 0 or b < 0:
        return ParsedField.missing("underlyings", "商品簡介找不到「連結標的資產：」")
    found = [
        (m, raw.lines_for(a + m.start(), a + m.end()))
        for m in re.finditer(r"彭博代碼\s*為\s*([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})(?=\s*[、。])", raw.text[a : b + 1])
    ]
    if not found:
        return ParsedField.missing("underlyings", "「連結標的資產：」讀不到彭博代碼")
    return ParsedField.present(
        "underlyings", [f"{m[1]} {m[2]}" for m, _ in found], [ln for _, lns in found for ln in lns]
    )


def _terms(ti: TextIndex, fields: dict[str, ParsedField]) -> None:
    """收益分配事項～發行或受託不成立之處理：最大利率值與年利率、第 1～k 期、記憶式、下限觸發事件（和商品簡介價格表的
    下限觸發價格欄須一致；範本規格 §4）。"""
    sec = iis.section(ti, TERMS)
    if sec is None:
        note = "找不到「收益分配事項」到「發行或受託不成立之處理」段落"
        for n in ("coupon_pa_pct", "monthly_coupon_ceiling_pct", "first_callable_period", "ko_memory", "ki_type"):
            fields[n] = ParsedField.missing(n, note)
        return
    st = TextIndex(sec)
    fields["coupon_pa_pct"] = capture_group(sec, dbs.MAX_RATE, 2, "coupon_pa_pct", Decimal)
    fields["monthly_coupon_ceiling_pct"] = capture_group(sec, dbs.MAX_RATE, 1, "monthly_coupon_ceiling_pct", Decimal)
    fields["first_callable_period"] = dbs.full_rate_periods(
        "first_callable_period", st, r"利率：對於第1(?:~(\d{1,2}))?個觀察期間而言，利率為最大利率值"
    )
    fields["ko_memory"] = dbs.memory(st, "ko_memory")
    fields["ki_type"] = dbs.ki_type(st, fields["price_table"])


def _prices(fields: dict[str, ParsedField], lines: list[Line]) -> set[str]:
    """商品簡介價格表（同說明書表1）→ 各標的價格與 K／KO／KI %；回傳額外交出的欄位（有下限觸發價格欄時的 ki_pct）。"""
    table = price_table.read("price_table", _table_region(lines), dbs.IIS_TABLE)
    uls = fields["underlyings"]
    if table.ok and uls.ok and table.value.tickers != uls.value:
        note = f"價格表的彭博代碼 {table.value.tickers} 和「連結標的資產：」{uls.value} 不同"
        table = ParsedField(table.name, FieldStatus.INVALID, evidence=list(table.evidence), note=note)
    fields["price_table"] = table
    fields["underlying_prices"] = dbs.price_rows(table)
    for key, col in (("strike_pct", "strike"), ("ko_pct", "ko")):
        fields[key] = derived(key, table, table.value.pcts[col] if table.ok else None)
    if table.ok and "ki" in table.value.pcts:
        fields["ki_pct"] = derived("ki_pct", table, table.value.pcts["ki"])
        return {"ki_pct"}
    return set()


def _table_region(lines: list[Line]) -> list[Line]:
    """價格表的範圍：「連結標的資產：…。」那一句結束的下一行起、「商品面額：」之前。這句換行後的下一行可能以彭博代碼
    開頭（`TSM UN。`），不能算進表（Issue #295）。"""
    region = dbs.table_region(lines, "連結標的資產：", "商品面額：")
    a = next((i for i, ln in enumerate(lines) if squash(ln.text).startswith("連結標的資產：")), None)
    if a is None or "。" in lines[a].text:
        return region
    end = next((ln for ln in region if "。" in ln.text), None)
    return region[region.index(end) + 1 :] if end is not None else region


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    raw = RawText(text_lines)
    fields: dict[str, ParsedField] = {
        "page_totals": iis.page_totals(lines, PAGE_OF, "第 N 頁/共 M 頁"),
        "print_dates": print_dates(lines),
    }
    provided = set(PROVIDED)

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 第 1 頁
    put("name_zh", r"^中文投資人須知\(最終版本\)(.+?)\(下稱「本商品」\)")
    fields["name_en"] = raw.capture("name_en", r"國際證券編碼ISIN\s*：\s*[A-Z0-9]{12}\s*\)\s*(.+?)\s*※\s*投資風險警語")
    put("isin", r"國際證券編碼ISIN：([A-Z]{2}[A-Z0-9]{10})\)")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (
            ("product_code_iis", "封面商品代號", "第 1 頁「商品代號」", r"\(商品代號：(\d{12})\)"),
            (
                "distributor_code_iis",
                "受託或銷售機構商品代號",
                "第 1 頁「受託或銷售機構商品代號」",
                r"受託或銷售機構商品代號：(\d{12})\)",
            ),
        ),
    )
    # 商品簡介
    put("currency_zh", rf"計價幣別：({dbs.ZH_CCY})\([A-Z]{{3}}\)；")
    denom = rf"商品面額：每單位({dbs.CCY})([\d,]+),發行價格：商品面額的" + PCT
    fields["denomination"] = capture_group(text_lines, denom, 2, "denomination", number)
    fields["issue_price_pct"] = capture_group(text_lines, denom, 3, "issue_price_pct", Decimal)
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others",
        [
            Occurrence(
                "denomination_currency",
                "每單位商品面額幣別",
                "商品簡介商品面額",
                capture_group(text_lines, denom, 1, "denomination_currency"),
            )
        ],
    )
    fields["underlyings"] = underlyings(raw)
    provided |= _prices(fields, text_lines)
    put("tenor_months", r"商品年期：(\d+)個月。", int)
    put("trade_date", r"交易日：" + D + "；", parse_date)
    put("issue_date", r"發行日：" + D + "；", parse_date)
    put("maturity_date", r"到期日：如未依下載之強制贖回條款發生自動提前出場事件之情形，為" + D, parse_date)
    put("final_valuation_date", r"(?<!規劃)定價日：" + D + "，如果被延遲", parse_date)
    _terms(ti, fields)
    # 相關機構事業概況（審查標準）
    org = (
        (
            "issuer_names",
            "issuer_name_org",
            "發行機構名稱（相關機構）",
            "相關機構事業概況 1.",
            r"1\.\s*發行機構\s*：\s*(.+?)\s*；\s*營業所在地",
            1,
        ),
        (
            "distributor_names",
            "distributor_name_org",
            "受託機構名稱（相關機構）",
            "相關機構事業概況 3.",
            r"3\.\s*受託或銷售機構\s*：\s*(.+?)\s*；\s*營業所在地\s*：\s*(.+?)\s*。",
            1,
        ),
        (
            "distributor_addresses",
            "distributor_address_org",
            "受託機構地址（相關機構）",
            "相關機構事業概況 3.",
            r"3\.\s*受託或銷售機構\s*：\s*(.+?)\s*；\s*營業所在地\s*：\s*(.+?)\s*。",
            2,
        ),
    )
    for container, field, label, where, pattern, group in org:
        fields[container] = standard_fields.occurrences(
            container, [Occurrence(field, label, where, raw.capture(field, pattern, group=group))]
        )
    fees = iis.section(ti, ("投資人應負擔的各項費用及金額或計算基準之表列", "相關機構之權利、義務及責任"))
    if fees is None:
        fields.update(
            {
                standard_fields.fee_field(x): ParsedField.missing(standard_fields.fee_field(x), "找不到費用表段落")
                for x in iis.FEES
            }
        )
    else:
        fields.update(fee_rates(fees))
    return iis.IisSheet(fields, ti, frozenset(provided))
