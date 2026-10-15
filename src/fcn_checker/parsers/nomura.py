"""Nomura 中文產品說明書（docs/templates/nomura-zh-product-description.md）：章＋條號＋子項＋標籤定位，保留原文證據。

章名「一、商品基本資料」（中文數字＋頓號，五章）；第一章條號「N.」在左邊界（1.～9. 獨立一行、標題在下一行，10. 以後與標題
同一行），共 21 條；封面以「◎ 標籤：值」分項。每頁頁首「N/M」不算內文。日期、數字在去空白的全文索引上比對（2025 年文件
數字與單位之間多一個空白）；名稱、地址等要保留空白的值在原文合併文字（`RawText`，英數字跨行補一個空白）上讀。
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import money
from .common import Hit, Mention, dec, derived, hits, number, one
from .layout import Document, LayoutSpec, TextIndex, capture, parse_date

TEMPLATE_ID = "nomura-zh-pd"
PARSER_VERSION = "1"
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
}
# 條號「N.」x≈45；子項「(n)」x≈55–71（第一章 70、第二章 55）
LAYOUT = LayoutSpec(
    r"{zh}、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    50,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    80,
    CHAPTER_NAMES,
)
COVER_HEAD = ("(適用於專業投資人及OSU客戶)", "中文產品說明書")
ISSUER_EN = "Nomura International Funding Pte. Ltd."
NAME_EN_MARKS = ("Autocall Fixed Coupon Notes", f"issued by {ISSUER_EN}")
NAME_EN_ISSUER = f" issued by {ISSUER_EN}"  # 封面英文名稱有、第一章第 1 項沒有（發行機構寫在最前面）
ARTICLES = 21  # 第一章條數
PAGE_MARK = re.compile(r"^\d+\s*/\s*\d+$")
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
X = r"[x×]"
AMOUNT = rf"([\d,]+(?:\.\d+)?)({CCY})"  # 金額與旁邊的幣別字（去空白後）
WARNING_HEAD = "◎本商品之投資風險警語"
FEE_PREFIX = {
    "申購費用": "申購價金的",
    "提前贖回費用": "投資人提前贖回價金的",
    "分銷費用": "申購價金的",
}  # 費率格的寫法
FEE_ITEMS = (
    "申購費用",
    "提前贖回費用",
    "管理費用",
    "分銷費用",
    "保費費用",
    "解約費用",
    "其他費用",
)  # 費用表各列（左欄開頭）
FEE_RATE = r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)"
# 第 7 項標的表與第 14 項百分比表的欄（表頭文字 → 欄）；欄位順序以表頭出現位置為準
HEADERS = (("initial", "期初價格"), ("strike", "執行價"), ("ko", "自動提前出場價"), ("ki", "下限觸發價"))
TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})\s+(?:Equity|EQUITY)\b")
# 第 7 項 KO 觀察方式（範本規格 §4.1）：每日觀察的起日；每期觀察看第 11 項(6) 日期表
KO_DAILY = r"自動提前出場決定日：自" + D + r"起（含）至最終評價日止（含）之各預定交易日。"
KO_PERIODIC = r"自動提前出場決定日：各自動提前出場決定日即下述第11項\(6\)「自動提前出場決定日」之表格所示。"
# 第 7 項記憶式三種句型（範本規格 §4.2）
MEMORY_EVENT = (
    "記憶事件：於任何自動提前出場決定日，任何連結標的之收盤價高於或等於其個別自動提前出場價時，"
    "該連結標的即發生記憶事件。"
)
EVENT_MEMORY = "自動提前出場事件：於一自動提前出場決定日當日或之前任一自動提前出場決定日，所有連結標的均發生記憶事件時"
EVENT_BASKET = "自動提前出場事件：於任何自動提前出場決定日，所有連結標的之收盤價均高於或等於其個別自動提前出場價時"
EVENT_SINGLE = "自動提前出場事件：於任何自動提前出場決定日，連結標的之收盤價高於或等於自動提前出場價時"
# 第 7 項下限觸發（KI）：事件條款與決定日寫法（範本規格 §4.4）
KI_EVENT = r"下限觸發事件：若(?:任一)?連結標的於下限觸發決定日之收盤價小於(?:其)?下限觸發價格"
# 投資人須知 KI 每期觀察寫「中文產品說明書第一章第11項(6)」（說明書寫「下述第11項(6)」），型態相同
KI_DAYS = {
    r"最終評價日": "AM",
    r"自交易日起（不含）至最終評價日止（含）之各預定交易日": "D",
    r"各下限觸發決定日即(?:下述|中文產品說明書第一章)第11項\(6\)「下限觸發決定日」之表格所示": "P",
}
MATURITY_CASE = r"\((\d)\)如於最終評價日，"  # 第 7 項到期贖回各款


# ---------------------------------------------------------------- 原文合併文字


class RawText:
    """多行原文合併（同 `layout.join_text`：英數字跨行補一個空白），並可由字元位置找回原始行（作為證據）。

    名稱、地址、電話等要保留空白的值在這裡讀；數字、日期與句型在去空白的 `TextIndex` 上讀。
    """

    def __init__(self, lines: Sequence[Line]):
        self.lines = list(lines)
        out, self._starts = "", []
        for ln in self.lines:
            t = ln.text.strip()
            if out and t and re.search(r"[A-Za-z0-9.,)]$", out) and re.match(r"[A-Za-z0-9(]", t):
                out += " "
            self._starts.append(len(out))
            out += t
        self.text = out

    def lines_for(self, start: int, end: int) -> list[Line]:
        if not self.lines:
            return []
        i = max(0, bisect.bisect_right(self._starts, start) - 1)
        j = max(i, bisect.bisect_right(self._starts, max(start, end - 1)) - 1)
        return self.lines[i : j + 1]

    def capture(self, name: str, pattern: str, convert: Callable = lambda x: x.strip(), group: int = 1) -> ParsedField:
        """`pattern` 第 `group` 組的每一處（證據為整個比對所在的行）；不同值 → 歧義，沒有 → 缺漏，轉不成值 → 不合法。"""
        found = []
        for m in re.finditer(pattern, self.text, re.S):
            lns = self.lines_for(m.start(), m.end())
            try:
                value = convert(m[group])
            except (ValueError, TypeError, InvalidOperation):
                value = None
            if value is None:
                return ParsedField.invalid(name, lns, f"「{m[group]}」無法辨識")
            found.append((value, lns))
        return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")


def rest_of_line(name: str, lines: Sequence[Line], label: str) -> ParsedField:
    """以 `label`（去空白比對）開頭的那一行，標籤之後的文字（例：受託機構聯絡方式的電話）；每一處須相同。"""
    found = []
    for ln in lines:
        text = ln.text.strip()
        if squash(text).startswith(squash(label)):
            m = re.match(r"^" + r"\s*".join(map(re.escape, squash(label))) + r"\s*(.*)$", text)
            found.append(((m[1] if m else "").strip(), [ln]))
    return ParsedField.from_hits(name, [(v, lns) for v, lns in found if v], missing_note=f"找不到「{label}」")


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁首「N/M」不算內文。"""
    return Document([ln for ln in lines if not PAGE_MARK.match(ln.text.strip())], LAYOUT)


