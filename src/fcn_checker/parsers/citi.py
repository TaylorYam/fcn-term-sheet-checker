"""Citi 中文產品說明書（docs/templates/citi-zh-product-description.md）：章＋條號＋子項＋標籤定位，保留原文證據。

章名「一、商品基本資料」（中文數字＋頓號，五章）；條號「N.」在左邊界（新文件獨立一行、舊文件與標題同一行）；
封面是 `‧` 兩欄表（標籤在左、值在右）。頁尾「第N頁，共M頁」不算內文。所有欄位都在去空白的全文索引上比對。
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
from . import money
from .common import NO_AUTOCALL_KO_PCT, NUM, Mention, dec, derived, ko_at_initial, number, zh_number
from .layout import DATE_RE, Document, LayoutSpec, TextIndex, capture, join_text, parse_date

TEMPLATE_ID = "citi-zh-pd"
PARSER_VERSION = "1"
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
}
# 條號「N.」x≈42.6（新文件獨立一行，舊文件「8. 主要給付…」同一行）；子項「(n)」x≈42–67（第二章 66.6）
LAYOUT = LayoutSpec(
    r"{zh}、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    50,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    95,
    CHAPTER_NAMES,
)
COVER_TITLE = "中文產品說明書"
TITLE_HEAD = "CGMHI"
ISSUER_EN = "Citigroup Global Markets Holdings Inc."
NAME_EN_ISSUER = "issued by Citigroup Global Markets Holdings Inc. and guaranteed by Citigroup Inc."
ARTICLES = 24  # 第一章條數
PAGE_MARK = re.compile(r"^第\s*\d+\s*頁\s*，\s*共\s*\d+\s*頁$")
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
X = r"[x×]"
FEES = ("申購費用", "提前贖回費用", "分銷費用")
FEE_PATTERNS = {  # 第四章 §2 費用表（去空白）：費用項目 → 該列「費率(百分比)」欄；新文件分銷費用取「費用」子列
    "申購費用": r"申購費用申購價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
    "提前贖回費用": r"提前贖回費用投資人提前贖回價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
    "分銷費用": r"分銷費用[(（].*?[)）](?:報酬無(?:不適用)*費用)?申購價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
}
COVER_LABELS = (
    "商品中文名稱",
    "商品英文名稱",
    "商品種類",
    "發行機構註冊地",
    "計價幣別",
    "發行機構之名稱、電話及地址",
    "總代理人之名稱、電話及地址",
    "保證機構之名稱及地址",
    "受託或銷售機構之名稱、電話及地址",
    "受託或銷售機構審查通過之日期/文號",
)
VALUE_X = 230.0  # 封面兩欄表：標籤在左（x < 230）、值與 ‧ 在右
WARNING_HEAD = "警語："
BULLETS = "‧•·"
# 標的表與假設價格表的欄（表頭文字 → 欄）；欄位順序以表頭出現位置為準
HEADERS = (("initial", "期初股價"), ("strike", "執行價"), ("ki", "下限價"), ("ko", "自動提前出場界限"))
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"  # 表格價格只寫中文幣別
PRICE_CELL = rf"({money.NUMBER})({ZH_CCY})"
TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})\s+(?:Equity|EQUITY)\b")
# §9 表頭「期初股價(期初評價日之X)」→ 參考條件表期初定價對照值（開盤價格：OOS 樣本，Issue #229）
INITIAL_PRICING = {"收盤價格": "close", "開盤價格": "open", "VWAP": "vwap"}
AUTOCALL_LIMIT = r"自動提前出場界限：係指相關連結標的之期初股價的" + PCT
NO_AUTOCALL_KO_NOTE = "說明書沒有自動提前出場界限（Non-Call = 天期，不會提前出場）：KO 視為期初股價的 100%"
ABSENT_KO = "說明書沒有自動提前出場（第 13 項沒有自動提前出場界限）"
MEMORY_CLAUSE = "其將被視為已在"  # 逐檔記憶條款「…其將被視為已在該…及其後的…發生「買回自動提前出場」」


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁尾「第N頁，共M頁」不算內文。"""
    return Document([ln for ln in lines if not PAGE_MARK.match(ln.text.strip())], LAYOUT)


# ---------------------------------------------------------------- 封面兩欄表


def cover_items(lines: Sequence[Line]) -> dict[str, list[Line]]:
    """封面「商品中文名稱：」到「受託或銷售機構審查通過之日期/文號：」的兩欄表：標籤（去空白、去冒號）→ 右欄的值行
    （開頭的 ‧ 去掉）。"""
    items: dict[str, list[Line]] = {}
    current: str | None = None
    for ln in lines:
        s = squash(ln.text)
        if s.startswith(WARNING_HEAD):
            break
        label = s.rstrip("：:")
        if ln.x0 < VALUE_X and label in COVER_LABELS:
            current = label
            items.setdefault(label, [])
        elif current is not None and ln.x0 >= VALUE_X and s.strip(BULLETS):
            text = ln.text.lstrip(BULLETS + " ")  # ‧ 與值擷取成同一行時去掉 ‧
            items[current].append(ln if text == ln.text else replace(ln, text=text))
    return items


