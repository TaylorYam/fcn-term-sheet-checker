"""MS 中文產品說明書（docs/templates/ms-zh-product-description.md）：章＋條號＋子項＋標籤定位，保留原文證據。

只支援新版範本（2025-10 起；第一章第 1 項「商品中文名稱」、英文「International Plc」）；舊版範本辨識失敗，轉人工覆核。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import full_brackets, squash
from . import ms_scenario, ms_tables, ms_wording
from .layout import Document, LayoutSpec, TextIndex, capture, join_text, parse_date

TEMPLATE_ID = "ms-zh-pd"
PARSER_VERSION = "1"
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "定義及其他條款"),
    6: ("六", "特別記載事項"),
}
# 章名用頓號；條號「N. 標題：」與內文同一行（第一章 x≈54、第四章 x≈36）；子項「(n)」
LAYOUT = LayoutSpec(
    r"第{zh}章、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    60,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    80,
    CHAPTER_NAMES,
)
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
NUM = r"([\d,]+(?:\.\d+)?)"
FEES = ("申購費用", "提前贖回費用", "分銷費用")


def document(lines: Sequence[Line]) -> Document:
    clean = [x for x in lines if not re.fullmatch(r"第\s*\d+\s*頁，共\s*\d+\s*頁", x.text)]
    return Document(clean, LAYOUT)


# ---------------------------------------------------------------- 封面項目


@dataclass(frozen=True)
class CoverItem:
    label: str  # 去空白的標籤（例：商品代號）
    value: str  # 冒號後的值，跨行合併
    lines: tuple[Line, ...]


_ITEM = re.compile(r"^(\d{1,2})\.\s*([^：:]+?)\s*[：:]\s*(.*)$")


def cover_items(lines: Sequence[Line]) -> dict[int, CoverItem]:
    """封面編號 1.–16. 的「標籤：值」：項號在左邊界（x < 45）、依序出現；續行接到上一項，刊印日期那行不算。"""
    parts: dict[int, tuple[str, list[Line], list[Line]]] = {}
    current, expect = None, 1
    for ln in lines:
        m = _ITEM.match(ln.text)
        if m and ln.x0 < 45 and int(m[1]) == expect:
            current = expect
            parts[current] = (squash(m[2]), [replace(ln, text=m[3])] if m[3] else [], [ln])
            expect += 1
        elif current is not None and ln.x0 >= 45 and not squash(ln.text).startswith("中文產品說明書刊印日期"):
            parts[current][1].append(ln)
            parts[current][2].append(ln)
    return {n: CoverItem(label, join_text(val).strip(), tuple(lns)) for n, (label, val, lns) in parts.items()}


def _item(items: dict[int, CoverItem], n: int, name: str, label: str, pattern: str | None = None) -> ParsedField:
    it = items.get(n)
    if it is None or it.label != label:
        return ParsedField.missing(name, f"找不到封面第 {n} 項「{label}」")
    value = it.value
    if pattern is not None:
        m = re.fullmatch(pattern, squash(value))
        if not m:
            return ParsedField.invalid(name, list(it.lines), f"封面第 {n} 項「{label}」的值格式未知")
        value = m[1]
    return ParsedField.present(name, value, list(it.lines))


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是新版 MS 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    zh, en, issuer = (squash(items[n].value) if n in items else "" for n in (4, 5, 10))
    arts = doc.articles(1)
    art1 = squash(doc.lines[arts[1].start].text) if 1 in arts else ""
    failed = []
    for test, message in (
        ("中文產品說明書" in TextIndex(cover).text, "封面缺少中文產品說明書"),
        (
            items.get(10) is not None and items[10].label == "發行機構" and "英商摩根士丹利國際股份有限公司" in issuer,
            "封面第 10 項發行機構不是英商摩根士丹利國際股份有限公司",
        ),
        (
            zh.startswith("英商摩根士丹利發行") and "固定配息" in zh and "結構型商品" in zh,
            "商品中文名稱不是 MS 固定配息結構型商品",
        ),
        (
            en.startswith("MorganStanley&Co.InternationalPlcissuanceof") and "FixedCouponNotes" in en,
            "商品英文名稱不是新版 MS 寫法（International Plc issuance of … Fixed Coupon Notes）",
        ),
        (
            art1.startswith("1.商品中文名稱") and set(range(1, 30)) <= set(arts),
            "第一章第 1 項不是「商品中文名稱」或條號 1–29 不連續（舊版範本不支援）",
        ),
        (all(n in doc.chapters for n in (1, 2, 3, 4)), "章名不是「第X章、」寫法或章節不完整"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


def product_code(lines: Sequence[Line]) -> ParsedField:
    return _item(cover_items(document(lines).before_chapter1()), 1, "product_code", "商品代號", r"(\d{12})")


# ---------------------------------------------------------------- 讀出


@dataclass(frozen=True)
class Mention:
    """月配息率或年化報酬率的一個出處；值讀不到時為 None。"""

    where: str
    value: Decimal | None
    evidence: tuple[Evidence, ...]


@dataclass
class MsTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    scenarios: ms_scenario.ScenarioSection  # 第 18 項
    coupon_mentions: list[Mention]  # 月配息率的每個出處（第 15 項、第 18 項假設與各情境算式）
    annualized_mentions: list[Mention]  # 獲利情境的年化報酬率

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


def _ki_type(lines: list[Line]) -> ParsedField:
    """第 16 項「觸及下限事件：」定義句（範本規格 §4.4）；沒有這句且到期贖回只有 ≥／< 執行價兩種時為無 KI。"""
    note = "找不到觸及下限事件定義，也不是無 KI 的到期贖回寫法"
    return ms_wording.ki_type(TextIndex(lines), invalid_note=note)


def _art17(lines: list[Line]) -> tuple[ParsedField, ParsedField]:
    """第 17 項：記憶式（`ko_memory`：要有記憶事件定義句與觀察日）與觀察寫法（`ko_observation_art17`：表型 D／P、
    第一個可提前出場期 k、D 型觀察起訖日）。寫法不在範本規格內一律不合法。"""
    ti = TextIndex(lines)
    note = "第 17 項記憶事件寫法缺漏或不在範本規格內"
    mem = ms_wording.ko_memory(ti, definition_required=True, invalid_note=note)
    found = []
    for m, kind in ms_wording.ko_observations(ti):
        dates = (parse_date(m[2]), parse_date(m[3])) if kind == "D" else (None, None)
        value = {"type": kind, "k": int(m[1]), "start": dates[0], "end": dates[1]}
        found.append((value, ti.lines_for(m.start(), m.end())))
    name = "ko_observation_art17"
    if len(found) != 1:
        obs = ParsedField.invalid(name, lines, "第 17 項觀察日寫法缺漏、重複或不在範本規格內")
    else:
        obs = ParsedField.present(name, found[0][0], found[0][1])
    return mem, obs


def read(lines: Sequence[Line]) -> MsTermSheet:
    """讀出標準欄位與 MS 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    items = cover_items(doc.before_chapter1())
    fields: dict[str, ParsedField] = {}

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    def sub(n: int, k: int) -> list[Line]:
        return doc.span_lines(doc.subitems(arts.get(n)).get(k))

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns, unify_brackets=True), pattern, convert, whole_match=True)

    # 封面
    fields["product_code"] = _item(items, 1, "product_code", "商品代號", r"(\d{12})")
    fields["trustee_product_code"] = _item(items, 2, "trustee_product_code", "受託機構商品代號", r"(\d{12}|)")
    fields["isin"] = _item(items, 3, "isin", "國際證券編碼ISIN", r"([A-Z]{2}[A-Z0-9]{10})")
    fields["name_zh"] = _item(items, 4, "name_zh", "商品中文名稱")
    fields["name_en"] = _item(items, 5, "name_en", "商品英文名稱")
    fields["product_type"] = _item(items, 6, "product_type", "商品種類", r"(.+)")
    fields["currency_zh"] = _item(items, 9, "currency_zh", "商品計價幣別", r"(.+?)[(（][A-Z]{3}[)）]")
    fields["issuer_name_cover"] = _item(items, 10, "issuer_name_cover", "發行機構")
    approval = _item(items, 15, "approval_date", "受託機構審查通過之日期及文號")
    approved = parse_date(squash(approval.value)) if approval.ok else None
    fields["approval_date"] = (
        ParsedField("approval_date", FieldStatus.INVALID, evidence=approval.evidence, note="審查通過日期不是日期")
        if approval.ok and approved is None
        else _derived("approval_date", approval, approved)
    )
    distributor = _item(items, 14, "distributor", "受託機構之名稱、電話及地址")
    parts = (
        re.fullmatch(
            r"(.+?)\s*[,，]\s*電話\s*[:：]\s*(.+?)\s*[,，]\s*地址\s*[:：]\s*(.+?)\s*(?:[(（]營業活動所在地[)）])?",
            distributor.value,
        )
        if distributor.ok
        else None
    )
    for k, key in enumerate(("distributor_name_cover", "distributor_phone_cover", "distributor_address_cover"), 1):
        if distributor.ok and parts is None:
            note = "封面第 14 項無法切出名稱、電話、地址"
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=list(distributor.evidence), note=note)
        else:
            fields[key] = _derived(key, distributor, parts[k].strip() if parts else None)
    put("print_date", doc.before_chapter1(), r"中文產品說明書刊印日期：" + D, parse_date)

    # 第一章
    art1 = article(1)
    m1 = re.match(r"^1\.\s*商品中文名稱[：:]\s*(.+)$", join_text(art1), re.S) if art1 else None
    fields["name_art1"] = (
        ParsedField.present("name_art1", m1[1].strip(), art1) if m1 else ParsedField.missing("name_art1")
    )
    put("issuer_name_ch1", article(3), r"本商品發行機構為(.+?)，")
    put("currency_art5", article(5), r"計價幣別：(.+?)（[A-Z]{3}）")
    put("denomination", article(6), r"每單位商品面額：\D*?" + NUM + r"元", _number)
    put("issue_price_pct", article(7), r"發行價格：商品面額之(\d+(?:\.\d+)?)%", Decimal)
    fields["underlyings_art11"] = ms_tables.underlying_table(article(11))
    fields["underlying_names_art11"] = ms_tables.underlying_names(article(11))
    put("tenor_months", sub(14, 1), r"商品年期：(\d+)個月期", int)
    put("trade_date", sub(14, 2), r"交易日：" + D, parse_date)
    put("issue_date", sub(14, 3), r"發行日：" + D, parse_date)
    put("maturity_date", sub(14, 4), r"到期日：" + D, parse_date)
    put("final_valuation_date", sub(14, 5), r"期末定價日\*?：" + D, parse_date)
    fields["date_table"] = ms_tables.date_table(sub(14, 6))
    art15 = article(15)
    put("monthly_coupon_pct", art15, r"固定配息率（(\d+(?:\.\d+)?)%）", Decimal)
    put("coupon_formula_pct", art15, r"\{(\d+(?:\.\d+)?)%×n（j）/N（j）\}", Decimal)
    put("coupon_range", art15, r"(j係為\d+至\d+的數字)", lambda x: tuple(map(int, re.findall(r"\d+", x))))
    art16 = article(16)
    fields["ki_type"] = _ki_type(art16)
    region = ms_tables.price_region(art16, ("（1）最低保證贖回率",))
    fields["price_table"] = (
        ms_tables.price_table("price_table", region[1], region[0])
        if region
        else ParsedField.missing("price_table", "找不到第 16 項價格表（標題「…：依下表所示」到「(1) 最低保證贖回率」）")
    )
    fields["ko_memory"], fields["ko_observation_art17"] = _art17(article(17))
    zh = fields["name_zh"]
    fields["name_memory"] = _derived(
        "name_memory", zh, zh.ok and ms_wording.MEMORY_NAME in full_brackets(squash(zh.value))
    )
    _schedule(fields)
    _prices(fields)

    # 第二章
    ch2 = doc.chapter_lines(2)
    put("issuer_name_ch2", ch2, r"1\.發行機構（1）事業名稱：(.+?)，係依")
    trustee = _trustee_block(ch2)
    put("distributor_name_ch2", trustee, r"◎事業名稱：(.+?)◎")
    put("distributor_address_ch2", trustee, r"◎營業所在地：(.+?)◎")
    put("chairman", trustee, r"◎負責人姓名：(.+)$")

    # 第四章：條號縮排不固定（第 1 項有時與子項同欄），以各項的標籤定位
    ch4 = doc.chapter_lines(4)
    put("redemption_start", ch4, r"（1）開始受理贖回日期：" + D, parse_date)
    for label in FEES:
        fields[standard_fields.fee_field(label)] = ms_tables.fee(label, standard_fields.fee_field(label), ch4)
    put("minimum_subscription", ch4, r"最低申購金額為[^，。]*，即\D{0,4}?" + NUM + r"元", _number)
    put("minimum_additional", ch4, r"並以\D{0,4}?" + NUM + r"元(?:（[^）]*）)?為最低加購單位", _number)
    put("minimum_redemption", ch4, r"最低贖回金額為[^，。]*，即\D{0,4}?" + NUM + r"元", _number)
    put("redemption_increment", ch4, r"並以\D{0,4}?" + NUM + r"元(?:（[^）]*）)?為累加贖回單位", _number)
    put("issue_price_ch4", ch4, r"發行價格（商品面額的(\d+(?:\.\d+)?)%）", Decimal)

    occ = standard_fields.Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_subscription", "最低申購金額", "第四章第 4 項最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購單位", "第四章第 4 項最低加購單位", fields["minimum_additional"]),
            occ("minimum_redemption", "最低贖回金額", "第四章第 8 項最低贖回金額", fields["minimum_redemption"]),
            occ("redemption_increment", "累加贖回單位", "第四章第 8 項累加贖回單位", fields["redemption_increment"]),
        ],
    )
    fields["currency_others"] = standard_fields.absent("currency_others", "承作幣別的其他出處")
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [occ("issue_price_ch4", "發行價格（第四章申購價金）", "第四章第 5 項申購價金", fields["issue_price_ch4"])],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "封面刊印日期", fields["print_date"])]
    )
    fields["subscription_dates"] = standard_fields.absent(
        "subscription_dates", "受理申購日（MS 改核對第四章開始受理贖回日期）"
    )
    fields["coupon_pa_pct"] = standard_fields.absent("coupon_pa_pct", "年利率（MS 改核對月配息率與情境年化報酬率）")

    cz = fields["currency_zh"]
    scenarios = ms_scenario.section(article(18), cz.value if cz.ok else "美元")
    coupons, annualized = _mentions(fields, scenarios)
    return MsTermSheet(fields, TextIndex(doc.lines), doc, scenarios, coupons, annualized)