# ---------------------------------------------------------------- 封面「◎ 標籤：值」


@dataclass(frozen=True)
class CoverItem:
    label: str  # 標籤（去空白、不含 ◎ 與冒號）
    raw: RawText  # 這一項的原文行（含標籤）
    value_start: int  # 值在 `raw.text` 的起點

    @property
    def value(self) -> str:
        return self.raw.text[self.value_start :].strip()


def cover_items(cover: Sequence[Line]) -> dict[str, CoverItem]:
    """封面每個「◎」開頭的項目（標籤可跨行：「◎ 商品代號 /」＋「商品中文名稱：」），到「◎本商品之投資風險警語」為止。"""
    groups: list[list[Line]] = []
    for ln in cover:
        s = squash(ln.text)
        if s.startswith(WARNING_HEAD):
            break
        if s.startswith("◎"):
            groups.append([ln])
        elif groups:
            groups[-1].append(ln)
    items: dict[str, CoverItem] = {}
    for group in groups:
        raw = RawText(group)
        colon = raw.text.find("：")
        if colon < 0:
            continue
        label = squash(raw.text[:colon]).lstrip("◎")
        items.setdefault(label, CoverItem(label, raw, colon + 1))
    return items


def _item(items: dict[str, CoverItem], label: str) -> CoverItem | None:
    """標籤去空白後等於 `label`，或以它結尾（審查通過之日期的標籤含受託機構名稱）。"""
    return items.get(label) or next((v for k, v in items.items() if k.endswith(label)), None)


