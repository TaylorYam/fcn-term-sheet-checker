"""HSBC 中文產品說明書：章＋條＋子項＋標籤定位，保留原文證據。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from . import hsbc_tables as tables
from .layout import Document, LayoutSpec, TextIndex, parse_date, squash

TEMPLATE_ID = "hsbc-zh-pd"
PARSER_VERSION = "1"
LAYOUT = LayoutSpec(r"第{zh}章\s*{name}", re.compile(r"^(\d{1,2})\.$"), 100, re.compile(r"^\((\d{1,2})\)\s*(.*)$"), 110)
N = r"([\d,]+(?:\.\d+)?)"
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"


def document(lines: Sequence[Line]) -> Document:
    clean = [x for x in lines if x.text != "PUBLIC" and not re.fullmatch(r"-?\s*第\s*\d+\s*頁，共\s*\d+\s*頁", x.text)]
    return Document(clean, LAYOUT)


def capture(name: str, lines: list[Line], pattern: str, convert: Callable = lambda x: x) -> ParsedField:
    ti = TextIndex(lines)
    matches = list(ti.finditer(pattern))
    if not matches:
        return ParsedField.missing(name, "找不到欄位標籤或已知寫法")
    values, evidence = [], []
    for m in matches:
        lns = ti.lines_for(m.start(), m.end())
        try:
            value = convert(m[1])
        except (ValueError, TypeError, InvalidOperation):
            return ParsedField.invalid(name, lns)
        if value is None:
            return ParsedField.invalid(name, lns)
        if value not in values:
            values.append(value)
        evidence.extend(lns)
    if len(values) != 1:
        return ParsedField.ambiguous(name, values, evidence)
    return ParsedField.present(name, values[0], evidence)


def product_code(lines: Sequence[Line]) -> ParsedField:
    return capture("product_code", document(lines).before_chapter1(), r"商品代號/商品中文名稱[:：](\d{12})/")


def detect(lines: Sequence[Line]) -> DetectionResult:
    doc = document(lines)
    cover = TextIndex(doc.before_chapter1())
    failed = []
    for test, message in (
        ("中文產品說明書" in cover.text, "封面缺少中文產品說明書"),
        (
            "香港上海滙豐銀行" in cover.text and "發行機構：香港商香港上海滙豐銀行股份有限公司" in cover.text,
            "發行機構不是HSBC",
        ),
        ("AutocallableFixedCouponNotes" in cover.text, "商品家族未知"),
        (len(doc.chapters) == 5, "章節不完整"),
    ):
        if not test:
            failed.append(message)
    articles = doc.articles(1)
    for n, label in ((11, "主要給付項目"), (12, "連結標的資產"), (15, "本商品年期")):
        if label not in TextIndex(doc.span_lines(articles.get(n))).text:
            failed.append(f"第一章第{n}條錨點不符")
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in doc.before_chapter1()[:2]])


@dataclass
class HsbcTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    scenarios: list[Line]

    def f(self, name: str) -> ParsedField:
        return self.fields.get(name, ParsedField.missing(name))


def _standard_prices(table: ParsedField) -> ParsedField:
    """標準欄位 `underlying_prices`：第 12 條價格表各列（代號、四種價格、該列原文）。"""
    name = "underlying_prices"
    if not table.ok:
        return ParsedField(name, table.status, None, list(table.evidence), list(table.candidates), table.note)
    value = tuple(
        standard_fields.PriceRow(r["ticker"], dict(r["prices"]), tuple(Evidence.of(ln) for ln in lns))
        for r, lns in zip(table.value["rows"], table.value["row_lines"], strict=True)
    )
    return ParsedField(name, FieldStatus.PRESENT, value, list(table.evidence))


def parse(lines: Sequence[Line]) -> tuple[DetectionResult, HsbcTermSheet]:
    doc = document(lines)
    art = doc.articles(1)
    cover = doc.before_chapter1()
    fields = {}

    def put(name, lns, pattern, convert=lambda x: x):
        fields[name] = capture(name, lns, pattern, convert)

    def article(n):
        return doc.span_lines(art.get(n))

    def sub(n, k):
        return doc.span_lines(doc.subitems(art.get(n)).get(k))

    def number(x):
        return Decimal(x.replace(",", ""))

    def legal_name(x):
        s = squash(x).translate(str.maketrans({"(": "（", ")": "）"}))
        if "THE" in s and "（" not in s:
            s = s.replace("THE", "（THE", 1) + "）"
        return s

    fields["product_code"] = product_code(lines)
    put("name_zh", cover, r"商品代號/商品中文名稱[:：]\d{12}/(.+?)商品英文名稱[:：]")
    put("name_en", cover, r"商品英文名稱[:：](.+?)商品種類[:：]")
    put("name_title", cover, r"^(香港上海滙豐銀行.+?結構型商品)")
    put("name_en_title", cover, r"結構型商品(.+?)中文產品說明書")
    put("currency_zh", cover, r"計價幣別[:：](.+?)發行機構[:：]")
    put("issuer_name_cover", cover, r"發行機構[:：](.+?)電話[:：]", legal_name)
    put("approval_date", cover, r"\[受託或銷售機構\]審查通過之日期[:：]" + D, parse_date)
    put("print_date_review", cover, r"\(參考性審閱版\)內容，刊印日期[:：]" + D, parse_date)
    put("print_date_final", cover, r"\(最終版\)刊印日期(?:預定為|為)?[:：]" + D, parse_date)
    put("distributor_name_cover", cover, r"受託或銷售機構之名稱、電話及地址[:：](.+?)電話[:：]")
    put("distributor_phone_cover", cover, r"受託或銷售機構之名稱、電話及地址[:：].+?電話[:：]([+\d-]+)")
    put(
        "distributor_address_cover",
        cover,
        r"受託或銷售機構之名稱、電話及地址[:：].+?電話[:：][+\d-]+(.+?)(?:\(營業活動所在地\))?公會審查",
    )
    put("name_art1", article(1), r"商品名稱[:：](.+)$")
    put("currency_art5", article(5), r"計價幣別[:：](.+)$")
    put("denomination", article(6), r"每單位面額[:：].+?" + N + r"元", number)
    put("minimum_trade", article(7), r"最低交易金額[:：].+?" + N + r"元", number)
    put("issue_price_pct", article(10), r"發行價格[:：]" + N + "%", number)
    put("coupon_pa_pct", sub(11, 1), r"固定配息率=" + N + r"%×1/12", number)
    put("coupon_periods", sub(11, 1), r"配息期數=(\d+)", int)
    put("ko_pct", sub(11, 2), r"自動提前到期價格為期初股價×" + N + "%", number)
    put("strike_pct", sub(11, 3), r"執行價為期初股價×" + N + "%", number)
    put("ki_pct", sub(11, 3), r"觸及不保本價格為期初股價×" + N + "%", number)
    put(
        "ki_type",
        sub(11, 3),
        r"觸及不保本事件決定日為(最後評價日|每個預定交易日)",
        lambda x: {"最後評價日": "AM", "每個預定交易日": "D"}[x],
    )
    ko = TextIndex(sub(11, 2))
    for name, possibilities in [
        ("ko_observation", [("D", "起每個預定交易日"), ("P", "自動提前到期金額付款日計息期間")]),
        ("ko_memory", [(True, "所有連結標的皆已成為鎖定股票"), (False, "評價等於或大於其自動提前到期價格")]),
    ]:
        values = [v for v, t in possibilities if t in ko.text]
        fields[name] = (
            ParsedField.present(name, values[0], sub(11, 2))
            if len(values) == 1
            else ParsedField.invalid(name, sub(11, 2), "觀察或記憶式定義缺漏／矛盾")
        )
    if fields["ko_observation"].status == FieldStatus.INVALID and "自動提前到期決定日自動提前到期金額付款日" in ko.text:
        fields["ko_observation"] = ParsedField.present("ko_observation", "P", sub(11, 2))
    put("ko_start", sub(11, 2), r"自動提前到期決定日為自" + D + r"\(含\)起每個預定交易日", parse_date)
    redemption = TextIndex(sub(11, 3)).text
    no_ki = (
        "觸及不保本" not in redemption
        and "期末股價〔等於或大於〕執行價" in redemption
        and "期末股價〔小於〕執行價" in redemption
    )
    if no_ki:
        fields["ki_type"] = ParsedField.present("ki_type", "none", sub(11, 3))
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=[Evidence.of(x) for x in sub(11, 3)]
        )
    daily = fields["ko_observation"].ok and fields["ko_observation"].value == "D"
    fields["coupon_table"] = tables.coupon_table(sub(11, 1), daily)
    fields["ko_table"] = (
        tables.ko_table(sub(11, 2)) if not daily else ParsedField("ko_table", FieldStatus.NOT_APPLICABLE)
    )
    fields["price_table"] = tables.price_table("price_table", sub(12, 1))
    pt = fields["price_table"]
    fields["underlyings"] = (
        ParsedField.present("underlyings", [r["ticker"] for r in pt.value["rows"]], sub(12, 1))
        if pt.ok
        else ParsedField("underlyings", pt.status, evidence=pt.evidence, note=pt.note)
    )
    fields["underlying_prices"] = _standard_prices(pt)
    put("tenor_months", sub(15, 1), r"為(\d+)個月", int)
    for name, n, pattern in [
        ("issue_date", 2, r"發行日[:：]" + D),
        ("maturity_date", 3, r"目前表定為" + D),
        ("final_valuation_date", 5, r"最後評價日[:：]" + D),
        ("trade_date", 6, r"交易日[:：]" + D),
    ]:
        put(name, sub(15, n), pattern, parse_date)
    put("isin", article(27), r"ISIN[:：]([A-Z]{2}[A-Z0-9]{10})")
    ch2 = doc.chapter_lines(2)
    put("issuer_name_ch2", ch2, r"發行機構[:：]\(1\)事業名稱[:：](.+?\))", legal_name)
    for name, pattern in [
        ("distributor_name_ch2", r"事業名稱[:：](.+?)\(b\)"),
        ("distributor_address_ch2", r"營業所在地[:：](.+?)\(d\)"),
        ("chairman", r"負責人姓名[:：](.+?)(?:結算機構|$)"),
    ]:
        put(name, ch2, r"受託或銷售機構[:：].*?" + pattern)
    ch4 = doc.chapter_lines(4)
    for name, pattern, convert in [
        ("subscription_start", r"商品開始受理申購日[:：]" + D, parse_date),
        ("subscription_end", r"商品申購結束受理日[:：]" + D, parse_date),
        ("minimum_subscription", r"最低申購金額[:：].+?" + N + r"元", number),
        ("minimum_additional", r"最低加購金額[:：].+?" + N + r"元", number),
    ]:
        put(name, ch4, pattern, convert)
    for label in ["申購費用", "提前贖回費用", "分銷費用"]:
        anchors = [ln for ln in ch4 if ln.x0 < 100 and squash(ln.text).startswith(label)]
        if len(anchors) != 1:
            fields["fee_" + label] = ParsedField.missing("fee_" + label, "費用列錨點缺漏／重複")
            continue
        lab = anchors[0]
        next_rows = [
            ln.y0
            for ln in ch4
            if ln.page == lab.page
            and ln.x0 < 100
            and ln.y0 > lab.y0 + 1
            and any(
                squash(ln.text).startswith(x)
                for x in ["申購費用", "提前贖回費用", "管理費用", "分銷費用", "保費費用", "解約費用", "其他費用"]
            )
        ]
        end = min(next_rows) if next_rows else lab.y0 + 100
        region = [ln for ln in ch4 if ln.page == lab.page and 150 < ln.x0 < 230 and lab.y0 - 1 <= ln.y0 < end - 1]
        put("fee_" + label, region, r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)")
    scenarios = article(18)
    ti = TextIndex(scenarios)
    start = list(ti.finditer(r"配息期數=\d+，且假設"))
    end = list(ti.finditer(r"\*假設天期"))
    if len(start) == 1 and len(end) == 1:
        a = scenarios.index(ti.lines_for(start[0].start(), start[0].end())[-1]) + 1
        b = scenarios.index(ti.lines_for(end[0].start(), end[0].end())[0])
        fields["scenario_table"] = tables.price_table("scenario_table", scenarios[a:b])
    else:
        fields["scenario_table"] = ParsedField.missing("scenario_table", "情境價格表錨點缺漏／重複")
    return detect(lines), HsbcTermSheet(fields, TextIndex(doc.lines), doc, scenarios)
