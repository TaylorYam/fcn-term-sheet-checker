"""HSBC 中文產品說明書：章＋條＋子項＋標籤定位，保留原文證據。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import ScenarioParam, ScenarioPrice
from ..text import full_brackets, squash
from . import hsbc_tables as tables
from . import money, price_table, schedule
from .layout import Document, LayoutSpec, TextIndex, capture, parse_date

TEMPLATE_ID = "hsbc-zh-pd"
# 2：價格表改由 parsers/price_table.py 讀（Issue #288）；3：日期表標準欄位與共用排程推導（Issue #289）；
# 4：情境價格表、情境假設值與情境的執行價、觸及不保本價格交成共用情境規則的標準欄位（Issue #294）
PARSER_VERSION = "4"
# 條號自成一行（`9.`），也可能與標題同一行（`10. 發行價格：`，真實樣本第 10 條起是這種版面，Issue #185）
LAYOUT = LayoutSpec(
    r"第{zh}章\s*{name}", re.compile(r"^(\d{1,2})\.(?:\s+\S.*)?$"), 100, re.compile(r"^\((\d{1,2})\)\s*(.*)$"), 110
)
N = r"([\d,]+(?:\.\d+)?)"
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
CURRENCY_WORD = "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True))  # 中文幣別字，長的先比
# 第 12 條(1) 價格表：彭博代號、中文名稱、幣別、交易所、期初股價、執行價、自動提前到期價格、（觸及不保本價格），
# 期初股價以外各帶「(即期初股價的X%)」；第 18 條情境價格表同一張表、沒有中文名稱欄
PRICE_TABLE = price_table.TableSpec(
    where="第一章第 12 條價格表",
    initial="期初股價",
    columns=(
        price_table.Column("strike", "執行價"),
        price_table.Column("ko", "自動提前到期價格"),
        price_table.Column("ki", "觸及不保本價格", required=False),
    ),
    anchor="ticker",
    pct=r"\(即期初股價的([\d.]+)%\)",
    pct_label="(即期初股價的X%)",
    code="彭博代號",
    texts=(
        price_table.Column("label", "中文名稱", required=False),
        price_table.Column("currency", "幣別"),
        price_table.Column("exchange", "交易所"),
    ),
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="第一章第 18 條情境價格表")


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


def _price_table(name: str, lines: list[Line], spec: price_table.TableSpec) -> ParsedField:
    """價格表；每列的幣別（已知的三碼幣別）與交易所（英數字）讀不到時轉人工覆核（情境價格表逐列比對要用）。"""
    table = price_table.read(name, lines, spec)
    if not table.ok:
        return table
    for k, r in enumerate(table.value.rows, start=1):
        if not re.fullmatch(r"USD|JPY|CNH|HKD|EUR|AUD|GBP", r.texts["currency"]) or not re.fullmatch(
            r"[A-Z0-9]+", r.texts["exchange"]
        ):
            return ParsedField.invalid(name, list(r.lines), f"{spec.where}第 {k} 列讀不到幣別或交易所")
    return table


def _standard_prices(table: ParsedField) -> ParsedField:
    """標準欄位 `underlying_prices`：第 12 條價格表各列（代號、四種價格、中文名稱、該列原文）。"""
    names = [r.texts.get("label") or None for r in table.value.rows] if table.ok else None
    return price_table.price_rows(table, names=names)


SCENARIO_HEADING = r"情境分析([一二三四五六])\)"
SCENARIO_CCY = money.CURRENCY  # 金額前的幣別字（任一已知寫法）：只讀數字，幣別由共用規則 field.currency 逐處核對
# 情境配息與本金給付算式：面額 × 月配息率 %（× 期數或 × 分子/分母）= 金額
SCENARIO_FORMULA = SCENARIO_CCY + N + r"[×xX]" + N + r"%(?:[×xX](\d+)(?:/(\d+))?)?=" + SCENARIO_CCY + N
SCENARIO_ANNUALIZED = r"平均年化報酬率\(以簡單平均年化報酬率之方式計算\)為" + N + "%"
SCENARIO_REFERENCES = (("執行價", "strike"), ("觸及不保本價格", "ki"))  # 較差情境寫出的價格（標籤 → 價格表欄）


def scenario_number(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


@dataclass(frozen=True)
class ScenarioText:
    """第 18 條情境文字：情境假設段與情境一～n（3 個或有 KI 時 4 個）。"""

    assumptions: ScenarioIndex
    segments: list[ScenarioIndex]


def scenario_text(ti: ScenarioIndex, ki_type: ParsedField) -> ScenarioText | None:
    """依情境標題切段；情境數量或順序不符已知範本（無 KI 3 個、有 KI 4 個）時為 None。"""
    headings = list(ti.finditer(SCENARIO_HEADING))
    expected = 3 if ki_type.ok and ki_type.value == "none" else 4
    if len(headings) != expected or [m[1] for m in headings] != list("一二三四")[:expected]:
        return None
    ends = [h.start() for h in headings[1:]] + [len(ti.text)]
    return ScenarioText(
        ScenarioIndex(ti.lines_for(0, headings[0].start())),
        [ScenarioIndex(ti.lines_for(h.start(), end)) for h, end in zip(headings, ends, strict=True)],
    )


def scenario_worst(segment: ScenarioIndex) -> bool:
    """較差情境（有交割股數、零股或實物給付）：完整贖回與損益不重算。"""
    return any(w in segment.text for w in ("交割股數", "零股數", "實物給付"))


def scenario_formulas(segment: ScenarioIndex) -> list[tuple[re.Match, bool]]:
    """情境裡每一個「面額 × % … = 金額」算式與它是不是本金給付（前面最近的是「提前到期給付金額／到期贖回金額」而不是
    「配息金額」）。"""
    out = []
    for m in segment.finditer(SCENARIO_FORMULA):
        before = segment.text[max(0, m.start() - 120) : m.start()]
        principal = max(before.rfind("提前到期給付金額"), before.rfind("到期贖回金額"))
        out.append((m, principal >= 0 and principal > before.rfind("配息金額")))
    return out


def _scenario_standard(fields: dict[str, ParsedField], ti: ScenarioIndex) -> None:
    """交給共用情境規則的標準欄位（Issue #294）：情境價格表；情境假設的天期、每單位面額、固定配息率、配息期數、發行價格，
    各情境的天期、配息次數、配息算式的面額與配息率、平均年化報酬率（`scenario_params`）；情境寫出的執行價、觸及不保本
    價格（`scenario_prices`，依「（中文名稱）」對到價格表那一列，單一標的為第 1 列）。配息金額、總額與損益仍由 HSBC 規則重算。"""
    fields["main_price_table"] = price_table.at("main_price_table", fields["price_table"], PRICE_TABLE)
    fields["scenario_reprint_table"] = price_table.at(
        "scenario_reprint_table", fields["scenario_table"], SCENARIO_TABLE
    )
    annual = fields["coupon_pa_pct"]  # 說明書只寫年利率：固定配息率 = 年利率 ÷ 12 四捨五入 4 位
    fields["monthly_coupon_pct"] = (
        ParsedField(
            "monthly_coupon_pct",
            FieldStatus.PRESENT,
            (annual.value / 12).quantize(Decimal("0.0001"), ROUND_HALF_UP),
            list(annual.evidence),
        )
        if annual.ok
        else replace(annual, name="monthly_coupon_pct")
    )
    text = scenario_text(ti, fields["ki_type"])
    if text is None:
        note = "第 18 條情境數量或順序不符已知範本"
        fields["scenario_params"] = ParsedField.missing("scenario_params", note)
        fields["scenario_prices"] = ParsedField.missing("scenario_prices", note)
        return
    params: list[ScenarioParam] = []

    def mention(
        index: ScenarioIndex, pattern: str, code: str, where: str, convert=scenario_number, required=True
    ) -> None:
        found = list(index.finditer(pattern))
        if required and not found:
            missing = ParsedField.missing(code, f"{where}的句子缺漏、損壞或寫法未知")
            params.append(
                ScenarioParam(code, where, replace(missing, evidence=[Evidence.of(x) for x in index.lines[:2]]))
            )
        for m in found:
            pf = ParsedField.present(code, convert(m[1]), index.lines_for(m.start(), m.end()))
            params.append(ScenarioParam(code, where, pf))

    a = text.assumptions
    mention(a, r"商品天期為(\d+)個月期", "scenario_tenor", "情境假設", int)
    mention(a, r"每單位面額為" + SCENARIO_CCY + N + "元", "scenario_denomination", "情境假設")
    mention(a, r"固定配息率為" + N + "%", "scenario_monthly_coupon", "情境假設固定")
    mention(a, r"配息期數=(\d+)", "scenario_coupon_periods", "情境假設", int)
    mention(a, r"發行價格為" + N + "%", "scenario_issue_price", "情境假設")
    table = fields["price_table"]
    rows = table.value.rows if table.ok else ()
    prices: list[ScenarioPrice] = []
    for i, segment in enumerate(text.segments):
        where = f"情境 {i + 1} "
        mention(segment, r"(?:存續期間|本商品於)(\d+)個月", "scenario_tenor", where, int, required=False)
        mention(segment, r"於(\d+)個月存續期間", "scenario_tenor", where, int, required=i > 0)
        mention(segment, r"共(\d+)次配息", "scenario_coupon_periods", where, int, required=i > 0)
        for m, principal in scenario_formulas(segment):
            if principal:
                continue
            lines = segment.lines_for(m.start(), m.end())
            params.append(
                ScenarioParam(
                    "scenario_denomination",
                    f"{where}配息計算的",
                    ParsedField.present("scenario_denomination", scenario_number(m[1]), lines),
                )
            )
            params.append(
                ScenarioParam(
                    "scenario_monthly_coupon",
                    where,
                    ParsedField.present("scenario_monthly_coupon", scenario_number(m[2]), lines),
                )
            )
        if i > 0 and not scenario_worst(segment):  # 較差情境（實物交割）的年化報酬率不核對（未涵蓋清單）
            mention(segment, SCENARIO_ANNUALIZED, "scenario_annualized_return", f"{where}平均")
        # 較差情境寫出的價格要對到同一檔，不是任一檔數值相同就算
        named = [
            n for n, r in enumerate(rows, start=1) if r.texts.get("label") and f"（{r.texts['label']}）" in segment.text
        ]
        row = 1 if len(rows) == 1 else named[0] if len(named) == 1 else None
        for label, key in SCENARIO_REFERENCES:
            refs = list(segment.finditer(re.escape(label) + SCENARIO_CCY + N))
            required = i >= 2 and (key == "strike" or len(text.segments) == 4)
            written = len(re.findall(re.escape(label) + SCENARIO_CCY, segment.text))
            if (required and not refs) or written > len(refs):
                missing = ParsedField.missing(f"scenario_{key}", f"{where}{label}缺漏、損壞或寫法未知")
                prices.append(
                    ScenarioPrice(
                        where, row, key, replace(missing, evidence=[Evidence.of(x) for x in segment.lines[:2]])
                    )
                )
            for m in refs:
                pf = ParsedField.present(
                    f"scenario_{key}", scenario_number(m[1]), segment.lines_for(m.start(), m.end())
                )
                prices.append(ScenarioPrice(where, row, key, pf))
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    fields["scenario_prices"] = standard_fields.occurrences("scenario_prices", prices)


def _scenario_currencies(scenarios: Sequence[Line]) -> list[standard_fields.Occurrence]:
    """`currency_others` 的第 18 條出處（Issue #170）：情境假設一處、每個情境一處（該情境裡每一處「幣別 金額」；
    執行價、觸及不保本價格與含「股」的實物交割算式跟著標的走，不算）。找不到情境標題時沒有出處（情境規則另轉人工覆核）。

    第 12 條價格表各列的幣別格跟著標的走（2026-10-10 使用者決定，取代 Issue #167 的做法），不是承作幣別的出處；
    情境價格表與正式價格表逐列（含幣別格）一致仍由 `doc.scenario_table` 核對。"""
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
    # 只讀幣別字：人民幣商品後面接「人民幣計價債券之說明事項：…」段落（Issue #180）；讀不到認得的幣別字時為缺漏
    put("currency_art5", article(5), rf"計價幣別[:：]({CURRENCY_WORD})")
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
    fields["price_table"] = _price_table("price_table", sub(12, 1), PRICE_TABLE)
    fields["underlyings"] = price_table.underlyings(fields["price_table"])
    fields["underlying_prices"] = _standard_prices(fields["price_table"])
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
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")
    fields.update(standard_fields.absent_all(standard_fields.DISTRIBUTOR_OTHERS, "受託機構的其他出處"))
    fields["initial_pricing"] = standard_fields.absent("initial_pricing", "期初定價（收盤價／VWAP）字樣")
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
            *_scenario_currencies(scenarios),
        ],
    )
    ti = TextIndex(scenarios)
    start = list(ti.finditer(r"配息期數=\d+，且假設"))
    end = list(ti.finditer(r"\*假設天期"))
    if len(start) == 1 and len(end) == 1:
        a = scenarios.index(ti.lines_for(start[0].start(), start[0].end())[-1]) + 1
        b = scenarios.index(ti.lines_for(end[0].start(), end[0].end())[0])
        fields["scenario_table"] = _price_table("scenario_table", scenarios[a:b], SCENARIO_TABLE)
    else:
        fields["scenario_table"] = ParsedField.missing("scenario_table", "情境價格表錨點缺漏／重複")
    _scenario_standard(fields, ScenarioIndex(scenarios))
    f = fields.get
    fields["first_callable_period"] = first_callable(lambda k: f(k, ParsedField.missing(k)))
    fields["schedule_table"] = schedule_table(lambda k: f(k, ParsedField.missing(k)), fields["first_callable_period"])
    fields["autocall_schedule"] = schedule.autocall_schedule(
        fields["ko_observation"], fields["first_callable_period"], fields["schedule_table"]
    )["autocall_schedule"]
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


SCHEDULE_WHERE = "第一章第 11 條日期表"


def schedule_table(f: Callable[[str], ParsedField], first: ParsedField) -> ParsedField:
    """標準欄位 `schedule_table`：配息表每期一列。

    期間每日觀察（D）：期始日、期末日、配息日，觀察日 = 期末日；第 1～k 期期始日為「-」，之後為前一期期末日後的下一個
    一籃子預定交易日（讀不到 k 時無法確認）。定期觀察（P）：配息日，觀察日為定期 KO 表付款日對到的那一期的決定日
    （對不到唯一期別的列不放，由 HSBC 規則 `schedule.autocall_dates` 檢查）；沒有期始日欄。
    """
    name = "schedule_table"
    obs, coupons = f("ko_observation"), f("coupon_table")
    bad = next((p for p in (obs, coupons) if not p.ok), None)
    if bad is not None:
        return ParsedField(name, bad.status, evidence=bad.evidence, note=bad.note)
    if obs.value == "D":
        rows = tuple(
            standard_fields.DateRow(r["period"], r["start"], r["end"], r["end"], r["payment"]) for r in coupons.value
        )
        start = standard_fields.PeriodStart("next_trading_day", None, first.value if first.ok else None)
        table = standard_fields.DateTable(SCHEDULE_WHERE, rows, start)
        return ParsedField(name, FieldStatus.PRESENT, table, list(coupons.evidence))
    ko = f("ko_table")
    if not ko.ok:
        return ParsedField(name, ko.status, evidence=ko.evidence, note=ko.note)
    decisions = {}
    for row in ko.value:
        periods = [c["period"] for c in coupons.value if c["payment"] == row["payment"]]
        if len(periods) == 1:
            decisions[periods[0]] = row["decision"]
    rows = tuple(
        standard_fields.DateRow(r["period"], observation=decisions.get(r["period"]), payment=r["payment"])
        for r in coupons.value
    )
    table = standard_fields.DateTable(SCHEDULE_WHERE, rows)
    return ParsedField(name, FieldStatus.PRESENT, table, list(coupons.evidence) + list(ko.evidence))
