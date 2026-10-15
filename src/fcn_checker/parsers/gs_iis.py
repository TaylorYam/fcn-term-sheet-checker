"""GS 中文投資人須知（docs/templates/gs-zh-iis.md）：段落標題＋標籤定位，句型在去空白的全文上讀。

依 5 份真實樣本建立（同說明書 GS1–GS5）。每頁頁底「第- N -頁，共4頁」；第 1 頁第一行就是「中文投資人須知刊印日期:」。
文字有 CJK 相容字（U+F9DD「利」等），讀之前先轉成標準字（只轉 U+F900–U+FAFF，不動全形括號與冒號）。章名「一. 相關機構」
…「七. …」；KO／KI 寫法只在「二. 3. 買回價金之計算」到「4. 發行或受託不成立之處理」之間判斷，名詞沒有「(i)」。
價格表同說明書第一章第 15 項（`gs.price_table`），價格後接標的報價幣別字。
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from . import gs, iis
from .common import RawText, derived, number
from .layout import TextIndex, parse_date

TEMPLATE_ID = "gs-zh-iis"
PARSER_VERSION = "1"
SECTIONS = (
    "本商品之投資風險警語",
    "一.相關機構",
    "二.境外結構型商品事項",
    "三.主要投資風險",
    "四.費用",
    "五.相關機構之權利、義務及責任",
    "六.協助投資人權益之保護方式",
    "七.發行機構、總代理人及受託機構與投資人爭議之處理方式",
)
REDEEM = ("3.買回價金之計算", "4.發行或受託不成立之處理")  # KO／KI 寫法所在的段落
PAGE = r"第-\s*(\d+)\s*-頁，共(\d+)頁"  # 頁底頁碼
# 記憶式（範本規格 §4.2）：名詞沒有「(i)」、「自動提前出場事件」沒有「」
EVENT_MEMORY = "各連結標的至少發生過一次記憶提前出場事件，則視為已發生自動提前出場事件"
MEMORY_EVENT = "「記憶提前出場事件」：就連結標的而言"
EVENT_SINGLE = "如於任一提前出場觀察日之參考價格等於或高於提前出場價格，則視為已發生自動提前出場事件"
PROVIDED = frozenset(
    {
        "page_totals",
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "product_type",
        "print_dates",
        "risk_level_summary",
        "currency_zh",
        "currency_others",
        "denomination",
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
        "monthly_coupons",
        "ko_observation",
        "first_callable_period",
        "ko_memory",
        "ki_type",
        "issuer_names_zh",
        "distributor_names",
        "distributor_addresses",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def _standard(text: str) -> str:
    """CJK 相容字（U+F900–U+FAFF）轉成標準字（例：U+F9DD → 利）；其他字元不動。"""
    return "".join(unicodedata.normalize("NFKC", c) if 0xF900 <= ord(c) <= 0xFAFF else c for c in text)


def normalized(lines: Sequence[Line]) -> list[Line]:
    return [dataclasses.replace(ln, text=_standard(ln.text)) if _standard(ln.text) != ln.text else ln for ln in lines]


def body(lines: Sequence[Line]) -> list[Line]:
    """相容字轉換後、頁底頁碼以外的行（頁碼另由 `iis.page_totals` 讀）。"""
    return [ln for ln in normalized(lines) if not re.fullmatch(PAGE, ln.text.strip())]


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    head = [squash(x.text) for x in lines[:2]]
    m = re.search(r"中文投資人須知\(專業投資人\)(.+?)（TDCC商品代號為", text)
    zh = m[1] if m else ""
    failed = []
    for ok, message in (
        (
            len(head) == 2 and head[0].startswith("中文投資人須知刊印日期") and head[1] == "中文投資人須知(專業投資人)",
            "前兩行不是「中文投資人須知刊印日期:…」「中文投資人須知(專業投資人)」",
        ),
        (
            gs.ISSUER_ZH in zh and zh.endswith("（無擔保）（不保本）"),
            f"封面中文名稱沒有「{gs.ISSUER_ZH}」或不是以「（無擔保）（不保本）」結尾",
        ),
        (squash(gs.NAME_EN_MARK) in text, f"封面英文名稱沒有「{gs.NAME_EN_MARK}」"),
        ("（商品種類:" in text, "封面找不到「（商品種類:…）」"),
        (f"一.相關機構1.發行機構:{gs.ISSUER_ZH}" in text, f"相關機構第 1 點的發行機構不是 {gs.ISSUER_ZH}"),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _redeem_terms(ti: TextIndex, fields: dict[str, ParsedField]) -> None:
    """買回價金之計算：KO 觀察（只有每日觀察，自第 k 個觀察期間結束日起）、記憶式、KI 型態（範本規格 §4.1–§4.3）。"""
    names = ("ko_observation", "first_callable_period", "ko_memory", "ki_type")
    sec = iis.section(ti, REDEEM)
    if sec is None:
        fields.update(
            {n: ParsedField.missing(n, "找不到「3. 買回價金之計算」到「4. 發行或受託不成立之處理」段落") for n in names}
        )
        return
    sec_ti = TextIndex(sec)
    starts = list(sec_ti.finditer(gs.KO_DAILY))
    if len(starts) == 1:
        m = starts[0]
        ev = sec_ti.lines_for(m.start(), m.end())
        fields["ko_observation"] = ParsedField.present("ko_observation", "D", ev)
        fields["first_callable_period"] = ParsedField.present("first_callable_period", int(m[1]), ev)
    else:
        note = "「提前出場觀察日」的寫法缺漏、重複或不在範本規格內"
        fields["ko_observation"] = ParsedField.invalid("ko_observation", sec, note)
        fields["first_callable_period"] = ParsedField.invalid("first_callable_period", sec, note)
    memory = EVENT_MEMORY in sec_ti.text and MEMORY_EVENT in sec_ti.text
    single = EVENT_SINGLE in sec_ti.text
    if memory == single:
        fields["ko_memory"] = ParsedField.invalid(
            "ko_memory", sec, "自動提前出場事件與記憶提前出場事件的句型缺漏、重複或不在範本規格內"
        )
    else:
        anchor = EVENT_MEMORY if memory else EVENT_SINGLE
        a = sec_ti.text.find(anchor)
        fields["ko_memory"] = ParsedField.present("ko_memory", memory, sec_ti.lines_for(a, a + len(anchor)))
    fields["ki_type"] = gs.ki_type(sec_ti, sec, where="買回價金之計算")


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(normalized(lines), PAGE, "第- N -頁，共M頁")}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 第 1 頁
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        ti,
        (("print_date_iis", "刊印日期", "第 1 頁中文投資人須知刊印日期", r"中文投資人須知刊印日期[:：]" + gs.D),),
        parse_date,
    )
    cover = (
        r"中文投資人須知\(專業投資人\)(.+?)（TDCC商品代號為(\d{12})，ISIN[:：]([A-Z]{2}[A-Z0-9]{10})，下稱「本商品」\)"
    )
    put("name_zh", cover)
    put("isin", r"中文投資人須知\(專業投資人\).+?（TDCC商品代號為\d{12}，ISIN[:：]([A-Z]{2}[A-Z0-9]{10})，下稱")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (("product_code_tdcc", "封面商品代號", "封面「TDCC 商品代號為」", r"（TDCC商品代號為(\d{12})，ISIN"),),
    )
    raw = RawText(text_lines)
    fields["name_en"] = raw.capture("name_en", r"英文名稱\s*[:：]\s*(.+?)\s*（商品種類")
    put("product_type", r"（商品種類[:：](.+?)）")
    # 二. 1. 商品簡介
    put("risk_level_summary", r"\(3\)本商品風險程度[:：](RR\d)")
    put("currency_zh", rf"\(6\)計價幣別[:：]({gs.ZH_CCY})（[A-Z]{{3}}）")
    denom = rf"\(7\)每單位面額[:：]([\d,]+)({gs.CCY})"
    put("denomination", denom, number)
    fields["currency_others"] = iis.occurrences(
        "currency_others",
        ti,
        (("denomination_currency", "每單位面額幣別", "二.1.(7) 每單位面額", rf"\(7\)每單位面額[:：][\d,]+({gs.CCY})"),),
    )
    a = next((i for i, ln in enumerate(text_lines) if squash(ln.text).startswith("(10)連結標的資產")), None)
    b = next(
        (i for i, ln in enumerate(text_lines) if a is not None and i > a and squash(ln.text).startswith("(11)")), None
    )
    table = gs.price_table("underlying_table", text_lines[a + 1 : b] if a is not None and b is not None else [])
    fields["underlyings"] = gs.underlyings_field(table)
    fields["underlying_prices"] = gs.price_rows(table)
    for col, key in (("strike", "strike_pct"), ("ko", "ko_pct"), ("ki", "ki_pct")):
        fields[key] = (
            ParsedField(key, FieldStatus.PRESENT, table.value["pcts"][col], list(table.evidence))
            if table.ok and col in table.value["pcts"]
            else derived(key, table)
            if not table.ok
            else ParsedField.missing(key, "價格表表頭沒有這一欄的 %")
        )
    put("tenor_months", r"\(11\)商品年期[:：](\d+)個月", int)
    put("issue_date", r"\(12\)發行日[:：]" + gs.D, parse_date)
    put("trade_date", r"\(13\)交易日[:：]" + gs.D, parse_date)
    put("final_valuation_date", r"\(14\)評價日[:：]" + gs.D, parse_date)
    put("maturity_date", r"\(15\)到期日[:：]評價日後的第\d+個營業日，現預定為" + gs.D, parse_date)
    # 二. 2. 收益分配事項
    put("coupon_pa_pct", gs.COUPON, Decimal)
    # 每月配息率 = 年利率 ÷ 12 四捨五入到 4 位（共用規則 iis.monthly_coupon 的「每一處」寫法，同 MS；範本規格 §4.5）
    monthly = r"「配息率」就每個觀察期間而言，指年利率\d+(?:\.\d+)?%/12（約為" + gs.PCT + r"）"
    fields["monthly_coupons"] = iis.occurrences(
        "monthly_coupons", ti, (("monthly_coupon_iis", "每月配息率", "二. 2. 收益分配事項", monthly),), Decimal
    )
    _redeem_terms(ti, fields)
    provided = set(PROVIDED)
    ki = fields["ki_type"]
    if fields["ki_pct"].status != FieldStatus.MISSING or not (
        ki.ok and ki.value == "none"
    ):  # 有下限觸發價格或讀不出無 KI
        provided.add("ki_pct")
    # 一. 相關機構（審查標準）
    org = (
        (
            "issuer_names_zh",
            "issuer_name_org",
            "發行機構名稱（相關機構）",
            "一. 1.",
            r"1\.\s*發行機構\s*[:：]\s*(.+?)\s*（營業所在地",
        ),
        (
            "distributor_names",
            "distributor_name_org",
            "受託機構名稱（相關機構）",
            "一. 4.",
            r"4\.\s*受託機構\s*[:：]\s*(.+?)\s*（營業所在地",
        ),
        (
            "distributor_addresses",
            "distributor_address_org",
            "受託機構地址（相關機構）",
            "一. 4.",
            r"4\.\s*受託機構\s*[:：]\s*.+?（營業所在地\s*[:：]\s*(.+?)\s*）。?\s*二\.",
        ),
    )
    for container, field, label, where, pattern in org:
        fields[container] = standard_fields.occurrences(
            container, [standard_fields.Occurrence(field, label, where, raw.capture(field, pattern))]
        )
    fees = iis.section(ti, ("四.費用", "五."))
    if fees is None:
        fields.update(
            {
                standard_fields.fee_field(x): ParsedField.missing(
                    standard_fields.fee_field(x), "找不到「四. 費用」段落"
                )
                for x in iis.FEES
            }
        )
    else:
        fields.update(gs.fee_rates(fees))
    return iis.IisSheet(fields, ti, frozenset(provided))
