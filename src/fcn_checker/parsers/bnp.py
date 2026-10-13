"""BNP 中文產品說明書（docs/templates/bnp-zh-product-description.md）：章＋條號＋子項＋標籤定位，保留原文證據。

五章、章名用頓號；封面與第一章的條號「N.」獨立一行，內容在下一行；全文半形括號；風險等級寫 [RR4]。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import bnp_scenario, bnp_tables, money
from .layout import Document, LayoutSpec, TextIndex, capture, join_text, parse_date

TEMPLATE_ID = "bnp-zh-pd"
PARSER_VERSION = "1"
CHAPTER_NAMES = {
    1: ("一", "商品相關基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
}
# 章名用頓號；條號「N.」獨立一行、標題在下一行（第四章後段「N. 標題」同一行），x≈50；子項「(n)」x≈54–63
LAYOUT = LayoutSpec(
    r"第{zh}章、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    60,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    80,
    CHAPTER_NAMES,
)
PAGE_HEADER = re.compile(r"^第\s*(\d+)\s*頁，共\s*(\d+)\s*頁$")
COVER_TITLE = "中文產品說明書(適用於專業投資人及OSU客戶)"
TITLE_START = "(下稱「本產品說明書」)"
ISSUER_EN = "BNP Paribas Issuance B.V."
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
NUM = r"([\d,]+(?:\.\d+)?)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
FEES = ("申購費用", "提前贖回費用", "分銷費用")
# 自動提前贖回事件定義句（第 16 項與第 20 項(1)）：記憶式有「曾經於…或之前任一自動提前贖回評價日」，非記憶式只看當日
MEMORY_MARK = "或之前任一自動提前贖回評價日"
NON_MEMORY = re.compile(r"^(每一連結標的之收盤價格皆|連結標的之收盤價格)大於或等於其(各自)?自動提前贖回價格$")
KO_D = r"自動提前贖回評價日：自第(\d+)個配息期結束日\(含該日\)起至期末評價日\(含該日\)的每一個預定交易日"
KO_P = (r"配息日：如下表所列之日期", r"自動提前贖回評價日：如下表所列之日期")
KI_SENTENCE = re.compile(r"「觸及生效事件觀察日」：指(.+?)。")
KI_WORDINGS = (  # 定義句開頭 → KI 型態（範本規格 §4.4）
    ("期末評價日", "AM"),
    ("自期初訂價日(不含)起至期末評價日(含)止之每一個預定交易日", "D"),
    ("每一個配息期結束日", "P"),
)
NO_KI = re.compile(r"若於期末評價日，(?:表現最差)?連結標的之收盤價格大於或等於其執行價格")
DECIMALS = {"四捨五入至小數點以下第二位": 2, "四捨五入至整數位": 0}


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁首「第N頁，共M頁」不算內文（總頁數另由 `page_headers` 讀）。"""
    return Document([x for x in lines if not PAGE_HEADER.fullmatch(squash(x.text))], LAYOUT)


def page_headers(lines: Sequence[Line]) -> list[tuple[int, int, Line]]:
    """每頁頁首的（頁碼, 總頁數, 原文行）。"""
    out = []
    for ln in lines:
        m = PAGE_HEADER.fullmatch(squash(ln.text))
        if m:
            out.append((int(m[1]), int(m[2]), ln))
    return out


# ---------------------------------------------------------------- 封面項目


@dataclass(frozen=True)
class CoverItem:
    label: str  # 冒號前的標籤（去空白）；第 1 項沒有冒號時為空
    value: str  # 冒號後的值，跨行合併（保留行內空白）
    text: str  # 整項文字，跨行合併
    lines: tuple[Line, ...]


def cover_items(lines: Sequence[Line]) -> dict[int, CoverItem]:
    """封面編號 1.–16.：項號獨立一行（x < 70）、依序出現；其後的行都是該項的內容，到下一個項號為止。"""
    parts: dict[int, list[Line]] = {}
    current, expect = None, 1
    for ln in lines:
        m = re.fullmatch(r"(\d{1,2})\.", ln.text.strip())
        if m and ln.x0 < 70 and int(m[1]) == expect:
            current = expect
            parts[current] = []
            expect += 1
        elif current is not None:
            parts[current].append(ln)
    out = {}
    for n, lns in parts.items():
        text = join_text(lns)
        label, colon, value = text.partition("：")
        out[n] = CoverItem(squash(label) if colon else "", value.strip() if colon else text, text, tuple(lns))
    return out


