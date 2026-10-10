"""HSBC 中文產品說明書：章＋條＋子項＋標籤定位，保留原文證據。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import full_brackets, squash
from . import hsbc_tables as tables
from . import money
from .layout import Document, LayoutSpec, TextIndex, capture, parse_date

TEMPLATE_ID = "hsbc-zh-pd"
PARSER_VERSION = "1"
LAYOUT = LayoutSpec(r"第{zh}章\s*{name}", re.compile(r"^(\d{1,2})\.$"), 100, re.compile(r"^\((\d{1,2})\)\s*(.*)$"), 110)
N = r"([\d,]+(?:\.\d+)?)"
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"


def document(lines: Sequence[Line]) -> Document:
    clean = [x for x in lines if x.text != "PUBLIC" and not re.fullmatch(r"-?\s*第\s*\d+\s*頁，共\s*\d+\s*頁", x.text)]
    return Document(clean, LAYOUT)


def product_code(lines: Sequence[Line]) -> ParsedField:
    cover = TextIndex(document(lines).before_chapter1())
    return capture("product_code", cover, r"商品代號/商品中文名稱[:：](\d{12})/", whole_match=True)


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


class ScenarioIndex(TextIndex):
    """保留相鄰數字行的邊界，避免金額尾數與下一項期數相黏。"""

    def __init__(self, lines):
        self.lines = list(lines)
        self._starts = []
        text = ""
        for line in self.lines:
            part = squash(line.text)
            if text and part and text[-1].isdigit() and part[0].isdigit():
                text += "|"
            self._starts.append(len(text))
            text += part
        self.text = text


@dataclass
class HsbcTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    scenarios: list[Line]
    scenario_index: ScenarioIndex  # 第 18 條情境文字（保留相鄰數字行的邊界）

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def _standard_prices(table: ParsedField) -> ParsedField:
    """標準欄位 `underlying_prices`：第 12 條價格表各列（代號、四種價格、該列原文）。"""
    name = "underlying_prices"
    if not table.ok:
        return ParsedField(name, table.status, None, list(table.evidence), list(table.candidates), table.note)
    value = tuple(
        standard_fields.PriceRow(r["ticker"], dict(r["prices"]), tuple(Evidence.of(ln) for ln in lns), r.get("label"))
        for r, lns in zip(table.value["rows"], table.value["row_lines"], strict=True)
    )
    return ParsedField(name, FieldStatus.PRESENT, value, list(table.evidence))


def _table_currencies(table: ParsedField) -> list[standard_fields.Occurrence]:
    """`currency_others` 的價格表出處：第 12 條價格表各列的幣別格是承作幣別（不是標的自己的交易幣別，Issue #167）。

    情境價格表不另列：`doc.scenario_table` 已逐列（含幣別）和正式價格表比。價格表讀不到時這些出處各自缺漏。
    """
    occ = standard_fields.Occurrence
    if not table.ok:
        pf = ParsedField(
            "price_table_currency", table.status, None, list(table.evidence), list(table.candidates), table.note
        )
        return [occ("price_table_currency", "價格表幣別", "第 12 條價格表幣別", pf)]
    items = []
    for i, (row, lns) in enumerate(zip(table.value["rows"], table.value["row_lines"], strict=True), 1):
        field = f"price_table_currency_{i}"
        where = f"第 12 條價格表 {row['ticker']} 幣別"
        items.append(occ(field, f"{row['ticker']} 幣別", where, ParsedField.present(field, row["currency"], lns)))
    return items


SCENARIO_HEADING = r"情境分析([一二三四五六])\)"


def _scenario_currencies(scenarios: Sequence[Line]) -> list[standard_fields.Occurrence]:
    """`currency_others` 的第 18 條出處（Issue #170）：情境假設一處、每個情境一處（該情境裡每一處「幣別 金額」；
    執行價、觸及不保本價格與含「股」的實物交割算式跟著標的走，不算）。找不到情境標題時沒有出處（情境規則另轉人工覆核）。"""
    ti = ScenarioIndex(scenarios)
    headings = list(ti.finditer(SCENARIO_HEADING))
    if not headings:
        return []
    segments = [("scenario_assumption_currency", "情境假設金額幣別", "第 18 條情境假設", 0, headings[0].start())]
    for i, h in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(ti.text)
        segments.append(
            (f"scenario_{i + 1}_currency", f"情境 {i + 1} 金額幣別", f"第 18 條情境分析{h[1]})", h.start(), end)
        )
    out = []
    for field, name, where, start, end in segments:
        found = money.amounts(ti, money.CURRENCY_THEN_AMOUNT, currency_group=1, start=start, end=end)
        out.append(money.scenario_occurrence(field, name, where, found, ti.lines_for(start, start + 1)))
    return out


def read(lines: Sequence[Line]) -> HsbcTermSheet:
    """讀出標準欄位與 HSBC 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    art = doc.articles(1)
    cover = doc.before_chapter1()
    fields = {}

    def put(name, lns, pattern, convert=lambda x: x):
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    def article(n):
        return doc.span_lines(art.get(n))

    def sub(n, k):
        return doc.span_lines(doc.subitems(art.get(n)).get(k))

    def number(x):
        return Decimal(x.replace(",", ""))

    def legal_name(x):
        s = full_brackets(squash(x))
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
    ccy, amount = money.CURRENCY, money.NUMBER
    put("denomination_currency", article(6), rf"每單位面額[:：]({ccy}){amount}元")
    put("minimum_trade_currency", article(7), rf"最低交易金額[:：]({ccy}){amount}元")
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
    fields["issuer_name_ch1"] = standard_fields.absent("issuer_name_ch1", "第一章只寫中文的發行機構名稱")
    fields["issue_price_others"] = standard_fields.absent("issue_price_others", "發行價格的其他出處")
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
        ("minimum_subscription_currency", rf"最低申購金額[:：]({ccy}){amount}元", str),
        ("minimum_additional_currency", rf"最低加購金額[:：]({ccy}){amount}元", str),
    ]:
        put(name, ch4, pattern, convert)
    for label in ["申購費用", "提前贖回費用", "分銷費用"]:
        fee = standard_fields.fee_field(label)
        anchors = [ln for ln in ch4 if ln.x0 < 100 and squash(ln.text).startswith(label)]
        if len(anchors) != 1:
            fields[fee] = ParsedField.missing(fee, "費用列錨點缺漏／重複")
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
        put(fee, region, r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)")
    occ = standard_fields.Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_trade", "最低交易金額", "第一章第 7 條最低交易金額", fields["minimum_trade"]),
            occ("minimum_subscription", "最低申購金額", "第四章最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "第四章最低加購金額", fields["minimum_additional"]),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ("subscription_start", "開始受理申購日", "第四章商品開始受理申購日", fields["subscription_start"]),
            occ("subscription_end", "申購結束受理日", "第四章商品申購結束受理日", fields["subscription_end"]),
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates",
        [
            occ(
                "print_date_review",
                "刊印日期（參考性審閱版）",
                "封面刊印日期（參考性審閱版）",
                fields["print_date_review"],
            ),
            occ("print_date_final", "刊印日期（最終版）", "封面刊印日期（最終版）", fields["print_date_final"]),
        ],
    )
    scenarios = article(18)
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others",
        [
            occ("denomination_currency", "每單位面額幣別", "第一章第 6 條每單位面額", fields["denomination_currency"]),
            occ(
                "minimum_trade_currency",
                "最低交易金額幣別",
                "第一章第 7 條最低交易金額",
                fields["minimum_trade_currency"],
            ),
            occ(
                "minimum_subscription_currency",
                "最低申購金額幣別",
                "第四章最低申購金額",
                fields["minimum_subscription_currency"],
            ),
            occ(
                "minimum_additional_currency",
                "最低加購金額幣別",
                "第四章最低加購金額",
                fields["minimum_additional_currency"],
            ),
            *_table_currencies(pt),
            *_scenario_currencies(scenarios),
        ],
    )
    ti = TextIndex(scenarios)
    start = list(ti.finditer(r"配息期數=\d+，且假設"))
    end = list(ti.finditer(r"\*假設天期"))
    if len(start) == 1 and len(end) == 1:
        a = scenarios.index(ti.lines_for(start[0].start(), start[0].end())[-1]) + 1
        b = scenarios.index(ti.lines_for(end[0].start(), end[0].end())[0])
        fields["scenario_table"] = tables.price_table("scenario_table", scenarios[a:b])
    else:
        fields["scenario_table"] = ParsedField.missing("scenario_table", "情境價格表錨點缺漏／重複")
    f = fields.get
    fields["first_callable_period"] = first_callable(lambda k: f(k, ParsedField.missing(k)))
    fields["autocall_schedule"] = autocall_schedule(
        lambda k: f(k, ParsedField.missing(k)), fields["first_callable_period"]
    )
    return HsbcTermSheet(fields, TextIndex(doc.lines), doc, scenarios, ScenarioIndex(scenarios))