def _cover_title(cover: Sequence[Line]) -> tuple[Line | None, list[Line]]:
    """封面「中文產品說明書」與其後到代號句之前的標題名稱行。"""
    for i, ln in enumerate(cover):
        if squash(ln.text) == COVER_TITLE:
            title = []
            for x in cover[i + 1 :]:
                if "商品代號為" in squash(x.text):
                    break
                title.append(x)
            return ln, title
    return None, []


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 Citi 說明書（舊文件同一範本）。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    head, title = _cover_title(cover)
    issuer = items.get("發行機構之名稱、電話及地址") or []
    en = squash(join_text(items.get("商品英文名稱") or []))
    arts = doc.articles(1)
    failed = []
    for test, message in (
        (
            head is not None and bool(title) and squash(title[0].text).startswith(TITLE_HEAD),
            f"封面標題不是「中文產品說明書」接 {TITLE_HEAD} 開頭的名稱",
        ),
        (bool(issuer) and squash(issuer[0].text).startswith(squash(ISSUER_EN)), f"封面發行機構名稱不是 {ISSUER_EN}"),
        (squash(NAME_EN_ISSUER) in en, f"商品英文名稱沒有「{NAME_EN_ISSUER}」"),
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
class CitiTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    coupon_mentions: list[Mention]  # 月配息率的每個出處（第 13 項、第 14 項(2)、第 13 項末段、第 15 項假設 d)）
    annual_mentions: list[Mention]  # 年息的其他出處（第 14 項(2)、第 13 項末段）

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def _item_text(items: dict[str, list[Line]], label: str, name: str) -> ParsedField:
    lns = items.get(label) or []
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    return ParsedField.present(name, join_text(lns).strip(), lns)


def _before_address(items: dict[str, list[Line]], label: str, name: str, pattern: str | None = None) -> ParsedField:
    """兩欄表某項右欄「地址：」之前的行合併（機構名稱），`pattern` 取其中一段（去空白後比對）。"""
    lns = items.get(label) or []
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    head = []
    for ln in lns:
        if squash(ln.text).startswith(("地址：", "電話：")):
            break
        head.append(ln)
    text = join_text(head).strip()
    if pattern is not None:
        m = re.fullmatch(pattern, text, re.S)
        if not m:
            return ParsedField.invalid(name, head, f"封面「{label}」的值格式未知")
        text = m[1].strip()
    return ParsedField.present(name, text, head)


def _labelled(name: str, lines: Sequence[Line], label: str) -> ParsedField:
    """以「標籤：值」開頭的那一行（去掉前面的項目符號）的值；標籤比對去空白。"""
    hits = []
    for ln in lines:
        text = re.sub(
            r"^[\s" + BULLETS + r"]*(?:[(（]\d{1,2}[)）]\s*)?", "", ln.text
        )  # 舊文件「(1) 公司名稱：…」同一行
        if squash(text).startswith(squash(label)):
            m = re.match(r"^\s*" + "".join(re.escape(c) + r"\s*" for c in squash(label)) + r"(.*)$", text)
            hits.append((m[1].strip() if m else "", [ln]))
    return ParsedField.from_hits(name, hits, missing_note=f"找不到「{label}」")


def _date_field(name: str, pf: ParsedField, what: str) -> ParsedField:
    value = parse_date(pf.value) if pf.ok else None
    if pf.ok and value is None:
        return ParsedField(name, FieldStatus.INVALID, evidence=pf.evidence, note=f"{what}不是日期")
    return derived(name, pf, value)


def read(lines: Sequence[Line]) -> CitiTermSheet:
    """讀出標準欄位與 Citi 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
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
    cover_ti = TextIndex(cover)
    codes = re.search(
        r"受託或銷售機構商品代號為(\d{12}|\[\]|)，ISIN為([A-Z]{2}[A-Z0-9]{10})，集保商品代號為(\d{12})", cover_ti.text
    )
    code_lines = [ln for ln in cover if "商品代號為" in squash(ln.text)]
    for key, group in (("trustee_product_code", 1), ("isin", 2), ("product_code", 3)):
        if codes:
            value = "" if key == "trustee_product_code" and not codes[group].isdigit() else codes[group]
            fields[key] = ParsedField.present(key, value, cover_ti.lines_for(codes.start(), codes.end()))
        elif code_lines:
            fields[key] = ParsedField.invalid(
                key, code_lines, "封面代號句的寫法未知（受託或銷售機構商品代號為…，ISIN 為…，集保商品代號為…）"
            )
        else:
            fields[key] = ParsedField.missing(key, "找不到封面代號句")
    _, title = _cover_title(cover)
    fields["cover_title"] = (
        ParsedField.present("cover_title", join_text(title).strip(), title)
        if title
        else ParsedField.missing("cover_title", "找不到封面標題名稱（「中文產品說明書」之後到代號句之前）")
    )
    zh_lines = items.get("商品中文名稱") or []
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
    fields["issuer_name_cover"] = _before_address(
        items, "發行機構之名稱、電話及地址", "issuer_name_cover", r"(.+?)\s*(?:\([“\"]CGMHI[”\"]\))?"
    )
    fields["agent_name_cover"] = _before_address(items, "總代理人之名稱、電話及地址", "agent_name_cover")
    fields["guarantor_name_cover"] = _before_address(items, "保證機構之名稱及地址", "guarantor_name_cover")
    dist = "受託或銷售機構之名稱、電話及地址"
    fields["distributor_name_cover"] = _before_address(items, dist, "distributor_name_cover", r"(.+?)\s*[(（]下稱.*")
    fields["distributor_address_cover"] = _labelled("distributor_address_cover", items.get(dist) or [], "地址：")
    fields["distributor_phone_cover"] = _labelled("distributor_phone_cover", items.get(dist) or [], "電話：")
    approval = _item_text(items, "受託或銷售機構審查通過之日期/文號", "approval_date")
    fields["approval_date"] = _date_field("approval_date", approval, "審查通過日期")
    fields["print_date"] = capture("print_date", cover_ti, r"產品說明書刊印日期：" + D, parse_date, whole_match=True)

    # 第一章
    put("name_art1", article(1), r"商品名稱：(.+?)（下稱「本商品」）")
    art3 = article(3)
    put("issuer_name_ch1", art3, r"發行機構名稱及其長期債務信用評等：(.+?)；")
    put("guarantor_name_ch1", art3, r"保證機構名稱及其長期債務信用評等：(.+?)；")
    art5 = article(5)
    put("currency_art5", art5, rf"計價幣別：({CCY})\([A-Z]{{3}}\)")
    put("currency_art5_iso", art5, rf"計價幣別：{CCY}\(([A-Z]{{3}})\)")
    put("denomination", art5, r"每單位商品面額：" + NUM + CCY, number)
    put("denomination_currency", art5, rf"每單位商品面額：{money.NUMBER}({CCY})")
    put("issue_price_pct", art5, r"發行價格：每單位商品面額的" + PCT, Decimal)

    art8 = article(8)
    t8 = TextIndex(art8)
    put("strike_pct", art8, r"執行價係指相關連結標的之期初股價的" + PCT, Decimal)
    ki_raw = capture("ki_pct", t8, r"下限價：係指相關連結標的之期初股價的" + PCT, Decimal, whole_match=True)
    put("maturity_redemption_pct", art8, rf"\(a\)若.*?以{CCY}支付本商品每單位商品面額的" + PCT, Decimal)
    put("currency_art8", art8, rf"\(a\)若.*?以({CCY})支付本商品每單位商品面額的")
    fields["ki_type"] = ki_type(t8, art8)
    put("ki_periods", art8, r"觸及下限事件：在任t個觀察日\(t=1-(\d+)\)", int)  # 只有 KI 每期觀察
    m4 = re.search(r"\(4\)發生(?:記憶式)?自動提前出場：", t8.text)  # 第 8 項 (4)：只有會提前出場的商品才有
    fields["art8_autocall"] = ParsedField.present(
        "art8_autocall", m4 is not None, t8.lines_for(m4.start(), m4.end()) if m4 else art8[:1]
    )
    ki = fields["ki_type"]
    if ki.ok and ki.value == "none" and not ki_raw.ok:
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence), note="說明書無 KI（第 8 項沒有下限價）"
        )
    else:
        fields["ki_pct"] = ki_raw

    art13 = article(13)
    t13 = TextIndex(art13)
    has_autocall = bool(re.search(AUTOCALL_LIMIT, t13.text))
    fields["has_autocall"] = ParsedField.present("has_autocall", has_autocall, art13[:1])
    if has_autocall:
        put("ko_pct", art13, AUTOCALL_LIMIT, Decimal)
    else:
        fields["ko_pct"] = ParsedField(
            "ko_pct",
            FieldStatus.PRESENT,
            NO_AUTOCALL_KO_PCT,
            [Evidence.of(x) for x in art13[:1]],
            note=NO_AUTOCALL_KO_NOTE,
        )
    put("coupon_pa_pct", art13, rf"每單位商品面額{X}\d+(?:\.\d+)?%\(約?等同年息為" + PCT + r"\)", Decimal)
    put("monthly_coupon_pct", art13, rf"每單位商品面額{X}" + PCT + r"\(約?等同年息為", Decimal)
    fields["ko_memory"] = ko_memory(t13, art13, has_autocall)
    fields["ko_k_art13"] = _k_in_art13(t13, art13, has_autocall)
    if re.search(rf"應以{CCY}支付下列固定配息金額", t13.text):  # P 型／無自動提前出場才有
        put("currency_art13", art13, rf"應以({CCY})支付下列固定配息金額")
    fields["memory_wording_ch1"] = _memory_wording(doc)

    # §9 標的表
    art9 = article(9)
    table = price_table("underlying_table", _table_region(art9, "連結標的資產：", "（每一連結標的下稱"))
    fields["underlying_table"] = table
    fields["underlyings"] = (
        ParsedField("underlyings", FieldStatus.PRESENT, list(table.value["tickers"]), list(table.evidence))
        if table.ok
        else derived("underlyings", table)
    )
    fields["initial_pricing"] = (
        ParsedField("initial_pricing", FieldStatus.PRESENT, table.value["initial_pricing"], list(table.evidence))
        if table.ok and table.value["initial_pricing"] is not None
        else ParsedField(
            "initial_pricing",
            FieldStatus.INVALID,
            evidence=list(table.evidence),
            note="標的表表頭「期初股價(期初評價日之…)」寫法未知",
        )
        if table.ok
        else derived("initial_pricing", table)
    )
    _prices(fields, table, has_autocall)

    # §12 日期
    art12 = article(12)
    t12 = TextIndex(art12)
    put("tenor_months", art12, r"商品年期：(\d+)個月", int)
    put("trade_date", art12, r"\(\d+\)交易日：" + D, parse_date)
    put("initial_valuation_date", art12, r"\(\d+\)期初評價日：" + D, parse_date)
    put("issue_date", art12, r"\(\d+\)發行日：" + D, parse_date)
    put("final_valuation_date", art12, r"\(\d+\)期末評價日：" + D, parse_date)
    put("maturity_date", art12, r"\(\d+\)到期日：" + D, parse_date)
    fields["ko_k_art12"] = _k_in_art12(t12, art12)
    fields["date_table"] = date_table(art12)
    _schedule(fields)

    # §14、§15
    art14 = article(14)
    put("monthly_coupon_art14", art14, rf"每單位商品面額{X}" + PCT + r"\(年化配息率約為", Decimal)
    put("coupon_pa_art14", art14, r"\(年化配息率約為" + PCT + r"\)", Decimal)
    art15 = article(15)
    t15 = TextIndex(art15)
    a = t15.text.find("假設：")
    b = t15.text.find("情境", a + 1) if a >= 0 else -1
    assumptions = t15.lines_for(a, b) if a >= 0 and b > a else []
    put("scenario_coupon", assumptions, rf"d\)固定配息金額為每單位商品面額{X}" + PCT, Decimal)
    put("scenario_denomination_currency", assumptions, rf"a\)每單位商品面額：{money.NUMBER}({CCY})")
    put("scenario_total_currency", assumptions, rf"c\)初始投資金額[=：].*?={money.NUMBER}({CCY})")
    fields["scenario_table"] = price_table(
        "scenario_table", _table_region(assumptions, "連結標的相關股價資訊", None), reprint=True
    )
    fields["dividend_example"] = capture(
        "dividend_example",
        t13,
        r"配息率為" + PCT + r"（即年化配息率為\d+(?:\.\d+)?%）",
        Decimal,
        whole_match=True,
    )
    fields["coupon_pa_example"] = capture(
        "coupon_pa_example", t13, r"配息率為\d+(?:\.\d+)?%（即年化配息率為" + PCT + r"）", Decimal, whole_match=True
    )

    # 第二章
    ch2 = doc.articles(2)
    fields["issuer_name_ch2"] = _labelled("issuer_name_ch2", doc.span_lines(ch2.get(1)), "公司名稱：")
    fields["guarantor_name_ch2"] = _labelled("guarantor_name_ch2", doc.span_lines(ch2.get(2)), "公司名稱：")
    blocks = doc.subitems(ch2.get(3))
    agent = _block(doc, blocks, "總代理人")
    fields["agent_name_ch2"] = _labelled("agent_name_ch2", agent, "公司名稱：")
    trustee = _block(doc, blocks, "受託或銷售機構")
    fields["distributor_name_ch2"] = _labelled("distributor_name_ch2", trustee, "公司名稱：")
    fields["distributor_address_ch2"] = _labelled("distributor_address_ch2", trustee, "營業所在地：")
    fields["chairman"] = _labelled("chairman", trustee, "負責人姓名：")
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")

    # 第四章
    ch4 = doc.articles(4)
    art1_ch4 = doc.span_lines(ch4.get(1))
    put("subscription_start", art1_ch4, r"開始受理申購日期：" + D, parse_date)
    put("subscription_end", art1_ch4, r"結束受理申購日期：" + D, parse_date)
    put("redemption_start", art1_ch4, r"開始受理贖回日期：" + D, parse_date)
    for label in FEES:
        fields[standard_fields.fee_field(label)] = capture(
            standard_fields.fee_field(label),
            TextIndex(doc.span_lines(ch4.get(2))),
            FEE_PATTERNS[label],
            whole_match=True,
        )
    art4 = doc.span_lines(ch4.get(4))
    put("minimum_subscription", art4, r"最低申購金額：" + NUM + CCY, number)
    put("minimum_additional", art4, r"最低加購金額：" + NUM + CCY, number)
    put("minimum_subscription_currency", art4, rf"最低申購金額：{money.NUMBER}({CCY})")
    put("minimum_additional_currency", art4, rf"最低加購金額：{money.NUMBER}({CCY})")
    put(
        "issue_price_ch4",
        doc.span_lines(ch4.get(5)),
        rf"申購價金之計算：每單位商品面額{X}申購單位數{X}" + PCT,
        Decimal,
    )
    put("minimum_issue_size", doc.span_lines(ch4.get(7)), r"有效申購金額至少達(等值[\d,]+萬?\D{1,5}?)之總申購價值")
    art8_ch4 = doc.span_lines(ch4.get(8))
    put("minimum_redemption", art8_ch4, r"最低贖回金額或單位數：" + NUM + CCY, number)
    put("minimum_redemption_currency", art8_ch4, rf"最低贖回金額或單位數：{money.NUMBER}({CCY})")

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
            occ("subscription_start", "開始受理申購日", "第四章第 1 項開始受理申購日期", fields["subscription_start"]),
            occ("subscription_end", "結束受理申購日", "第四章第 1 項結束受理申購日期", fields["subscription_end"]),
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "封面產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [
            occ(
                "issue_price_ch4",
                "發行價格（第四章申購價金）",
                "第四章第 5 項申購價金之計算",
                fields["issue_price_ch4"],
            )
        ],
    )
    fields["currency_others"] = standard_fields.occurrences("currency_others", _currency_occurrences(fields))
    one = Mention.of_field
    coupons = [one("第 13 項配息率", fields["monthly_coupon_pct"])]
    coupons += [Mention(f"第 13 項配息率（第 {i} 處）", v, ev) for i, (v, ev) in enumerate(_art13_rates(t13), start=1)]
    coupons += [
        one("第 14 項(2) 最低保證配息率", fields["monthly_coupon_art14"]),
        one("第 13 項配息後市場價值舉例", fields["dividend_example"]),
        one("第 15 項假設 d) 固定配息金額", fields["scenario_coupon"]),
    ]
    annual = [
        one("第 14 項(2) 年化配息率", fields["coupon_pa_art14"]),
        one("第 13 項配息後市場價值舉例的年化配息率", fields["coupon_pa_example"]),
    ]
    return CitiTermSheet(fields, TextIndex(doc.lines), doc, coupons, annual)


# ---------------------------------------------------------------- 第 8、13 項


def ki_type(t8: TextIndex, art8: list[Line], where: str = "第 8 項") -> ParsedField:
    """第 8 項(3)「觸及下限事件：」的寫法 → KI 型態（範本規格 §4.4）；無 KI 以 present「none」交出。

    投資人須知到期給付金額段的句型相同（parsers/citi_iis.py），`where` 是錯訊裡的出處。"""
    name = "ki_type"
    text = t8.text
    m = re.search(r"觸及下限事件[：:](.{0,60})", text)
    if m is None:
        if "下限價" in text:
            return ParsedField.invalid(name, art8, f"{where}有下限價，但找不到「觸及下限事件：」")
        n = re.search(r"\(a\)若在期末評價日(?:表現最差)?連結標的之最終收盤價等於或大於執行價", text)
        if n is None:
            return ParsedField.invalid(name, art8, f"{where}到期給付的寫法不在範本規格內")
        return ParsedField.present(name, "none", t8.lines_for(n.start(), n.end()))
    body, lns = m[1], t8.lines_for(m.start(), m.end())
    if body.startswith("在期末評價日"):
        return ParsedField.present(name, "AM", lns)
    if body.startswith("於期初評價日（不含當日）起至期末評價日（含當日）止之期間內，每一個預定交易日"):
        return ParsedField.present(name, "D", lns)
    p = re.match(r"在任t個觀察日\(t=1-(\d+)\)", body)
    if p:
        return ParsedField.present(name, "P", lns)
    return ParsedField.invalid(name, lns, f"{where}觸及下限事件的觀察寫法不在範本規格內")


def ko_memory(t13: TextIndex, art13: list[Line], has_autocall: bool) -> ParsedField:
    """第 13 項段名「記憶式自動提前出場事件：」且有逐檔記憶條款 → 記憶式；段名「自動提前出場事件：」且沒有 → 非記憶式。"""
    name = "ko_memory"
    if not has_autocall:
        return ParsedField.present(name, False, art13[:1])
    text = t13.text
    memo = re.search(r"記憶式自動提前出場事件[：:]", text)
    plain = re.search(r"(?<!記憶式)自動提前出場事件[：:]", text)
    clause = MEMORY_CLAUSE in text
    if memo and not plain and clause:
        return ParsedField.present(name, True, t13.lines_for(memo.start(), memo.end()))
    if plain and not memo and not clause:
        return ParsedField.present(name, False, t13.lines_for(plain.start(), plain.end()))
    return ParsedField.invalid(name, art13, "第 13 項自動提前出場事件的段名與記憶條款不一致或不在範本規格內")


def _k_in_art13(t13: TextIndex, art13: list[Line], has_autocall: bool) -> ParsedField:
    """第 13 項 D 型「(即第k個配息觀察期間結束日（含當日）起至期末評價日…)」的 k；P 型「於觀察日，所有連結標的」為 P。"""
    name = "ko_k_art13"
    if not has_autocall:
        return standard_fields.absent(name, "自動提前出場觀察日（無自動提前出場）")
    d = list(t13.finditer(r"即第(\S{1,3}?)個配息觀察期間結束日（含當日）起至期末評價日"))
    if d:
        ks = {zh_number(m[1]) for m in d}
        lns = t13.lines_for(d[0].start(), d[0].end())
        if len(ks) != 1 or None in ks:
            return ParsedField.invalid(name, lns, "第 13 項自動提前出場觀察日的期別寫法不一致")
        return ParsedField.present(name, ("D", ks.pop()), lns)
    p = re.search(r"於觀察日，所有連結標的", t13.text)
    if p:
        return ParsedField.present(name, ("P", 1), t13.lines_for(p.start(), p.end()))
    return ParsedField.invalid(name, art13, "第 13 項自動提前出場事件的觀察寫法不在範本規格內")


def _k_in_art12(t12: TextIndex, art12: list[Line]) -> ParsedField:
    """第 12 項(9)「於第{k}個配息觀察期間結束日（含當日）起至期末評價日…每一個預定交易日」的 k（只有 D 型）。"""
    name = "ko_k_art12"
    found = list(
        t12.finditer(
            r"於第(\S{1,3}?)個配息觀察期間結束日（含當日）起至期末評價日（含當日）止之期間內，每一個預定交易日"
        )
    )
    if not found:
        return ParsedField.present(name, None, art12[:1])
    ks = {zh_number(m[1]) for m in found}
    lns = t12.lines_for(found[0].start(), found[0].end())
    if len(ks) != 1 or None in ks:
        return ParsedField.invalid(name, lns, "第 12 項自動提前出場觀察日的期別寫法不在範本規格內")
    return ParsedField.present(name, ks.pop(), lns)


def _art13_rates(t13: TextIndex) -> list[tuple[Decimal, tuple[Evidence, ...]]]:
    """第 13 項其他配息率出處：D 型「每單位商品面額 × X%」（第 1 期以外、按比例條款 `m ÷ M ×`），不含第一處（已是主來源）。"""
    out = []
    for i, m in enumerate(t13.finditer(rf"每單位商品面額{X}(?:m[÷/]M{X})?" + PCT)):
        if i == 0:
            continue
        out.append((Decimal(m[1]), tuple(Evidence.of(x) for x in t13.lines_for(m.start(), m.end()))))
    return out


def _memory_wording(doc: Document) -> ParsedField:
    """第一章出現「記憶式」的每一處（非記憶式商品不得出現；比對「記憶式」三個字，不是標的說明的「記憶體」）。"""
    ti = TextIndex(doc.chapter_lines(1))
    hits = [ti.lines_for(m.start(), m.end())[0] for m in ti.finditer("記憶式")]
    return ParsedField.present("memory_wording_ch1", len(hits), hits or ti.lines[:1])  # 沒有時證據為章名


# ---------------------------------------------------------------- 標的表


def _table_region(lines: list[Line], start: str, end: str | None) -> list[Line]:
    """`start` 那一行之後到 `end` 那一行之前（去空白比對開頭）。"""
    a = next(
        (
            i
            for i, ln in enumerate(lines)
            if re.sub(r"^(?:\d{1,2}\.|[a-z]\))", "", squash(ln.text)).startswith(squash(start))
        ),
        None,
    )
    if a is None:
        return []
    b = next(
        (i for i in range(a + 1, len(lines)) if end is not None and squash(lines[i].text).startswith(end)), len(lines)
    )
    return lines[a + 1 : b]


def price_table(name: str, lines: list[Line], *, reprint: bool = False) -> ParsedField:
    """§9 標的表（`reprint` 為 §15 假設價格表）：表頭決定欄位與順序、表頭 %、期初定價寫法；每檔一列（列號、名稱、代碼、價格）。

    值：{"columns", "tickers", "rows": [{欄: Decimal}], "row_lines", "pcts": {欄: Decimal}, "initial_pricing",
    "currencies"}。代碼以「代號 交易所碼 Equity」切列，價格以「數字＋幣別字」切格。
    """
    if not lines:
        return ParsedField.missing(name, "找不到標的表" if not reprint else "第 15 項找不到假設價格表")
    first_row = next((i for i, ln in enumerate(lines) if re.fullmatch(r"\s*1\s*", ln.text)), None)
    if first_row is None:
        return ParsedField.invalid(name, lines, "標的表找不到第 1 列")
    head = "".join(squash(ln.text) for ln in lines[:first_row])
    positions = sorted((head.find(label), col) for col, label in HEADERS if label in head)
    columns = [col for _, col in positions]
    if columns[:2] != ["initial", "strike"]:
        return ParsedField.invalid(name, lines[:first_row], "標的表表頭不是「期初股價、執行價…」的順序")
    found = [Decimal(x) for x in re.findall(r"期初股價的" + PCT, head)]
    pct_cols = [c for c in columns if c != "initial"]
    if len(found) != len(pct_cols):
        return ParsedField.invalid(name, lines[:first_row], "標的表表頭的百分比數與欄位數不符")
    pcts = dict(zip(pct_cols, found, strict=True))
    pricing = re.search(r"期初評價日之(" + "|".join(INITIAL_PRICING) + ")", head)
    body = lines[first_row:]
    joined = " ".join(ln.text.strip() for ln in body)
    tickers = [f"{m[1]} {m[2]}" for m in TICKER.finditer(joined)]
    bti = TextIndex(body)
    cells = list(bti.finditer(PRICE_CELL))
    ncol = len(columns)
    if not tickers or len(cells) != len(tickers) * ncol:
        return ParsedField.invalid(
            name, lines, f"標的表有 {len(cells)} 個價格格、{len(tickers)} 個代碼，與 {ncol} 欄不符"
        )
    rows, row_lines = [], []
    for i in range(len(tickers)):
        prices: dict[str, Decimal] = {}
        lns: list[Line] = []
        for j, col in enumerate(columns):
            m = cells[i * ncol + j]
            prices[col] = dec(m[1])
            lns += [x for x in bti.lines_for(m.start(), m.end()) if x not in lns]
        rows.append(prices)
        row_lines.append(lns)
    value = {
        "columns": columns,
        "tickers": tickers,
        "rows": rows,
        "row_lines": row_lines,
        "pcts": pcts,
        "initial_pricing": INITIAL_PRICING.get(pricing[1]) if pricing else None,
        "currencies": {m[2] for m in cells},
    }
    return ParsedField.present(name, value, lines)


def _prices(fields: dict[str, ParsedField], table: ParsedField, has_autocall: bool) -> None:
    """§9 標的表 → 標準欄位 `underlying_prices`；無自動提前出場時 KO 價 = 期初股價（KO 100%）。"""
    if not table.ok:
        fields["underlying_prices"] = derived("underlying_prices", table)
        return
    fields["underlying_prices"] = ParsedField(
        "underlying_prices",
        FieldStatus.PRESENT,
        tuple(
            standard_fields.PriceRow(
                ticker,
                dict(prices) if has_autocall else ko_at_initial(prices),
                tuple(Evidence.of(ln) for ln in lns),
                name=f"第 {i} 檔",
            )
            for i, (ticker, prices, lns) in enumerate(
                zip(table.value["tickers"], table.value["rows"], table.value["row_lines"], strict=True), start=1
            )
        ),
        list(table.evidence),
    )


# ---------------------------------------------------------------- 第 12 項日期表


def date_table(lines: list[Line]) -> ParsedField:
    """§12(7) 日期表：「(t)」表頭之後的表頭格決定表型（D：配息觀察期間開始日／結束日／配息日；P：觀察日／配息日；
    none：只有配息日），資料列從「1」起到「每一期配息日…」說明句為止。

    值：{"type": "D"|"P"|"none", "rows": [...], "row_lines": [...]}；D 列為 period／start／end／payment（不適用為 None），
    P 列為 period／observation／payment，none 列為 period／payment。
    """
    name = "date_table"
    heads = [i for i, ln in enumerate(lines) if squash(ln.text) == "(t)"]
    if len(heads) != 1:
        return ParsedField.missing(name, "找不到日期表表頭「(t)」" if not heads else "日期表表頭出現多次")
    first = next((i for i in range(heads[0] + 1, len(lines)) if squash(lines[i].text) == "1"), None)
    if first is None:
        return ParsedField.invalid(name, lines[heads[0] :], "日期表沒有資料列")
    header = "".join(squash(ln.text) for ln in lines[heads[0] + 1 : first])
    if header == "配息觀察期間開始日（不含當日）配息觀察期間結束日（含當日）配息日":
        kind, keys = "D", ("start", "end", "payment")
    elif header == "觀察日配息日":
        kind, keys = "P", ("observation", "payment")
    elif header == "配息日":
        kind, keys = "none", ("payment",)
    else:
        return ParsedField.invalid(name, lines[heads[0] : first], "日期表表頭不是已知的三種寫法")
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    end = first
    for i in range(first, len(lines)):
        t = squash(lines[i].text)
        if re.fullmatch(r"\d{1,2}", t):
            rows.append({"period": int(t), "cells": []})
            row_lines.append([lines[i]])
        elif t == "不適用" or DATE_RE.fullmatch(t):
            value = None if t == "不適用" else parse_date(t)
            if t != "不適用" and value is None:
                return ParsedField.invalid(name, [lines[i]], "日期表日期不合法")
            rows[-1]["cells"].append(value)
            row_lines[-1].append(lines[i])
        else:
            break
        end = i + 1
    out = []
    for row, lns in zip(rows, row_lines, strict=True):
        cells = row["cells"]
        if len(cells) != len(keys) or cells[-1] is None:
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列欄位數或內容不符範本")
        if kind == "P" and cells[0] is None:
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列觀察日是「不適用」")
        out.append({"period": row["period"], **dict(zip(keys, cells, strict=True))})
    if not out or [r["period"] for r in out] != list(range(1, len(out) + 1)):
        return ParsedField.invalid(name, lines[first:end], "日期表期數不是從 1 起連續")
    return ParsedField.present(name, {"type": kind, "rows": out, "row_lines": row_lines}, lines[first:end])


def _schedule(fields: dict[str, ParsedField]) -> None:
    """KO 觀察方式、第一個可提前出場期與提前出場排程（範本規格 §4.1）。"""
    table, k12, k13, has = fields["date_table"], fields["ko_k_art12"], fields["ko_k_art13"], fields["has_autocall"]
    keys = ("ko_observation", "first_callable_period", "autocall_schedule")
    deps = [table, k12] + ([k13] if has.value else [])
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        for key in keys:
            fields[key] = derived(key, bad)
        return
    kind, rows = table.value["type"], table.value["rows"]
    ev = list(table.evidence) + list(k12.evidence) + (list(k13.evidence) if has.value else [])

    def invalid(note: str) -> None:
        for key in keys:
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)

    n = len(rows)
    if not has.value:
        if kind != "none" or k12.value is not None:
            invalid("第 13 項沒有自動提前出場界限，但日期表或第 12 項(9) 有自動提前出場觀察日")
            return
        k, observation = n, "P"
        dates = {n: fields["final_valuation_date"].value} if fields["final_valuation_date"].ok else {}
        if not dates:
            fields_bad = fields["final_valuation_date"]
            for key in keys:
                fields[key] = derived(key, fields_bad)
            return
    elif kind == "D":
        if k12.value is None or k13.value[0] != "D":
            invalid("日期表是每日觀察（三欄），但第 12 項(9)／第 13 項的自動提前出場觀察日不是每日觀察寫法")
            return
        if k13.value[1] != k12.value:
            invalid(f"第 12 項(9) 寫第 {k12.value} 個配息觀察期間、第 13 項寫第 {k13.value[1]} 個，不一致")
            return
        k = k12.value
        if not 1 <= k <= n or rows[k - 1]["end"] is None:
            invalid(f"第一個可提前出場期 {k} 在日期表沒有配息觀察期間結束日")
            return
        dates, observation = {k: rows[k - 1]["end"], n: rows[-1]["end"]}, "D"
    elif kind == "P":
        if k12.value is not None or k13.value[0] != "P":
            invalid("日期表是每期觀察（觀察日），但第 12 項(9)／第 13 項的自動提前出場觀察日不是每期觀察寫法")
            return
        k = k13.value[1]
        if not 1 <= k <= n:
            invalid(f"第一個可提前出場期 {k} 不在日期表第 1～{n} 期之間")
            return
        dates, observation = {r["period"]: r["observation"] for r in rows if r["period"] >= k}, "P"
    else:
        invalid("第 13 項有自動提前出場界限，但日期表只有配息日")
        return
    fields["ko_observation"] = ParsedField("ko_observation", FieldStatus.PRESENT, observation, ev)
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule", FieldStatus.PRESENT, standard_fields.AutocallSchedule(observation, k, n, dates), ev
    )


# ---------------------------------------------------------------- 第二章、幣別出處


def _block(doc: Document, blocks, title: str) -> list[Line]:
    """第二章第 3 項中標題以 `title` 開頭的子項（總代理人、受託或銷售機構）那一段。"""
    found = [sp for sp in blocks.values() if squash(sp.title).startswith(title)]
    return doc.span_lines(found[0]) if len(found) == 1 else []


def _currency_occurrences(fields: dict[str, ParsedField]) -> list[Occurrence]:
    """金額旁的幣別字（標準欄位 `currency_others`，Issue #170）：第一章 §5 面額、§8 到期給付幣別、§13 配息幣別
    （P 型／無自動提前出場才有）、§15 假設 a)、c)、第四章三個最低金額。標的價格、執行價與最終匯率不列入。"""
    occ = Occurrence
    out = [
        occ(
            "denomination_currency",
            "每單位商品面額幣別",
            "第一章第 5 項每單位商品面額",
            fields["denomination_currency"],
        ),
        occ("currency_art8", "到期給付幣別", "第一章第 8 項到期日給付「以…支付」", fields["currency_art8"]),
        occ(
            "scenario_denomination_currency",
            "情境假設面額幣別",
            "第 15 項假設 a) 每單位商品面額",
            fields["scenario_denomination_currency"],
        ),
        occ(
            "scenario_total_currency",
            "情境假設初始投資金額幣別",
            "第 15 項假設 c) 初始投資金額",
            fields["scenario_total_currency"],
        ),
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
    ]
    if "currency_art13" in fields:
        out.insert(2, occ("currency_art13", "配息幣別", "第一章第 13 項「應以…支付」", fields["currency_art13"]))
    return out
