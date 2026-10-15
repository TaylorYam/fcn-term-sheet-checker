"""Citi 中文投資人須知（docs/templates/citi-zh-iis.md）：段落標題＋標籤定位，一律在去空白的全文上讀。

依 8 份真實樣本建立（同說明書 CT1–CT8）。每頁頁首「第N頁，共4頁」。KO／KI 寫法與「記憶式」用字只在「二、商品事項」到
「三、投資風險」之間判斷，KI 句型同說明書第 8 項（parsers/citi.py 的 `ki_type`）；三、投資風險也有類似句子。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import citi, iis, money
from .layout import TextIndex, join_text, parse_date

TEMPLATE_ID = "citi-zh-iis"
PARSER_VERSION = "1"
TITLE = "中文投資人須知"
NAME_EN_ISSUER = squash(citi.NAME_EN_ISSUER)
SECTIONS = (
    "本商品之投資風險警語",
    "一、相關機構事業概況",
    "二、商品事項",
    "1.商品簡介",
    "2.收益分配事項",
    "3.贖回價金之計算",
    "4.受託或發行不成立之情形",
    "三、投資風險",
    "四、投資人應負擔的各項費用",
    "五、相關機構之權利、義務及責任",
    "六、協助投資人權益保護之方式",
    "七發行機構、總代理人及受託或銷售機構與投資人爭議之處理方式",
)
PRODUCT = ("二、商品事項", "三、投資風險")  # 產品條件、KO／KI 寫法與「記憶式」用字（D40）所在的段落
REDEMPTION = ("3.贖回價金之計算", "4.受託或發行不成立之情形")  # 到期給付金額（KI 型態）
CCY = money.CURRENCY
PCT = r"(\d+(?:\.\d+)?)%"
KO_D = r"自動提前出場觀察日：就所有連結標的而言，於第(\d+)個配息觀察期間結束日（含當日）起至期末評價日（含當日）止之期間內，每一個預定交易日"
TICKER = re.compile(r"彭博代碼[：:]\s*([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})\s*\)")
FEE_PATTERNS = {  # 費用表（去空白）：舊文件分銷費用的括號說明被表格拆開，「費用」子列可能在括號說明之前
    **citi.FEE_PATTERNS,
    "分銷費用": r"分銷費用.{0,80}?(?:費用|[)）])申購價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
}
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
        "underlyings",
        "tenor_months",
        "trade_date",
        "issue_date",
        "final_valuation_date",
        "maturity_date",
        "redemption_start",
        "coupon_pa_pct",
        "monthly_coupons",
        "strike_pct",
        "has_autocall",
        "ko_memory",
        "memory_wording",
        "ki_type",
        "issuer_names",
        "guarantor_names",
        "guarantor_names_org",
        "agent_names",
        "distributor_names",
        "distributor_addresses",
        "minimum_issue_size",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """頁首「第N頁，共4頁」以外的行（頁首另由 `iis.page_totals` 讀）。"""
    return [ln for ln in lines if not citi.PAGE_MARK.match(ln.text.strip())]


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    first = squash(lines[0].text) if lines else ""
    names = r"中文投資人須知(?:刊印日期：" + iis.DATE + r")?CGMHI.+?\(無擔保\)\(不保本\)\(下稱「商品」\)"
    failed = []
    for ok, message in (
        (first.startswith(TITLE), "第一列不是「中文投資人須知」"),
        (re.match(names, text) is not None, "封面名稱不是「CGMHI …(無擔保) (不保本)」"),
        (NAME_EN_ISSUER in text, f"封面英文名稱沒有「{citi.NAME_EN_ISSUER}」"),
        (
            all(x in text for x in ("商品種類：", "ISIN：", "受託或銷售機構商品代號", "集保商品代號")),
            "封面找不到商品種類、ISIN、受託或銷售機構商品代號、集保商品代號",
        ),
        ("一、相關機構事業概況1.發行機構：CitigroupGlobalMarketsHoldingsInc." in text, "相關機構的發行機構不是 CGMHI"),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _section_text(ti: TextIndex, anchors: tuple[str, str]) -> tuple[TextIndex, list[Line]] | None:
    sec = iis.section(ti, anchors)
    return (TextIndex(sec), sec) if sec else None


def _underlyings(lines: list[Line], ti: TextIndex) -> ParsedField:
    """商品簡介「連結標的資產：」各檔的「(彭博代碼：XXX UW)」。

    代碼可能被換行拆開，連在代號與交易所碼之間（`(彭博代碼：NVDA` / `UW)`，Issue #229）：原文行用 `join_text` 接回
    （英數字跨行補一個空白）再找。"""
    name = "underlyings"
    a, b = ti.text.find("連結標的資產："), ti.text.find("商品年期：")
    if a < 0 or b <= a:
        return ParsedField.missing(name, "找不到商品簡介「連結標的資產」")
    region = ti.lines_for(a, b)
    joined = join_text([replace(ln, text=ln.text.strip()) for ln in region])
    tickers = [f"{m[1]} {m[2]}" for m in TICKER.finditer(joined)]
    if not tickers:
        return ParsedField.invalid(name, region, "連結標的資產找不到「(彭博代碼：代號 交易所碼)」")
    return ParsedField.present(name, tickers, region)


def _ko_terms(ti: TextIndex, fields: dict[str, ParsedField]) -> None:
    """有無自動提前出場、KO 觀察方式、D 型第一個可提前出場期、記憶式、KI 型態（範本規格 §4.1–§4.3）。"""
    names = ("has_autocall", "ko_observation", "first_callable_period", "ko_memory", "ki_type", "memory_wording")
    found = _section_text(ti, PRODUCT)
    if found is None:
        fields.update({n: ParsedField.missing(n, "找不到「二、商品事項」到「三、投資風險」段落") for n in names})
        return
    sec_ti, sec = found
    text = sec_ti.text
    has_autocall = bool(re.search(r"自動提前出場(?:事件|觀察日|配息)", text))
    fields["has_autocall"] = ParsedField.present("has_autocall", has_autocall, sec[:1])
    hits = [sec_ti.lines_for(m.start(), m.end())[0] for m in sec_ti.finditer("記憶式")]
    fields["memory_wording"] = ParsedField.present("memory_wording", len(hits), hits or sec[:1])
    red = _section_text(ti, REDEMPTION)
    fields["ki_type"] = (
        citi.ki_type(red[0], red[1], where="到期給付金額")
        if red
        else ParsedField.missing("ki_type", "找不到「3.贖回價金之計算」段落")
    )
    if not has_autocall:
        fields["ko_memory"] = ParsedField.present("ko_memory", False, sec[:1])
        return
    tenor = re.search(r"商品年期：\d+個月，惟受限於發行機構得提前贖回之條件以及(記憶式)?自動提前出場事件是否發生", text)
    payout = re.search(r"到期給付金額：本商品若未發生(記憶式)?自動提前出場事件", text)
    if tenor and payout and bool(tenor[1]) == bool(payout[1]):
        fields["ko_memory"] = ParsedField.present(
            "ko_memory", bool(tenor[1]), sec_ti.lines_for(tenor.start(), tenor.end())
        )
    else:
        fields["ko_memory"] = ParsedField.invalid(
            "ko_memory", sec, "商品年期句與到期給付金額句的記憶式寫法缺漏或不一致"
        )
    daily = list(sec_ti.finditer(KO_D))
    if len(daily) == 1:
        ev = sec_ti.lines_for(daily[0].start(), daily[0].end())
        fields["ko_observation"] = ParsedField.present("ko_observation", "D", ev)
        fields["first_callable_period"] = ParsedField.present("first_callable_period", int(daily[0][1]), ev)
    elif not daily and "觀察日及配息日：" in text:
        m = text.find("觀察日及配息日：")
        fields["ko_observation"] = ParsedField.present("ko_observation", "P", sec_ti.lines_for(m, m + 8))
    else:
        note = "自動提前出場觀察日的寫法缺漏、重複或不在範本規格內"
        fields["ko_observation"] = ParsedField.invalid("ko_observation", sec, note)
        fields["first_callable_period"] = ParsedField.invalid("first_callable_period", sec, note)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines)}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 封面
    put("name_zh", r"中文投資人須知(?:刊印日期：\d{4}年\d{1,2}月\d{1,2}日)?(CGMHI.+?)\(下稱「商品」\)")
    put("name_en", r"\(下稱「商品」\)(.+?\(Non-principalprotected\))商品種類：")
    put("product_type", r"商品種類：(.+?)；ISIN")
    put("isin", r"ISIN：([A-Z]{2}[A-Z0-9]{10})")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (
            (
                "product_code_trustee",
                "封面受託或銷售機構商品代號",
                "封面「受託或銷售機構商品代號」",
                r"受託或銷售機構商品代號(?:為)?：?(\d{12}|\[\])",
            ),
            ("product_code_tdcc", "封面集保商品代號", "封面「集保商品代號」", r"集保商品代號(?:為)?：?(\d{12})"),
        ),
    )
    fields["print_dates"] = iis.occurrences(
        "print_dates", ti, (("print_date_iis", "刊印日期", "封面刊印日期", r"刊印日期：" + iis.DATE),), parse_date
    )
    # 商品簡介
    put("risk_level_summary", r"本商品之風險程度：(RR\d)")
    put("currency_zh", rf"計價幣別：({CCY})。?\W?原計價幣別本金保本率")
    fields["underlyings"] = _underlyings(text_lines, ti)
    put("tenor_months", r"商品年期：(\d+)個月", int)
    put("trade_date", r"交易日：" + iis.DATE, parse_date)
    put("issue_date", r"發行日：" + iis.DATE, parse_date)
    put("final_valuation_date", r"期末評價日[:：]" + iis.DATE, parse_date)
    put("maturity_date", r"到期日：" + iis.DATE, parse_date)
    put("redemption_start", r"開始受理贖回日期：" + iis.DATE, parse_date)
    # 收益分配事項與贖回價金
    put("coupon_pa_pct", r"\(約等同年息為" + PCT + r"\)", Decimal)
    rates = [
        Occurrence(
            f"monthly_coupon_{i}",
            "月配息率",
            f"收益分配事項配息金額（第 {i} 處）",
            ParsedField.present(f"monthly_coupon_{i}", Decimal(m[1]), ti.lines_for(m.start(), m.end())),
        )
        for i, m in enumerate(ti.finditer(r"每單位商品面額[x×](?:m/M[x×])?" + PCT), start=1)
    ]
    fields["monthly_coupons"] = (
        standard_fields.occurrences("monthly_coupons", rates)
        if rates
        else ParsedField.missing("monthly_coupons", "收益分配事項找不到「每單位商品面額×X%」")
    )
    put("strike_pct", r"執行價係指相關連結標的之期初股價的" + PCT, Decimal)
    ki = iis.capture("ki_pct", ti, r"下限價：係指相關連結標的之期初股價的" + PCT, Decimal)
    _ko_terms(ti, fields)
    provided = set(PROVIDED)
    if ki.ok:
        fields["ki_pct"] = ki
        provided.add("ki_pct")
    if fields["has_autocall"].ok and fields["has_autocall"].value:
        provided.add("ko_observation")
        if fields.get("ko_observation") is not None and fields["ko_observation"].value == "D":
            provided.add("first_callable_period")  # P 型投資人須知不寫第一個可提前出場期
    fields["currency_others"] = iis.occurrences(  # 金額旁的幣別字 = 參考條件表承作幣別（Issue #170）
        "currency_others",
        ti,
        tuple(
            spec
            for spec in (
                ("payment_currency_coupon", "配息給付幣別", "收益分配事項配息金額", rf"以({CCY})支付下列金額"),
                (
                    "payment_currency_maturity",
                    "到期給付幣別",
                    "贖回價金之計算到期給付金額",
                    rf"以({CCY})支付本商品每單位商品面額的100%",
                ),
            )
            if re.search(spec[3], ti.text)
        ),
    )
    put("minimum_issue_size", r"有效申購金額至少達(等值[\d,]+萬?\D{1,5}?)之總申購價值")
    # 警語與相關機構（審查標準）
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (
            ("issuer_name_w5", "發行機構名稱（警語 5）", "警語 5", r"保證保本率，係由(.+?)（發行機構）及"),
            (
                "issuer_name_org",
                "發行機構名稱（相關機構）",
                "相關機構第 1 點",
                r"1\.發行機構：(.+?)\([“\"]CGMHI[”\"]\)",
            ),
        ),
    )
    fields["guarantor_names"] = iis.occurrences(
        "guarantor_names",
        ti,
        (("guarantor_name_w5", "保證機構名稱（警語 5）", "警語 5", r"（發行機構）及(.+?)（保證機構）保證"),),
    )
    fields["guarantor_names_org"] = iis.occurrences(  # 只寫中文「花旗集團」由審查標準的等價寫法接受
        "guarantor_names_org",
        ti,
        (("guarantor_name_org", "保證機構名稱（相關機構）", "相關機構第 2 點", r"2\.保證機構：(.+?)[（(]營業所在地"),),
    )
    fields["agent_names"] = iis.occurrences(
        "agent_names",
        ti,
        (("agent_name_org", "總代理人名稱（相關機構）", "相關機構第 3 點", r"3\.總代理人：(.+?)[（(]營業所在地"),),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (
            ("distributor_name_w4a", "受託機構名稱（警語 4）", "警語 4", r"本商品雖經(.+?)審查"),
            ("distributor_name_w4b", "受託機構名稱（警語 4）", "警語 4", r"之價值，且(.+?)對本商品投資盈虧"),
            ("distributor_name_w4c", "受託機構名稱（警語 4）", "警語 4", r"不負任何責任。(.+?)依法不得承諾"),
            ("distributor_name_w5", "受託機構名稱（警語 5）", "警語 5", r"而非由(.+?)（受託或銷售機構）所保證"),
            (
                "distributor_name_org",
                "受託機構名稱（相關機構）",
                "相關機構第 4 點",
                r"4\.受託或銷售機構：(.+?)[（(]營業所在地",
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
                "相關機構第 4 點",
                r"4\.受託或銷售機構：[^（(]{1,40}[（(]營業所在地：(.+?)[）)]二、",
            ),
        ),
    )
    fees = iis.section(ti, ("四、投資人應負擔的各項費用", "五、"))
    for label in iis.FEES:
        name = standard_fields.fee_field(label)
        fields[name] = (
            iis.capture(name, TextIndex(fees), FEE_PATTERNS[label])
            if fees is not None
            else ParsedField.missing(name, "找不到「四、投資人應負擔的各項費用」段落")
        )
    return iis.IisSheet(fields, ti, frozenset(provided))
