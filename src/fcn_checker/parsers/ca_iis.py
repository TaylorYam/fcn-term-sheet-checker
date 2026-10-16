"""CA 中文投資人須知（docs/templates/ca-zh-iis.md）：段落標題＋標籤定位，句型在去空白的全文上讀。

依 5 份真實樣本建立（同說明書 CA1–CA5）。A4 版面、每頁頁底「第N頁，共4頁」；日期的「年」是 CJK 相容字（U+F98E），
先做 NFKC（同 gs_iis）。第 1 頁中文名稱（帶「(ISIN：…)(下稱「商品」或「本商品」)」）後緊接英文名稱（帶「(ISIN：…)」），
再來是「(種類：…)(TDCC 商品代號：…)」與警語 1.～12.、刊印日期。章名「一、 相關機構事業概況」～「七、…」。
沒有價格表與 %：KO／KI 只有句型；P 型 KO 觀察日只寫參照說明書、界限事件沒有定義，只交出 D 型起始期與有無 KI。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import ca, iis, money
from .common import RawText, compat_normalized, fee_rates
from .layout import TextIndex, join_wrapped_tickers, parse_date

TEMPLATE_ID = "ca-zh-iis"
PARSER_VERSION = "3"  # 2：警語的受託機構名稱（Issue #306）；3：贖回 %、說明文字幣別（Issue #319）
SECTIONS = (
    "本商品之投資風險警語：",
    "中文投資人須知刊印日期：",
    "一、相關機構事業概況",
    "二、本商品事項",
    "三、投資風險",
    "四、費用",
    "五、相關機構之權利、義務及責任",
    "六、協助投資人權益之保護方式",
    "七、總代理人對商品或投資人發生爭議之處理方式及管轄法院",
)
PRODUCT = ("二、本商品事項", "三、投資風險")  # KO／KI 寫法所在的段落
NOISE = re.compile(iis.PAGE_OF)
D = ca.D
PCT = ca.PCT
COUPON = r"配息率" + PCT + r"，換算年利率為" + PCT
TICKER = re.compile(r"(?:^|\s)([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})$")  # 名稱和代碼可能在同一行
# 警語裡寫受託機構名稱的地方（Issue #306：每處都算受託機構名稱出處）：(欄位, 位置, pattern)
WARNING_NAMES = (
    ("distributor_name_w4a", "警語 4.「本商品雖經…審查」", r"4\.本商品雖經(.+?)審查"),
    ("distributor_name_w4b", "警語 4.「…不負本商品投資盈虧之責」", r"之價值，(.+?)不負本商品投資盈虧之責"),
    ("distributor_name_w4c", "警語 4.「…依法不得承諾擔保」", r"盈虧之責。(.+?)依法不得承諾"),
    ("distributor_name_w5", "警語 5.「而非由…所保證」", r"而非由(.+?)所保證"),
    ("distributor_name_w6", "警語 6.「(即…)負責」", r"受託或銷售機構[(（]即(.+?)[)）]負責外"),
)
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
        "currency_others",
        "underlyings",
        "tenor_months",
        "trade_date",
        "issue_date",
        "final_valuation_date",
        "maturity_date",
        "coupon_pa_pct",
        "monthly_coupons",
        "ko_memory",
        "has_ki",
        "redemption_pcts",
        "issuer_names",
        "distributor_names",
        "distributor_addresses",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """CJK 相容字正規化；頁碼以外的行（頁碼另由 `iis.page_totals` 讀）。"""
    return [ln for ln in compat_normalized(lines) if not NOISE.fullmatch(ln.text.strip())]


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    head = squash(lines[0].text) if lines else ""
    issuer = squash(ca.ISSUER)
    m = re.search(r"^中文投資人須知(.+?)\(ISIN：[A-Z0-9]+\)\(下稱「商品」或「本商品」\)(.+?)\(ISIN：", text)
    zh, en = (m[1], m[2]) if m else ("", "")
    failed = []
    for ok, message in (
        (head == "中文投資人須知", "第一行不是「中文投資人須知」"),
        (
            zh.startswith(issuer) and ca.NAME_ZH_MARK in zh,
            f"中文名稱不是以「{ca.ISSUER}」開頭或沒有「{ca.NAME_ZH_MARK}」",
        ),
        (
            en.startswith(issuer) and squash(ca.NAME_EN_MARK) in en,
            f"英文名稱不是以「{ca.ISSUER}」開頭或沒有「{ca.NAME_EN_MARK}」",
        ),
        ("(種類：" in text and "(TDCC商品代號：" in text, "找不到「(種類：…)(TDCC 商品代號：…)」"),
        (
            f"一、相關機構事業概況1.發行機構：{issuer}" in text and "3.總代理人：" in text,
            f"相關機構第 1 點的發行機構不是 {ca.ISSUER} 或沒有第 3 點總代理人",
        ),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def underlyings(lines: list[Line]) -> ParsedField:
    """二、1.(9) 連結標的表的彭博代碼（`ABC UW`），依表上順序（同說明書第 10 項）。"""
    ti = TextIndex(lines)
    a, b = ti.text.find("(9)連結標的："), ti.text.find("(10)商品年期")
    if a < 0 or b < a:
        return ParsedField.missing("underlyings", "找不到二、1.(9) 連結標的表")
    found = [
        (ln, TICKER.search(ln.text.strip())) for ln in join_wrapped_tickers(lines[ti.line_at(a) + 1 : ti.line_at(b)])
    ]
    tickers = [(f"{m[1]} {m[2]}", ln) for ln, m in found if m]
    if not tickers:
        return ParsedField.missing("underlyings", "二、1.(9) 連結標的表讀不到彭博代碼")
    return ParsedField.present("underlyings", [t for t, _ in tickers], [ln for _, ln in tickers])


def _product_terms(ti: TextIndex, fields: dict[str, ParsedField], provided: set[str]) -> None:
    """二、本商品事項：KO 每日的起始期（P 型只寫參照說明書，不交出）、記憶式、有無 KI（範本規格 §4）。"""
    names = ("ko_memory", "has_ki")
    sec = iis.section(ti, PRODUCT)
    if sec is None:
        fields.update({n: ParsedField.missing(n, "找不到「二、本商品事項」到「三、投資風險」段落") for n in names})
        return
    st = TextIndex(sec)
    daily = list(st.finditer(ca.KO_DAILY))
    if len(daily) == 1:
        m = daily[0]
        ev = st.lines_for(m.start(), m.end())
        fields["ko_observation"] = ParsedField.present("ko_observation", "D", ev)
        fields["first_callable_period"] = ParsedField.present("first_callable_period", int(m[1]), ev)
        provided.update({"ko_observation", "first_callable_period"})
    elif daily:
        fields["ko_observation"] = ParsedField.invalid("ko_observation", sec, "自動提前出場觀察日的寫法重複")
        provided.add("ko_observation")
    events = list(st.finditer(ca.KO_EVENT))
    if len(events) != 1:
        fields["ko_memory"] = ParsedField.invalid("ko_memory", sec, "二、2. 自動提前出場事件的句型缺漏或重複")
    else:
        m = events[0]
        tail = st.text[m.end() : m.end() + 40]
        wording = ParsedField.present(
            "ko_memory_wording", (ca.MEMORY_PRIOR in m[1], ca.MEMORY_SAME in tail), st.lines_for(m.start(), m.end())
        )
        fields["ko_memory"] = ca.memory_of(wording)
    ki, plain = ca.KI_MATURITY in st.text, "期末訂價低於執行價" in st.text
    if ki:
        p = st.text.find(ca.KI_MATURITY)
        fields["has_ki"] = ParsedField.present("has_ki", True, st.lines_for(p, p + len(ca.KI_MATURITY)))
    elif plain:
        p = st.text.find("期末訂價低於執行價")
        fields["has_ki"] = ParsedField.present("has_ki", False, st.lines_for(p, p + 9))
    else:
        fields["has_ki"] = ParsedField.invalid("has_ki", sec, "二、2.(c) 到期贖回的寫法不在範本規格內，判斷不出有無 KI")


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    raw = RawText(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(compat_normalized(lines))}
    provided = set(PROVIDED)

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 第 1 頁
    put("name_zh", r"^中文投資人須知(.+?)\(ISIN：")
    fields["name_en"] = raw.capture("name_en", r"\(下稱「商品」或「本商品」\)\s*(.+?)\s*\(ISIN")
    put("isin", ca.ISIN)
    put("product_type", r"\(種類：(.+?)\)")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (("product_code_tdcc", "product_code", "第 1 頁「TDCC 商品代號」", r"\(TDCC商品代號：(\d{12})\)"),),
    )
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        ti,
        (("print_date_iis", "print_date", "中文投資人須知刊印日期", r"中文投資人須知刊印日期：" + D),),
        parse_date,
    )
    # 二、1. 商品簡介
    put("currency_zh", rf"\(6\)計價幣別：({ca.ZH_CCY})\([A-Z]{{3}}\)")
    fields["underlyings"] = underlyings(text_lines)
    put("tenor_months", r"\(10\)商品年期：(\d+)個月", int)
    put("trade_date", r"\(11\)期初訂價日：" + D, parse_date)
    put("issue_date", r"\(12\)發行日：" + D, parse_date)
    put("final_valuation_date", r"\(13\)期末訂價日：" + D, parse_date)
    put("maturity_date", r"\(14\)到期日：" + D, parse_date)
    # 二、2. 收益分配事項：月配息率（共用規則 iis.monthly_coupon 的每處寫法）與年利率；到期贖回的幣別字
    fields["coupon_pa_pct"] = iis.capture("coupon_pa_pct", ti, r"配息率\d+(?:\.\d+)?%，換算年利率為" + PCT, Decimal)
    fields["monthly_coupons"] = iis.occurrences(
        "monthly_coupons",
        ti,
        (("monthly_coupon_iis", "monthly_coupon", "二、2. 收益分配事項", r"配息率" + PCT + r"，換算年利率為"),),
        Decimal,
    )
    fields["currency_others"] = iis.occurrences(
        "currency_others",
        ti,
        (
            (
                "redemption_currency",
                "remainder_currency",
                "二、2.(c) 到期贖回「以X支付剩餘金額」",
                ca.REMAINDER_CURRENCY,
            ),
        ),
    )
    fields["currency_others"] = standard_fields.occurrences(  # 說明文字的幣別字（Issue #319，同說明書）
        "currency_others",
        [
            *fields["currency_others"].value,
            *money.currency_words(
                text_lines,
                ca.PRINCIPAL_CURRENCY,
                "principal_currency",
                "principal_currency",
                "二、3.(4)「可能低於100%X投資本金」",
            ),
            *money.currency_words(
                text_lines, ca.INTEREST_CURRENCY, "interest_rate_currency", "interest_rate_currency", "三、 利率風險"
            ),
        ],
    )
    fields["redemption_pcts"] = iis.occurrences(  # 贖回金額「商品面額x N%」= 同商品說明書第 9 項（Issue #319）
        "redemption_pcts",
        ti,
        (
            ("ko_redemption_pct", "autocall_redemption_pct", "二、2.(b) 自動提前出場贖回金額", ca.KO_REDEMPTION),
            ("maturity_redemption_pct", "maturity_redemption_pct", "二、2.(c) 到期贖回金額", ca.MATURITY_REDEMPTION),
        ),
        Decimal,
    )
    _product_terms(ti, fields, provided)
    # 一、相關機構（審查標準）
    org = (
        (
            "issuer_names",
            "issuer_name_org",
            "issuer_name_org",
            "一、1.",
            r"1\.\s*發行機構\s*[:：]\s*(.+?)\s*\(營業所在地",
        ),
        (
            "distributor_names",
            "distributor_name_org",
            "distributor_name_org",
            "一、4.",
            r"4\.\s*受託或銷售機構\s*[:：]\s*(.+?)\s*，\s*地址",
        ),
        (
            "distributor_addresses",
            "distributor_address_org",
            "distributor_address_org",
            "一、4.",
            r"4\.\s*受託或銷售機構\s*[:：]\s*.+?，\s*地址\s*[:：]\s*(.+?)\s*二、",
        ),
    )
    for container, field, label, where, pattern in org:
        fields[container] = standard_fields.occurrences(
            container, [standard_fields.Occurrence(field, label, where, raw.capture(field, pattern))]
        )
    iis.warning_distributor_names(fields, ti, ("本商品之投資風險警語：", "一、相關機構事業概況"), WARNING_NAMES)
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
    else:  # 表後的「附註：…分銷費用…」不算費用項目
        cut = next((i for i, ln in enumerate(fees) if squash(ln.text).startswith("附註")), None)
        fields.update(fee_rates(fees[:cut]))
    return iis.IisSheet(fields, ti, frozenset(provided))