def _trustee_block(ch2: list[Line]) -> list[Line]:
    """第二章「(3) 受託機構」那一段（到下一個「(n)」之前）；找不到或重複時為空。"""
    starts = [i for i, ln in enumerate(ch2) if full_brackets(squash(ln.text)) == "（3）受託機構"]
    if len(starts) != 1:
        return []
    a = starts[0]
    b = next((i for i in range(a + 1, len(ch2)) if re.match(r"^[(（]\d+[)）]", ch2[i].text)), len(ch2))
    return ch2[a:b]


def _schedule(fields: dict[str, ParsedField]) -> None:
    """KO 觀察方式（日期表型且與第 17 項一致）、第一個可提前出場期、提前出場排程（範本規格 §4.1、§4.3）。"""
    table, obs17 = fields["date_table"], fields["ko_observation_art17"]
    bad = next((p for p in (table, obs17) if not p.ok), None)
    if bad is not None:
        for key in ("ko_observation", "first_callable_period", "autocall_schedule"):
            fields[key] = _derived(key, bad)
        return
    if table.value["type"] != obs17.value["type"]:
        note = "第 14 項(6) 日期表型與第 17 項觀察日寫法不一致"
        for key in ("ko_observation", "first_callable_period", "autocall_schedule"):
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=table.evidence + obs17.evidence, note=note)
        return
    kind, k, rows = table.value["type"], obs17.value["k"], table.value["rows"]
    ev = table.evidence + obs17.evidence
    fields["ko_observation"] = ParsedField("ko_observation", FieldStatus.PRESENT, kind, ev)
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, k, list(obs17.evidence))
    key = "end" if kind == "D" else "pricing"
    dates: dict[int, dt.date] = {r["period"]: r[key] for r in rows if r["period"] >= k}
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule", FieldStatus.PRESENT, standard_fields.AutocallSchedule(kind, k, len(rows), dates), ev
    )


