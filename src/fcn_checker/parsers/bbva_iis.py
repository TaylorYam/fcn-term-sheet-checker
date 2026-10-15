"""BBVA 中文投資人須知（docs/templates/bbva-zh-iis.md）：段落標題＋標籤定位，句型在去空白的全文上讀。

依 5 份真實樣本建立（同說明書 BV1–BV5）。每頁頁底「N/4」；第 1 頁「(僅適用於專業投資人)」「中文投資人須知」後是英文名稱
（`(the "Product")`）、中文名稱、`(ISIN: …)`、商品種類、「TDCC 商品代號：… 受託機構商品代號：…」。沒有刊印日期、交易日、
面額、最低申購金額、發行價格與價格表；連結標的表有彭博代碼。章名「一、 相關機構事業概況」…「七、…」（BV4、BV5 第三章
寫成「三、 品風險揭露」，少「商」字）。文字有 CJK 相容字（同說明書，先轉成標準字）。KO／KI 寫法只在「二、 本商品事項」
到「三、」之間判斷。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from . import bbva, iis
from .common import RawText, fee_rates
from .layout import TextIndex, parse_date

TEMPLATE_ID = "bbva-zh-iis"
PARSER_VERSION = "1"
SECTIONS = (
    "商品之投資風險警語：",
    "一、相關機構事業概況",
    "二、本商品事項",
    "風險揭露",  # 「三、商品風險揭露」（BV4、BV5 寫成「三、品風險揭露」）
    "四、費用",
    "五、相關機構之權利、義務及責任",
    "六、協助投資人權益之保護方式",
    "七、發行機構、總代理人及受託機構與投資人爭議之處理方式",
)
PRODUCT = ("二、本商品事項", "風險揭露")  # KO／KI 寫法所在的段落
D = bbva.D
PCT = bbva.PCT
KO_EVENT = r"「自動提前贖回事件」：(.+?)大於或等於" + PCT + "。"
PROVIDED = frozenset(
    {
        "page_totals",
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "product_type",
        "currency_zh",
        "underlyings",
        "tenor_months",
        "issue_date",
        "maturity_date",
        "redemption_start",
        "ko_pct",
        "ko_memory",
        "ki_type",
        "issuer_names",
        "distributor_names",
        "distributor_addresses",
        "distributor_phones",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """相容字轉成標準字；頁底頁碼「N/4」另由 `iis.page_totals` 讀，不算內文。"""
    out = []
    for ln in lines:
        if re.fullmatch(iis.PAGE_SLASH, ln.text.strip()):
            continue
        fixed = bbva.unify(ln.text)
        out.append(ln if fixed == ln.text else replace(ln, text=fixed))
    return out


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    raw = RawText(lines).text
    head = [squash(x.text) for x in lines[:2]]
    en = re.search(r"中文投資人須知\s*(.+?)\s*\(the", raw, re.S)
    zh = re.search(r"\(the \"Product\"\)\s*(.+?)\s*\(ISIN", raw, re.S)
    en_s, zh_s = squash(en[1]) if en else "", squash(zh[1]) if zh else ""
    issuer = squash(bbva.ISSUER_PREFIX + bbva.ISSUER_EN)
    failed = []
    for ok, message in (
        (head == ["(僅適用於專業投資人)", "中文投資人須知"], "前兩行不是「(僅適用於專業投資人)」「中文投資人須知」"),
        (
            en_s.startswith(squash(bbva.ISSUER_EN)) and squash("Autocallable Basket of Equity Linked Notes") in en_s,
            f"英文名稱不是以「{bbva.ISSUER_EN}」開頭或沒有「Autocallable Basket of Equity Linked Notes」",
        ),
        (
            zh_s.startswith(squash(bbva.ISSUER_EN)) and bbva.NAME_ZH_MARK in zh_s,
            f"中文名稱不是以「{bbva.ISSUER_EN}」開頭或沒有「{bbva.NAME_ZH_MARK}」",
        ),
        (
            all(x in text for x in ("(ISIN:", "(商品種類：", "TDCC商品代號：", "受託機構商品代號：")),
            "找不到「(ISIN: …)」「(商品種類：…)」「TDCC 商品代號：」「受託機構商品代號：」",
        ),
        (
            f"一、相關機構事業概況1.發行機構:{issuer}" in text and "3.總代理人：" in text,
            f"相關機構第 1 點的發行機構不是「{bbva.ISSUER_PREFIX}{bbva.ISSUER_EN}」或第 3 點不是總代理人",
        ),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _product_terms(ti: TextIndex, fields: dict[str, ParsedField], provided: set[str]) -> None:
    """二、 本商品事項：配息（KO 每日年利率／KO 每期每期配息率與天數慣例）、到期條件（下限值或無 KI 門檻、出場值）、
    自動提前贖回事件（KO %、記憶式）（範本規格 §4）。"""
    names = ("ko_pct", "ko_memory", "ki_type")
    sec = iis.section(ti, PRODUCT)
    if sec is None:
        fields.update({n: ParsedField.missing(n, "找不到「二、 本商品事項」到「三、…風險揭露」段落") for n in names})
        return
    st = TextIndex(sec)
    # 配息：KO 每日寫「年利率：y%」；KO 每期寫「配息率（Rate of Interest）：x%」與計息天數慣例
    annual = list(st.finditer(r"年利率：" + PCT))
    per_period = list(st.finditer(r"配息率（RateofInterest）：" + PCT))
    if annual and not per_period:
        fields["coupon_pa_pct"] = iis.capture("coupon_pa_pct", st, r"年利率：" + PCT, Decimal)
        provided.add("coupon_pa_pct")
    elif per_period and not annual:
        fields["coupon_per_period_pct"] = iis.capture(
            "coupon_per_period_pct", st, r"配息率（RateofInterest）：" + PCT, Decimal
        )
        fields["day_count"] = iis.capture("day_count", st, r"計息天數慣例（DayCountFraction）：「([^｣」（]+)")
        provided.update({"coupon_per_period_pct", "day_count"})
    else:
        fields["coupon_pa_pct"] = ParsedField.invalid(
            "coupon_pa_pct", sec, "收益分配事項的配息率寫法缺漏或不在範本規格內"
        )
        provided.add("coupon_pa_pct")
    # 自動提前贖回事件：「最佳表現值」→ 記憶式、「最差表現值」→ 非記憶式；KO %
    found = list(st.finditer(KO_EVENT))
    if len(found) != 1:
        bad = ParsedField.invalid("ko_memory", sec, "自動提前贖回事件的句型缺漏、重複或不在範本規格內")
        fields["ko_memory"], fields["ko_pct"] = bad, replace(bad, name="ko_pct")
    else:
        m = found[0]
        ev = st.lines_for(m.start(), m.end())
        fields["ko_pct"] = ParsedField.present("ko_pct", Decimal(m[2]), ev)
        if "最佳表現值" in m[1]:
            fields["ko_memory"] = ParsedField.present("ko_memory", True, ev)
        elif "最差表現值" in m[1]:
            fields["ko_memory"] = ParsedField.present("ko_memory", False, ev)
        else:
            fields["ko_memory"] = ParsedField.invalid("ko_memory", ev, "自動提前贖回事件的記憶式寫法不在範本規格內")
    # 到期條件
    ki = iis.capture("ki_pct", st, bbva.KI_DEF, Decimal)
    barrier = iis.capture("no_ki_barrier_pct", st, bbva.NO_KI_BARRIER, Decimal)
    if ki.ok and barrier.status == FieldStatus.MISSING:
        fields["ki_type"] = ParsedField("ki_type", FieldStatus.PRESENT, "AM", list(ki.evidence))
        fields["ki_pct"] = ki
        provided.add("ki_pct")
    elif barrier.ok and ki.status == FieldStatus.MISSING:
        fields["ki_type"] = ParsedField("ki_type", FieldStatus.PRESENT, "none", list(barrier.evidence))
        fields["no_ki_barrier_pct"] = barrier
        provided.add("no_ki_barrier_pct")
    else:
        fields["ki_type"] = ParsedField.invalid("ki_type", sec, "到期條件的下限值寫法缺漏、重複或不在範本規格內")
    at_maturity = iis.capture("ko_at_maturity_pct", st, bbva.KO_AT_MATURITY, Decimal)
    memory = fields["ko_memory"]
    if not (memory.ok and memory.value is False):  # 非記憶式沒有出場值條件（同說明書）
        fields["ko_at_maturity_pct"] = at_maturity
        provided.add("ko_at_maturity_pct")


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    raw = RawText(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines, iis.PAGE_SLASH, "N/4")}
    provided = set(PROVIDED)

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 第 1 頁
    fields["name_en"] = raw.capture("name_en", r"中文投資人須知\s*(BBVA.+?)\s*\(the \"Product\"\)")
    fields["name_zh"] = raw.capture("name_zh", r"\(the \"Product\"\)\s*(BBVA.+?)\s*\(ISIN", squash)
    put("isin", r"\(ISIN:([A-Z]{2}[A-Z0-9]{10})\)")
    put("product_type", r"\(商品種類：(.+?)\)")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (
            ("product_code_tdcc", "TDCC 商品代號", "第 1 頁「TDCC 商品代號」", r"TDCC商品代號：(\d{12})"),
            (
                "product_code_distributor",
                "受託機構商品代號",
                "第 1 頁「受託機構商品代號」",
                r"受託機構商品代號：(\d{12})",
            ),
        ),
    )
    # 二、 1. 商品簡介
    put("currency_zh", rf"\(\d+\)計價幣別：({bbva.ZH_CCY})\(\d+\)")
    a = ti.text.find("連結標的資產：")
    b = ti.text.find("商品年期：", a)
    if a < 0 or b < a:
        fields["underlyings"] = ParsedField.missing("underlyings", "找不到「連結標的資產：」表")
    else:
        table = ti.lines_for(a, b)
        tickers = [f"{m[1]} {m[2]}" for m in bbva.TICKER.finditer(RawText(table).text)]
        fields["underlyings"] = (
            ParsedField.present("underlyings", tickers, table)
            if tickers
            else ParsedField.invalid("underlyings", table, "連結標的資產表讀不到彭博代碼（代號 交易所碼 Equity）")
        )
    put("tenor_months", r"\(\d+\)商品年期：(\d+)個月", int)
    put("issue_date", r"\(\d+\)發行日：" + D, parse_date)
    put("maturity_date", r"\(\d+\)到期日：" + D, parse_date)
    put("redemption_start", r"開始受理透過次級市場買賣之贖回日期：" + D, parse_date)
    _product_terms(ti, fields, provided)
    # 一、 相關機構（審查標準）
    org = (
        (
            "issuer_names",
            "issuer_name_org",
            "發行機構名稱（相關機構）",
            "一、 1.",
            r"1\.\s*發行機構\s*[:：]\s*(.+?)\s*\(營業所在地",
        ),
        (
            "distributor_names",
            "distributor_name_org",
            "受託機構名稱（相關機構）",
            "一、 4.",
            r"4\.\s*受託機構\s*[:：]\s*(.+?)\s*\(電話",
        ),
        (
            "distributor_phones",
            "distributor_phone_org",
            "受託機構電話（相關機構）",
            "一、 4.",
            r"4\.\s*受託機構\s*[:：]\s*.+?\(電話\s*[:：]\s*(.+?)\s*，\s*營業所在地",
        ),
        (
            "distributor_addresses",
            "distributor_address_org",
            "受託機構地址（相關機構）",
            "一、 4.",
            r"4\.\s*受託機構\s*[:：]\s*.+?營業所在地\s*[:：]\s*(.+?)\s*\)\s*二、",
        ),
    )
    for container, field, label, where, pattern in org:
        fields[container] = standard_fields.occurrences(
            container, [standard_fields.Occurrence(field, label, where, raw.capture(field, pattern))]
        )
    fees = iis.section(ti, ("四、費用", "五、"))
    if fees is None:
        fields.update(
            {
                standard_fields.fee_field(x): ParsedField.missing(
                    standard_fields.fee_field(x), "找不到「四、 費用」段落"
                )
                for x in iis.FEES
            }
        )
    else:
        fields.update(fee_rates(bbva.fee_table(fees)))
    return iis.IisSheet(fields, ti, frozenset(provided))