def _item_field(items: dict[str, CoverItem], label: str, name: str, pattern: str = r"(.+)", group: int = 1):
    item = _item(items, label)
    if item is None:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    m = re.fullmatch(pattern, item.value, re.S)
    if m is None:
        return ParsedField.invalid(name, item.raw.lines, f"封面「{label}」的值格式未知")
    return ParsedField.present(name, m[group].strip(), item.raw.lines)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 Nomura 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    head = [squash(x.text) for x in cover[:2]]
    zh = squash(_item(items, "商品代號/商品中文名稱").value) if _item(items, "商品代號/商品中文名稱") else ""
    en = squash(_item(items, "商品英文名稱").value) if _item(items, "商品英文名稱") else ""
    issuer = squash(_item(items, "發行機構之名稱及地址").value) if _item(items, "發行機構之名稱及地址") else ""
    arts = doc.articles(1)
    failed = []
    for test, message in (
        (head == [squash(x) for x in COVER_HEAD], "封面前兩行不是「(適用於專業投資人及OSU客戶)」「中文產品說明書」"),
        (
            zh.startswith(squash(ISSUER_EN) + "發行") and "(ISINNo." in zh and "TDCC商品代號為" in zh,
            f"封面商品中文名稱不是「{ISSUER_EN}發行…(ISIN No. … / TDCC商品代號為…)」",
        ),
        (all(squash(x) in en for x in NAME_EN_MARKS), "商品英文名稱沒有「" + "」「".join(NAME_EN_MARKS) + "」"),
        (issuer.startswith(squash(ISSUER_EN)), f"封面發行機構名稱不是 {ISSUER_EN}"),
        (
            all(n in doc.chapters for n in range(1, 6)),
            "章名不是「一、商品基本資料」…「五、特別記載事項」寫法或五章不完整",
        ),
        (set(range(1, ARTICLES + 1)) <= set(arts), f"第一章條號 1–{ARTICLES} 不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class NomuraTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    coupon_mentions: list[Mention]  # 每期配息率主來源（第 7 項）以外的出處（第 14 項設定參數、第 15 項(1)）
    annualized_mentions: list[Mention]  # 第 14 項情境一、二的年化報酬率

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def _date_field(name: str, pf: ParsedField, what: str) -> ParsedField:
    value = parse_date(pf.value) if pf.ok else None
    if pf.ok and value is None:
        return ParsedField(name, FieldStatus.INVALID, evidence=pf.evidence, note=f"{what}不是日期")
    return derived(name, pf, value)


def read(lines: Sequence[Line]) -> NomuraTermSheet:
    """讀出標準欄位與 Nomura 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    fields: dict[str, ParsedField] = {}

    def article(n: int, chapter: int = 1) -> list[Line]:
        """條的內文（不含條號那一行）。"""
        sp = (arts if chapter == 1 else doc.articles(chapter)).get(n)
        return doc.lines[sp.start : sp.end] if sp else []

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    # 封面
    _cover(fields, items, cover)

    # 第一章
    art1 = RawText(article(1))
    names = (
        r"商品名稱：\s*(.+?)\s*\(\s*("
        + re.escape(ISSUER_EN)
        + r".+?\(unsecured\))\s*\)\s*\(ISIN\s*No\.\s*([A-Z]{2}[A-Z0-9]{10})\s*\)"
    )
    fields["name_art1_zh"] = art1.capture("name_art1_zh", names)
    fields["name_art1_en"] = art1.capture("name_art1_en", names, group=2)
    fields["isin_art1"] = art1.capture("isin_art1", names, group=3)
    fields["guarantor_name_ch1"] = RawText(article(3)).capture(
        "guarantor_name_ch1", r"保證機構名稱：\s*(.+?)\s*保證機構之長期債務"
    )
    fields["issuer_name_ch1"] = standard_fields.absent("issuer_name_ch1", "第一章發行機構名稱（第 3 項只寫保證機構）")
    art5 = RawText(article(5))
    fields["currency_art5"] = art5.capture("currency_art5", rf"計價幣別：\s*({ZH_CCY})\s*\([A-Z]{{3}}\)")
    fields["currency_art5_iso"] = art5.capture("currency_art5_iso", rf"計價幣別：\s*{ZH_CCY}\s*\(([A-Z]{{3}})\)")

    _article7(fields, article(7), put)
    _article8_9(fields, article(8), article(9))

    art11 = article(11)
    put("tenor_months", art11, r"\(1\)商品年期：(\d+)個月", int)
    put("trade_date", art11, r"\(2\)交易日：" + D, parse_date)
    put("issue_date", art11, r"\(3\)發行日：" + D, parse_date)
    put("maturity_date", art11, r"\(4\)預定到期日：" + D, parse_date)
    put("final_valuation_date", art11, r"\(8\)最終評價日：即" + D, parse_date)
    tables = date_tables(art11)
    fields["date_table"], fields["ki_table"] = tables
    _schedule(fields)

    annualized = _article14(fields, article(14), put)
    put("coupon_art15", article(15), r"\(1\)固定配息率：就每配息日而言，" + PCT, Decimal)
    art16 = article(16)
    early = rf"最低金額為{AMOUNT}，並以{AMOUNT}為增加贖回之單位"
    put("early_redemption_minimum", art16, early, number)
    fields["early_redemption_increment"] = _group(art16, early, 3, "early_redemption_increment", number)
    fields["early_redemption_minimum_currency"] = _group(art16, early, 2, "early_redemption_minimum_currency")
    fields["early_redemption_increment_currency"] = _group(art16, early, 4, "early_redemption_increment_currency")

    # 第二章
    _chapter2(fields, doc)

    # 第四章
    ch4 = doc.articles(4)

    def ch4_article(n: int) -> list[Line]:
        return doc.span_lines(ch4.get(n))

    art1_ch4 = ch4_article(1)
    put("subscription_start", art1_ch4, r"\(1\)商品開始受理申購日期：" + D, parse_date)
    put("subscription_end", art1_ch4, r"\(2\)接受申購截止日：" + D, parse_date)
    raw1 = RawText(art1_ch4)
    fields["redemption_start_text"] = raw1.capture(
        "redemption_start_text", r"\(3\)\s*開始受理投資人贖回日期：\s*(.+?)\s*\(4\)\s*後續受理投資人贖回日期"
    )
    fields["redemption_later_text"] = raw1.capture(
        "redemption_later_text", r"\(4\)\s*後續受理投資人贖回日期：\s*(.+?)\s*\(5\)\s*每營業日"
    )
    fields.update(fee_rates(ch4_article(2)))
    art4 = ch4_article(4)
    subscribe = rf"最低申購金額：{AMOUNT}（相當1單位商品面額）最低加價申購金額：{AMOUNT}"
    put("minimum_subscription", art4, subscribe, number)
    fields["minimum_additional"] = _group(art4, subscribe, 3, "minimum_additional", number)
    fields["minimum_subscription_currency"] = _group(art4, subscribe, 2, "minimum_subscription_currency")
    fields["minimum_additional_currency"] = _group(art4, subscribe, 4, "minimum_additional_currency")
    put("issue_price_ch4", ch4_article(5), rf"申購價金之計算：每單位商品面額{X}申購單位數{X}" + PCT, Decimal)
    put("minimum_issue_size", ch4_article(7), r"有效申購金額總計至少達(.+?)；")
    art8_ch4 = ch4_article(8)
    redeem = rf"最低贖回金額：{AMOUNT}（相當1單位商品面額），並以{AMOUNT}為增加贖回之單位"
    put("minimum_redemption", art8_ch4, redeem, number)
    fields["redemption_increment"] = _group(art8_ch4, redeem, 3, "redemption_increment", number)
    fields["minimum_redemption_currency"] = _group(art8_ch4, redeem, 2, "minimum_redemption_currency")
    fields["redemption_increment_currency"] = _group(art8_ch4, redeem, 4, "redemption_increment_currency")
    fields["distributor_phone_ch4"] = rest_of_line("distributor_phone_ch4", doc.chapter_lines(4), "受託機構聯絡方式：")

    _occurrences(fields)
    coupons = [
        Mention.of_field("第 14 項設定參數固定配息率", fields["coupon_art14"]),
        Mention.of_field("第 15 項(1) 固定配息率", fields["coupon_art15"]),
    ]
    return NomuraTermSheet(fields, TextIndex(doc.lines), doc, coupons, annualized)


def _group(lns: list[Line], pattern: str, group: int, name: str, convert: Callable = lambda x: x) -> ParsedField:
    """同一句的另一組（例：最低加價申購金額接在最低申購金額後面）。"""
    ti = TextIndex(lns)
    found = []
    for m in ti.finditer(pattern):
        try:
            found.append((convert(m[group]), ti.lines_for(m.start(), m.end())))
        except (ValueError, TypeError, InvalidOperation):
            return ParsedField.invalid(name, ti.lines_for(m.start(), m.end()), f"「{m[group]}」無法辨識")
    return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")


def _cover(fields: dict[str, ParsedField], items: dict[str, CoverItem], cover: list[Line]) -> None:
    zh_label, en_label = "商品代號/商品中文名稱", "商品英文名稱"
    zh = r"(.+?)\s*\(ISIN\s*No\.\s*([A-Z]{2}[A-Z0-9]{10})\s*/\s*TDCC\s*商品\s*代號為\s*(\d{12})\s*\)"
    en = r"(.+?)\s*\(ISIN\s*No\.\s*([A-Z]{2}[A-Z0-9]{10})\s*/\s*TDCC\s*No\.\s*(\d{12})\s*\)"
    fields["name_zh"] = _item_field(items, zh_label, "name_zh", zh)
    fields["isin"] = _item_field(items, zh_label, "isin", zh, 2)
    fields["product_code"] = _item_field(items, zh_label, "product_code", zh, 3)
    fields["name_en"] = _item_field(items, en_label, "name_en", en)
    fields["isin_en"] = _item_field(items, en_label, "isin_en", en, 2)
    fields["product_code_en"] = _item_field(items, en_label, "product_code_en", en, 3)
    fields["product_type"] = _item_field(items, "商品種類", "product_type")
    currency = rf"({ZH_CCY})\s*\(([A-Z]{{3}})\)"
    fields["currency_zh"] = _item_field(items, "計價幣別", "currency_zh", currency)
    fields["currency_cover_iso"] = _item_field(items, "計價幣別", "currency_cover_iso", currency, 2)
    before_phone = r"(.+?)\s*(?:電話：.*)?"
    fields["issuer_name_cover"] = _item_field(items, "發行機構之名稱及地址", "issuer_name_cover", before_phone)
    fields["agent_name_cover"] = _item_field(items, "總代理人之名稱、電話及地址", "agent_name_cover", before_phone)
    fields["guarantor_name_cover"] = _item_field(items, "保證機構之名稱及地址", "guarantor_name_cover", before_phone)
    dist, contact = "受託機構之名稱、電話及地址", r"(.+?)\s*電話：\s*(.+?)\s*地址：\s*(.+)"
    fields["distributor_name_cover"] = _item_field(items, dist, "distributor_name_cover", contact)
    fields["distributor_phone_cover"] = _item_field(items, dist, "distributor_phone_cover", contact, 2)
    fields["distributor_address_cover"] = _item_field(items, dist, "distributor_address_cover", contact, 3)
    approval = _item_field(items, "審查通過之日期", "approval_date")
    fields["approval_date"] = _date_field("approval_date", approval, "審查通過日期")
    fields["print_date"] = capture(
        "print_date", TextIndex(cover), r"產品說明書刊印日期：" + D, parse_date, whole_match=True
    )


# ---------------------------------------------------------------- 第 7 項


def _article7(fields: dict[str, ParsedField], art7: list[Line], put: Callable) -> None:
    t7 = TextIndex(art7)
    minimum, denomination = rf"最低投資金額{AMOUNT}", rf"每單位商品面額{AMOUNT}"
    put("minimum_investment", art7, minimum, number)
    fields["minimum_investment_currency"] = _group(art7, minimum, 2, "minimum_investment_currency")
    put("denomination", art7, denomination, number)
    fields["denomination_currency"] = _group(art7, denomination, 2, "denomination_currency")
    put("issue_price_pct", art7, r"發行價格每單位商品面額的(\d+(?:\.\d+)?)[％%]", Decimal)
    put("coupon_period_pct", art7, r"固定配息率：就每配息日而言，" + PCT, Decimal)
    fields["coupon_pa_pct"] = standard_fields.absent(
        "coupon_pa_pct", "年利率（Nomura 改核對每期配息率 × 12 與情境年化報酬率）"
    )
    fields["initial_pricing"] = standard_fields.absent("initial_pricing", "期初定價方式（標的表表頭只寫「期初價格」）")
    fields["ko_wording"] = _ko_wording(t7, art7)
    fields["ko_memory"], fields["memory_style"] = memory_terms(t7, art7)
    fields["ki_type"] = ki_type(t7, art7)
    cases = [int(m[1]) for m in t7.finditer(MATURITY_CASE)]
    fields["maturity_cases"] = (
        ParsedField.present("maturity_cases", len(cases), art7[:1])
        if cases == list(range(1, len(cases) + 1)) and cases
        else ParsedField.invalid("maturity_cases", art7, "第 7 項到期贖回各款「(n) 如於最終評價日，」不是從 (1) 起連續")
    )
    found = hits(t7, r"每單位商品面額x" + PCT, lambda m: Decimal(m[1]))  # 到期贖回款（自動提前出場贖回金額寫「×」）
    fields["maturity_redemption_pcts"] = (
        ParsedField.present("maturity_redemption_pcts", [h.value for h in found], [x for h in found for x in h.lines])
        if found
        else ParsedField.missing("maturity_redemption_pcts", "第 7 項找不到「每單位商品面額x 100.00%」")
    )
    table = price_table(_table_region(art7))
    fields["underlying_table"] = table
    fields["underlyings"] = (
        ParsedField("underlyings", FieldStatus.PRESENT, list(table.value["tickers"]), list(table.evidence))
        if table.ok
        else derived("underlyings", table)
    )
    for col, key in (("strike", "strike_pct"), ("ko", "ko_pct"), ("ki", "ki_pct")):
        if table.ok and col in table.value["pcts"]:
            fields[key] = ParsedField(key, FieldStatus.PRESENT, table.value["pcts"][col], list(table.evidence))
        elif table.ok and col == "ki" and fields["ki_type"].ok and fields["ki_type"].value == "none":
            fields[key] = ParsedField(
                key,
                FieldStatus.NOT_APPLICABLE,
                evidence=list(table.evidence),
                note="說明書無 KI（標的表沒有下限觸發價欄）",
            )
        elif table.ok:
            fields[key] = ParsedField.invalid(key, table.value["header_lines"], "標的表表頭讀不到這一欄的百分比")
        else:
            fields[key] = derived(key, table)
    if table.ok:
        fields["underlying_prices"] = ParsedField(
            "underlying_prices",
            FieldStatus.PRESENT,
            tuple(
                standard_fields.PriceRow(ticker, dict(prices), tuple(Evidence.of(ln) for ln in lns), name=name)
                for ticker, prices, lns, name in zip(
                    table.value["tickers"],
                    table.value["rows"],
                    table.value["row_lines"],
                    table.value["names"],
                    strict=True,
                )
            ),
            list(table.evidence),
        )
    else:
        fields["underlying_prices"] = derived("underlying_prices", table)


def _ko_wording(t7: TextIndex, art7: list[Line]) -> ParsedField:
    """第 7 項「自動提前出場決定日：」寫法 → ("D", 起日)／("P", None)；要剛好一種一處。"""
    name = "ko_wording"
    daily = list(t7.finditer(KO_DAILY))
    periodic = list(t7.finditer(KO_PERIODIC))
    if len(daily) + len(periodic) != 1:
        return ParsedField.invalid(name, art7, "第 7 項「自動提前出場決定日：」的寫法缺漏、重複或不在範本規格內")
    if daily:
        m = daily[0]
        return ParsedField.present(name, ("D", parse_date(m[1])), t7.lines_for(m.start(), m.end()))
    m = periodic[0]
    return ParsedField.present(name, ("P", None), t7.lines_for(m.start(), m.end()))


def memory_terms(t7: TextIndex, art7: list[Line], where: str = "第 7 項") -> tuple[ParsedField, ParsedField]:
    """三種句型三選一（範本規格 §4.2）：記憶式（多標的）、非記憶式多標的、單一標的（非記憶式）；投資人須知同句型。"""
    text = t7.text
    found = {
        "memory": MEMORY_EVENT in text and EVENT_MEMORY in text,
        "basket": EVENT_BASKET in text,
        "single": EVENT_SINGLE in text,
    }
    styles = [k for k, v in found.items() if v]
    stray = "記憶事件：" in text and not found["memory"]
    if len(styles) != 1 or stray:
        note = f"{where}自動提前出場事件與記憶事件的句型缺漏、重複或不在範本規格內"
        return ParsedField.invalid("ko_memory", art7, note), ParsedField.invalid("memory_style", art7, note)
    style = styles[0]
    anchor = {"memory": EVENT_MEMORY, "basket": EVENT_BASKET, "single": EVENT_SINGLE}[style]
    a = text.find(anchor)
    lns = t7.lines_for(a, a + len(anchor))
    return ParsedField.present("ko_memory", style == "memory", lns), ParsedField.present("memory_style", style, lns)


def ki_type(ti: TextIndex, lns: list[Line], where: str = "第 7 項") -> ParsedField:
    """「下限觸發事件：」與「下限觸發決定日：」的寫法 → KI 型態（範本規格 §4.4）；無 KI 以 present「none」交出。

    投資人須知收益分配事項的句型相同（標籤加「」，parsers/nomura_iis.py 先把「」移開再交進來）；`where` 是錯訊裡的出處。"""
    name = "ki_type"
    text = ti.text
    event = re.search(KI_EVENT, text)
    days = re.search(r"下限觸發決定日：", text)
    if event is None and days is None:
        if "下限觸發" in text:
            return ParsedField.invalid(name, lns, f"{where}有「下限觸發」字樣，但找不到下限觸發事件與決定日")
        return ParsedField.present(name, "none", lns[:1])
    if event is None or days is None:
        return ParsedField.invalid(name, lns, f"{where}下限觸發事件與下限觸發決定日要同時出現")
    body = text[days.end() :]
    kind = next((v for k, v in KI_DAYS.items() if re.match(k, body)), None)
    ev = ti.lines_for(days.start(), days.end() + 30)
    if kind is None:
        return ParsedField.invalid(name, ev, f"{where}下限觸發決定日的寫法不在範本規格內")
    return ParsedField.present(name, kind, ev)


# ---------------------------------------------------------------- 第 7 項標的表


def _table_region(art7: list[Line]) -> list[Line]:
    """標的表：表頭「連結標的」「彭博代碼」那一行起到第 7 項結束。"""
    for i in range(len(art7) - 1):
        if squash(art7[i].text) == "連結標的" and squash(art7[i + 1].text).startswith("彭博代碼"):
            return art7[i:]
    return []


def price_table(lines: list[Line]) -> ParsedField:
    """第 7 項標的表：表頭決定欄位與 K／KO／KI %（`(期初價格之X% )`）；每檔一列，以左邊界的列號（`1`、`2 KAWASAKI`）切列。

    價格只取期初價格欄（含）以右的數字（一格一行，相鄰兩格可能擷取成同一行，以空白分開）。
    值：{"columns", "pcts", "tickers", "names", "rows": [{欄: Decimal}], "row_lines", "header_lines"}。
    """
    name = "underlying_table"
    if not lines:
        return ParsedField.missing(name, "第 7 項找不到標的表（表頭「連結標的」「彭博代碼」）")
    marks: list[int] = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(\d{1,2})(?:\s|$)", ln.text.strip())
        if m and ln.x0 < 60 and int(m[1]) == len(marks) + 1:
            marks.append(i)
    if not marks:
        return ParsedField.invalid(name, lines, "標的表找不到第 1 列")
    header = lines[: marks[0]]
    head = column_text(header)
    positions = sorted((head.find(label), col) for col, label in HEADERS if label in head)
    columns = [col for _, col in positions]
    if columns[:3] != ["initial", "strike", "ko"]:
        return ParsedField.invalid(name, header, "標的表表頭不是「期初價格、執行價、自動提前出場價…」的順序")
    pcts: dict[str, Decimal] = {}
    for col, label in HEADERS[1:]:
        if col not in columns:
            continue
        m = re.search(re.escape(label) + r"\(期初價格之" + PCT + r"\)", head)
        if m is None:
            return ParsedField.invalid(name, header, f"標的表表頭「{label}」讀不到「(期初價格之X% )」")
        pcts[col] = Decimal(m[1])
    price_x = min((ln.x0 for ln in header if "期初價格" in squash(ln.text)), default=None)
    if price_x is None:
        return ParsedField.invalid(name, header, "標的表表頭找不到「期初價格」")
    # 每列：列號之後到下一個列號之前（依頁與 y 座標，避免擷取順序交錯）
    body = lines[marks[0] :]
    starts = [(ln.page, ln.y0) for ln in (lines[i] for i in marks)]
    rows_lines: list[list[Line]] = [[] for _ in marks]
    for ln in body:
        key = (ln.page, ln.y0 + 2)
        k = max((j for j, s in enumerate(starts) if s <= key), default=0)
        rows_lines[k].append(ln)
    tickers, names, rows, row_lines = [], [], [], []
    for k, lns in enumerate(rows_lines, start=1):
        joined = " ".join(ln.text.strip() for ln in by_column(lns))
        found = [f"{m[1]} {m[2]}" for m in TICKER.finditer(joined)]
        cells = [
            tok for ln in sorted((x for x in lns if x.x0 >= price_x - 5), key=lambda x: x.x0) for tok in ln.text.split()
        ]
        if len(found) != 1:
            return ParsedField.invalid(name, lns, f"標的表第 {k} 列讀不到彭博代碼（代號 交易所碼 Equity）")
        try:
            values = [dec(t) for t in cells]
        except InvalidOperation:
            return ParsedField.invalid(name, lns, f"標的表第 {k} 列的價格不是數字")
        if len(values) != len(columns):
            return ParsedField.invalid(name, lns, f"標的表第 {k} 列有 {len(values)} 個價格，與 {len(columns)} 欄不符")
        tickers.append(found[0])
        names.append(_row_name(lns, price_x))
        rows.append(dict(zip(columns, values, strict=True)))
        row_lines.append(lns)
    value = {
        "columns": columns,
        "pcts": pcts,
        "tickers": tickers,
        "names": names,
        "rows": rows,
        "row_lines": row_lines,
        "header_lines": header,
    }
    return ParsedField.present(name, value, lines)


def by_column(lines: list[Line], gap: float = 20) -> list[Line]:
    """表格的行依 x 座標分欄（相鄰兩行 x 相差超過 `gap` 為不同欄），由左到右、每欄由上到下。

    一格拆成多行時，擷取順序可能逐欄也可能逐列；分欄後的順序固定（`執行價 (期初價格之X% )`、`ABC` / `UW Equity` 會接在一起）。"""
    columns: list[list[Line]] = []
    for ln in sorted(lines, key=lambda x: x.x0):
        if columns and ln.x0 - columns[-1][-1].x0 <= gap:
            columns[-1].append(ln)
        else:
            columns.append([ln])
    return [ln for col in columns for ln in sorted(col, key=lambda x: (x.page, x.y0))]


def column_text(lines: list[Line]) -> str:
    """`by_column` 順序接起來、去空白（表頭）。"""
    return "".join(squash(ln.text) for ln in by_column(lines))


def _row_name(lns: list[Line], price_x: float) -> str:
    """列上的標的英文名稱（彭博代碼之前的文字；只用來指出是哪一檔，不核對）。"""
    text = " ".join(ln.text.strip() for ln in by_column([x for x in lns if x.x0 < price_x - 5]))
    text = re.sub(r"^\d{1,2}\s*", "", text)
    m = TICKER.search(text)
    return (text[: m.start()] if m else text).strip()


# ---------------------------------------------------------------- 費用表


def fee_rates(lines: list[Line]) -> dict[str, ParsedField]:
    """費用表（說明書第四章 2.、投資人須知第七）各費用項目的費率區間（標準欄位 `fee_<費用項目>`）。

    依座標讀：費率欄是表頭「費率(百分比)」右邊最近的一欄；每個費用項目一列，從左欄項目名稱那一行到下一個項目名稱之前
    （項目名稱與費率都可能拆行，例：`投資人提前` / `贖回價金的` / `0%~5%`），費率欄各行由上到下接起來後 =
    「申購價金的X%~Y%」等寫法。"""
    out = {label: standard_fields.fee_field(label) for label in FEE_PREFIX}
    head = next((ln for ln in lines if squash(ln.text) == "費率(百分比)"), None)
    if head is None:
        return {name: ParsedField.missing(name, "找不到費用表表頭「費率(百分比)」") for name in out.values()}
    lo = head.x0 - 10
    rate_x = min((ln.x0 for ln in lines if ln.x0 >= lo and (ln.page, ln.y0) > (head.page, head.y0 + 3)), default=None)
    starts = [
        (ln, item)
        for ln in lines
        if ln.x0 < lo and (item := next((i for i in FEE_ITEMS if squash(ln.text).startswith(i)), None))
    ]
    fields: dict[str, ParsedField] = {}
    for label, prefix in FEE_PREFIX.items():
        name = out[label]
        rows = [k for k, (_, item) in enumerate(starts) if item == label]
        if len(rows) != 1 or rate_x is None:
            fields[name] = ParsedField.missing(name, f"費用表找不到「{label}」這一列或出現多次")
            continue
        k = rows[0]
        top = starts[k][0]
        bottom = starts[k + 1][0] if k + 1 < len(starts) else None
        cells = [
            ln
            for ln in lines
            if abs(ln.x0 - rate_x) <= 10
            and (ln.page, ln.y0 + 3) >= (top.page, top.y0)
            and (bottom is None or (ln.page, ln.y0 + 3) < (bottom.page, bottom.y0))
        ]
        cells.sort(key=lambda x: (x.page, x.y0))
        text = "".join(squash(ln.text) for ln in cells)
        m = re.fullmatch(re.escape(prefix) + FEE_RATE, text)
        fields[name] = (
            ParsedField.present(name, m[1], [top, *cells])
            if m
            else ParsedField.invalid(name, [top, *cells], f"費用表「{label}」的費率不是「{prefix}X%~Y%」")
        )
    return fields


# ---------------------------------------------------------------- 第 8、9 項


def _article8_9(fields: dict[str, ParsedField], art8: list[Line], art9: list[Line]) -> None:
    """第 8 項「連結標的資產：連結標的1：…」各檔英文名稱（單一標的寫「連結標的：…」）；第 9 項各檔「彭博代碼：… Equity」。"""
    raw8 = RawText(art8)
    a, b = raw8.text.find("連結標的資產："), raw8.text.find("相對權重：")
    if a < 0 or b <= a:
        fields["art8_names"] = ParsedField.missing("art8_names", "第 8 項找不到「連結標的資產：」到「相對權重：」")
    else:
        region = raw8.text[a + len("連結標的資產：") : b]
        entries = list(re.finditer(r"連結標的(\d*)：\s*(.+?)\s*(?=連結標的\d*：|$)", region, re.S))
        lns = raw8.lines_for(a, b)
        numbers = [m[1] for m in entries]
        if not entries:
            fields["art8_names"] = ParsedField.invalid("art8_names", lns, "第 8 項讀不到「連結標的n：名稱」")
        elif numbers == [""]:
            fields["art8_names"] = ParsedField.present("art8_names", ("single", [entries[0][2]]), lns)
        elif numbers == [str(n) for n in range(1, len(entries) + 1)]:
            fields["art8_names"] = ParsedField.present("art8_names", ("basket", [m[2] for m in entries]), lns)
        else:
            fields["art8_names"] = ParsedField.invalid("art8_names", lns, "第 8 項連結標的的編號不是單一或 1 起連續")
    raw9 = RawText(art9)
    found = list(re.finditer(r"彭博代碼：\s*([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})\s+Equity", raw9.text))
    fields["art9_tickers"] = (
        ParsedField.present(
            "art9_tickers",
            [f"{m[1]} {m[2]}" for m in found],
            [x for m in found for x in raw9.lines_for(m.start(), m.end())],
        )
        if found
        else ParsedField.missing("art9_tickers", "第 9 項找不到「彭博代碼：… Equity」")
    )


# ---------------------------------------------------------------- 第 11 項日期表


CELL = re.compile(
    r"(?P<date>\d{4}年\d{1,2}月\d{1,2}日)(?:\((?P<note>到期日|最終評價日)\))?|(?P<na>不適用)|連結標的期初價格的(?P<pct>\d+(?:\.\d+)?)%"
)
TABLE_HEADS = {  # 表頭（去空白）→ 表型與各欄
    "自動提前出場決定日配息日/自動提前出場日": ("P", ("observation", "payment")),
    "自動提前出場決定日自動提前出場價配息日/自動提前出場日": ("P", ("observation", "ko_pct", "payment")),
    "觀察期間開始日(含)觀察期間終止日(含)配息日": ("D", ("start", "end", "payment")),
    "下限觸發決定日": ("KI", ("ki_date",)),
}


def date_tables(art11: list[Line]) -> tuple[ParsedField, ParsedField]:
    """第 11 項(6) 的日期表（D／P）與 KI 每期觀察的「下限觸發決定日」表：以「t」表頭切表，到「營業日順延慣例」說明句為止。

    值：{"type": "D"|"P", "rows": [{"period", 各欄…}], "row_lines"}；KI 表為 {"type": "KI", ...}，沒有時為不適用。
    """
    ti = TextIndex(art11)
    a = re.search(r"\(6\)配息日：", ti.text)
    b = ti.text.find("「營業日順延慣例」係指")
    if a is None or b < a.end():
        missing = ParsedField.missing("date_table", "第 11 項找不到(6) 配息日的日期表")
        return missing, ParsedField.missing("ki_table", "第 11 項找不到(6) 配息日的日期表")
    region = ti.lines_for(a.start(), b)
    heads = [i for i, ln in enumerate(region) if squash(ln.text) == "t"]
    parsed = [
        _one_table(region[i : (heads[k + 1] if k + 1 < len(heads) else len(region))]) for k, i in enumerate(heads)
    ]
    main = [p for p in parsed if not p.ok or p.value["type"] in ("D", "P")]
    ki = [p for p in parsed if p.ok and p.value["type"] == "KI"]
    if len(main) != 1:
        date_table = ParsedField.invalid(
            "date_table", region, "第 11 項(6) 找不到日期表或有多張" if not main else "第 11 項(6) 日期表有多張"
        )
    else:
        date_table = main[0]
    if len(ki) > 1:
        ki_table = ParsedField.invalid("ki_table", region, "第 11 項(6) 下限觸發決定日表有多張")
    elif ki:
        ki_table = ki[0]
    else:
        ki_table = ParsedField(
            "ki_table",
            FieldStatus.NOT_APPLICABLE,
            evidence=[Evidence.of(x) for x in region[:1]],
            note="沒有下限觸發決定日表",
        )
    return date_table, ki_table


def _one_table(lines: list[Line]) -> ParsedField:
    name = "date_table"
    first = next((i for i in range(1, len(lines)) if re.fullmatch(r"\d{1,2}", squash(lines[i].text))), None)
    if first is None:
        return ParsedField.invalid(name, lines, "日期表沒有資料列")
    header = "".join(squash(ln.text) for ln in lines[1:first])
    if header not in TABLE_HEADS:
        return ParsedField.invalid(name, lines[:first], "日期表表頭不是已知的寫法")
    kind, keys = TABLE_HEADS[header]
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    for ln in lines[first:]:
        t = squash(ln.text)
        if re.fullmatch(r"\d{1,2}", t):
            rows.append({"period": int(t), "text": ""})
            row_lines.append([ln])
        else:
            rows[-1]["text"] += t
            row_lines[-1].append(ln)
    out = []
    for row, lns in zip(rows, row_lines, strict=True):
        # 一格可能拆成兩行（「連結標的期初價格的」／「105.00%」、「(到期」／「日)」）：除期別外依 x 座標由左到右接起來
        row["text"] = "".join(squash(ln.text) for ln in sorted(lns[1:], key=lambda x: (x.x0, x.page, x.y0)))
        cells, pos = [], 0
        for m in CELL.finditer(row["text"]):
            if row["text"][pos : m.start()]:
                break
            pos = m.end()
            if m["date"]:
                d = parse_date(m["date"])
                if d is None:
                    return ParsedField.invalid(name, lns, "日期表日期不合法")
                cells.append(d)
            elif m["na"]:
                cells.append(None)
            else:
                cells.append(Decimal(m["pct"]))
        if pos != len(row["text"]) or len(cells) != len(keys):
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列欄位數或內容不符範本")
        record = {"period": row["period"], **dict(zip(keys, cells, strict=True))}
        if record.get("payment") is None and kind != "KI" or kind == "KI" and record["ki_date"] is None:
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列沒有日期")
        if kind == "D" and (record["start"] is None or record["end"] is None):
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列觀察期間是「不適用」")
        if "ko_pct" in record and not isinstance(record["ko_pct"], Decimal):
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列自動提前出場價不是百分比")
        out.append(record)
    if not out or [r["period"] for r in out] != list(range(1, len(out) + 1)):
        return ParsedField.invalid(name, lines[first:], "日期表期數不是從 1 起連續")
    if kind == "P" and any(isinstance(r["observation"], Decimal) for r in out):
        return ParsedField.invalid(name, lines[first:], "日期表自動提前出場決定日欄不是日期")
    return ParsedField.present(name, {"type": kind, "rows": out, "row_lines": row_lines}, lines[first:])


def _schedule(fields: dict[str, ParsedField]) -> None:
    """KO 觀察方式、第一個可提前出場期與提前出場排程（範本規格 §4.1）：第 7 項寫法須與日期表排法一致。

    D 型：第 7 項 KO 起日剛好等於日期表第 k 期「觀察期間終止日(含)」→ Non-Call = k；P 型：第一個不是「不適用」的決定日期別。
    """
    table, wording = fields["date_table"], fields["ko_wording"]
    keys = ("ko_observation", "first_callable_period", "autocall_schedule")
    bad = next((p for p in (wording, table) if not p.ok), None)
    if bad is not None:
        for key in keys:
            fields[key] = derived(key, bad)
        return
    kind, rows = table.value["type"], table.value["rows"]
    ev = list(wording.evidence) + list(table.evidence)

    def invalid(note: str) -> None:
        for key in keys:
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)

    n = len(rows)
    observed, start = wording.value
    if observed != kind:
        invalid(f"第 7 項自動提前出場決定日是{'每日' if observed == 'D' else '每期'}觀察寫法，但日期表是另一種排法")
        return
    if kind == "D":
        k = next((r["period"] for r in rows if r["end"] == start), None)
        if k is None:
            invalid(f"第 7 項自動提前出場決定日起日 {start} 不是日期表任何一期的觀察期間終止日")
            return
        dates = {k: rows[k - 1]["end"], n: rows[-1]["end"]}
    else:
        k = next((r["period"] for r in rows if r["observation"] is not None), None)
        if k is None:
            invalid("日期表每一期的自動提前出場決定日都是「不適用」")
            return
        dates = {r["period"]: r["observation"] for r in rows if r["period"] >= k and r["observation"] is not None}
    fields["ko_observation"] = ParsedField("ko_observation", FieldStatus.PRESENT, kind, ev)
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule", FieldStatus.PRESENT, standard_fields.AutocallSchedule(kind, k, n, dates), ev
    )


# ---------------------------------------------------------------- 第 14 項情境分析


def _article14(fields: dict[str, ParsedField], art14: list[Line], put: Callable) -> list[Mention]:
    """設定參數（年期、面額、發行價格、固定配息率）、百分比表、情境一／二年化報酬率（範本規格 §5.2、核對規則 §3.7）。"""
    t14 = TextIndex(art14)
    a = re.search(r"設定參數[:：]", t14.text)
    b = t14.text.find("情境一（")
    params = t14.lines_for(a.end(), b) if a and b > a.end() else []
    put("scenario_tenor", params, r"年期(\d+)個月", int)
    put("scenario_denomination", params, rf"每單位商品面額{AMOUNT}", number)
    fields["scenario_denomination_currency"] = _group(
        params, rf"每單位商品面額{AMOUNT}", 2, "scenario_denomination_currency"
    )
    put("scenario_issue_price", params, r"發行價格(\d+(?:\.\d+)?)%", Decimal)
    put("coupon_art14", params, r"固定配息率就每配息日而言，" + PCT, Decimal)
    fields["scenario_table"] = _pct_table(params)
    sections = {k: t14.text.find(f"情境{k}（") for k in ("一", "二", "三")}
    found = hits(t14, r"本情境中年化報酬率為(?:\[[^\]]*\][^=]*=)?" + PCT, lambda m: Decimal(m[1]))
    out = []
    for k, nxt in (("一", "二"), ("二", "三")):
        lo, hi = sections[k], sections[nxt] if sections[nxt] >= 0 else len(t14.text)
        mine = _in_section(t14, found, lo, hi)
        out.append(Mention.of_optional_hit(f"第 14 項情境{k}年化報酬率", one(mine), lambda v: v, art14[:1]))
    return out


def _in_section(ti: TextIndex, found: list[Hit], lo: int, hi: int) -> list[Hit]:
    """`found` 中原文行落在 `ti.text[lo:hi]` 的那幾處（`lo` < 0 表示找不到這一段）。"""
    if lo < 0:
        return []
    first, last = ti.line_at(lo), ti.line_at(max(lo, hi - 1))
    return [h for h in found if first <= ti.lines.index(h.lines[0]) <= last]


def _pct_table(params: list[Line]) -> ParsedField:
    """第 14 項百分比表：表頭「期初價格 執行價 自動提前出場價（下限觸發價格）」，每檔一列「名稱 100% X% Y% (Z%)」。

    值：{"columns", "rows": [[Decimal…]]}；同一頁、同一高度的百分比為同一列（名稱可能跨行、跨頁，不讀）。
    """
    name = "scenario_table"
    start = next((i for i, ln in enumerate(params) if squash(ln.text) == "期初價格"), None)
    if start is None:
        return ParsedField.missing(name, "第 14 項找不到百分比表（表頭「期初價格」）")
    y = params[start].y0
    header = [ln for ln in params[start:] if ln.page == params[start].page and abs(ln.y0 - y) < 3]
    head = "".join(squash(ln.text) for ln in sorted(header, key=lambda x: x.x0))
    positions = sorted((head.find(label), col) for col, label in HEADERS if label in head)
    columns = [col for _, col in positions]
    cells = [ln for ln in params[start:] if ln not in header and re.fullmatch(PCT, squash(ln.text))]
    rows: list[list[Line]] = []
    for ln in sorted(cells, key=lambda x: (x.page, x.y0, x.x0)):
        if rows and rows[-1][0].page == ln.page and abs(rows[-1][0].y0 - ln.y0) < 3:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    if not rows or any(len(r) != len(columns) for r in rows):
        return ParsedField.invalid(name, header + cells, "第 14 項百分比表的欄數與表頭不符")
    values = [[Decimal(squash(ln.text)[:-1]) for ln in r] for r in rows]
    return ParsedField.present(name, {"columns": columns, "rows": values}, header + cells)


# ---------------------------------------------------------------- 第二章


def _chapter2(fields: dict[str, ParsedField], doc: Document) -> None:
    ch2 = doc.articles(2)
    raw1 = RawText(doc.span_lines(ch2.get(1)))
    fields["issuer_name_ch2"] = raw1.capture("issuer_name_ch2", r"\(1\)\s*事業名稱：\s*(.+?)\s*\(2\)\s*設立日期")
    raw2 = RawText(doc.span_lines(ch2.get(2)))
    fields["guarantor_name_ch2"] = raw2.capture("guarantor_name_ch2", r"\(1\)\s*事業名稱：\s*(.+?)\s*\(2\)\s*設立日期")
    raw3 = RawText(doc.span_lines(ch2.get(3)))
    fields["agent_name_ch2"] = raw3.capture(
        "agent_name_ch2", r"\(1\)\s*總代理人：\s*\(a\)\s*事業名稱：\s*(.+?)\s*\(b\)\s*設立日期"
    )
    trustee = r"\(4\)\s*受託機構：\s*\(a\)\s*事業名稱：\s*(.+?)\s*\(b\)\s*設立日期：.*?\(c\)\s*營業所在地：\s*(.+?)\s*\(d\)\s*負責人姓名：\s*(.+?)\s*\(5\)"
    fields["distributor_name_ch2"] = raw3.capture("distributor_name_ch2", trustee)
    fields["distributor_address_ch2"] = raw3.capture("distributor_address_ch2", trustee, group=2)
    fields["chairman"] = raw3.capture("chairman", trustee, group=3)


# ---------------------------------------------------------------- 出處清單


def _occurrences(fields: dict[str, ParsedField]) -> None:
    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_investment", "最低投資金額", "第一章第 7 項最低投資金額", fields["minimum_investment"]),
            occ(
                "early_redemption_minimum",
                "提前贖回最低金額",
                "第一章第 16 項(1) 投資人提前贖回最低金額",
                fields["early_redemption_minimum"],
            ),
            occ(
                "early_redemption_increment",
                "提前贖回增加單位",
                "第一章第 16 項(1) 增加贖回之單位",
                fields["early_redemption_increment"],
            ),
            occ("minimum_subscription", "最低申購金額", "第四章第 4 項最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "第四章第 4 項最低加價申購金額", fields["minimum_additional"]),
            occ("minimum_redemption", "最低贖回金額", "第四章第 8 項最低贖回金額", fields["minimum_redemption"]),
            occ("redemption_increment", "增加贖回單位", "第四章第 8 項增加贖回之單位", fields["redemption_increment"]),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ(
                "subscription_start",
                "開始受理申購日",
                "第四章第 1 項(1) 商品開始受理申購日期",
                fields["subscription_start"],
            ),
            occ("subscription_end", "結束受理申購日", "第四章第 1 項(2) 接受申購截止日", fields["subscription_end"]),
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "封面產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [
            occ(
                "issue_price_art14",
                "發行價格（第 14 項設定參數）",
                "第一章第 14 項設定參數",
                fields["scenario_issue_price"],
            ),
            occ(
                "issue_price_ch4",
                "發行價格（第四章申購價金）",
                "第四章第 5 項申購價金之計算",
                fields["issue_price_ch4"],
            ),
        ],
    )
    currencies = (  # 金額旁的幣別字（Issue #170）：標的表價格、情境算式金額不列入
        ("minimum_investment_currency", "最低投資金額幣別", "第一章第 7 項最低投資金額"),
        ("denomination_currency", "每單位商品面額幣別", "第一章第 7 項每單位商品面額"),
        ("scenario_denomination_currency", "情境設定參數面額幣別", "第一章第 14 項設定參數每單位商品面額"),
        ("early_redemption_minimum_currency", "提前贖回最低金額幣別", "第一章第 16 項(1) 最低金額"),
        ("early_redemption_increment_currency", "提前贖回增加單位幣別", "第一章第 16 項(1) 增加贖回之單位"),
        ("minimum_subscription_currency", "最低申購金額幣別", "第四章第 4 項最低申購金額"),
        ("minimum_additional_currency", "最低加購金額幣別", "第四章第 4 項最低加價申購金額"),
        ("minimum_redemption_currency", "最低贖回金額幣別", "第四章第 8 項最低贖回金額"),
        ("redemption_increment_currency", "增加贖回單位幣別", "第四章第 8 項增加贖回之單位"),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [occ(key, label, where, fields[key]) for key, label, where in currencies]
    )