def _prices(fields: dict[str, ParsedField]) -> None:
    """價格表 → 標準欄位：標的（彭博代碼）、各標的價格列與執行／下限／KO 百分比（範本規格 §4.6；第 11 項標的表為交叉驗證）。"""
    pt, ki, k, tenor = (fields[x] for x in ("price_table", "ki_type", "first_callable_period", "tenor_months"))
    if not pt.ok:
        for key in ("underlyings", "underlying_prices", "strike_pct", "ko_pct", "ki_pct"):
            fields[key] = _derived(key, pt)
        return
    tickers = [r["ticker"] for r in pt.value["rows"]]
    fields["underlyings"] = _derived("underlyings", pt, tickers)
    # 標的名稱取第 11 項標的表（代碼須依序相同才對得上列；投資人須知的標的名稱和它比）
    art11, names = fields["underlyings_art11"], fields["underlying_names_art11"]
    aligned = art11.ok and names.ok and art11.value == tickers and len(names.value) == len(tickers)
    fields["underlying_prices"] = ParsedField(
        "underlying_prices",
        FieldStatus.PRESENT,
        tuple(
            standard_fields.PriceRow(
                r["ticker"],
                dict(r["prices"]),
                tuple(Evidence.of(ln) for ln in lns),
                names.value[i] if aligned else None,
            )
            for i, (r, lns) in enumerate(zip(pt.value["rows"], pt.value["row_lines"], strict=True))
        ),
        list(pt.evidence),
    )
    headers, ev = pt.value["headers"], list(pt.evidence)
    fields["strike_pct"] = ParsedField("strike_pct", FieldStatus.PRESENT, headers["strike"], ev)
    if ki.ok and ki.value == "none":
        fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence))
    elif "ki" in headers:
        fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.PRESENT, headers["ki"], ev)
    else:
        fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.INVALID, evidence=ev, note="價格表沒有下限價格欄")
    if "ko" in headers:
        fields["ko_pct"] = ParsedField("ko_pct", FieldStatus.PRESENT, headers["ko"], ev)
    elif k.ok and tenor.ok and k.value == tenor.value:
        note = "說明書沒有自動提前出場價（Non-Call = 天期）"
        fields["ko_pct"] = ParsedField("ko_pct", FieldStatus.NOT_APPLICABLE, evidence=ev, note=note)
    else:
        note = "價格表沒有自動提前出場價欄，但第一個可提前出場期早於到期"
        fields["ko_pct"] = ParsedField("ko_pct", FieldStatus.INVALID, evidence=ev, note=note)


