"""SG 中文產品說明書（docs/templates/sg-zh-product-description.md）：章＋條號＋子項＋標籤定位，保留原文證據。

章名「一、商品基本資料」（中文數字＋頓號，五章）；條號「N.」獨立一行、標題在同一列右側（第 10 條起「N. 標題」同一行）；
封面是兩欄表（標籤在左、值在右）。兩種版面（Smart Communications 與數字後帶空白的舊版面）在去空白後寫法相同，
所有欄位都在去空白的全文索引上比對。頁尾的註腳（1 非許可之受讓人…、3 特殊因素…）從該行起到頁底不算內文。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import money, price_table, schedule, sg_scenario, sg_tables
from .common import NO_AUTOCALL_KO_PCT, NUM, Hit, Mention, derived, ko_at_initial, number
from .layout import Document, LayoutSpec, TextIndex, capture, join_text, parse_date

TEMPLATE_ID = "sg-zh-pd"
# 2：價格表改由 parsers/price_table.py 讀（Issue #288）；3：日期表標準欄位與共用排程推導（Issue #289）
PARSER_VERSION = "3"
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
}
# 章名「一、商品基本資料」；條號「N.」x≈28–36（舊版面 35）；子項「(n)」x≈50–57，第 8 項到期贖回的「(1) 若於…」x≈72–79 不算子項
LAYOUT = LayoutSpec(
    r"{zh}、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    45,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    65,
    CHAPTER_NAMES,
)
COVER_TITLE = "中文產品說明書"
ISSUER_EN = "SG ISSUER"
NAME_EN_TAIL = "Fixed Coupon Equity Linked Notes (Non Principal Guaranteed and Unsecured)"
# 頁尾註腳的開頭（註腳編號＋固定文字）；從這一行到頁底都不算內文
FOOTNOTE = re.compile(
    r"^\d{1,2}\s*(非許可之受讓人|本商品之一切付款|特殊因素指|連結標的中斷日之後果|評價時間指|買賣中斷指|交易所中斷指|提早收盤|持有由SG)"
)
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
FEES = ("申購費用", "提前贖回費用", "分銷費用")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9./-]*\s+[A-Z]{2}$")
AUTOCALL_EVENT = "自動提前出場事件："
MEMORY_EVENT = "記憶事件："
MEMORY_WORDING = "所有連結標的發生記憶事件成為自動提前出場標的"
PLAIN_WORDING = re.compile(r"(當日最差表現之連結標的|連結標的)收盤價大於或等於自動提前出場價")
KO_D = r"觀察期間：自" + D + r"（含）起至最終評價日（含）止之每一個預定交易日"
KO_P = r"自動提前出場事件：若於任一評價日i\(i=(\d+)-(\d+)\)"
ABSENT_KO = "說明書沒有自動提前出場（第 8 項沒有自動提前出場事件、自動提前出場價）"
NO_AUTOCALL_KO_NOTE = "說明書沒有自動提前出場事件（Non-Call = 天期，不會提前出場）：KO 視為期初價格的 100%"


# 第二章之後的「YYYY年發行機構年報之會計師查核報告」附件（範本規格 §1：空白頁或掃描，不核對）
ANNEX_TITLE = re.compile(r"\d{4}年發行機構年報之會計師查核報告")
CHAPTER3 = "三、商品風險揭露"


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁尾註腳（從註腳開頭那一行到頁底）與第二章後的年報附件（標題到第三章章名之前）不算內文。"""
    kept: list[Line] = []
    skipping_page: int | None = None
    in_annex = False
    for ln in lines:
        text = squash(ln.text)
        if in_annex:
            if text != CHAPTER3:
                continue
            in_annex = False
        elif ANNEX_TITLE.fullmatch(text):
            in_annex = True
            continue
        if skipping_page is not None and ln.page == skipping_page:
            continue
        skipping_page = None
        if ln.x0 < 40 and FOOTNOTE.match(ln.text):
            skipping_page = ln.page
            continue
        kept.append(ln)
    return Document(kept, LAYOUT)


# ---------------------------------------------------------------- 辨識


