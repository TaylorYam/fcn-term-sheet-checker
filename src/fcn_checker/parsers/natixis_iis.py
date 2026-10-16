"""Natixis 中文投資人須知（docs/templates/natixis-zh-iis.md）：段落標題＋標籤定位，句型在去空白的全文上讀。

依 8 份真實樣本建立（同說明書 NX1–NX8）。每頁頁底「第N頁/共M頁」與分類標記（`C1 - Public Natixis`、`C2 - Internal
Natixis`）；第 1 頁第一行是標題與「刊印日期：」（同一行），中文名稱「(下稱「本商品」)」後緊接英文名稱。章名「壹、相關機構」
…「柒、…」（第三章寫「叁」）。沒有價格表：執行／KO／界限價格只有 % 定義；KO／KI 寫法只在「貳、商品事項」到「叁、投資風險」
之間判斷。英文名稱的「(Non Principal」視同說明書封面的「(Non-Principal」（2026-10-15 確認）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from . import iis, natixis
from .common import RawText, fee_rates, number
from .layout import TextIndex, parse_date

TEMPLATE_ID = "natixis-zh-iis"
# 2：計價幣別「● 」（Issue #270）；3：KO 定義沒寫 % 視為參照說明書（Issue #272）；4：無 KI 要有明確寫法（Issue #297）
PARSER_VERSION = "4"
# 貳 2. KO 定義的兩種寫法：寫出 %，或沒寫 %（參照說明書第一章第 14 項，投資人須知不核對 KO %）
KO_DEF_PCT = r"期初進場價格之(\d+(?:\.\d+)?)%"
KO_DEF_INITIAL = "中文產品說明書第一章第14項所列之期初進場價格"
SECTIONS = (
    "本商品之投資風險警語",
    "壹、相關機構",
    "貳、商品事項",
    "叁、投資風險",
    "肆、投資人應負擔的各項費用",
    "伍、相關機構之權利、義務及責任",
    "陸、協助投資人權益保護之方式",
    "柒、發行機構、發行人及受託",  # 新版「受託或銷售機構」、舊版「受託機構」與投資人爭議之處理方式
)
PRODUCT = ("貳、商品事項", "叁、投資風險")  # KO／KI 寫法所在的段落
PAGE = r"第(\d+)頁/共(\d+)頁"  # 頁底頁碼
NOISE = re.compile(r"^(第\d+頁/共\d+頁|C[12] - (?:Inter ?nal|Public) Natixis)$")
D = natixis.D
PCT = natixis.PCT
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
        "denomination",
        "min_subscription",
        "issue_price_pct",
        "strike_pct",
        "tenor_months",
        "trade_date",
        "issue_date",
        "final_valuation_date",
        "maturity_date",
        "coupon_pa_pct",
        "monthly_coupons",
        "ko_observation",
        "ko_memory",
        "ki_type",
        "issuer_names",
        "distributor_names",
        "distributor_addresses",
        "distributor_code",
        "ko_definition",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """頁底頁碼與分類標記以外的行（頁碼另由 `iis.page_totals` 讀）。"""
    return [ln for ln in lines if not NOISE.match(ln.text.strip())]


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    head = squash(lines[0].text) if lines else ""
    m = re.search(r"刊印日期：\d{4}年\d{1,2}月\d{1,2}日(.+?)\(下稱「本商品」\)(.+?)商品種類：", text)
    zh, en = (m[1], m[2]) if m else ("", "")
    failed = []
    for ok, message in (
        (
            head.startswith("中文投資人須知(適用於專業投資人及OSU客戶)") and "刊印日期：" in head,
            "第一行不是「中文投資人須知 (適用於專業投資人及OSU客戶) 刊印日期：…」",
        ),
        (
            zh.startswith(natixis.ISSUER) and "(不保本、無擔保及無保證機構)" in zh,
            f"中文名稱不是以「{natixis.ISSUER}」開頭或沒有「(不保本、無擔保及無保證機構)」",
        ),
        (
            en.startswith("Natixis–") and squash(natixis.NAME_EN_MARK) in en,
            f"英文名稱沒有「Natixis –」與「{natixis.NAME_EN_MARK}」",
        ),
        ("商品種類：" in text and "TDCC商品代號為" in text, "找不到「商品種類：…，TDCC商品代號為…」"),
        (
            f"壹、相關機構1.發行機構：{natixis.ISSUER}" in text and "2.發行人：" in text,
            f"相關機構第 1 點的發行機構不是 {natixis.ISSUER} 或第 2 點不是發行人",
        ),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _product_terms(ti: TextIndex, fields: dict[str, ParsedField], provided: set[str]) -> None:
    """貳、商品事項：KO 觀察方式（D 型另有起始期）、記憶式、KO／K／KI % 定義、KI 型態（範本規格 §4）。"""
    names = ("ko_observation", "first_callable_period", "ko_memory", "ki_type", "strike_pct", "ki_pct", "ko_definition")
    sec = iis.section(ti, PRODUCT)
    if sec is None:
        fields.update({n: ParsedField.missing(n, "找不到「貳、商品事項」到「叁、投資風險」段落") for n in names})
        return
    st = TextIndex(sec)
    daily = list(st.finditer(natixis.KO_DAILY))
    periodic = list(st.finditer(r"自動提前到期觀察日：如中文產品說明書第一章第12\(7\)項所列"))
    if len(daily) == 1 and not periodic:
        m = daily[0]
        ev = st.lines_for(m.start(), m.end())
        fields["ko_observation"] = ParsedField.present("ko_observation", "D", ev)
        k = natixis.CN_NUM.get(m[1])
        fields["first_callable_period"] = (
            ParsedField.present("first_callable_period", k, ev)
            if k
            else ParsedField.invalid("first_callable_period", ev, f"起始期「{m[1]}」無法辨識")
        )
        provided.add("first_callable_period")
    elif len(periodic) == 1 and not daily:
        m = periodic[0]
        fields["ko_observation"] = ParsedField.present("ko_observation", "P", st.lines_for(m.start(), m.end()))
    else:
        fields["ko_observation"] = ParsedField.invalid(
            "ko_observation", sec, "自動提前到期觀察日的寫法缺漏、重複或不在範本規格內"
        )
    fields["ko_memory"] = natixis.ko_memory(st, sec, where="貳、商品事項")
    fields["strike_pct"] = iis.capture("strike_pct", st, natixis.STRIKE_DEF, Decimal)
    # KO 價格定義：「期初進場價格之Y%」→ ko_pct = Y；寫成「…所列之期初進場價格」（沒有 %）是參照說明書第一章第 14 項，
    # 不交出 ko_pct、不核對 KO %（2026-10-15 確認，Issue #272）
    found = list(
        st.finditer(r"自動提前到期贖回價格為(.{0,40}?期初進場價格(?:之\d+(?:\.\d+)?%)?)")
    )  # 值的兩種寫法見 KO_DEF_PCT／KO_DEF_INITIAL
    if len(found) != 1:
        fields["ko_definition"] = ParsedField.missing(
            "ko_definition", "找不到或有多處「自動提前到期贖回價格為…期初進場價格」"
        )
    else:
        m = found[0]
        ev = st.lines_for(m.start(), m.end())
        fields["ko_definition"] = ParsedField.present("ko_definition", m[1], ev)
        pct = re.fullmatch(KO_DEF_PCT, m[1])
        if pct:
            fields["ko_pct"] = ParsedField.present("ko_pct", Decimal(pct[1]), ev)
            provided.add("ko_pct")
    day = natixis.ki_day(st, sec, r"觸及事件判定日：(.+?)，?並依「中斷日」", "商品簡介")
    if day.status == FieldStatus.MISSING:  # 舊版只有到期贖回段的定義（NX1）
        day = natixis.ki_day(st, sec, r"「觸及事件判定日」係指於(.+?)，並依「中斷日」", "到期贖回")
    fields["ki_type"] = natixis.ki_type(day, st, sec, where="貳、商品事項")
    ki = fields["ki_type"]
    if not (ki.ok and ki.value == "none"):
        fields["ki_pct"] = iis.capture("ki_pct", st, natixis.KI_DEF, Decimal)
        provided.add("ki_pct")


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    raw = RawText(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines, PAGE, "第N頁/共M頁")}
    provided = set(PROVIDED)

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 第 1 頁
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        ti,
        (
            (
                "print_date_iis",
                "刊印日期",
                "第 1 頁中文投資人須知刊印日期",
                r"^中文投資人須知\(適用於專業投資人及OSU客戶\)刊印日期：" + D,
            ),
        ),
        parse_date,
    )
    put("name_zh", r"刊印日期：\d{4}年\d{1,2}月\d{1,2}日(.+?)\(下稱「本商品」\)")
    fields["name_en"] = raw.capture(
        "name_en",
        r"\(下稱「\s*本商品」\)\s*(Natixis.+?)\s*商品種類\s*[:：]",
        lambda x: natixis.non_principal(x.strip()),
    )
    put("product_type", r"商品種類：(.+?)，TDCC")
    code = natixis.PRODUCT_CODES
    fields["product_codes"] = iis.occurrences(
        "product_codes", ti, (("product_code_tdcc", "封面商品代號", "第 1 頁「TDCC商品代號為」", code),)
    )
    found = list(ti.finditer(code))
    fields["isin"] = (
        ParsedField.present("isin", found[0][3], ti.lines_for(found[0].start(), found[0].end()))
        if len(found) == 1
        else ParsedField.missing("isin", "找不到「TDCC商品代號為…，ISIN為…」或出現多次")
    )
    fields["distributor_code"] = natixis.distributor_code(ti, code)
    # 貳、1. 商品簡介
    put("currency_zh", rf"[•‧●]\s*計價幣別：({natixis.ZH_CCY})\([A-Z]{{3}}\)")  # 列項符號也可能是「● 」（Issue #270）
    denom = rf"●每單位商品面額：([\d,]+)({natixis.CCY})"
    put("denomination", denom, number)
    minimum = rf"●最低申購金額：\d+單位商品面額，即([\d,]+)({natixis.CCY})"
    put("min_subscription", minimum, number)
    fields["currency_others"] = iis.occurrences(
        "currency_others",
        ti,
        (
            (
                "denomination_currency",
                "每單位商品面額幣別",
                "貳 1. 每單位商品面額",
                rf"●每單位商品面額：[\d,]+({natixis.CCY})",
            ),
            (
                "min_subscription_currency",
                "最低申購金額幣別",
                "貳 1. 最低申購金額",
                minimum.replace("([\\d,]+)", "[\\d,]+"),
            ),
        ),
    )
    put("issue_price_pct", r"[•‧]發行價格：" + PCT, Decimal)
    put("tenor_months", r"[•‧]商品年期：(\d+)個月", int)
    put("trade_date", r"[•‧]交易日/期初進場日：" + D, parse_date)
    put("issue_date", r"●發行日：" + D, parse_date)
    put("maturity_date", r"●到期日：最終評價日後的第.{1,2}個營業日，即" + D, parse_date)
    put("final_valuation_date", r"[•‧]最終評價日：" + D, parse_date)
    # 貳、2. 收益分配事項：月配息率（共用規則 iis.monthly_coupon 的每處寫法）與年利率
    put("coupon_pa_pct", r"月配息率：\d+(?:\.\d+)?%\(即年利率" + PCT + r"\)", Decimal)
    fields["monthly_coupons"] = iis.occurrences(
        "monthly_coupons",
        ti,
        (("monthly_coupon_iis", "月配息率", "貳 2. 收益分配事項", r"月配息率：" + PCT + r"\(即年利率"),),
        Decimal,
    )
    _product_terms(ti, fields, provided)
    # 壹、相關機構（審查標準）
    org = (
        (
            "issuer_names",
            "issuer_name_org",
            "發行機構名稱（相關機構）",
            "壹 1.",
            r"1\.\s*發行機構\s*[:：]\s*(.+?)\s*\(地址",
        ),
        (
            "distributor_names",
            "distributor_name_org",
            "受託機構名稱（相關機構）",
            "壹 4.",
            rf"4\.\s*{natixis.DISTRIBUTOR}\s*[:：]\s*(.+?)\s*\(地址",
        ),
        (
            "distributor_addresses",
            "distributor_address_org",
            "受託機構地址（相關機構）",
            "壹 4.",
            rf"4\.\s*{natixis.DISTRIBUTOR}\s*[:：]\s*.+?\(地址\s*[:：]\s*(.+?)\s*\)\s*貳、",
        ),
    )
    for container, field, label, where, pattern in org:
        fields[container] = standard_fields.occurrences(
            container, [standard_fields.Occurrence(field, label, where, raw.capture(field, pattern))]
        )
    fees = iis.section(ti, ("肆、投資人應負擔的各項費用", "伍、"))
    if fees is None:
        fields.update(
            {
                standard_fields.fee_field(x): ParsedField.missing(
                    standard_fields.fee_field(x), "找不到「肆、投資人應負擔的各項費用」段落"
                )
                for x in iis.FEES
            }
        )
    else:
        fields.update(fee_rates(fees))
    return iis.IisSheet(fields, ti, frozenset(provided))