def cover_title(lines: Sequence[Line]) -> ParsedField:
    """封面標題名稱：「(下稱「本產品說明書」)」之後、第 1 項之前的行。"""
    name = "cover_title"
    start = next((i for i, ln in enumerate(lines) if squash(ln.text) == squash(TITLE_START)), None)
    end = next((i for i, ln in enumerate(lines) if re.fullmatch(r"1\.", ln.text.strip()) and ln.x0 < 70), None)
    if start is None or end is None or end <= start + 1:
        return ParsedField.missing(name, "找不到封面標題名稱（「(下稱「本產品說明書」)」與第 1 項之間）")
    title = lines[start + 1 : end]
    return ParsedField.present(name, join_text(title).strip(), list(title))


def _item(items: dict[int, CoverItem], n: int, name: str, label: str, pattern: str | None = None) -> ParsedField:
    it = items.get(n)
    if it is None or it.label != squash(label):
        return ParsedField.missing(name, f"找不到封面第 {n} 項「{label}」")
    value = it.value
    if pattern is not None:
        m = re.fullmatch(pattern, value, re.S)
        if not m:
            return ParsedField.invalid(name, list(it.lines), f"封面第 {n} 項「{label}」的值格式未知")
        value = m[1].strip()
    return ParsedField.present(name, value, list(it.lines))


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 BNP 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    zh, en = (squash(items[n].value) if n in items else "" for n in (2, 3))
    issuer = items.get(5)
    arts = doc.articles(1)
    failed = []
    for test, message in (
        (COVER_TITLE in TextIndex(cover).text, "封面標題不是「中文產品說明書(適用於專業投資人及OSU 客戶)」"),
        (
            issuer is not None and issuer.label == "發行機構名稱" and squash(issuer.value) == squash(ISSUER_EN),
            f"封面第 5 項發行機構名稱不是 {ISSUER_EN}",
        ),
        (
            zh.startswith("法國巴黎銀行") and "固定配息" in zh and "結構型商品(不保本)(無擔保)" in zh,
            "商品中文名稱不是法國巴黎銀行固定配息…結構型商品(不保本)(無擔保)",
        ),
        (squash(f"issued by {ISSUER_EN} (unsecured)") in en, f"商品英文名稱不含 issued by {ISSUER_EN} (unsecured)"),
        (all(n in doc.chapters for n in range(1, 6)), "章名不是「第X章、」寫法或五章不完整"),
        (set(range(1, 28)) <= set(arts), "第一章條號 1–27 不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass(frozen=True)
class Mention:
    """月配息率或年化報酬率的一個出處；值讀不到時為 None。"""

    where: str
    value: Decimal | None
    evidence: tuple[Evidence, ...]


@dataclass
class BnpTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    scenarios: bnp_scenario.ScenarioSection  # 第 18 項(3)
    coupon_mentions: list[Mention]  # 月配息率的每個出處（第 15 項兩處、第 18 項假設與各情境算式）
    annualized_mentions: list[Mention]  # 獲利情境（自動提前贖回、最佳）的年化報酬率
    page_headers: list[tuple[int, int, Line]]  # 每頁頁首的（頁碼, 總頁數, 原文行）

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def _number(x: str) -> int | Decimal:
    d = Decimal(x.replace(",", ""))
    return int(d) if d == d.to_integral_value() else d


def _derived(name: str, src: ParsedField, value=None) -> ParsedField:
    """由另一個欄位推得（證據沿用來源）：來源有問題時沿用來源的狀態與說明。"""
    if not src.ok:
        return ParsedField(name, src.status, None, list(src.evidence), list(src.candidates), src.note)
    return ParsedField(name, FieldStatus.PRESENT, value, list(src.evidence))


def _labelled(name: str, lines: Sequence[Line], label: str) -> ParsedField:
    """以「標籤：值」開頭的那一行（剛好一行）的值，保留行內空白；標籤比對去空白。"""
    hits = [ln for ln in lines if squash(ln.text).startswith(squash(label))]
    if not hits:
        return ParsedField.missing(name, f"找不到「{label}」")
    values = []
    for ln in hits:
        m = re.match(r"^\s*" + "".join(re.escape(c) + r"\s*" for c in squash(label)) + r"(.*)$", ln.text)
        values.append(m[1].strip() if m else "")
    return ParsedField.from_hits(name, [(v, [ln]) for v, ln in zip(values, hits, strict=True)])


def memory_of(clause: str) -> bool | None:
    """自動提前贖回事件定義句的條件子句 → 是否記憶式；寫法不在範本規格內為 None。"""
    if MEMORY_MARK in clause and "曾經於" in clause and "大於或等於其" in clause:
        return True
    if NON_MEMORY.fullmatch(clause):
        return False
    return None


def _ko_memory(name: str, lines: list[Line], pattern: str, missing: str) -> ParsedField:
    ti = TextIndex(lines)
    hits = []
    for m in ti.finditer(pattern):
        memory = memory_of(m[1])
        lns = ti.lines_for(m.start(), m.end())
        if memory is None:
            return ParsedField.invalid(name, lns, "自動提前贖回事件的條件寫法不在範本規格內")
        hits.append((memory, lns))
    return ParsedField.from_hits(name, hits, missing_note=missing)


def _ki_type(lines: list[Line]) -> ParsedField:
    """第 17 項「觸及生效事件觀察日」定義句 → KI 型態；沒有這句、也沒有「觸及生效事件」字樣、且到期贖回寫
    「若於期末評價日，…收盤價格大於或等於其執行價格」時為無 KI。"""
    name = "ki_type"
    ti = TextIndex(lines)
    hits = []
    for m in KI_SENTENCE.finditer(ti.text):
        lns = ti.lines_for(m.start(), m.end())
        kind = next((k for start, k in KI_WORDINGS if m[1].startswith(start)), None)
        if kind is None:
            return ParsedField.invalid(name, lns, "觸及生效事件觀察日的寫法不在範本規格內")
        hits.append((kind, lns))
    if hits:
        return ParsedField.from_hits(name, hits)
    if "觸及生效事件" not in ti.text and NO_KI.search(ti.text):
        return ParsedField.present(name, "none", lines)
    return ParsedField.invalid(name, lines, "找不到觸及生效事件觀察日定義，也不是無 KI 的到期贖回寫法")


def read(lines: Sequence[Line]) -> BnpTermSheet:
    """讀出標準欄位與 BNP 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    fields: dict[str, ParsedField] = {}

    def article(n: int, chapter: int = 1) -> list[Line]:
        return doc.span_lines((arts if chapter == 1 else doc.articles(chapter)).get(n))

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    # 封面
    codes = items.get(1)
    for key, pattern, note in (
        ("product_code", r"TDCC商品代號為(\d{12})", "封面第 1 項找不到 12 位商品代號"),
        ("trustee_product_code", r"受託機構商品代號為(\d{12}|)，", "封面第 1 項找不到受託機構商品代號"),
        ("isin", r"ISIN為([A-Z]{2}[A-Z0-9]{10})", "封面第 1 項找不到 ISIN"),
    ):
        if codes is None:
            fields[key] = ParsedField.missing(key, "找不到封面第 1 項")
            continue
        m = re.search(pattern, squash(codes.text))
        fields[key] = (
            ParsedField.present(key, m[1], list(codes.lines))
            if m
            else ParsedField.invalid(key, list(codes.lines), note)
        )
    fields["cover_title"] = cover_title(cover)
    zh = _item(items, 2, "name_zh", "商品中文名稱", r"(.+?)\(下稱「商品」\)")
    fields["name_zh"] = zh
    fields["name_en"] = _item(items, 3, "name_en", "英文名稱")
    fields["product_type"] = _item(items, 4, "product_type", "商品種類")
    fields["issuer_name_cover"] = _item(items, 5, "issuer_name_cover", "發行機構名稱")
    fields["currency_zh"] = _item(items, 9, "currency_zh", "計價幣別", r"([^\s(（]+)")
    fields["guarantor_name_cover"] = _item(items, 11, "guarantor_name_cover", "保證機構之名稱及地址", r"(.+?)\(下稱.*")
    distributor = _item(items, 12, "distributor", "受託機構之名稱、電話及地址")
    parts = re.fullmatch(r"(.+?)，電話：(.+?)，地址：(.+)", distributor.value, re.S) if distributor.ok else None
    for k, key in enumerate(("distributor_name_cover", "distributor_phone_cover", "distributor_address_cover"), 1):
        if distributor.ok and parts is None:
            note = "封面第 12 項無法切出名稱、電話、地址"
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=list(distributor.evidence), note=note)
        else:
            fields[key] = _derived(key, distributor, parts[k].strip() if parts else None)
    approval = _item(items, 13, "approval_date", "玉山綜合證券股份有限公司(受託機構)審查通過之日期")
    approved = parse_date(approval.value) if approval.ok else None
    fields["approval_date"] = (
        ParsedField("approval_date", FieldStatus.INVALID, evidence=approval.evidence, note="審查通過日期不是日期")
        if approval.ok and approved is None
        else _derived("approval_date", approval, approved)
    )
    printed = _item(items, 16, "print_date", "產品說明書刊印日期")
    print_date = parse_date(printed.value) if printed.ok else None
    fields["print_date"] = (
        ParsedField("print_date", FieldStatus.INVALID, evidence=printed.evidence, note="刊印日期不是日期")
        if printed.ok and print_date is None
        else _derived("print_date", printed, print_date)
    )
    full = TextIndex(doc.lines)
    fields["base_prospectus_dates"] = capture("base_prospectus_dates", full, r"發行機構於?" + D + r"公告之", parse_date)

    # 第一章
    art1 = article(1)
    fields["name_art1"] = (
        ParsedField.present("name_art1", join_text(art1).split("：", 1)[1].strip(), art1)
        if art1 and "：" in join_text(art1)
        else ParsedField.missing("name_art1", "找不到第一章第 1 項商品名稱")
    )
    art3 = article(3)
    fields["issuer_name_ch1"] = _labelled("issuer_name_ch1", art3, "發行機構：")
    fields["guarantor_name_ch1"] = _labelled("guarantor_name_ch1", art3, "保證機構：")
    fields["currency_art5"] = _labelled("currency_art5", article(5), "計價幣別：")
    art6 = article(6)
    put("denomination", art6, r"商品面額：每單位商品" + NUM + CCY, _number)
    put("denomination_currency", art6, rf"商品面額：每單位商品{money.NUMBER}({CCY})")
    put("issue_price_pct", article(7), r"發行價格：" + PCT, Decimal)
    art11 = article(11)
    table = bnp_tables.price_table("underlying_table", _art11_region(art11), with_tickers=True)
    fields["underlying_table"] = table
    _prices(fields, table, art11)

    art14 = article(14)
    put("tenor_months", art14, r"商品年期：(\d+)個月期", int)
    put("trade_date", art14, r"期初訂價日：" + D, parse_date)
    put("issue_date", art14, r"發行日：" + D, parse_date)
    put("final_valuation_date", art14, r"期末評價日：" + D, parse_date)
    put("maturity_date", art14, r"到期日：[^(]*?預定為" + D, parse_date)
    fields["date_table"] = bnp_tables.date_table(art14)
    fields["ko_wording"] = _ko_wording(art14)
    _schedule(fields)

    art15 = article(15)
    put("coupon_pa_pct", art15, r"月配息率：\d+(?:\.\d+)?%\(即年利率" + PCT + r"\)", Decimal)
    put("monthly_coupon_pct", art15, r"月配息率：" + PCT + r"\(即年利率", Decimal)
    put("coupon_formula_pct", art15, r"固定配息金額=每單位面額×" + PCT, Decimal)
    put("coupon_decimals", art15, r"\((四捨五入至小數點以下第二位|四捨五入至整數位)\)計算以", lambda x: DECIMALS[x])
    put("coupon_currency_art15", art15, rf"計算以({CCY})計值的配息款項")
    art16 = article(16)
    put("autocall_redemption_pct", art16, r"自動提前贖回金額=商品面額×" + PCT, Decimal)
    put("currency_art16", art16, rf"計算之({CCY})金額\(「自動提前贖回金額」\)")
    fields["ko_memory"] = _ko_memory(
        "ko_memory",
        art16,
        r"「自動提前贖回事件」：計算代理機構將依專業合理的方式釐定，若於任一自動提前贖回評價日，(.+?)，則視為發生自動提前贖回事件",
        "找不到第 16 項自動提前贖回事件定義句",
    )
    art17 = article(17)
    put("maturity_redemption_pct", art17, r"商品面額×" + PCT + r"；或", Decimal)
    put("min_coupon_rate", art17, r"最低配息率為" + PCT + r"[（(]年化[）)]", Decimal)
    put("currency_art17", art17, rf"計算之({CCY})金額或實體連結標的")
    fields["ki_type"] = _ki_type(art17)
    fields["ko_memory_art20"] = _ko_memory(
        "ko_memory_art20",
        doc.span_lines(doc.subitems(arts.get(20)).get(1)),
        r"自動提前贖回事件：若於任一自動提前贖回評價日，(.+?)，則視為發生自動提前贖回事件",
        "找不到第 20 項(1) 自動提前贖回事件定義句",
    )
    _pcts(fields)
    scenarios = bnp_scenario.section(doc.span_lines(doc.subitems(arts.get(18)).get(3)))

    # 第二章
    ch2 = doc.articles(2)
    fields["issuer_name_ch2"] = _labelled("issuer_name_ch2", doc.span_lines(ch2.get(1)), "(1)事業名稱：")
    fields["guarantor_name_ch2"] = _labelled("guarantor_name_ch2", doc.span_lines(ch2.get(2)), "(1)事業名稱：")
    trustee = _trustee_block(doc.span_lines(ch2.get(3)))
    fields["distributor_name_ch2"] = _labelled("distributor_name_ch2", trustee, "◎事業名稱：")
    fields["distributor_address_ch2"] = _labelled("distributor_address_ch2", trustee, "◎營業所在地：")
    fields["chairman"] = _labelled("chairman", trustee, "◎負責人姓名：")

    # 第四章
    ch4 = doc.articles(4)
    put("subscription_start", doc.span_lines(ch4.get(1)), r"商品開始受理申購日期：" + D + r"至", parse_date)
    put(
        "subscription_end",
        doc.span_lines(ch4.get(1)),
        r"商品開始受理申購日期：\d{4}年\d{1,2}月\d{1,2}日至" + D,
        parse_date,
    )
    for label in FEES:
        fields[standard_fields.fee_field(label)] = bnp_tables.fee(
            label, standard_fields.fee_field(label), doc.span_lines(ch4.get(2))
        )
    art4 = doc.span_lines(ch4.get(4))
    put("minimum_subscription", art4, r"最低申購金額：" + NUM + CCY, _number)
    put("minimum_additional", art4, r"最低加購金額：" + NUM + CCY, _number)
    put("minimum_subscription_currency", art4, rf"最低申購金額：{money.NUMBER}({CCY})")
    put("minimum_additional_currency", art4, rf"最低加購金額：{money.NUMBER}({CCY})")
    put("issue_price_ch4", doc.span_lines(ch4.get(5)), r"每單位商品面額×" + PCT + r"\+申購費用", Decimal)
    put("minimum_issue_size", doc.span_lines(ch4.get(7)), r"至少達(\(等值\)[^之]*?)之總申購價值")
    art8 = doc.span_lines(ch4.get(8))
    put("minimum_redemption", art8, r"最低贖回金額為" + NUM + CCY, _number)
    put("redemption_increment", art8, r"並以" + NUM + CCY + r"為增加贖回之單位", _number)
    put("minimum_redemption_currency", art8, rf"最低贖回金額為{money.NUMBER}({CCY})")
    put("redemption_increment_currency", art8, rf"並以{money.NUMBER}({CCY})為增加贖回之單位")
    fields["distributor_phone_ch4"] = _labelled(
        "distributor_phone_ch4", doc.span_lines(ch4.get(16)), "受託機構聯絡方式："
    )

    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_subscription", "最低申購金額", "第四章第 4 項最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "第四章第 4 項最低加購金額", fields["minimum_additional"]),
            occ("minimum_redemption", "最低贖回金額", "第四章第 8 項最低贖回金額", fields["minimum_redemption"]),
            occ("redemption_increment", "增加贖回單位", "第四章第 8 項增加贖回之單位", fields["redemption_increment"]),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ(
                "subscription_start",
                "開始受理申購日（起）",
                "第四章第 1 項商品開始受理申購日期（起）",
                fields["subscription_start"],
            ),
            occ(
                "subscription_end",
                "開始受理申購日（迄）",
                "第四章第 1 項商品開始受理申購日期（迄）",
                fields["subscription_end"],
            ),
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "封面第 16 項刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [
            occ(
                "issue_price_scenario",
                "發行價格（第 18 項假設）",
                "第 18 項假設發行價格",
                _hit_field("issue_price_scenario", scenarios.issue_price, 0, "第 18 項假設找不到發行價格或出現多次"),
            ),
            occ(
                "issue_price_principal",
                "發行價格（第 18 項投資本金）",
                "第 18 項投資本金算式",
                _hit_field("issue_price_principal", scenarios.principal, 0, "第 18 項假設找不到投資本金算式或出現多次"),
            ),
            occ("issue_price_ch4", "發行價格（第四章申購價金）", "第四章第 5 項申購價金", fields["issue_price_ch4"]),
        ],
    )
    fields["currency_others"] = standard_fields.occurrences("currency_others", _currency_occurrences(fields, scenarios))
    coupons, annualized = _mentions(fields, scenarios)
    return BnpTermSheet(fields, full, doc, scenarios, coupons, annualized, page_headers(lines))


def _hit_field(name: str, hit: bnp_scenario.Hit | None, index: int, missing: str) -> ParsedField:
    if hit is None:
        return ParsedField.missing(name, missing)
    return ParsedField.present(name, hit.values[index], list(hit.lines))


def _art11_region(art11: list[Line]) -> list[Line]:
    """第 11 項標的表：「連結標的名稱」那一行起，到「「籃子」指…」或「相對權重」之前。"""
    start = next((i for i, ln in enumerate(art11) if squash(ln.text).startswith("連結標的名稱")), None)
    if start is None:
        return []
    end = next(
        (i for i in range(start + 1, len(art11)) if squash(art11[i].text).startswith(("「籃子」指", "相對權重"))),
        len(art11),
    )
    return art11[start:end]


def _ko_wording(art14: list[Line]) -> ParsedField:
    """第 14 項 (6)(7)(8) 的寫法 → KO 觀察方式與 D 型的第一個可提前出場期：{"type": "D"|"P", "k": int | None}。"""
    name = "ko_wording"
    ti = TextIndex(art14)
    d_hits = list(ti.finditer(KO_D))
    p_hit = all(re.search(p, ti.text) for p in KO_P)
    if len(d_hits) == 1 and not p_hit:
        m = d_hits[0]
        return ParsedField.present(name, {"type": "D", "k": int(m[1])}, ti.lines_for(m.start(), m.end()))
    if p_hit and not d_hits:
        lns = [ln for ln in art14 if any(re.search(p, squash(ln.text)) for p in KO_P)]
        return ParsedField.present(name, {"type": "P", "k": None}, lns)
    return ParsedField.invalid(name, art14, "第 14 項配息日／自動提前贖回評價日的寫法缺漏或不在範本規格內")


def _schedule(fields: dict[str, ParsedField]) -> None:
    """KO 觀察方式（日期表表頭與第 14 項寫法一致）、第一個可提前出場期、提前出場排程（範本規格 §4.1、§4.3）。"""
    table, wording = fields["date_table"], fields["ko_wording"]
    keys = ("ko_observation", "first_callable_period", "autocall_schedule")
    bad = next((p for p in (table, wording) if not p.ok), None)
    if bad is not None:
        for key in keys:
            fields[key] = _derived(key, bad)
        return
    kind, rows = table.value["type"], table.value["rows"]
    ev = table.evidence + wording.evidence
    if wording.value["type"] != kind:
        note = "第 14 項日期表表頭與 (6)(7)(8) 的寫法不一致（一個是每日觀察、一個是每期觀察）"
        for key in keys:
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)
        return
    if kind == "D":
        k = wording.value["k"]
        dates = {r["period"]: r["end"] for r in rows if r["period"] >= k}
    else:
        dated = [r["period"] for r in rows if r["pricing"] is not None]
        if not dated:
            note = "日期表每一期的自動提前贖回評價日都是「不適用」，找不到第一個可提前出場期"
            for key in keys:
                fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)
            return
        k = dated[0]
        dates = {r["period"]: r["pricing"] for r in rows if r["pricing"] is not None}
    fields["ko_observation"] = ParsedField("ko_observation", FieldStatus.PRESENT, kind, ev)
    fields["first_callable_period"] = ParsedField(
        "first_callable_period", FieldStatus.PRESENT, k, list(wording.evidence)
    )
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule", FieldStatus.PRESENT, standard_fields.AutocallSchedule(kind, k, len(rows), dates), ev
    )


def _prices(fields: dict[str, ParsedField], table: ParsedField, art11: list[Line]) -> None:
    """第 11 項標的表 → 標準欄位：標的（彭博代碼）與各標的價格列。"""
    if not table.ok:
        for key in ("underlyings", "underlying_prices"):
            fields[key] = _derived(key, table)
        return
    tickers = table.value["tickers"]
    fields["underlyings"] = ParsedField.present("underlyings", list(tickers), bnp_tables.ticker_lines(art11))
    fields["underlying_prices"] = ParsedField(
        "underlying_prices",
        FieldStatus.PRESENT,
        tuple(
            standard_fields.PriceRow(ticker, dict(row["prices"]), tuple(Evidence.of(ln) for ln in lns))
            for ticker, row, lns in zip(tickers, table.value["rows"], table.value["row_lines"], strict=True)
        ),
        list(table.evidence),
    )


def _pcts(fields: dict[str, ParsedField]) -> None:
    """執行價／自動提前贖回價／下限價的百分比：第 11 項每檔都寫，須全部相同（否則歧義）；無 KI 時下限價不適用。"""
    table, ki = fields["underlying_table"], fields["ki_type"]
    for key, col in (("strike_pct", "strike"), ("ko_pct", "ko"), ("ki_pct", "ki")):
        if col == "ki" and ki.ok and ki.value == "none":
            fields[key] = ParsedField(key, FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence))
            continue
        if not table.ok:
            fields[key] = _derived(key, table)
            continue
        pcts = table.value["pcts"].get(col)
        if pcts is None:
            fields[key] = ParsedField(
                key, FieldStatus.INVALID, evidence=list(table.evidence), note="標的表沒有下限價格列"
            )
            continue
        hits = [(p, lns) for p, lns in zip(pcts, table.value["row_lines"], strict=True)]
        fields[key] = ParsedField.from_hits(key, hits, ambiguous_note="第 11 項各檔的百分比不同")


def _trustee_block(art3: list[Line]) -> list[Line]:
    """第二章第 3 項「(4) 受託機構」那一段（到下一個「(n)」之前）；找不到或重複時為空。"""
    starts = [i for i, ln in enumerate(art3) if re.match(r"^[(（]4[)）]\s*受託機構", ln.text)]
    if len(starts) != 1:
        return []
    a = starts[0]
    b = next((i for i in range(a + 1, len(art3)) if re.match(r"^[(（]\d+[)）]", art3[i].text)), len(art3))
    return art3[a:b]


def _currency_occurrences(fields: dict[str, ParsedField], scenarios: bnp_scenario.ScenarioSection) -> list[Occurrence]:
    """金額旁的幣別字（標準欄位 `currency_others`，Issue #170）：第一章 §6 面額、§15 配息款項、§16／§17 金額、
    第四章四個最低金額、§18 假設（面額、投資本金）各一處；§18 每個情境一處（股價、剩餘金額算式與含「股」的算式不算）。"""
    occ = Occurrence
    out = [
        occ("denomination_currency", "商品面額幣別", "第一章第 6 項商品面額", fields["denomination_currency"]),
        occ("coupon_currency_art15", "配息款項幣別", "第一章第 15 項配息款項", fields["coupon_currency_art15"]),
        occ("currency_art16", "自動提前贖回金額幣別", "第一章第 16 項自動提前贖回金額", fields["currency_art16"]),
        occ("currency_art17", "到期贖回金額幣別", "第一章第 17 項到期贖回金額", fields["currency_art17"]),
        occ(
            "minimum_subscription_currency",
            "最低申購金額幣別",
            "第四章第 4 項最低申購金額",
            fields["minimum_subscription_currency"],
        ),
        occ(
            "minimum_additional_currency",
            "最低加購金額幣別",
            "第四章第 4 項最低加購金額",
            fields["minimum_additional_currency"],
        ),
        occ(
            "minimum_redemption_currency",
            "最低贖回金額幣別",
            "第四章第 8 項最低贖回金額",
            fields["minimum_redemption_currency"],
        ),
        occ(
            "redemption_increment_currency",
            "增加贖回單位幣別",
            "第四章第 8 項增加贖回之單位",
            fields["redemption_increment_currency"],
        ),
        occ(
            "scenario_denomination_currency",
            "情境假設商品面額幣別",
            "第 18 項假設商品面額",
            _hit_field(
                "scenario_denomination_currency", scenarios.denomination, 1, "第 18 項假設找不到商品面額或出現多次"
            ),
        ),
        occ(
            "scenario_principal_currency",
            "情境假設投資本金幣別",
            "第 18 項假設投資本金",
            _hit_field("scenario_principal_currency", scenarios.principal, 2, "第 18 項假設找不到投資本金或出現多次"),
        ),
    ]
    for s in scenarios.scenarios:
        if s.kind == "extreme":
            continue
        field = f"情境{s.number}_currency"
        out.append(
            money.scenario_occurrence(field, f"情境{s.number}金額幣別", f"第 18 項情境{s.number}", s.money, s.lines[:1])
        )
    return out


def _mentions(
    fields: dict[str, ParsedField], scenarios: bnp_scenario.ScenarioSection
) -> tuple[list[Mention], list[Mention]]:
    """月配息率的每個出處與獲利情境的年化報酬率（核對規則 §3.3）；讀不到的出處值為 None。"""

    def one(where: str, pf: ParsedField) -> Mention:
        return Mention(where, pf.value if pf.ok else None, tuple(pf.evidence))

    def hit(where: str, h: bnp_scenario.Hit | None, index: int, fallback: Sequence[Line]) -> Mention:
        if h is None:
            return Mention(where, None, tuple(Evidence.of(ln) for ln in fallback))
        return Mention(where, h.values[index], tuple(Evidence.of(ln) for ln in h.lines))

    coupons = [
        one("第 15 項月配息率", fields["monthly_coupon_pct"]),
        one("第 15 項固定配息金額算式", fields["coupon_formula_pct"]),
    ]
    if len(scenarios.monthly) == 1:
        coupons.append(hit("第 18 項假設每月配息率", scenarios.monthly[0], 0, []))
    else:
        coupons.append(
            Mention("第 18 項假設每月配息率", None, tuple(Evidence.of(ln) for ln in scenarios.assumptions[:1]))
        )
    annualized = []
    for s in scenarios.scenarios:
        if s.kind == "extreme":
            continue
        where = f"第 18 項情境{s.number}"
        coupons.append(hit(f"{where}配息算式", s.coupon, 4, s.lines[:1]))
        if s.kind in ("autocall", "best"):
            annualized.append(hit(f"{where}年化報酬率", s.total_return, 4, s.lines[:1]))
    return coupons, annualized
