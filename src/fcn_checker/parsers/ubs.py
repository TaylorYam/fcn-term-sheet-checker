"""UBS（瑞銀）中文產品說明書（docs/templates/ubs-zh-product-description.md）：段名＋項號＋標籤定位，保留原文證據。

沒有封面與章號：第 1～2 頁是「一、」～「十二、」清單，之後各段只有段名（單獨一行：商品基本資料、相關機構事業概況、
商品風險揭露、投資風險警語、一般交易事項、特別記載事項），段內項號「一、」起重新起算（x≈36）。每頁頁底
「第N頁，共M頁」（擷取時排在該頁第一行）不算內文；新版每頁頁首有私用區字元 U+F020（SymbolMT），換頁時可能夾在句子
中間，一律先刪除 U+F000–U+F0FF。K／KI／KO % 在價格表表頭；價格表只有中文名稱，依名稱（= 第九項標的表同列）對到彭博代碼。
只支援新版範本（商品種類「股權結構型商品」）；舊版（「股權結構型債券」）辨識失敗。
數字、日期與句型在去空白的全文索引上讀；名稱、地址等要保留空白的值在原文合併文字（`common.RawText`）上讀。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence, ScenarioParam
from ..text import squash
from . import money, price_table, schedule
from .common import RawText, capture_group, derived, fee_rates, number, zh_number
from .layout import TextIndex, capture, join_wrapped_tickers, parse_date

TEMPLATE_ID = "ubs-zh-pd"
# 2：價格表改由 parsers/price_table.py 讀（Issue #287）；3：非記憶式要有明確的提前出場事件句（Issue #297）；
# 4：補讀發行價格金額、申購價金、十五設定參數的變動配息（Issue #303）；5：說明文字與十五情境重複寫出的值（Issue #320）；
# 6：日期表標準欄位與共用排程推導（Issue #289）；7：情境價格表與設定參數交成共用情境規則的標準欄位（Issue #294）
PARSER_VERSION = "7"
SECTIONS = ("商品基本資料", "相關機構事業概況", "商品風險揭露", "投資風險警語", "一般交易事項", "特別記載事項")
PRODUCT_TYPE = "股權結構型商品"
OLD_PRODUCT_TYPE = "股權結構型債券"
ISSUER = "瑞士銀行"
NAME_ZH_MARK = "提前出場固定配息連結股票"
NAME_EN_MARK = "Callable Variable Maturity Equity Linked Notes"
REP_LABEL = "六、發行人（即依「境外結構型商品管理規則」所定義之發行機構在台分行）名稱："
ARTICLES = 26  # 商品基本資料項數
PAGE = re.compile(r"^第\d+\s?頁，共\d+頁$")
PAGE_Y = 770  # 頁碼在頁底（y≈781）
PUA = re.compile("[\uf000-\uf0ff]")  # Symbol 字型的私用區字元（U+F020）
ITEM = re.compile(r"^([一二三四五六七八九十]{1,3})、")
ITEM_X = 45  # 項號左緣 x≈36
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
AMOUNT = rf"([\d,]+\.\d{{2}})({CCY})"
SCENARIO_AMOUNT = rf"(?<![\d.,])(\d[\d,]*\.\d{{2}}|0)({CCY})"  # 情境的金額（小數 2 位；股價小數 4 位不算）
TICKER = re.compile(r"^([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})$")
# 提前出場觀察日（範本規格 §4.1）
KO_DAILY = r"為自第(\d{1,2})個配息觀察期間結束日.{0,30}?至期末訂價日.{0,12}?的任一預定交易日"
KO_PERIODIC = r"為自第(\d{1,2})個配息觀察的每個配息觀察日"
# 記憶式（範本規格 §4.2）
MEMORY = "即使各股票在不同提前出場觀察日成為鎖定股票，提前出場事件仍被視為發生"
LOCK = r"鎖定股票[:：]係指"
# 非記憶式的提前出場事件句（範本規格 §4.2；多標的「所有…之收盤價皆高於或等於其提前出場價」、單一標的「…的收盤價高於或等於提前出場價」）
NO_MEMORY_EVENT = (
    r"若(?:籃子中)?(?:所有)?連結標的股票在任一提前出場觀察日.{0,80}?[之的]收盤價皆?高於或等於其?提前出場價.{0,30}?，"
    r"則提前出場事件視同"
)
# 觸及下限事件（範本規格 §4.4）
KI_DEFINITION = r"觸及下限事件[:：]?(若在.{0,40}?)，(?:籃子中)?(?:任一)?連結標的股票的收盤價低於(?:其)?下限價，則觸及下限事件視同發生"
KI_KINDS = (
    ("D", "若在期初訂價日（含）至期末訂價日（含）間的任一預定交易日"),
    ("P", "若在任一配息觀察日"),
    ("AM", "若在期末訂價日"),
)
# 價格表（商品基本資料八到期日給付、十五情境）：表內沒有列號與代碼（名稱依第九項標的表對到代碼），以價格切列；
# 欄序 K → KI → KO，表頭 %「（即期初價格的X%）」，價格後接報價幣別字
PRICE_TABLE = price_table.TableSpec(
    where="商品基本資料八價格表",
    initial="期初價格",
    columns=(
        price_table.Column("strike", "轉換價"),
        price_table.Column("ki", "下限價", required=False),
        price_table.Column("ko", "提前出場價"),
    ),
    anchor="price",
    pct=r"的(\d+(?:\.\d+)?)%",
    pct_label="（即期初價格的X%）",
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="商品基本資料十五情境價格表")


def clean_lines(lines: Sequence[Line]) -> list[Line]:
    """刪除私用區字元（U+F020）與頁底頁碼；只剩空白的行不算內文。"""
    out = []
    for ln in lines:
        text = PUA.sub("", ln.text)
        if text != ln.text:
            text = text.strip()
            if not text:
                continue
            ln = replace(ln, text=text)
        if ln.y0 >= PAGE_Y and PAGE.match(ln.text.strip()):
            continue
        out.append(ln)
    return out


def clean_name(text: str) -> str:
    """名稱去掉句尾「。」；「瑞銀（定期觀察）」「瑞銀（每日觀察）」視為不加括號的寫法（2026-10-15 確認）。"""
    text = text.strip().rstrip("。").strip()
    return re.sub(r"^瑞銀\s*[（(]\s*(定期觀察|每日觀察)\s*[)）]", r"瑞銀\1", text)


def without_place_note(text: str) -> str:
    """地址後的「(營業活動所在地)」不屬於地址；句尾「。」不算。"""
    return re.sub(r"\s*[(（]\s*營業活動所在地\s*[)）]\s*$", "", text.strip().rstrip("。").strip())


class UbsDocument:
    """版面結構：段（段名單獨一行）→ 項（行首中文數字＋「、」，段內依序）。第 1～2 頁（第一段之前）也是一組項。"""

    def __init__(self, lines: Sequence[Line]):
        self.lines = clean_lines(lines)
        starts: dict[str, list[int]] = {}
        for i, ln in enumerate(self.lines):
            s = squash(ln.text)
            if s in SECTIONS:
                starts.setdefault(s, []).append(i)
        ordered = sorted((idx[0], name) for name, idx in starts.items() if len(idx) == 1)
        self.sections: dict[str, tuple[int, int]] = {}
        for k, (i, name) in enumerate(ordered):
            end = ordered[k + 1][0] if k + 1 < len(ordered) else len(self.lines)
            self.sections[name] = (i, end)
        self.order = [name for _, name in ordered]

    def section(self, name: str) -> list[Line]:
        span = self.sections.get(name)
        return self.lines[span[0] : span[1]] if span else []

    def cover(self) -> list[Line]:
        first = min((a for a, _ in self.sections.values()), default=len(self.lines))
        return self.lines[:first]

    def items(self, lines: list[Line]) -> dict[int, list[Line]]:
        """項號「一、」起依序（行首、x≈36）；項到下一項為止。"""
        marks: list[tuple[int, int]] = []
        expect = 1
        for i, ln in enumerate(lines):
            m = ITEM.match(squash(ln.text))
            if m and ln.x0 < ITEM_X and zh_number(m[1]) == expect:
                marks.append((expect, i))
                expect += 1
        out = {}
        for k, (n, i) in enumerate(marks):
            stop = marks[k + 1][1] if k + 1 < len(marks) else len(lines)
            out[n] = lines[i:stop]
        return out


def document(lines: Sequence[Line]) -> UbsDocument:
    return UbsDocument(lines)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 UBS（新版）說明書。"""
    doc = document(lines)
    cover = doc.cover()
    ti = TextIndex(cover)
    raw = RawText(cover)
    head = [squash(x.text) for x in cover[:2]]
    zh = re.search(r"商品中文名稱[:：](.+?)[（(]下稱", ti.text)
    en = re.search(r"商品英文名稱\s*[:：]\s*(.+?)\s*三、", raw.text, re.S)
    kind = re.search(r"三、商品種類[:：](.+?)。?四、", ti.text)
    arts = doc.items(doc.section("商品基本資料"))
    failed = []
    for test, message in (
        (
            len(head) == 2 and head[0] == "中文產品說明書" and head[1].startswith("一、商品代號：TDCC商品代號為"),
            "前兩行不是「中文產品說明書」「一、 商品代號：TDCC 商品代號為…」",
        ),
        (
            zh is not None and zh[1].startswith("瑞銀") and NAME_ZH_MARK in zh[1],
            f"第 1 頁商品中文名稱不是以「瑞銀」開頭或沒有「{NAME_ZH_MARK}」",
        ),
        (
            en is not None and en[1].startswith("UBS") and squash(NAME_EN_MARK) in squash(en[1]),
            f"第 1 頁商品英文名稱不是以「UBS」開頭或沒有「{NAME_EN_MARK}」",
        ),
        (
            "四、發行機構：瑞士銀行" in ti.text and REP_LABEL in ti.text,
            "第 1 頁沒有「四、發行機構：瑞士銀行」與發行人標籤",
        ),
        (
            kind is not None and kind[1] == PRODUCT_TYPE,
            f"商品種類不是新版「{PRODUCT_TYPE}」"
            + ("（舊版範本「股權結構型債券」不支援）" if kind is not None and kind[1] == OLD_PRODUCT_TYPE else ""),
        ),
        (doc.order == list(SECTIONS), "段名不是依序單獨成行的「" + "、".join(SECTIONS) + "」"),
        (set(range(1, ARTICLES + 1)) <= set(arts), "商品基本資料項號一～二十六不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class UbsTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: UbsDocument

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def read(lines: Sequence[Line]) -> UbsTermSheet:
    """讀出標準欄位與 UBS 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    fields: dict[str, ParsedField] = {}

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    _cover(fields, doc, put)
    arts = doc.items(doc.section("商品基本資料"))

    def article(n: int) -> list[Line]:
        return arts.get(n, [])

    fields["name_art1_zh"] = RawText(article(1)).capture("name_art1_zh", r"商品名稱\s*[:：]\s*(.+?)\s*$", clean_name)
    fields["issuer_full_ch1"] = RawText(article(3)).capture("issuer_full_ch1", r"發行機構為\s*(.+?)\s*。\s*截至")
    fields["issuer_name_ch1"] = derived_name(fields["issuer_full_ch1"], "issuer_name_ch1")
    put("currency_art5", article(5), rf"計價幣別[:：]({ZH_CCY})")
    art8 = article(8)
    _article8(fields, art8, put)
    fields["art9_table"] = art9_table(article(9))
    _dates(fields, article(12), put)
    _table_fields(fields)
    _schedule(fields)
    _article15(fields, article(15))
    fields["scenario_reprint_table"] = price_table.at(
        "scenario_reprint_table", fields["scenario_table"], SCENARIO_TABLE
    )
    fields["scenario_prices"] = standard_fields.absent("scenario_prices", "要核對的較差情境價格")
    scenarios = _scenarios(fields, article(15))
    put("isin", article(25), r"ISIN代碼[:：]([A-Z]{2}[A-Z0-9]{10})")
    _organizations(fields, doc)
    _general(fields, doc, put)
    _occurrences(fields)
    wording = [  # 說明文字的幣別字（Issue #320，比照 BBVA #305）；ADE 提前贖回「美元價值的美元金額」固定寫美元，不列入
        *money.currency_words(
            art8, rf"換算後的({CCY})金額", "residual_conversion_currency", "說明文字幣別", "商品基本資料八剩餘金額定義"
        ),
        *money.currency_words(
            article(15),
            rf"則以({CCY})支付[（(]",
            "scenario_residual_currency",
            "說明文字幣別",
            "商品基本資料十五情境「則以X支付（“剩餘金額”）」",
        ),
        *money.currency_words(
            article(15),
            rf"以({CCY})支付的剩餘金額",
            "scenario_residual_paid_currency",
            "說明文字幣別",
            "商品基本資料十五情境「以X支付的剩餘金額」",
        ),
    ]
    if re.search(rf"以每一{CCY}可兌", TextIndex(art8).text):  # 跨幣別商品才有的期末匯率定義
        wording += money.currency_words(
            art8, rf"以每一({CCY})可兌", "fx_definition_currency", "說明文字幣別", "商品基本資料八期末匯率定義"
        )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [*fields["currency_others"].value, *wording, *scenarios]
    )
    fields["initial_pricing"] = standard_fields.absent(
        "initial_pricing", "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP）"
    )
    return UbsTermSheet(fields, TextIndex(doc.lines), doc)


def derived_name(full: ParsedField, name: str) -> ParsedField:
    """發行機構名稱本身：到第一個「（」或「，」之前（後接的「（下稱…）」「（UBS AG）」與發行分行不屬於名稱）。"""
    if not full.ok:
        return derived(name, full)
    return ParsedField(
        name, FieldStatus.PRESENT, re.split(r"[（(，,]", full.value, maxsplit=1)[0].strip(), list(full.evidence)
    )


def _cover(fields: dict[str, ParsedField], doc: UbsDocument, put: Callable) -> None:
    """第 1～2 頁「一、」～「十二、」。"""
    cover = doc.cover()
    items = doc.items(cover)
    raw = RawText(cover)
    put("product_code", cover, r"TDCC商品代號為(\d{12})")
    put("trustee_product_code", cover, r"受託機構(?:/銷售機構)?商品代號為(\d{12})")
    fields["name_zh"] = raw.capture("name_zh", r"商品中文名稱\s*[:：]\s*(.+?)\s*[（(]\s*下稱", clean_name)
    fields["name_en"] = raw.capture("name_en", r"商品英文名稱\s*[:：]\s*(.+?)\s*三、", clean_name)
    put("product_type", cover, r"三、商品種類[:：](.+?)。?四、")
    fields["issuer_full_cover"] = raw.capture(
        "issuer_full_cover", r"四、\s*發行機構\s*[:：]\s*(.+?)\s*發行機構\s*（之發行分行）"
    )
    fields["issuer_name_cover"] = derived_name(fields["issuer_full_cover"], "issuer_name_cover")
    put("currency_zh", cover, rf"五、計價幣別[:：]({ZH_CCY})。?六、")
    dist = RawText(items.get(7, []))
    fields["distributor_name_cover"] = dist.capture(
        "distributor_name_cover", r"名稱\s*[:：]\s*(.+?)\s*(?:[；;。（(]|受託機構電話)"
    )
    fields["distributor_phone_cover"] = dist.capture("distributor_phone_cover", r"電話\s*[:：]\s*([+\d][\d\-() ]*\d)")
    fields["distributor_address_cover"] = dist.capture(
        "distributor_address_cover", r"地址\s*[:：]\s*(.+?)\s*$", without_place_note
    )
    put("approval_date", cover, r"九、" + D + r"經受託機構", parse_date)
    fields["print_date"] = capture(
        "print_date", TextIndex(cover), r"本產品說明書[刋刊]印日期[:：]" + D, parse_date, whole_match=True
    )


# ---------------------------------------------------------------- 商品基本資料八：面額、配息表、KO、KI、價格表


def _rows(text: str, pattern: str) -> list[re.Match[str]]:
    """表格的列；期別須從 1 起連續，遇到不連續就停。"""
    out = []
    for m in re.finditer(pattern, text):
        if int(m[1]) != len(out) + 1:
            break
        out.append(m)
    return out


def _variable_coupon(fields: dict[str, ParsedField], lns: list[Line], prefix: str) -> None:
    """KO 每日型的「變動配息率：x%X（nt/Nt）（年化配息率為y%）」與「t 係為a 至b 的數字」（八，及十五設定參數的重複）。"""
    variable = r"配息率[:：]" + PCT + r"[xX×]（nt/Nt）（年化配息率為" + PCT + r"）"
    fields[f"{prefix}variable_coupon_pct"] = capture_group(lns, variable, 1, f"{prefix}variable_coupon_pct", Decimal)
    fields[f"{prefix}variable_coupon_pa_pct"] = capture_group(
        lns, variable, 2, f"{prefix}variable_coupon_pa_pct", Decimal
    )
    name = f"{prefix}variable_periods"
    ti = TextIndex(lns, separate_digits=True)
    t = list(ti.finditer(r"t係為(\d{1,2})至(\d{1,2})的數字"))
    if len(t) == 1:
        fields[name] = ParsedField.present(name, (int(t[0][1]), int(t[0][2])), ti.lines_for(t[0].start(), t[0].end()))
    elif t:
        fields[name] = ParsedField.invalid(name, lns, "「t 係為a 至b 的數字」出現多處")
    else:
        fields[name] = ParsedField.missing(name, "找不到「t 係為a 至b 的數字」")


def _article8(fields: dict[str, ParsedField], art8: list[Line], put: Callable) -> None:
    ti = TextIndex(art8, separate_digits=True)
    issue = r"發行價格[:：](?:每單位面額之)?" + PCT
    put("issue_price_pct", art8, issue, Decimal)
    amount = r"發行價格[:：]\d+(?:\.\d+)?%，即" + AMOUNT
    fields["issue_price_amount"] = capture_group(art8, amount, 1, "issue_price_amount", number)
    fields["issue_price_currency"] = capture_group(art8, amount, 2, "issue_price_currency")
    if re.search(r"發行價格[:：]每單位面額之", ti.text):  # 寫「每單位面額之100%」時沒有金額；其他讀不到的寫法照常缺漏
        for name in ("issue_price_amount", "issue_price_currency"):
            fields[name] = standard_fields.absent(name, "發行價格的金額")
    denom = rf"每單位面額[:：]{AMOUNT}。"
    put("denomination", art8, denom, number)
    fields["denomination_currency"] = capture_group(art8, denom, 2, "denomination_currency")
    trade = rf"最低(?:申購)?交易金額[:：]{AMOUNT}"
    fields["minimum_trade"] = capture_group(art8, trade, 1, "minimum_trade", number)
    fields["minimum_trade_currency"] = capture_group(art8, trade, 2, "minimum_trade_currency")
    fields["redemption_currency"] = capture(
        "redemption_currency", TextIndex(art8), rf"以({CCY})支付剩餘金額", whole_match=True
    )
    _coupon_table(fields, ti)
    coupon = r"固定配息率[:：]" + PCT + r"。?（年化配息率(?:為)?" + PCT + r"）"
    fields["monthly_coupon_pct"] = capture("monthly_coupon_pct", ti, coupon, Decimal, whole_match=True)
    fields["coupon_pa_pct"] = capture_group(art8, coupon, 2, "coupon_pa_pct", Decimal)
    _variable_coupon(fields, art8, "")
    fields["ko_wording_art8"] = ko_wording("ko_wording_art8", ti)
    _memory(fields, ti)
    ko_pct = list(ti.finditer(r"提前出場價（即期初價格的" + PCT + r"）"))
    table = price_table.read(
        "underlying_table", table_region(art8, r"其中[:：]", "將四捨五入至小數點後第4位"), PRICE_TABLE
    )
    fields["underlying_table"] = table
    fields["main_price_table"] = price_table.at("main_price_table", table, PRICE_TABLE)
    # KO 條款寫出的 KO %（UB3）：價格表表頭以外的出處
    head = table.value.header_lines if table.ok else ()
    mentions = [m for m in ko_pct if not set(ti.lines_for(m.start(), m.end())) & set(head)]
    fields["ko_pct_mentions"] = ParsedField.present(
        "ko_pct_mentions",
        [(Decimal(m[1]), tuple(ti.lines_for(m.start(), m.end()))) for m in mentions],
        [ln for m in mentions for ln in ti.lines_for(m.start(), m.end())],
    )
    fields["ki_type"] = ki_type(ti, table)


def _coupon_table(fields: dict[str, ParsedField], ti: TextIndex) -> None:
    """配息表（範本規格 §5.2）：KO 每期「第n 個月配息／配息觀察日／配息給付日」；KO 每日「第n 個配息觀察期間／開始日／
    結束日／給付日／每單位面額x 固定（變動）配息率」。"""
    text = ti.text
    sep = r"\|?"
    daily = _rows(
        text,
        r"第(\d{1,2})個配息觀察(?:期間|期|)"
        + sep
        + D
        + sep
        + D
        + sep
        + D
        + r"對應的配息金額=(?:期?間)?每單位面額[xX×](固定|變動)配息率",
    )
    periodic = _rows(text, r"第(\d{1,2})個月配息" + sep + D + sep + D)
    if daily and not periodic:
        rows = [
            {
                "period": int(m[1]),
                "start": parse_date(m[2]),
                "end": parse_date(m[3]),
                "payment": parse_date(m[4]),
                "fixed": m[5] == "固定",
            }
            for m in daily
        ]
        kind, found = "D", daily
    elif periodic and not daily:
        rows = [{"period": int(m[1]), "observation": parse_date(m[2]), "payment": parse_date(m[3])} for m in periodic]
        kind, found = "P", periodic
    else:
        note = "商品基本資料八找不到配息表" if not (daily or periodic) else "配息表同時有每期與每日兩種寫法"
        fields["coupon_table"] = ParsedField.missing("coupon_table", note)
        return
    ev = ti.lines_for(found[0].start(), found[-1].end())
    if any(None in r.values() for r in rows):
        fields["coupon_table"] = ParsedField.invalid("coupon_table", ev, "配息表的日期讀不到")
    else:
        fields["coupon_table"] = ParsedField.present("coupon_table", {"kind": kind, "rows": rows}, ev)


def ko_wording(name: str, ti: TextIndex) -> ParsedField:
    """提前出場觀察日的寫法 → (D／P, k)；只能有一種（同一句可重複出現）。"""
    found = {("D", int(m[1])): m for m in ti.finditer(KO_DAILY)} | {
        ("P", int(m[1])): m for m in ti.finditer(KO_PERIODIC)
    }
    if len(found) != 1:
        note = "找不到提前出場觀察日的寫法" if not found else "提前出場觀察日有不同的寫法"
        return ParsedField.missing(name, note) if not found else ParsedField.invalid(name, ti.lines, note)
    ((key, m),) = found.items()
    return ParsedField.present(name, key, ti.lines_for(m.start(), m.end()))


def _memory(fields: dict[str, ParsedField], ti: TextIndex) -> None:
    """記憶式（範本規格 §4.2）：「即使各股票在不同提前出場觀察日成為鎖定股票…仍被視為發生」與「鎖定股票：係指」都有 = Y；
    都沒有且有非記憶式的提前出場事件句（收盤價高於或等於提前出場價）= N；其他寫法不合法。"""
    sentence, lock = MEMORY in ti.text, re.search(LOCK, ti.text) is not None
    plain = re.search(NO_MEMORY_EVENT, ti.text)
    if sentence != lock:
        fields["ko_memory"] = ParsedField.invalid(
            "ko_memory", ti.lines, "記憶式句型「即使各股票在不同提前出場觀察日成為鎖定股票」與「鎖定股票」定義只有其一"
        )
    elif sentence and plain:
        fields["ko_memory"] = ParsedField.invalid(
            "ko_memory", ti.lines, "商品基本資料八、記憶式句型與非記憶式的提前出場事件句同時出現"
        )
    elif sentence:
        p = ti.text.find(MEMORY)
        fields["ko_memory"] = ParsedField.present("ko_memory", True, ti.lines_for(p, p + len(MEMORY)))
    elif plain:
        fields["ko_memory"] = ParsedField.present("ko_memory", False, ti.lines_for(plain.start(), plain.end()))
    else:
        fields["ko_memory"] = ParsedField.invalid(
            "ko_memory", ti.lines, "商品基本資料八、提前出場事件句（記憶式或非記憶式）的寫法缺漏或不在範本規格內"
        )


def ki_type(ti: TextIndex, table: ParsedField) -> ParsedField:
    """觸及下限事件（範本規格 §4.4）：定義句、到期日給付「若觸及下限事件」、價格表下限價欄三者須一致。"""
    text = ti.text
    defs = list(ti.finditer(KI_DEFINITION))
    maturity = "若觸及下限事件" in text
    has_col = table.ok and "ki" in table.value.columns
    if not defs and not maturity:
        # 無 KI 要由說明書明確判定：沒有定義句、到期日給付直接比轉換價、價格表讀得到且沒有下限價欄（核對規則 §4）
        if not table.ok:
            return derived("ki_type", table)
        if has_col:
            return ParsedField.invalid("ki_type", ti.lines, "價格表有下限價欄，但沒有觸及下限事件")
        m = re.search(r"收盤價低於其?轉換價", text)  # OOS 的版本寫「低於其轉換價」（Issue #295）
        if m is None:
            return ParsedField.invalid("ki_type", ti.lines, "沒有觸及下限事件，到期日給付也不是直接比轉換價")
        return ParsedField.present("ki_type", "none", ti.lines_for(m.start(), m.end()))
    if len(defs) != 1 or not maturity:
        return ParsedField.invalid("ki_type", ti.lines, "觸及下限事件的定義與到期日給付寫法不一致或不在範本規格內")
    m = defs[0]
    kind = next((k for k, w in KI_KINDS if m[1].startswith(w)), None)
    lns = ti.lines_for(m.start(), m.end())
    if kind is None:
        return ParsedField.invalid("ki_type", lns, "觸及下限事件的觀察寫法不在範本規格內")
    if table.ok and not has_col:
        return ParsedField.invalid("ki_type", lns, "有觸及下限事件，但價格表沒有下限價欄")
    return ParsedField.present("ki_type", kind, lns)


def table_region(lns: list[Line], start_marker: str, end_marker: str) -> list[Line]:
    """價格表的範圍：`start_marker`（regex）之後、`end_marker` 那一行之前（去空白比對）。"""
    ti = TextIndex(lns)
    m = re.search(start_marker, ti.text)
    b = ti.text.find(end_marker, m.end()) if m else -1
    return lns[ti.line_at(m.end() - 1) + 1 : ti.line_at(b)] if m and b >= 0 else []


def art9_table(art9: list[Line]) -> ParsedField:
    """商品基本資料九、標的表：(名稱, 彭博代碼) 依表上順序；名稱是代碼左邊、y 最近的行（可能拆兩行）。"""
    ti = TextIndex(art9)
    a = ti.text.find("彭博代碼")
    b = ti.text.find("權重", a)
    if a < 0 or b < a:
        return ParsedField.missing("art9_table", "商品基本資料九找不到連結標的資產表")
    body = join_wrapped_tickers(art9[ti.line_at(a) + 1 : ti.line_at(b)])
    tickers = [(ln, m) for ln in body if (m := TICKER.match(ln.text.strip()))]
    if not tickers:
        return ParsedField.invalid("art9_table", body, "商品基本資料九的標的表讀不到彭博代碼")
    left = min(ln.x0 for ln, _ in tickers)
    names: list[list[Line]] = [[] for _ in tickers]
    for ln in body:
        if ln.x1 < left - 2 and not squash(ln.text).startswith(("交易所", "•", "‧")):
            same = [k for k, (t, _) in enumerate(tickers) if t.page == ln.page]
            if same:
                k = min(same, key=lambda j: abs((tickers[j][0].y0 + tickers[j][0].y1) - (ln.y0 + ln.y1)))
                names[k].append(ln)
    value = [
        ("".join(squash(x.text) for x in sorted(n, key=lambda x: (x.y0, x.x0))), f"{m[1]} {m[2]}")
        for n, (_, m) in zip(names, tickers, strict=True)
    ]
    return ParsedField.present("art9_table", value, [ln for ln, _ in tickers])


def _table_fields(fields: dict[str, ParsedField]) -> None:
    """價格表＋第九項標的表 → 標準欄位：標的、各標的價格、K／KI／KO %（表頭）。價格表列名須等於第九項同列名稱。"""
    table, art9 = fields["underlying_table"], fields["art9_table"]
    if table.ok and art9.ok:
        tickers = [t for _, t in art9.value]
        names9 = [n for n, _ in art9.value]
        ev = list(table.evidence) + list(art9.evidence)
        if [squash(n) for n in table.value.names] != names9:
            note = "價格表的標的名稱或列數和商品基本資料九的標的表不同，無法對到彭博代碼"
            for key in ("underlyings", "underlying_prices"):
                fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)
        else:
            fields["underlyings"] = ParsedField("underlyings", FieldStatus.PRESENT, tickers, list(art9.evidence))
            fields["underlying_prices"] = price_table.price_rows(table, tickers)
    else:
        bad = table if not table.ok else art9
        fields["underlyings"] = derived("underlyings", bad)
        fields["underlying_prices"] = derived("underlying_prices", bad)
    for key, col in (("strike_pct", "strike"), ("ko_pct", "ko")):
        fields[key] = derived(key, table, table.value.pcts[col] if table.ok else None)
    ki = fields["ki_type"]
    if ki.ok and ki.value == "none":
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence), note="說明書無 KI（沒有觸及下限事件）"
        )
    else:
        bad = next((p for p in (ki, table) if not p.ok), None)
        fields["ki_pct"] = derived("ki_pct", bad or table, None if bad else table.value.pcts["ki"])


# ---------------------------------------------------------------- 商品基本資料十二：日期；KO 排程


def _dates(fields: dict[str, ParsedField], art12: list[Line], put: Callable) -> None:
    ti = TextIndex(art12)
    put("tenor_months", art12, r"天期/年期[:：](\d+)個月", int)
    put("trade_date", art12, r"交易日/期初訂價日[:：]" + D, parse_date)
    put("issue_date", art12, r"發行日/交割日[:：]" + D, parse_date)
    put("final_valuation_date", art12, r"(?<![/末])期末訂價日[:：]" + D, parse_date)
    a = ti.text.find("期末交割日/到期日")
    if a < 0:
        fields["maturity_date"] = ParsedField.missing("maturity_date", "商品基本資料十二找不到「期末交割日/到期日」")
    else:
        stop = min(
            (p for p in (ti.text.find(x, a) for x in ("營業日：", "營業日:", "*", "以上各觀察日")) if p > a),
            default=len(ti.text),
        )
        found = [
            (parse_date(m[1]), ti.lines_for(a + m.start(), a + m.end()))
            for m in re.finditer(r"即現(?:預計為?)?" + D, ti.text[a:stop])
        ]
        fields["maturity_date"] = ParsedField.from_hits(
            "maturity_date", found, missing_note="「期末交割日/到期日」讀不到日期"
        )
    fields["ko_wording_art12"] = ko_wording("ko_wording_art12", ti)


SCHEDULE_WHERE = "商品基本資料八日期表"
# D 型配息觀察期間開始日 = 前一期結束日後的下一個一籃子預定交易日，第 1 期 = 發行日後的下一個一籃子預定交易日
SCHEDULE_START = standard_fields.PeriodStart("next_trading_day", "after_issue")


def _schedule_table(value: dict) -> standard_fields.DateTable:
    """日期表：D 為配息觀察期間表（觀察日 = 結束日）；P 為配息觀察日表，沒有開始日欄。"""
    if value["kind"] == "D":
        return standard_fields.DateTable(SCHEDULE_WHERE, schedule.rows_of(value["rows"], "end"), SCHEDULE_START)
    return standard_fields.DateTable(SCHEDULE_WHERE, schedule.rows_of(value["rows"], "observation"))


def _schedule(fields: dict[str, ParsedField]) -> None:
    """日期表、KO 觀察方式、第一個可提前出場期與提前出場排程（`parsers/schedule.py`，範本規格 §4.1）：八與十二的寫法須相同、
    須和配息表種類一致。D：比價日為第 k 期與末期的配息觀察期間結束日；P：第 k 期起每期的配息觀察日（含最後一期）。"""
    w8, w12, table = fields["ko_wording_art8"], fields["ko_wording_art12"], fields["coupon_table"]
    fields["schedule_table"] = schedule.table_field(table, _schedule_table)
    bad = next((p for p in (w8, w12, table) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        return
    ev = list(w8.evidence) + list(w12.evidence) + list(table.evidence)
    note = ""
    if w8.value != w12.value:
        note = "商品基本資料八與十二的提前出場觀察日寫法不同"
    elif w8.value[0] != table.value["kind"]:
        note = "提前出場觀察日的寫法和配息表的種類不一致（每日型配息表要有配息觀察期間開始日、結束日）"
    if note:
        fields.update(schedule.invalid(ev, note))
        return
    kind, k = w8.value
    obs = ParsedField("ko_observation", FieldStatus.PRESENT, kind, list(w8.evidence))
    first = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    fields.update(schedule.autocall_schedule(obs, first, fields["schedule_table"]))


# ---------------------------------------------------------------- 商品基本資料十五：情境設定參數


def _article15(fields: dict[str, ParsedField], art15: list[Line]) -> None:
    ti = TextIndex(art15)
    a = ti.text.find("設定參數")
    b = ti.text.find("情境分析一", a)
    params = art15[ti.line_at(a) : ti.line_at(b)] if a >= 0 and b > a else []
    if not params:
        for name in ("scenario_tenor", "scenario_denomination", "scenario_table", "scenario_monthly_pct"):
            fields[name] = ParsedField.missing(name, "商品基本資料十五找不到「設定參數」到「情境分析一」")
        for name in ("scenario_denomination_currency", "scenario_investment_currency"):
            fields[name] = ParsedField.missing(name, "商品基本資料十五找不到「設定參數」")
        fields["scenario_issue_price_pct"] = ParsedField.missing("scenario_issue_price_pct", "找不到「設定參數」")
        fields["scenario_investment"] = ParsedField.missing("scenario_investment", "找不到「設定參數」")
        fields["scenario_coupon_pa_pct"] = ParsedField.missing("scenario_coupon_pa_pct", "找不到「設定參數」")
        for name in ("scenario_variable_coupon_pct", "scenario_variable_coupon_pa_pct", "scenario_variable_periods"):
            fields[name] = ParsedField.missing(name, "找不到「設定參數」")
        return
    pt = TextIndex(params)
    fields["scenario_tenor"] = capture("scenario_tenor", pt, r"天期[:：](\d+)個月", int, whole_match=True)
    denom = rf"每單位面額[:：]{AMOUNT}/"
    fields["scenario_denomination"] = capture_group(params, denom, 1, "scenario_denomination", number)
    fields["scenario_denomination_currency"] = capture_group(params, denom, 2, "scenario_denomination_currency")
    fields["scenario_issue_price_pct"] = capture_group(
        params, r"發行價格[:：]" + PCT + "/", 1, "scenario_issue_price_pct", Decimal
    )
    invest = rf"每單位面額初始投資金額[:：]{AMOUNT}"
    fields["scenario_investment"] = capture_group(params, invest, 1, "scenario_investment", number)
    fields["scenario_investment_currency"] = capture_group(params, invest, 2, "scenario_investment_currency")
    coupon = list(pt.finditer(r"(?:每月)?固定配息率[:：]" + PCT + r"(?:。?（年化配息率(?:為)?" + PCT + r"）)?"))
    if len(coupon) == 1:
        m = coupon[0]
        lns = pt.lines_for(m.start(), m.end())
        fields["scenario_monthly_pct"] = ParsedField.present("scenario_monthly_pct", Decimal(m[1]), lns)
        fields["scenario_coupon_pa_pct"] = (
            ParsedField.present("scenario_coupon_pa_pct", Decimal(m[2]), lns)
            if m[2]
            else standard_fields.absent("scenario_coupon_pa_pct", "情境設定參數的年化配息率")
        )
    else:
        note = "情境設定參數找不到或有多處「固定配息率：x%」"
        fields["scenario_monthly_pct"] = ParsedField.missing("scenario_monthly_pct", note)
        fields["scenario_coupon_pa_pct"] = ParsedField.missing("scenario_coupon_pa_pct", note)
    _variable_coupon(fields, params, "scenario_")
    fields["scenario_table"] = price_table.read(
        "scenario_table", table_region(params, r"設定參數[:：]?", "固定配息率"), SCENARIO_TABLE
    )


def _assumed(fields: dict[str, ParsedField]) -> list[ScenarioParam]:
    """十五設定參數交給共用情境規則的出處（Issue #294）：天期、配息觀察次數、每單位面額、初始投資金額、發行價格、固定配息率
    與年化配息率；KO 每日型另有變動配息率與它的年化配息率。範本沒有的（例：沒寫年化配息率）不列。"""
    where = "商品基本資料十五設定參數"
    observation, table = fields["ko_observation"], fields["coupon_table"]
    daily = (observation.ok and observation.value == "D") or (table.ok and table.value["kind"] == "D")
    items = [
        ("scenario_tenor", where, "scenario_tenor"),
        ("scenario_coupon_periods", f"{where}「每月（共N個）」的", "scenario_coupon_count"),
        ("scenario_denomination", where, "scenario_denomination"),
        ("scenario_initial_investment", f"{where}每單位面額", "scenario_investment"),
        ("scenario_issue_price", where, "scenario_issue_price_pct"),
        ("scenario_monthly_coupon", f"{where}固定", "scenario_monthly_pct"),
        ("scenario_coupon_pa", f"{where}固定配息率的", "scenario_coupon_pa_pct"),
    ]
    if daily:
        items += [
            ("scenario_monthly_coupon", f"{where}變動", "scenario_variable_coupon_pct"),
            ("scenario_coupon_pa", f"{where}變動配息率的", "scenario_variable_coupon_pa_pct"),
        ]
    return [
        ScenarioParam(code, place, fields[name])
        for code, place, name in items
        if fields[name].status != FieldStatus.NOT_APPLICABLE
    ]


# 十五情境算式代入的值 → 共用情境規則的出處代號
SCENARIO_MENTIONS = {
    "scenario_denomination_mentions": "scenario_denomination",
    "scenario_coupon_mentions": "scenario_monthly_coupon",
}


def _scenarios(fields: dict[str, ParsedField], art15: list[Line]) -> list[Occurrence]:
    """十五重複寫出的值（Issue #320）：設定參數「配息觀察日／期間：每月（共N個）」、「提前出場觀察日」（有這句才核）、
    情境分析一～四算式代入的每單位面額與配息率；設定參數與算式代入的值交成共用情境規則的 `scenario_params`（同一個值
    一筆，Issue #294）。回傳各情境金額旁的幣別出處（併入 `currency_others`）。

    情境的金額寫到小數 2 位（或「價值為0X」），股價寫到小數 4 位：小數 4 位的股價跟著標的走（UB3 澳幣連日股、UB6 日圓
    連美股），不列入；期末匯率、股數、報酬率不讀。"""
    ti = TextIndex(art15)
    fields["scenario_coupon_count"] = capture(
        "scenario_coupon_count", ti, r"配息觀察(?:日|期間)[:：]每月（共(\d+)個）", int, whole_match=True
    )
    ko = ko_wording("ko_wording_art15", ti)
    if ko.status == FieldStatus.MISSING and re.search(r"提前出場觀察日[:：]?(?:每日觀察，)?為自", ti.text) is None:
        ko = standard_fields.absent("ko_wording_art15", "十五的提前出場觀察日")  # 依型態可有可無：沒有這句不核對
    fields["ko_wording_art15"] = ko
    marks = [(m.start(), m[1]) for m in ti.finditer(r"情境分析([一二三四五])[:：]")]
    end = ti.text.find("以上情境分析相關之免責聲明", marks[0][0]) if marks else -1
    where = "商品基本資料十五情境算式代入的"
    if not marks:
        note = "商品基本資料十五找不到「情境分析一：」"
        fields["scenario_params"] = standard_fields.occurrences(
            "scenario_params",
            [
                *_assumed(fields),
                *(ScenarioParam(c, where, ParsedField.missing(c, note)) for c in SCENARIO_MENTIONS.values()),
            ],
        )
        return [
            Occurrence(
                "scenario_currency",
                "情境金額幣別",
                "商品基本資料十五情境分析",
                ParsedField.missing("scenario_currency", note),
            )
        ]
    end = len(ti.text) if end < 0 else end
    lo = marks[0][0]
    params = _assumed(fields)
    for name, patterns, convert in (
        # 算式第一個代入值（`=X…`、`=（X/`）與減去的初始投資金額（`-X=`）都是每單位面額
        (
            "scenario_denomination_mentions",
            (rf"=（?([\d,]+\.\d{{2}}){CCY}[xX×/]", rf"-([\d,]+\.\d{{2}}){CCY}="),
            number,
        ),
        ("scenario_coupon_mentions", (r"[xX×](\d+\.\d+)%[xX×]",), Decimal),  # 配息率有小數；100% 等整數 % 不算
    ):
        hits = sorted(
            [
                (m.start(), convert(m[1]), ti.lines_for(m.start(), m.end()))
                for p in patterns
                for m in ti.finditer(p)
                if lo <= m.start() < end
            ],
            key=lambda h: h[0],
        )
        code = SCENARIO_MENTIONS[name]
        if not hits:
            params.append(ScenarioParam(code, where, ParsedField.missing(code, "商品基本資料十五情境分析的算式讀不到")))
        values: dict = {}  # 同一個值一筆（證據為每一處）
        for _, v, lns in hits:
            values.setdefault(v, []).extend(lns)
        params += [ScenarioParam(code, where, ParsedField.present(code, v, lns)) for v, lns in values.items()]
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    out = []
    for k, (start, zh) in enumerate(marks):
        stop = marks[k + 1][0] if k + 1 < len(marks) else end
        found = money.amounts(ti, SCENARIO_AMOUNT, currency_group=2, skip=lambda _: False, start=start, end=stop)
        out.append(
            money.scenario_occurrence(
                f"scenario{k + 1}_currency",
                f"情境分析{zh}金額幣別",
                f"商品基本資料十五情境分析{zh}",
                found,
                ti.lines_for(start, start + 1),
            )
        )
    return out


# ---------------------------------------------------------------- 相關機構事業概況、一般交易事項


def _organizations(fields: dict[str, ParsedField], doc: UbsDocument) -> None:
    raw = RawText(doc.section("相關機構事業概況"))
    fields["issuer_full_ch2"] = raw.capture(
        "issuer_full_ch2", r"發行機構簡介\s*[:：]\s*[•‧]?\s*事業名稱\s*[:：]\s*(.+?)\s*。"
    )
    fields["issuer_name_ch2"] = derived_name(fields["issuer_full_ch2"], "issuer_name_ch2")
    trustee = (
        r"受託機構簡介\s*[:：]\s*[•‧]?\s*事業名稱\s*[:：]\s*(.+?)\s*，\s*地址\s*(?:[:：]|為)\s*(.+?)\s*。\s*[•‧]?\s*設立日期"
        r".*?負責人姓名\s*[:：]\s*(.+?)\s*。"
    )
    fields["distributor_name_ch2"] = raw.capture("distributor_name_ch2", trustee)
    fields["distributor_address_ch2"] = raw.capture("distributor_address_ch2", trustee, without_place_note, group=2)
    fields["chairman"] = raw.capture("chairman", trustee, group=3)


def _general(fields: dict[str, ParsedField], doc: UbsDocument, put: Callable) -> None:
    items = doc.items(doc.section("一般交易事項"))

    def item(n: int) -> list[Line]:
        return items.get(n, [])

    put("subscription_start", item(1), r"商品申購開始受理日[:：]" + D, parse_date)
    fees = item(2)  # 表後的「附註：通路服務費…」不算費用項目
    fields.update(fee_rates(fees[: next((i for i, ln in enumerate(fees) if squash(ln.text).startswith("附註")), None)]))
    for name, lns, pattern in (
        ("denomination_ch4", item(4), rf"每單位面額[:：]{AMOUNT}"),
        ("subscription_amount_ch5", item(5), rf"申購價金之計算[:：]{AMOUNT}"),
        ("minimum_subscription", item(4), rf"最低申購金額[:：]{AMOUNT}"),
        ("minimum_additional", item(4), rf"最低累加申購金額[:：]{AMOUNT}"),
        ("minimum_redemption", item(8), rf"最低投資人提前贖回(?:單位數|金額)[:：]{AMOUNT}"),
        ("redemption_increment", item(8), rf"最低累加投資人提前贖回(?:單位數|金額)[:：]{AMOUNT}"),
    ):
        fields[name] = capture_group(lns, pattern, 1, name, number)
        fields[f"{name}_currency"] = capture_group(lns, pattern, 2, f"{name}_currency")
    contact = RawText(item(16))
    fields["distributor_phone_ch4"] = contact.capture(
        "distributor_phone_ch4", r"受託機構聯絡方式\s*[:：].*?電話\s*[:：]?\s*([+\d][\d\-() ]*\d)"
    )
    fields.update(standard_fields.absent_all(standard_fields.DISTRIBUTOR_OTHERS, "受託機構的其他出處"))


# ---------------------------------------------------------------- 出處清單


def _occurrences(fields: dict[str, ParsedField]) -> None:
    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_trade", "最低交易金額", "商品基本資料八最低交易金額", fields["minimum_trade"]),
            occ("minimum_subscription", "最低申購金額", "一般交易事項四最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "一般交易事項四最低累加申購金額", fields["minimum_additional"]),
            occ(
                "minimum_redemption",
                "最低贖回金額",
                "一般交易事項八最低投資人提前贖回單位數",
                fields["minimum_redemption"],
            ),
            occ(
                "redemption_increment",
                "最低累加贖回金額",
                "一般交易事項八最低累加投資人提前贖回單位數",
                fields["redemption_increment"],
            ),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [occ("subscription_start", "開始受理申購日", "一般交易事項一商品申購開始受理日", fields["subscription_start"])],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "第 1 頁本產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.absent("issue_price_others", "發行價格的其他出處")
    currencies = (  # 金額旁的幣別字（Issue #170）：價格表幣別字（標的報價幣別）、到期交割句的股價與情境算式不列入
        ("issue_price_currency", "發行價格金額幣別", "商品基本資料八發行價格"),
        ("denomination_currency", "每單位面額幣別", "商品基本資料八每單位面額"),
        ("minimum_trade_currency", "最低交易金額幣別", "商品基本資料八最低交易金額"),
        ("redemption_currency", "剩餘金額幣別", "商品基本資料八到期日給付「以X支付剩餘金額」"),
        ("denomination_ch4_currency", "每單位面額幣別", "一般交易事項四每單位面額"),
        ("minimum_subscription_currency", "最低申購金額幣別", "一般交易事項四最低申購金額"),
        ("subscription_amount_ch5_currency", "申購價金幣別", "一般交易事項五申購價金之計算"),
        ("minimum_additional_currency", "累加申購金額幣別", "一般交易事項四最低累加申購金額"),
        ("minimum_redemption_currency", "最低贖回金額幣別", "一般交易事項八最低投資人提前贖回單位數"),
        ("redemption_increment_currency", "累加贖回金額幣別", "一般交易事項八最低累加投資人提前贖回單位數"),
        ("scenario_denomination_currency", "情境每單位面額幣別", "商品基本資料十五設定參數每單位面額"),
        ("scenario_investment_currency", "情境初始投資金額幣別", "商品基本資料十五設定參數初始投資金額"),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others",
        [  # 發行價格寫「每單位面額之100%」時沒有金額，不是幣別出處
            occ(key, label, where, fields[key])
            for key, label, where in currencies
            if fields[key].status != FieldStatus.NOT_APPLICABLE
        ],
    )
