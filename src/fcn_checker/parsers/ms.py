"""MS 中文產品說明書（docs/templates/ms-zh-product-description.md）：章＋條號＋子項＋標籤定位，保留原文證據。

只支援新版範本（2025-10 起；第一章第 1 項「商品中文名稱」、英文「International Plc」）；舊版範本辨識失敗，轉人工覆核。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import ScenarioParam, ScenarioPrice
from ..text import full_brackets, squash
from . import money, ms_scenario, ms_tables, ms_wording, price_table, schedule
from .common import NO_AUTOCALL_KO_PCT, NUM, Hit, Mention, derived, ko_at_initial, number
from .layout import Document, LayoutSpec, TextIndex, capture, join_text, parse_date

TEMPLATE_ID = "ms-zh-pd"
# 2：價格表改由 parsers/price_table.py 讀（Issue #288）；3：日期表標準欄位與共用排程推導（Issue #289）；
# 4：重印價格表、情境假設值與較差情境執行價交成共用情境規則的標準欄位（Issue #294）
PARSER_VERSION = "4"
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


@dataclass
class MsTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    scenarios: ms_scenario.ScenarioSection  # 第 18 項
    coupon_mentions: list[Mention]  # 月配息率的出處（第 15 項；第 18 項的交給共用情境規則）

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


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
        else derived("approval_date", approval, approved)
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
            fields[key] = derived(key, distributor, parts[k].strip() if parts else None)
    put("print_date", doc.before_chapter1(), r"中文產品說明書刊印日期：" + D, parse_date)

    # 第一章
    art1 = article(1)
    m1 = re.match(r"^1\.\s*商品中文名稱[：:]\s*(.+)$", join_text(art1), re.S) if art1 else None
    fields["name_art1"] = (
        ParsedField.present("name_art1", m1[1].strip(), art1) if m1 else ParsedField.missing("name_art1")
    )
    put("issuer_name_ch1", article(3), r"本商品發行機構為(.+?)，")
    put("currency_art5", article(5), r"計價幣別：(.+?)（[A-Z]{3}）")
    put("denomination", article(6), r"每單位商品面額：\D*?" + NUM + r"元", number)
    put("denomination_currency", article(6), rf"每單位商品面額：({money.CURRENCY}){money.NUMBER}元")
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
        ms_tables.price_table("price_table", region[1], ms_tables.PRICE_TABLE, title=region[0])
        if region
        else ParsedField.missing("price_table", "找不到第 16 項價格表（標題「…：依下表所示」到「(1) 最低保證贖回率」）")
    )
    fields["ko_memory"], fields["ko_observation_art17"] = _art17(article(17))
    zh = fields["name_zh"]
    fields["name_memory"] = derived(
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
    put("minimum_subscription", ch4, r"最低申購金額為[^，。]*，即\D{0,4}?" + NUM + r"元", number)
    put("minimum_additional", ch4, r"並以\D{0,4}?" + NUM + r"元(?:（[^）]*）)?為最低加購單位", number)
    put("minimum_redemption", ch4, r"最低贖回金額為[^，。]*，即\D{0,4}?" + NUM + r"元", number)
    put("redemption_increment", ch4, r"並以\D{0,4}?" + NUM + r"元(?:（[^）]*）)?為累加贖回單位", number)
    put("issue_price_ch4", ch4, r"發行價格（商品面額的(\d+(?:\.\d+)?)%）", Decimal)
    ccy, amount = money.CURRENCY, money.NUMBER
    put("minimum_subscription_currency", ch4, rf"最低申購金額為[^，。]*，即({ccy}){amount}元")
    put("minimum_additional_currency", ch4, rf"並以({ccy}){amount}元(?:（[^）]*）)?為最低加購單位")
    put("minimum_redemption_currency", ch4, rf"最低贖回金額為[^，。]*，即({ccy}){amount}元")
    put("redemption_increment_currency", ch4, rf"並以({ccy}){amount}元(?:（[^）]*）)?為累加贖回單位")

    occ = standard_fields.Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ(
                "minimum_subscription",
                "minimum_subscription",
                "第四章第 4 項最低申購金額",
                fields["minimum_subscription"],
            ),
            occ("minimum_additional", "minimum_additional", "第四章第 4 項最低加購單位", fields["minimum_additional"]),
            occ("minimum_redemption", "minimum_redemption", "第四章第 8 項最低贖回金額", fields["minimum_redemption"]),
            occ(
                "redemption_increment",
                "redemption_increment",
                "第四章第 8 項累加贖回單位",
                fields["redemption_increment"],
            ),
        ],
    )
    scenarios = ms_scenario.section(article(18))
    fields["currency_others"] = standard_fields.occurrences("currency_others", _currency_occurrences(fields, scenarios))
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [occ("issue_price_ch4", "issue_price_subscription", "第四章第 5 項申購價金", fields["issue_price_ch4"])],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "print_date", "封面刊印日期", fields["print_date"])]
    )
    fields["subscription_dates"] = standard_fields.absent(
        "subscription_dates", "受理申購日（MS 改核對第四章開始受理贖回日期）"
    )
    fields["coupon_pa_pct"] = standard_fields.absent("coupon_pa_pct", "年利率（MS 改核對月配息率與情境年化報酬率）")
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")
    fields.update(standard_fields.absent_all(standard_fields.DISTRIBUTOR_OTHERS, "受託機構的其他出處"))
    fields["initial_pricing"] = standard_fields.absent("initial_pricing", "期初定價（收盤價／VWAP）字樣")

    _scenario_standard(fields, scenarios)
    return MsTermSheet(fields, TextIndex(doc.lines), doc, scenarios, _mentions(fields))


def _currency_occurrences(
    fields: dict[str, ParsedField], scenarios: ms_scenario.ScenarioSection
) -> list[standard_fields.Occurrence]:
    """金額旁的幣別字（標準欄位 `currency_others`，Issue #170）：第一章 §6 面額、第四章四個最低金額、§18 假設總投資金額
    各一處，§18 每個情境一處（該情境裡每一處「金額 幣別」；股價與含「股」的實物交割算式用標的幣別，不算）。"""
    occ = standard_fields.Occurrence
    out = [
        occ(
            "denomination_currency",
            "denomination_currency",
            "第一章第 6 項每單位商品面額",
            fields["denomination_currency"],
        ),
        occ(
            "minimum_subscription_currency",
            "minimum_subscription_currency",
            "第四章第 4 項最低申購金額",
            fields["minimum_subscription_currency"],
        ),
        occ(
            "minimum_additional_currency",
            "minimum_additional_currency",
            "第四章第 4 項最低加購單位",
            fields["minimum_additional_currency"],
        ),
        occ(
            "minimum_redemption_currency",
            "minimum_redemption_currency",
            "第四章第 8 項最低贖回金額",
            fields["minimum_redemption_currency"],
        ),
        occ(
            "redemption_increment_currency",
            "redemption_increment_currency",
            "第四章第 8 項累加贖回單位",
            fields["redemption_increment_currency"],
        ),
    ]
    name, where = "scenario_denomination_currency", "第 18 項假設總投資金額"
    hit = scenarios.denomination_currency
    out.append(
        occ(
            name,
            "scenario_investment_currency",
            where,
            ParsedField.present(name, hit.value, list(hit.lines))
            if hit
            else ParsedField.missing(name, "第 18 項假設找不到總投資金額或出現多次"),
        )
    )
    for s in scenarios.scenarios:
        if s.kind == "default":
            continue
        field = f"情境{s.number}_currency"
        out.append(
            money.scenario_occurrence(field, "scenario_currency", f"第 18 項情境{s.number}", s.money, s.lines[:1])
        )
    return out


def _trustee_block(ch2: list[Line]) -> list[Line]:
    """第二章「(3) 受託機構」那一段（到下一個「(n)」之前）；找不到或重複時為空。"""
    starts = [i for i, ln in enumerate(ch2) if full_brackets(squash(ln.text)) == "（3）受託機構"]
    if len(starts) != 1:
        return []
    a = starts[0]
    b = next((i for i in range(a + 1, len(ch2)) if re.match(r"^[(（]\d+[)）]", ch2[i].text)), len(ch2))
    return ch2[a:b]


SCHEDULE_WHERE = "第 14 項(6) 日期表"
# D 型配息週期起始日 = 前期終止日後的下一個一籃子預定交易日；第 1 期起始日 = 發行日（範本規格 §6）
SCHEDULE_START = standard_fields.PeriodStart("next_trading_day", "issue_date")


def _schedule_table(value: dict) -> standard_fields.DateTable:
    """日期表：D 觀察日 = 配息週期終止日；P 觀察日 = 定價日，沒有起始日欄。"""
    if value["type"] == "D":
        rows = schedule.rows_of(value["rows"], "end", value["row_lines"])
        return standard_fields.DateTable(SCHEDULE_WHERE, rows, SCHEDULE_START)
    return standard_fields.DateTable(SCHEDULE_WHERE, schedule.rows_of(value["rows"], "pricing", value["row_lines"]))


def _schedule(fields: dict[str, ParsedField]) -> None:
    """日期表、KO 觀察方式（日期表型且與第 17 項一致）、第一個可提前出場期、提前出場排程（`parsers/schedule.py`，
    範本規格 §4.1、§4.3）。"""
    table, obs17 = fields["date_table"], fields["ko_observation_art17"]
    fields["schedule_table"] = schedule.table_field(table, _schedule_table)
    bad = next((p for p in (table, obs17) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        return
    ev = table.evidence + obs17.evidence
    if table.value["type"] != obs17.value["type"]:
        note = "第 14 項(6) 日期表型與第 17 項觀察日寫法不一致"
        fields.update(schedule.invalid(ev, note))
        return
    kind = table.value["type"]
    obs = ParsedField("ko_observation", FieldStatus.PRESENT, kind, ev)
    first = ParsedField("first_callable_period", FieldStatus.PRESENT, obs17.value["k"], list(obs17.evidence))
    fields.update(
        schedule.autocall_schedule(obs, first, fields["schedule_table"], "end" if kind == "D" else "observation")
    )


def _prices(fields: dict[str, ParsedField]) -> None:
    """價格表 → 標準欄位：標的（彭博代碼）、各標的價格列與執行／下限／KO 百分比（範本規格 §4.6；第 11 項標的表為交叉驗證）。

    Non-Call = 天期時價格表沒有 KO 欄：KO 視為期初價格的 100%、KO 價 = 期初價格（`NO_AUTOCALL_KO_PCT`），照常比對。
    """
    pt, ki, k, tenor = (fields[x] for x in ("price_table", "ki_type", "first_callable_period", "tenor_months"))
    if not pt.ok:
        for key in ("underlyings", "underlying_prices", "strike_pct", "ko_pct", "ki_pct"):
            fields[key] = derived(key, pt)
        return
    tickers = list(pt.value.tickers)
    fields["underlyings"] = derived("underlyings", pt, tickers)
    # 標的名稱取第 11 項標的表（代碼須依序相同才對得上列；投資人須知的標的名稱和它比）
    art11, names = fields["underlyings_art11"], fields["underlying_names_art11"]
    aligned = art11.ok and names.ok and art11.value == tickers and len(names.value) == len(tickers)
    headers, ev = pt.value.pcts, list(pt.evidence)
    no_ko_column_at_tenor = "ko" not in headers and k.ok and tenor.ok and k.value == tenor.value
    rows = price_table.price_rows(pt, names=list(names.value) if aligned else [None] * len(tickers))
    if no_ko_column_at_tenor:
        rows.value = tuple(replace(r, prices=ko_at_initial(r.prices)) for r in rows.value)
    fields["underlying_prices"] = rows
    fields["strike_pct"] = ParsedField("strike_pct", FieldStatus.PRESENT, headers["strike"], ev)
    if ki.ok and ki.value == "none":
        fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence))
    elif "ki" in headers:
        fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.PRESENT, headers["ki"], ev)
    else:
        fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.INVALID, evidence=ev, note="價格表沒有下限價格欄")
    if "ko" in headers:
        fields["ko_pct"] = ParsedField("ko_pct", FieldStatus.PRESENT, headers["ko"], ev)
    elif no_ko_column_at_tenor:
        note = "說明書沒有自動提前出場價（Non-Call = 天期，不會提前出場）：KO 視為期初價格的 100%"
        fields["ko_pct"] = ParsedField("ko_pct", FieldStatus.PRESENT, NO_AUTOCALL_KO_PCT, ev, note=note)
    else:
        note = "價格表沒有自動提前出場價欄，但第一個可提前出場期早於到期"
        fields["ko_pct"] = ParsedField("ko_pct", FieldStatus.INVALID, evidence=ev, note=note)


def _mentions(fields: dict[str, ParsedField]) -> list[Mention]:
    """月配息率主來源（第 15 項(2)）與 D 型第 15 項配息公式（核對規則 §3.3）；第 18 項情境的月配息率與年化報酬率交給共用
    情境規則（`_scenario_standard`）。讀不到的出處值為 None。"""
    coupons = [Mention.of_field("第 15 項(2) 固定配息率", fields["monthly_coupon_pct"])]
    table = fields["date_table"]
    if table.ok and table.value["type"] == "D":
        coupons.append(Mention.of_field("第 15 項配息公式", fields["coupon_formula_pct"]))
    return coupons


def _scenario_standard(fields: dict[str, ParsedField], sec: ms_scenario.ScenarioSection) -> None:
    """第 18 項交給共用情境規則的標準欄位（Issue #294）：重印價格表；假設的總投資金額、年期、月配息率，各情境算式的
    每個月配息率與獲利情境的年化報酬率（`scenario_params`；說明書沒有年利率，年化報酬率和參考條件表年利率比）；較差
    情境的執行價（`scenario_prices`，依彭博代碼對到第 16 項價格表那一列）。"""
    fields["main_price_table"] = price_table.at("main_price_table", fields["price_table"], ms_tables.PRICE_TABLE)
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", sec.table, ms_tables.SCENARIO_TABLE)
    head = [Evidence.of(x) for x in sec.lines[:1]]

    def value(code: str, h: Hit | None, lines: list, note: str) -> ParsedField:
        if h is None:
            return ParsedField(code, FieldStatus.MISSING, evidence=lines or head, note=note)
        return ParsedField.present(code, h.value, h.lines)

    where = "第 18 項假設"
    monthly = sec.monthly[0] if len(sec.monthly) == 1 else None
    params = [
        ScenarioParam(
            "scenario_investment",
            where,
            value("scenario_investment", sec.denomination, [], f"{where}找不到總投資金額或出現多次"),
        ),
        ScenarioParam("scenario_tenor", where, value("scenario_tenor", sec.tenor, [], f"{where}找不到年期或出現多次")),
        ScenarioParam(
            "scenario_monthly_coupon",
            f"{where}月",
            value(
                "scenario_monthly_coupon",
                monthly,
                [Evidence.of(h.lines[0]) for h in sec.monthly],
                f"{where}找不到月配息率或出現多次",
            ),
        ),
    ]
    prices = []
    pt = fields["price_table"]
    tickers = [r.ticker.replace(" ", "") for r in pt.value.rows] if pt.ok else []
    for s in sec.scenarios:
        if s.kind == "default":
            continue
        where = f"第 18 項情境{s.number}"
        lines = [Evidence.of(x) for x in s.lines[:1]]
        many = len(s.rates) > 1
        params += [
            ScenarioParam(
                "scenario_monthly_coupon",
                f"{where}算式" + (f"第 {i} 處" if many else "") + "的",
                value("scenario_monthly_coupon", h, [], ""),
            )
            for i, h in enumerate(s.rates, 1)
        ] or [
            ScenarioParam(
                "scenario_monthly_coupon",
                f"{where}算式的",
                value("scenario_monthly_coupon", None, lines, f"{where}算式找不到月配息率"),
            )
        ]
        if s.kind == "profit":
            params += [
                ScenarioParam("scenario_annualized_return", where, value("scenario_annualized_return", h, [], ""))
                for h in s.annualized
            ] or [
                ScenarioParam(
                    "scenario_annualized_return",
                    where,
                    value("scenario_annualized_return", None, lines, f"{where}找不到年化報酬率"),
                )
            ]
        if s.kind == "worse":
            strike = s.strike[0] if len(s.strike) == 1 else None
            row = tickers.index(strike.value.ticker) + 1 if strike and tickers.count(strike.value.ticker) == 1 else None
            pf = (
                ParsedField.present("scenario_strike", strike.value.strike, strike.lines)
                if strike
                else ParsedField(
                    "scenario_strike", FieldStatus.MISSING, evidence=lines, note=f"{where}找不到執行價或出現多次"
                )
            )
            prices.append(ScenarioPrice(f"{where}（較差情境）", row, "strike", pf))
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    fields["scenario_prices"] = standard_fields.occurrences("scenario_prices", prices)