def _cover_title_lines(cover: Sequence[Line]) -> tuple[Line | None, Line | None]:
    """封面第一行「中文產品說明書」與其後第一個非空行（標題名稱）。"""
    for i, ln in enumerate(cover):
        if squash(ln.text) == COVER_TITLE:
            nxt = next((x for x in cover[i + 1 :] if x.text.strip()), None)
            return ln, nxt
    return None, None


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 SG 說明書（兩種版面同一範本）。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    items = sg_tables.cover_items(cover)
    head, title = _cover_title_lines(cover)
    issuer = items.get("發行機構之名稱、電話及地址") or []
    en = squash(join_text(items.get("商品英文名稱") or []))
    arts = doc.articles(1)
    failed = []
    for test, message in (
        (
            head is not None and title is not None and squash(title.text).startswith(squash(ISSUER_EN)),
            "封面標題不是「中文產品說明書」接 SG ISSUER 開頭的名稱",
        ),
        (bool(issuer) and squash(issuer[0].text) == squash(ISSUER_EN), f"封面發行機構名稱不是 {ISSUER_EN}"),
        (
            en.startswith(squash(ISSUER_EN)) and squash(NAME_EN_TAIL) in en,
            f"商品英文名稱不是 SG ISSUER … {NAME_EN_TAIL}",
        ),
        (
            all(n in doc.chapters for n in range(1, 6)),
            "章名不是「一、商品基本資料」…「五、特別記載事項」寫法或五章不完整",
        ),
        (set(range(1, 27)) <= set(arts), "第一章條號 1–26 不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class SgTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    scenarios: sg_scenario.ScenarioSection  # 第 15 項(3)
    coupon_mentions: list[Mention]  # 配息率的每個出處（第 8 項、第 15 項假設、各情境算式）
    annualized_mentions: list[Mention]  # 獲利情境（較佳、中間）的平均年化報酬率

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def _absent(name: str, note: str) -> ParsedField:
    return ParsedField(name, FieldStatus.NOT_APPLICABLE, note=note)


def _labelled(name: str, lines: Sequence[Line], label: str) -> ParsedField:
    """以「標籤：值」開頭的那一行（去掉前面的項目符號）的值；標籤比對去空白。"""
    hits = []
    for ln in lines:
        text = re.sub(r"^[\s‧•·]*(?:[(（]\d{1,2}[)）]\s*)?", "", ln.text)  # 舊版面：「(1) 事業名稱：…」同一行
        if squash(text).startswith(squash(label)):
            m = re.match(r"^\s*" + "".join(re.escape(c) + r"\s*" for c in squash(label)) + r"(.*)$", text)
            hits.append((m[1].strip() if m else "", [ln]))
    return ParsedField.from_hits(name, hits, missing_note=f"找不到「{label}」")


def _item_lines(items: dict[str, list[Line]], label: str) -> list[Line]:
    return items.get(label) or []


def _item_text(items: dict[str, list[Line]], label: str, name: str) -> ParsedField:
    lns = _item_lines(items, label)
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    return ParsedField.present(name, join_text(lns).strip(), lns)


def _first_line(items: dict[str, list[Line]], label: str, name: str) -> ParsedField:
    """兩欄表某項右欄的第一行（機構名稱；之後是地址、電話）。"""
    lns = _item_lines(items, label)
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    return ParsedField.present(name, lns[0].text.strip(), lns[:1])


def _before_address(items: dict[str, list[Line]], label: str, name: str, pattern: str | None) -> ParsedField:
    """兩欄表某項右欄「地址：」之前的行合併（跨行的機構名稱），`pattern` 取其中一段。"""
    lns = _item_lines(items, label)
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    head = []
    for ln in lns:
        if squash(ln.text).startswith(("地址：", "電話：")):
            break
        head.append(ln)
    text = join_text(head).strip()
    if pattern is not None:
        m = re.fullmatch(pattern, squash(text), re.S)
        if not m:
            return ParsedField.invalid(name, head, f"封面「{label}」的值格式未知")
        text = m[1]
    return ParsedField.present(name, text, head)


def _prefixed(items: dict[str, list[Line]], label: str, name: str, prefix: str) -> ParsedField:
    """兩欄表某項右欄以 `prefix`（地址：／電話：）開頭的那一行的值。"""
    lns = _item_lines(items, label)
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    return _labelled(name, lns, prefix)


def _date_field(name: str, pf: ParsedField, what: str) -> ParsedField:
    value = parse_date(pf.value) if pf.ok else None
    if pf.ok and value is None:
        return ParsedField(name, FieldStatus.INVALID, evidence=pf.evidence, note=f"{what}不是日期")
    return derived(name, pf, value)


def read(lines: Sequence[Line]) -> SgTermSheet:
    """讀出標準欄位與 SG 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    cover = doc.before_chapter1()
    items = sg_tables.cover_items(cover)
    fields: dict[str, ParsedField] = {}

    def article(n: int, chapter: int = 1) -> list[Line]:
        return doc.span_lines((arts if chapter == 1 else doc.articles(chapter)).get(n))

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    # 封面
    cover_ti = TextIndex(cover)
    codes = re.search(
        r"TDCC商品代號(\d{12})，受託或銷售機構商品代號(\d{12}|)，ISIN為([A-Z]{2}[A-Z0-9]{10})", cover_ti.text
    )
    code_lines = [ln for ln in cover if "TDCC" in ln.text]
    for key, group in (("product_code", 1), ("trustee_product_code", 2), ("isin", 3)):
        if codes:
            fields[key] = ParsedField.present(key, codes[group], cover_ti.lines_for(codes.start(), codes.end()))
        elif code_lines:
            fields[key] = ParsedField.invalid(
                key, code_lines, "封面代號句的寫法未知（TDCC商品代號…，受託或銷售機構商品代號…，ISIN為…）"
            )
        else:
            fields[key] = ParsedField.missing(key, "找不到封面代號句")
    _, title = _cover_title_lines(cover)
    fields["cover_title"] = (
        ParsedField.present("cover_title", title.text.strip(), [title])
        if title is not None
        else ParsedField.missing("cover_title", "找不到封面標題名稱（「中文產品說明書」之後的第一行）")
    )
    zh_lines = _item_lines(items, "商品中文名稱")
    zh = re.fullmatch(r"(.+?)\s*（下稱「本商品」）", join_text(zh_lines).strip(), re.S) if zh_lines else None
    fields["name_zh"] = (
        ParsedField.present("name_zh", zh[1].strip(), zh_lines)
        if zh
        else ParsedField.invalid("name_zh", zh_lines, "商品中文名稱找不到「（下稱「本商品」）」")
        if zh_lines
        else ParsedField.missing("name_zh", "找不到封面「商品中文名稱」")
    )
    fields["name_en"] = _item_text(items, "商品英文名稱", "name_en")
    fields["product_type"] = _item_text(items, "商品種類", "product_type")
    fields["currency_zh"] = _item_text(items, "計價幣別", "currency_zh")
    fields["issuer_name_cover"] = _first_line(items, "發行機構之名稱、電話及地址", "issuer_name_cover")
    fields["agent_name_cover"] = _first_line(items, "總代理人之名稱、電話及地址", "agent_name_cover")
    fields["guarantor_name_cover"] = _before_address(
        items, "保證機構之名稱及地址", "guarantor_name_cover", r"(.+?)（下稱.*"
    )
    dist = "受託或銷售機構之名稱、電話及地址"
    fields["distributor_name_cover"] = _before_address(items, dist, "distributor_name_cover", r"(.+?)（下稱.*")
    fields["distributor_address_cover"] = _prefixed(items, dist, "distributor_address_cover", "地址：")
    fields["distributor_phone_cover"] = _prefixed(items, dist, "distributor_phone_cover", "電話：")
    approval = _item_text(items, "受託或銷售機構審查通過之日期及文號", "approval_date")
    fields["approval_date"] = _date_field("approval_date", approval, "審查通過日期")
    fields["print_date"] = capture("print_date", cover_ti, r"本產品說明書刊印日期：" + D, parse_date, whole_match=True)

    # 第一章
    put("name_art1", article(1), r"商品名稱：(.+)$")
    put("guarantor_name_ch1", article(3), r"保證機構名稱及其長期債務信用評等：(.+?)，截至")
    art5 = article(5)
    put("currency_art5", art5, rf"計價幣別：({CCY})")
    put("denomination", art5, r"每單位面額：" + NUM + CCY, number)
    put("denomination_currency", art5, rf"每單位面額：{money.NUMBER}({CCY})")
    put("issue_price_pct", art5, r"發行價格：每單位面額的" + PCT, Decimal)
    put("isin_art26", article(26), r"ISIN代碼：([A-Z]{2}[A-Z0-9]{10})")

    art8 = article(8)
    t8 = TextIndex(art8)
    put("monthly_coupon_pct", art8, r"配息率＝" + PCT + r"\(年率", Decimal)
    put("coupon_pa_pct", art8, r"配息率＝\d+(?:\.\d+)?%\(年率" + PCT + r"\)", Decimal)
    has_autocall = AUTOCALL_EVENT in t8.text
    fields["has_autocall"] = ParsedField.present("has_autocall", has_autocall, art8[:1])
    if has_autocall:
        put("ko_pct", art8, r"自動提前出場價：連結標的之期初價格的" + PCT, Decimal)
        put("autocall_redemption_pct", art8, r"自動提前出場贖回金額＝每單位面額×" + PCT, Decimal)
    else:
        fields["ko_pct"] = ParsedField(
            "ko_pct",
            FieldStatus.PRESENT,
            NO_AUTOCALL_KO_PCT,
            [Evidence.of(x) for x in art8[:1]],
            note=NO_AUTOCALL_KO_NOTE,
        )
        fields["autocall_redemption_pct"] = _absent("autocall_redemption_pct", ABSENT_KO)
    put("strike_pct", art8, r"執行價：連結標的期初價格的" + PCT, Decimal)
    ki_raw = capture("ki_pct", t8, r"下限價：連結標的期初價格的" + PCT, Decimal, whole_match=True)
    put("maturity_redemption_pct", art8, r"到期贖回金額＝每單位面額×" + PCT, Decimal)
    put("currency_art8", art8, rf"以({CCY})為計價單位給付")
    fields["ko_memory"] = ko_memory(t8, art8, has_autocall)
    fields["ko_wording"] = _ko_wording(t8, art8, has_autocall)
    prorate = re.search(r"惟若自動提前出場事件發生於第(\d+)-(\d+)個配息週期", t8.text)
    fields["prorate_range"] = (
        ParsedField.present(
            "prorate_range", (int(prorate[1]), int(prorate[2])), t8.lines_for(prorate.start(), prorate.end())
        )
        if prorate
        else ParsedField.missing("prorate_range", "找不到配息按比例條款「惟若自動提前出場事件發生於第k-n個配息週期」")
    )
    fields["ki_type"] = ki_type(t8, art8)
    ki = fields["ki_type"]
    if ki.ok and ki.value == "none" and not ki_raw.ok:
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence), note="說明書無 KI（第 8 項沒有下限價）"
        )
    else:
        fields["ki_pct"] = ki_raw
    fields["worst_k"] = _worst_k(t8, art8)
    tickers = _tickers(doc.span_lines(doc.subitems(arts.get(9)).get(1)))
    fields["underlyings"] = tickers
    n = len(tickers.value) if tickers.ok else None
    table = sg_tables.price_table("underlying_table", _table_region(art8), sg_tables.PRICE_TABLE, n)
    fields["underlying_table"] = table
    _prices(fields, table, tickers, has_autocall)

    art12 = article(12)
    t12 = TextIndex(art12)
    put("tenor_months", art12, r"商品年期：(\d+)個月", int)
    put("trade_date", art12, r"訂價日：" + D, parse_date)
    put("issue_date", art12, r"發行日[／∕/]交割日：" + D, parse_date)
    put("maturity_date", art12, r"到期日：" + D, parse_date)
    fields["autocall_day"] = _autocall_day(t12, art12, has_autocall)
    fields["date_table"] = sg_tables.date_table(art12)
    _schedule(fields)
    scenarios = sg_scenario.section(doc.span_lines(doc.subitems(arts.get(15)).get(3)), n)

    # 第二章
    ch2 = doc.articles(2)
    fields["issuer_name_ch2"] = _labelled("issuer_name_ch2", doc.span_lines(ch2.get(1)), "事業名稱：")
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")
    fields.update(standard_fields.absent_all(standard_fields.DISTRIBUTOR_OTHERS, "受託機構的其他出處"))
    fields["initial_pricing"] = standard_fields.absent("initial_pricing", "期初定價（收盤價／VWAP）字樣")
    fields["issuer_name_ch1"] = standard_fields.absent(
        "issuer_name_ch1", "第一章的發行機構名稱（SG 第一章第 3 項寫的是保證機構）"
    )
    fields["guarantor_name_ch2"] = _labelled("guarantor_name_ch2", doc.span_lines(ch2.get(2)), "事業名稱：")
    blocks = doc.subitems(ch2.get(3))
    agent = _block(doc, blocks, "總代理人")
    fields["agent_name_ch2"] = _labelled("agent_name_ch2", agent, "事業名稱：")
    trustee = _block(doc, blocks, "受託或銷售機構")
    fields["distributor_name_ch2"] = _labelled("distributor_name_ch2", trustee, "事業名稱：")
    fields["distributor_address_ch2"] = _labelled("distributor_address_ch2", trustee, "營業所在地：")
    fields["chairman"] = _labelled("chairman", trustee, "負責人姓名：")

    # 第四章
    ch4 = doc.articles(4)
    put("subscription_start", doc.span_lines(ch4.get(1)), r"商品開始受理申購日期：自" + D + r"起", parse_date)
    for label in FEES:
        fields[standard_fields.fee_field(label)] = sg_tables.fee(
            label, standard_fields.fee_field(label), doc.span_lines(ch4.get(2))
        )
    art4 = doc.span_lines(ch4.get(4))
    put("minimum_subscription", art4, r"最低申購金額：" + NUM + CCY, number)
    put("minimum_additional", art4, r"最低加購金額：" + NUM + CCY, number)
    put("minimum_subscription_currency", art4, rf"最低申購金額：{money.NUMBER}({CCY})")
    put("minimum_additional_currency", art4, rf"最低加購金額：{money.NUMBER}({CCY})")
    put("issue_price_ch4", doc.span_lines(ch4.get(5)), r"申購金額之計算：每單位面額×申購單位數×" + PCT, Decimal)
    art8_ch4 = doc.span_lines(ch4.get(8))
    put("minimum_redemption", art8_ch4, r"最低贖回金額或單位數：" + NUM + CCY, number)
    put("minimum_redemption_currency", art8_ch4, rf"最低贖回金額或單位數：{money.NUMBER}({CCY})")
    put("minimum_redemption_units", art8_ch4, rf"最低贖回金額或單位數：{money.NUMBER}{CCY}（即(\d+)單位）", int)

    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_subscription", "最低申購金額", "第四章第 4 項最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "第四章第 4 項最低加購金額", fields["minimum_additional"]),
            occ("minimum_redemption", "最低贖回金額", "第四章第 8 項最低贖回金額", fields["minimum_redemption"]),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ(
                "subscription_start",
                "開始受理申購日",
                "第四章第 1 項商品開始受理申購日期",
                fields["subscription_start"],
            )
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "封面刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [
            occ(
                "issue_price_ch4",
                "發行價格（第四章申購金額）",
                "第四章第 5 項申購金額之計算",
                fields["issue_price_ch4"],
            )
        ],
    )
    fields["currency_others"] = standard_fields.occurrences("currency_others", _currency_occurrences(fields, scenarios))
    coupons, annualized = _mentions(fields, scenarios)
    return SgTermSheet(fields, TextIndex(doc.lines), doc, scenarios, coupons, annualized)


# ---------------------------------------------------------------- 第 8 項


def ko_memory(t8: TextIndex, art8: list[Line], has_autocall: bool, where: str = "第 8 項") -> ParsedField:
    """第 8 項有「記憶事件：」定義 → 記憶式；非記憶式寫「…連結標的收盤價大於或等於自動提前出場價」；無自動提前出場為非記憶式。

    投資人須知收益分配事項的句型相同（parsers/sg_iis.py），`where` 是錯訊裡的出處。"""
    name = "ko_memory"
    if not has_autocall:
        return ParsedField.present(name, False, art8[:1])
    text = t8.text
    if MEMORY_EVENT in text:
        m = re.search(re.escape(MEMORY_WORDING), text)
        if m is None:
            return ParsedField.invalid(name, art8, f"{where}有記憶事件，但自動提前出場事件的寫法不在範本規格內")
        return ParsedField.present(name, True, t8.lines_for(m.start(), m.end()))
    m = PLAIN_WORDING.search(text)
    if m is None:
        return ParsedField.invalid(name, art8, f"{where}自動提前出場事件的寫法不在範本規格內")
    return ParsedField.present(name, False, t8.lines_for(m.start(), m.end()))


def _ko_wording(t8: TextIndex, art8: list[Line], has_autocall: bool) -> ParsedField:
    """第 8 項觀察期間寫法 → KO 觀察方式：{"type": "D", "start": 日期}／{"type": "P", "k", "n"}／{"type": "none"}。"""
    name = "ko_wording"
    if not has_autocall:
        return ParsedField.present(name, {"type": "none"}, art8[:1])
    d = list(t8.finditer(KO_D))
    p = list(t8.finditer(KO_P))
    if len(d) == 1 and not p:
        m = d[0]
        start = parse_date(m[1])
        if start is None:
            return ParsedField.invalid(name, t8.lines_for(m.start(), m.end()), "觀察期間起始日不是日期")
        return ParsedField.present(name, {"type": "D", "start": start}, t8.lines_for(m.start(), m.end()))
    if len(p) == 1 and not d:
        m = p[0]
        return ParsedField.present(
            name, {"type": "P", "k": int(m[1]), "n": int(m[2])}, t8.lines_for(m.start(), m.end())
        )
    return ParsedField.invalid(name, art8, "第 8 項自動提前出場的觀察期間寫法缺漏或不在範本規格內")


def ki_type(t8: TextIndex, art8: list[Line], where: str = "第 8 項") -> ParsedField:
    """第 8 項到期贖回款數與觀察寫法 → KI 型態（範本規格 §4.4）；無 KI 以 present「none」交出。

    投資人須知收益分配事項的各款相同（parsers/sg_iis.py），`where` 是錯訊裡的出處。"""
    name = "ki_type"
    text = t8.text
    a, b = text.find("到期贖回金額："), text.find("應實際交付數")
    if a < 0 or b <= a:
        return ParsedField.invalid(name, art8, f"找不到{where}到期贖回金額的各款")
    red = text[a:b]
    lns = t8.lines_for(a, b)
    cases = len(re.findall(r"\(\d\)若於", red))
    if cases == 2 and re.search(r"\(1\)若於最終評價日(?:最差表現之)?連結標的收盤價大於或等於執行價", red):
        if "下限價" in red:
            return ParsedField.invalid(name, lns, "到期贖回以執行價判，但出現下限價")
        return ParsedField.present(name, "none", lns)
    if cases == 2 and re.search(r"\(1\)若於最終評價日最差表現之連結標的收盤價大於或等於下限價", red):
        return ParsedField.present(name, "AM", lns)
    if cases == 3 and "每一個預定交易日（「觀察期間」）" in red and "不曾小於下限價" in red:
        return ParsedField.present(name, "D", lns)
    if cases == 3 and re.search(r"\(1\)若於每一個評價日i", red) and "不曾小於下限價" in red:
        return ParsedField.present(name, "P", lns)
    return ParsedField.invalid(name, lns, f"{where}到期贖回的款數或觀察寫法不在範本規格內")


def _worst_k(t8: TextIndex, art8: list[Line]) -> ParsedField:
    """「最差表現之連結標的：指…連結標的(k)(k=1、2、3)表現最差者」的 k 數；單一標的沒有這句。"""
    name = "worst_k"
    m = re.search(r"連結標的\(k\)\(k=([\d、]+)\)表現(?:最差|較差)者", t8.text)
    if m:
        return ParsedField.present(name, len([x for x in m[1].split("、") if x]), t8.lines_for(m.start(), m.end()))
    if "最差表現之連結標的" not in t8.text:
        return ParsedField.present(name, 1, art8[:1])
    return ParsedField.invalid(name, art8, "第 8 項「最差表現之連結標的」的寫法不在範本規格內")


def _table_region(art8: list[Line]) -> list[Line]:
    """第 8 項標的表：「執行價：連結標的期初價格的X%」那一行之後到條末。"""
    start = next((i for i, ln in enumerate(art8) if squash(ln.text).startswith("執行價：連結標的期初價格的")), None)
    return art8[start + 1 :] if start is not None else []


def _tickers(item1: list[Line]) -> ParsedField:
    """第 9 項(1) 表「彭博代碼」欄：代號與交易所碼同一行（`1234 JT`），依 k 順序。"""
    name = "underlyings"
    hits = [ln for ln in item1 if TICKER.fullmatch(ln.text.strip())]
    if not hits:
        return ParsedField.missing(name, "第 9 項(1) 找不到彭博代碼（代號 空白 交易所碼）")
    return ParsedField.present(name, [re.sub(r"\s+", " ", ln.text.strip()) for ln in hits], hits)


def _prices(fields: dict[str, ParsedField], table: ParsedField, tickers: ParsedField, has_autocall: bool) -> None:
    """第 8 項標的表 → 標準欄位 `underlying_prices`（無 KI 時沒有下限價）。

    無自動提前出場時表上沒有 KO 欄：KO 價 = 期初價格（KO 100%，見 `NO_AUTOCALL_KO_PCT`），照常與參考條件表比對。
    """
    if not table.ok:
        fields["underlying_prices"] = derived("underlying_prices", table)
        return
    n = len(table.value.rows)
    codes = tickers.value if tickers.ok and len(tickers.value) == n else [None] * n
    rows = price_table.price_rows(table, codes, names=[f"k={i}" for i in range(1, n + 1)])
    if not has_autocall:
        rows.value = tuple(replace(r, prices=ko_at_initial(r.prices)) for r in rows.value)
    fields["underlying_prices"] = rows


# ---------------------------------------------------------------- 第 12 項


def _autocall_day(t12: TextIndex, art12: list[Line], has_autocall: bool) -> ParsedField:
    """§12 自動提前出場日：D 型「為發生自動提前出場事件後第N個營業日」（值 N）；P 型「請參考下表」（值 "table"）。"""
    name = "autocall_day"
    if not has_autocall:
        return _absent(name, ABSENT_KO)
    m = re.search(r"自動提前出場日：為發生自動提前出場事件後第(\d+)個營業日", t12.text)
    if m:
        return ParsedField.present(name, int(m[1]), t12.lines_for(m.start(), m.end()))
    m = re.search(r"自動提前出場日：請參考下表", t12.text)
    if m:
        return ParsedField.present(name, "table", t12.lines_for(m.start(), m.end()))
    return ParsedField.missing(name, "第 12 項找不到自動提前出場日的定義")


SCHEDULE_WHERE = "第 12 項日期表"


def _schedule_table(value: dict) -> standard_fields.DateTable:
    """日期表：D 觀察日 = 配息週期終止日，起始日（不含）= 前期終止日，第 1～Non-Call 期起始日「不適用」；P 觀察日 = 評價日，
    沒有起始日欄。"""
    if value["type"] != "D":
        rows = schedule.rows_of(value["rows"], "pricing", value["row_lines"])
        return standard_fields.DateTable(SCHEDULE_WHERE, rows)
    rows = schedule.rows_of(value["rows"], "end", value["row_lines"])
    start = standard_fields.PeriodStart("previous_end", None, schedule.blank_starts(rows))
    return standard_fields.DateTable(SCHEDULE_WHERE, rows, start)


def _schedule(fields: dict[str, ParsedField]) -> None:
    """日期表、KO 觀察方式、第一個可提前出場期、提前出場排程（`parsers/schedule.py`）與最終評價日（範本規格 §4.1、§4.3）。

    D：日期表第一個有起始日的期別 i → k = i − 1，比價日為第 8 項觀察期間起始日與末期的配息週期終止日。P：k 取第 8 項，比價日為第 k 期起的
    評價日。無自動提前出場：k = 天期，以 P 表示，比價日只有末期評價日。"""
    table, wording = fields["date_table"], fields["ko_wording"]
    fields["schedule_table"] = schedule.table_field(table, _schedule_table)
    bad = next((p for p in (table, wording) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        fields["final_valuation_date"] = derived("final_valuation_date", bad)
        return
    kind, rows = table.value["type"], table.value["rows"]
    ev = table.evidence + wording.evidence
    wtype = wording.value["type"]
    last = rows[-1]
    final = last["end"] if kind == "D" else last["pricing"]
    fields["final_valuation_date"] = (
        ParsedField("final_valuation_date", FieldStatus.PRESENT, final, list(table.evidence))
        if final is not None
        else ParsedField.invalid("final_valuation_date", table.value["row_lines"][-1], "日期表末列的評價日是「不適用」")
    )
    note, k = "", None
    if (wtype == "D") != (kind == "D"):
        note = "第 8 項觀察期間的寫法與第 12 項日期表表頭不一致（一個是每日觀察、一個是每期觀察）"
    elif wtype == "D":
        dated = next((r["period"] for r in rows if r["start"] is not None), None)
        if dated is None:
            note = "日期表每一期的起始日都是「不適用」，找不到第一個可提前出場期"
        elif dated == 1:
            note = "日期表第 1 期就有起始日，找不到第一個可提前出場期（SG 範本第 1～Non-Call 期應為「不適用」）"
        else:
            k = dated - 1
    else:
        k = wording.value["k"] if wtype == "P" else len(rows)  # 無自動提前出場：Non-Call = 天期
    if note:
        fields.update(schedule.invalid(ev, note))
        return
    obs = ParsedField("ko_observation", FieldStatus.PRESENT, "D" if wtype == "D" else "P", ev)
    first = ParsedField("first_callable_period", FieldStatus.PRESENT, k, list(wording.evidence))
    start = (  # D：第 k 期終止日「不適用」，比價日取第 8 項觀察期間起始日
        ParsedField("ko_start", FieldStatus.PRESENT, wording.value["start"], list(wording.evidence))
        if wtype == "D"
        else None
    )
    fields.update(schedule.autocall_schedule(obs, first, fields["schedule_table"], first_date=start))


# ---------------------------------------------------------------- 第二章、幣別出處、出處清單


def _block(doc: Document, blocks, title: str) -> list[Line]:
    """第二章第 3 項中標題以 `title` 開頭的子項（總代理人、受託或銷售機構）那一段。"""
    found = [sp for sp in blocks.values() if squash(sp.title).startswith(title)]
    return doc.span_lines(found[0]) if len(found) == 1 else []


def _hit_field(name: str, hit: Hit | None, missing: str, pick: Callable = lambda v: v) -> ParsedField:
    """文件上一處值（`pick` 取出其中一個具名欄位）；找不到或多處時為缺漏。"""
    if hit is None:
        return ParsedField.missing(name, missing)
    return ParsedField.present(name, pick(hit.value), list(hit.lines))


def _currency_occurrences(fields: dict[str, ParsedField], scenarios: sg_scenario.ScenarioSection) -> list[Occurrence]:
    """金額旁的幣別字（標準欄位 `currency_others`，Issue #170）：第一章 §5 面額、§8 給付幣別、第四章三個最低金額、
    §15 假設總投資金額各一處；§15 每個情境一處（股價、實體交付算式、現金結算總報酬率算式與匯率不算）。"""
    occ = Occurrence
    out = [
        occ("denomination_currency", "每單位面額幣別", "第一章第 5 項每單位面額", fields["denomination_currency"]),
        occ("currency_art8", "給付幣別", "第一章第 8 項「以…為計價單位給付」", fields["currency_art8"]),
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
            "scenario_total_currency",
            "情境假設總投資金額幣別",
            "第 15 項假設總投資金額",
            _hit_field(
                "scenario_total_currency",
                scenarios.total,
                "第 15 項假設找不到總投資金額或出現多次",
                lambda v: v.currency,
            ),
        ),
    ]
    for s in scenarios.scenarios:
        field = f"情境{s.number}_currency"
        out.append(
            money.scenario_occurrence(field, f"情境{s.number}金額幣別", f"第 15 項情境{s.number}", s.money, s.lines[:1])
        )
    return out


def _mentions(
    fields: dict[str, ParsedField], scenarios: sg_scenario.ScenarioSection
) -> tuple[list[Mention], list[Mention]]:
    """配息率的每個出處與獲利情境的平均年化報酬率（核對規則 §3.3）；讀不到的出處值為 None。"""

    one, hit = Mention.of_field, Mention.of_optional_hit

    coupons = [
        one("第 8 項配息率", fields["monthly_coupon_pct"]),
        hit("第 15 項假設配息率", scenarios.rate, lambda v: v, scenarios.assumptions[-1:]),
    ]
    annualized = []
    for s in scenarios.scenarios:
        where = f"第 15 項情境{s.number}"
        coupons.append(hit(f"{where}配息算式", s.coupon, lambda v: v.rate, s.lines[:1]))
        if s.kind in ("best", "middle"):
            annualized.append(hit(f"{where}平均年化報酬率", s.total_return, lambda v: v.annualized, s.lines[:1]))
    return coupons, annualized
