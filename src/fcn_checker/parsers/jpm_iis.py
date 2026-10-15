"""JPM 中文投資人須知（docs/templates/jpm-zh-iis.md）：段落標題＋標籤定位，一律在去空白的全文上讀。

依 5 份真實樣本建立（同說明書 JP1–JP5）。每頁頁碼「N/4」。KO／KI 寫法只在「二、境外結構型商品事項」到「三、投資風險」之間
判斷，句型同說明書第 8 項（parsers/jpm.py 的 `ko_wording`、`ko_memory`、`ki_type`）；標的表同說明書第 9 項（`price_table`）。
開始受理贖回日期只有文字，不核對（2026-10-15 確認，同 BNP、SG）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from . import iis, jpm, money
from .common import derived
from .layout import TextIndex, parse_date

TEMPLATE_ID = "jpm-zh-iis"
PARSER_VERSION = "1"
TITLE = "中文投資人須知"
SECTIONS = (
    "一、相關機構",
    "二、境外結構型商品事項",
    "1.商品簡介",
    "2.收益分配事項",
    "3.贖回價金之計算",
    "4.受託或銷售不成立之處理",
    "三、投資風險",
    "四、費用",
    "五、相關機構之權利、義務及責任",
    "六、協助投資人權益之保護方式",
    "七、發行機構、總代理人及受託機構與投資人爭議之處理方式",
)
PRODUCT = ("二、境外結構型商品事項", "三、投資風險")  # 產品條件與 KO／KI 寫法所在的段落
CCY = money.CURRENCY
PCT = r"(\d+(?:\.\d+)?)%"
ISSUER_ORG = "一、相關機構1.發行機構：" + squash(jpm.ISSUER)
PROVIDED = frozenset(
    {
        "page_totals",
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "product_type",
        "print_dates",
        "currency_zh",
        "underlyings",
        "underlying_prices",
        "tenor_months",
        "trade_date",
        "issue_date",
        "final_valuation_date",
        "maturity_date",
        "coupon_pa_pct",
        "strike_pct",
        "ko_pct",
        "ko_observation",
        "first_callable_period",
        "ko_memory",
        "ki_type",
        "issuer_names",
        "guarantor_names",
        "guarantor_names_short",
        "agent_names",
        "distributor_names",
        "distributor_addresses",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """頁碼「N/4」以外的行（頁碼另由 `page_totals` 讀）。"""
    return [ln for ln in lines if not jpm.PAGE_MARK.match(ln.text.strip())]


def page_totals(lines: Sequence[Line]) -> ParsedField:
    """每頁頁碼「N/M」的 M（共用讀法只認「第 N 頁，共 M 頁」）。"""
    hits = [(int(m[2]), ln) for ln in lines if (m := re.fullmatch(r"(\d+)\s*/\s*(\d+)", ln.text.strip()))]
    if not hits:
        return ParsedField.missing("page_totals", "找不到頁碼「N/M」")
    return ParsedField.present("page_totals", tuple(m for m, _ in hits), [ln for _, ln in hits])


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    first = squash(lines[0].text) if lines else ""
    names = r"中文投資人須知摩根大通.+?\(無擔保\)\(不保本\)JPMorgan"
    failed = []
    for ok, message in (
        (first == TITLE, "第一列不是「中文投資人須知」"),
        (re.match(names, text) is not None, "封面名稱不是「摩根大通…(無擔保)(不保本)」接 JPMorgan 英文名稱"),
        (squash(jpm.NAME_EN_BODY) in text, f"封面英文名稱沒有「{jpm.NAME_EN_BODY}」"),
        (
            all(
                x in text for x in ("商品種類：", "TDCC商品代號", "受託機構商品代號", "ISIN為", "投資人須知刊印日期：")
            ),
            "封面找不到商品種類、TDCC 商品代號、受託機構商品代號、ISIN、刊印日期",
        ),
        (ISSUER_ORG in text, f"相關機構的發行機構不是 {jpm.ISSUER}"),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _section(ti: TextIndex, anchors: tuple[str, str]) -> tuple[TextIndex, list[Line]] | None:
    sec = iis.section(ti, anchors)
    return (TextIndex(sec), sec) if sec else None


def _ko_terms(ti: TextIndex, fields: dict[str, ParsedField]) -> None:
    """KO 觀察方式、第一個可提前出場期、記憶式、KI 型態（句型同說明書第 8 項；範本規格 §4）。

    KO 評價日只看 D／P 型與起始期 k：說明書寫「觀察期間終止日(t)」、投資人須知寫「評價日(t)」都算同一種（2026-10-15 決定）。"""
    names = ("ko_observation", "first_callable_period", "ko_memory", "ki_type")
    found = _section(ti, PRODUCT)
    if found is None:
        fields.update(
            {n: ParsedField.missing(n, "找不到「二、境外結構型商品事項」到「三、投資風險」段落") for n in names}
        )
        return
    sec_ti, sec = found
    where = "贖回價金之計算"
    fields["ko_memory"] = jpm.ko_memory(sec_ti, sec, where)
    fields["ki_type"] = jpm.ki_type(sec_ti, sec, where)
    wording = jpm.ko_wording(sec_ti, sec, where)
    tenor = fields["tenor_months"]
    if wording.ok and wording.value[0] == "P" and tenor.ok and wording.value[2] != tenor.value:
        wording = ParsedField.invalid(
            "ko_wording", list(wording.evidence), f"自動提前出場評價日的最後一期 t={wording.value[2]} 不等於商品年期"
        )
    fields["ko_observation"] = derived("ko_observation", wording, wording.value[0] if wording.ok else None)
    fields["first_callable_period"] = derived(
        "first_callable_period", wording, wording.value[1] if wording.ok else None
    )


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": page_totals(lines)}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 封面
    put("name_zh", r"中文投資人須知(摩根大通.+?\(無擔保\)\(不保本\))JPMorgan")
    en = iis.raw(ti, r"JPMorgan.+?\(Non-PrincipalProtected\)(?=商品種類：)")
    fields["name_en"] = (
        ParsedField.present("name_en", en[0].strip(), en[1])
        if en
        else ParsedField.missing("name_en", "找不到封面英文名稱（JPMorgan …(Non-Principal Protected)）")
    )
    put("product_type", r"商品種類：(.+?)TDCC商品代號")
    put("isin", r"ISIN為([A-Z]{2}[A-Z0-9]{10})")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (
            ("product_code_tdcc", "封面 TDCC 商品代號", "封面「TDCC商品代號」", r"TDCC商品代號(\d{12})"),
            ("product_code_trustee", "封面受託機構商品代號", "封面「受託機構商品代號」", r"受託機構商品代號(\d{12})"),
        ),
    )
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        ti,
        (("print_date_iis", "刊印日期", "封面刊印日期", r"投資人須知刊印日期：" + iis.DATE),),
        parse_date,
    )
    # 商品簡介
    put("currency_zh", rf"計價幣別：({CCY})●?原計價幣別本金保本率")
    table = jpm.price_table("underlying_table", jpm.table_region(text_lines, "連結標的資產：", "商品年期："))
    fields["underlyings"] = (
        ParsedField("underlyings", FieldStatus.PRESENT, list(table.value["tickers"]), list(table.evidence))
        if table.ok
        else derived("underlyings", table)
    )
    fields["underlying_prices"] = jpm.price_rows(table)
    put("tenor_months", r"商品年期：(\d+)個月", int)
    put("trade_date", r"交易日/期初評價日：" + iis.DATE, parse_date)
    put("issue_date", r"發行日：" + iis.DATE, parse_date)
    put("final_valuation_date", r"最後評價日：" + iis.DATE, parse_date)
    put("maturity_date", r"到期日：" + iis.DATE, parse_date)
    # 收益分配事項與贖回價金：年利率在配息金額與 D 型自動提前出場配息金額兩處，不同時歧義（轉人工覆核）
    put("coupon_pa_pct", r"配息金額：每單位面額x" + PCT + r"x1/12", Decimal)
    put("strike_pct", r"執行價：" + PCT + r"x期初價格", Decimal)
    put("ko_pct", r"自動提前出場價：" + PCT + r"x期初價格", Decimal)
    ki = iis.capture("ki_pct", ti, r"觸碰價：" + PCT + r"x期初價格", Decimal)
    _ko_terms(ti, fields)
    provided = set(PROVIDED)
    if ki.ok:
        fields["ki_pct"] = ki
        provided.add("ki_pct")
    # 警語與相關機構（審查標準）
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (
            (
                "issuer_name_w5",
                "發行機構名稱（警語 5）",
                "警語 5",
                r"而非由受託機構所保證。(.+?)與.+?有財務會計準則公報",
            ),
            ("issuer_name_org", "發行機構名稱（相關機構）", "相關機構第 1 點", r"1\.發行機構：(.+?)\(地址："),
        ),
    )
    fields["guarantor_names"] = iis.occurrences(  # 全名 = guarantor_name.jpm.cover
        "guarantor_names",
        ti,
        (("guarantor_name_org", "保證機構名稱（相關機構）", "相關機構第 2 點", r"2\.保證機構：(.+?)\(地址："),),
    )
    fields["guarantor_names_short"] = iis.occurrences(  # 簡稱 = guarantor_name.jpm.ch1
        "guarantor_names_short",
        ti,
        (
            ("guarantor_name_w5a", "保證機構名稱（警語 5）", "警語 5", r"保證保本率，係由(.+?)保證，而非由受託機構"),
            (
                "guarantor_name_w5b",
                "保證機構名稱（警語 5）",
                "警語 5",
                r"而非由受託機構所保證。.+?與(.+?)有財務會計準則公報",
            ),
        ),
    )
    fields["agent_names"] = iis.occurrences(
        "agent_names",
        ti,
        (("agent_name_org", "總代理人名稱（相關機構）", "相關機構第 3 點", r"3\.總代理人：(.+?)\(地址："),),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (("distributor_name_org", "受託機構名稱（相關機構）", "相關機構第 4 點", r"4\.受託機構：(.+?)\(地址："),),
    )
    fields["distributor_addresses"] = iis.occurrences(  # 地址後的「(營業活動所在地)」不屬於地址
        "distributor_addresses",
        ti,
        (
            (
                "distributor_address_org",
                "受託機構地址（相關機構）",
                "相關機構第 4 點",
                r"4\.受託機構：[^(（]{1,40}\(地址：(.+?)(?:\(營業活動所在地\))?\)二、",
            ),
        ),
    )
    fees = iis.section(ti, ("四、費用", "五、"))
    for label in iis.FEES:
        name = standard_fields.fee_field(label)
        fields[name] = (
            iis.capture(name, TextIndex(fees), jpm.FEE_PATTERNS[label])
            if fees is not None
            else ParsedField.missing(name, "找不到「四、費用」段落")
        )
    return iis.IisSheet(fields, ti, frozenset(provided))