def first_callable(f: Callable[[str], ParsedField]) -> ParsedField:
    coupons, obs = f("coupon_table"), f("ko_observation")
    deps = [coupons, obs]
    if obs.ok and obs.value == "D":
        deps.append(f("ko_start"))
    else:
        deps.append(f("ko_table"))
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return ParsedField("first_callable_period", bad.status, evidence=bad.evidence, note=bad.note)
    if obs.value == "D":
        periods = [r["period"] for r in coupons.value if r["end"] == f("ko_start").value]
    else:
        first = f("ko_table").value[0]
        periods = [r["period"] for r in coupons.value if r["payment"] == first["payment"]]
    ev = [e for p in deps for e in p.evidence]
    if len(periods) != 1:
        return ParsedField(
            "first_callable_period",
            FieldStatus.AMBIGUOUS,
            evidence=ev,
            candidates=periods,
            note="首個KO日期無法唯一對應配息期別",
        )
    return ParsedField("first_callable_period", FieldStatus.PRESENT, periods[0], ev)


def autocall_schedule(f: Callable[[str], ParsedField], first: ParsedField) -> ParsedField:
    """標準欄位 `autocall_schedule`：期別與比價日；比價日填法由共用回填規則決定。"""
    obs, coupons = f("ko_observation"), f("coupon_table")
    bad = next((p for p in [first, obs, coupons] if not p.ok), None)
    if bad is not None:
        return ParsedField("autocall_schedule", bad.status, evidence=bad.evidence, note=bad.note)
    if obs.value == "D":
        dates = {row["period"]: row["end"] for row in coupons.value if row["period"] >= first.value}
    else:
        ko = f("ko_table")
        if not ko.ok:
            return ParsedField("autocall_schedule", ko.status, evidence=ko.evidence, note=ko.note)
        dates = {}
        for row in ko.value:
            periods = [c["period"] for c in coupons.value if c["payment"] == row["payment"]]
            if len(periods) != 1:
                return ParsedField.invalid("autocall_schedule", [], "KO 付款日無法唯一對應期別")
            dates[periods[0]] = row["decision"]
    return ParsedField(
        "autocall_schedule",
        FieldStatus.PRESENT,
        standard_fields.AutocallSchedule(obs.value, first.value, len(coupons.value), dates),
        [e for p in [first, obs, coupons] for e in p.evidence],
    )