def _mentions(
    fields: dict[str, ParsedField], scenarios: ms_scenario.ScenarioSection
) -> tuple[list[Mention], list[Mention]]:
    """月配息率的每個出處與獲利情境的年化報酬率（核對規則 §3.3）；讀不到的出處值為 None。"""

    def one(where: str, pf: ParsedField) -> Mention:
        return Mention(where, pf.value if pf.ok else None, tuple(pf.evidence))

    def hit(where: str, h: ms_scenario.Hit) -> Mention:
        return Mention(where, h.values[0], tuple(Evidence.of(ln) for ln in h.lines))

    def missing(where: str, lines: Sequence[Line]) -> Mention:
        return Mention(where, None, tuple(Evidence.of(ln) for ln in lines))

    coupons = [one("第 15 項(2) 固定配息率", fields["monthly_coupon_pct"])]
    table = fields["date_table"]
    if table.ok and table.value["type"] == "D":
        coupons.append(one("第 15 項配息公式", fields["coupon_formula_pct"]))
    if len(scenarios.monthly) == 1:
        coupons.append(hit("第 18 項假設月配息率", scenarios.monthly[0]))
    else:
        coupons.append(missing("第 18 項假設月配息率", [h.lines[0] for h in scenarios.monthly]))
    annualized = []
    for s in scenarios.scenarios:
        if s.kind == "default":
            continue
        where = f"第 18 項情境{s.number}"
        many = len(s.rates) > 1
        coupons.extend(hit(f"{where}算式" + (f"第 {i} 處" if many else ""), h) for i, h in enumerate(s.rates, 1))
        if not s.rates:
            coupons.append(missing(f"{where}算式", s.lines[:1]))
        if s.kind == "profit":
            annualized.extend(hit(f"{where}年化報酬率", h) for h in s.annualized)
            if not s.annualized:
                annualized.append(missing(f"{where}年化報酬率", s.lines[:1]))
    return coupons, annualized
