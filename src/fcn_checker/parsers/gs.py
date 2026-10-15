"""GS 中文產品說明書（docs/templates/gs-zh-product-description.md）：章＋條號＋子項＋標籤定位，保留原文證據。

沒有獨立封面：第 1 頁「中文產品說明書」「受託機構商品代號為…」後是編號項目 1.～13.，第 2 頁末是刊印日期。章名
「第一章:商品基本資料」～「第六章:定義」（半形冒號、六章，在左邊界）；每頁右上角另有重複的章名頁首、頁底置中
「第- N -頁，共M頁」，兩者都不算內文。條號「N.」與標題同一行（x≈72），子項「(n)」縮排。數字、日期與句型在去空白的
全文索引上讀；名稱、地址等要保留空白的值在原文合併文字（`common.RawText`）上讀。
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
from . import money, price_table
from .common import Mention, RawText, capture_group, dec, derived, fee_rates, number
from .layout import Document, LayoutSpec, TextIndex, capture, parse_date

TEMPLATE_ID = "gs-zh-pd"
PARSER_VERSION = "2"  # 2：價格表改由 parsers/price_table.py 讀（Issue #287）
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
    6: ("六", "定義"),
}
# 條號「N.」x≈72；子項「(n)」x≈86
LAYOUT = LayoutSpec(
    r"第{zh}章:{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    80,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    100,
    CHAPTER_NAMES,
)
ISSUER_ZH = "英商高盛國際公司"
ISSUER_EN = "Goldman Sachs International"
ISSUER = f"{ISSUER_ZH}（{ISSUER_EN}）"
NAME_EN_MARK = f"Autocallable Fixed Coupon Equity Linked Notes by {ISSUER_EN}"
ARTICLES = 36  # 第一章條數（有存託憑證標的時另有第 37 項）
COVER_ITEMS = 13  # 第 1 頁編號項目
PAGE_MARK = re.compile(r"^第-(\d+)-頁，共(\d+)頁$")  # 頁底頁碼（去空白後；投資人須知同寫法）
RUNNING_HEADER = re.compile(r"^第[一二三四五六]章:\S+$")
RUNNING_HEADER_X = 300  # 右上角章名頁首的 x 下限（章的開頭在左邊界 x≈72）
D = r"(?:西元)?(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
OPT_I = r"(?:\(i\))?"  # 多標的寫「(i)」，單一標的沒有
PRICE = r"\d[\d,]*\.\d{4}"  # 價格表價格（4 位小數）
TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})\s+(?:Equity|EQUITY)(?![A-Za-z])")
# 第 16 項 KO 觀察（範本規格 §4.1）：只有每日觀察；k = 第一個可提前出場期
KO_DAILY = r"「提前出場觀察日」指自第(\d+)個觀察期間結束日開始（含）至評價日結束（不含）之期間內"
# 第 16 項記憶式（範本規格 §4.2）
EVENT_MEMORY = "各連結標的(i)至少發生過一次記憶提前出場事件(i)，則視為已發生「自動提前出場事件」"
MEMORY_EVENT = "「記憶提前出場事件(i)」:就連結標的(i)而言"
EVENT_SINGLE = "如於一提前出場觀察日之參考價格等於或高於提前出場價格，則視為已發生「自動提前出場事件」"
# 第 15 項 KI（範本規格 §4.4）
KI_EVENT = rf"「下限觸發事件」:?指如(?:任一)?連結標的{OPT_I}於其?任一下限觸發觀察日之參考價格{OPT_I}低於其?對應之下限觸發價格{OPT_I}"
KI_DAILY = "「下限觸發觀察期間」指自交易日開始（不含）至評價日結束（含）之期間"
KI_AT_MATURITY = rf"最終參考價格{OPT_I}等於或高於其?下限觸發價格{OPT_I}時"
NO_KI = rf"最終參考價格{OPT_I}等於或高於其?執行價格{OPT_I}時"
# 第 15 項期初定價（範本規格 §4.7）
INITIAL_SENTENCE = r"(?<!「)初始參考價格(?:\(i\))?(?:就各連結標的\(i\)而言，)?為(.{1,80}?)。"
INITIAL_OPEN = r"(?:該連結標的(?:\(i\))?)?於交易日經參考交易所(?:\(i\))?官方決定及公布之(?:連結標的之)?開盤價格"
INITIAL_VWAP = r"(?:該連結標的(?:\(i\))?)?於交易日經發行機構以誠信及商業上合理方式決定之成交量加權平均價格"
INITIAL_REFERENCE = r"(?:該連結標的(?:\(i\))?)?於交易日之參考價格(?:\(i\))?"
REFERENCE_CLOSE = (
    rf"「參考價格{OPT_I}」(?:就各連結標的\(i\)而言，)?指經(?:相關)?參考交易所官方決定及公布之(?:該)?連結標的收盤價格"
)
# 價格表（說明書第一章第 15 項、投資人須知二.1.(10)）：每檔一列、以左欄列號切列，價格後可能接報價幣別字
PRICE_TABLE = price_table.TableSpec(
    where="第一章第 15 項價格表",
    initial="初始參考價格",
    columns=(
        price_table.Column("strike", "執行價格"),
        price_table.Column("ko", "提前出場價格"),
        price_table.Column("ki", "下限觸發價格", required=False),
    ),
    anchor="number",
    pct=r"\(初始參考價格(?:\(i\))?之(\d+(?:\.\d+)?)%\)",
    pct_label="(初始參考價格之X%)",
    code="彭博代碼",
)
# 第 14 項（投資人須知二. 2.）配息率：第 1 組年利率、第 2 組每月配息率
COUPON = r"「配息率」就每個觀察期間而言，指年利率" + PCT + r"/12（約為" + PCT + r"）"


def _is_noise(ln: Line) -> bool:
    text = ln.text.strip()
    return bool(PAGE_MARK.match(squash(text))) or (ln.x0 >= RUNNING_HEADER_X and bool(RUNNING_HEADER.match(text)))


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁底頁碼與右上角重複的章名頁首不算內文（頁碼另由投資人須知的 `page_totals` 讀）。"""
    return Document([ln for ln in lines if not _is_noise(ln)], LAYOUT)


def cover_items(cover: Sequence[Line]) -> dict[int, RawText]:
    """第 1 頁編號項目 1.～13.（左邊界、依序）各自的原文行；第 13 項到刊印日期那一行之前。"""
    starts: list[int] = []
    for i, ln in enumerate(cover):
        m = LAYOUT.article_re.match(ln.text)
        if m and ln.x0 < LAYOUT.article_max_x and int(m[1]) == len(starts) + 1:
            starts.append(i)
    end = next((i for i, ln in enumerate(cover) if squash(ln.text).startswith("本中文產品說明書刊印日期")), len(cover))
    out = {}
    for k, i in enumerate(starts):
        stop = starts[k + 1] if k + 1 < len(starts) else end
        out[k + 1] = RawText(cover[i:stop])
    return out


def _item_value(items: dict[int, RawText], n: int, label: str) -> str:
    raw = items.get(n)
    if raw is None:
        return ""
    m = re.match(rf"\d+\.\s*{re.escape(label)}\s*[:：]\s*(.*)$", raw.text, re.S)
    return m[1] if m else ""


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 GS 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    head = [squash(x.text) for x in cover[:2]]
    zh = squash(_item_value(items, 1, "商品中文名稱"))
    en = squash(_item_value(items, 2, "商品英文名稱"))
    issuer = squash(_item_value(items, 7, "發行機構之名稱、電話及地址"))
    arts = doc.articles(1)
    failed = []
    for test, message in (
        (
            len(head) == 2 and head[0] == "中文產品說明書" and head[1].startswith("受託機構商品代號為"),
            "前兩行不是「中文產品說明書」「受託機構商品代號為…」",
        ),
        (ISSUER_ZH in zh and "（TDCC商品代號為" in zh, f"第 1 項商品中文名稱沒有「{ISSUER_ZH}」與「（TDCC商品代號為」"),
        (squash(NAME_EN_MARK) in en, f"第 2 項商品英文名稱沒有「{NAME_EN_MARK}」"),
        (issuer.startswith(squash(ISSUER)), f"第 7 項發行機構不是 {ISSUER}"),
        (all(n in doc.chapters for n in CHAPTER_NAMES), "章名不是「第一章:商品基本資料」…「第六章:定義」或六章不完整"),
        (set(range(1, ARTICLES + 1)) <= set(arts), f"第一章條號 1–{ARTICLES} 不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class GsTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    # 年利率主來源（第 14 項）以外、須等於年利率的出處：第 15 項(2) 最低保證收益率、第 17 項情境假設
    coupon_mentions: list[Mention]

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def read(lines: Sequence[Line]) -> GsTermSheet:
    """讀出標準欄位與 GS 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    cover = doc.before_chapter1()
    fields: dict[str, ParsedField] = {}

    def article(n: int, chapter: int = 1) -> list[Line]:
        sp = (arts if chapter == 1 else doc.articles(chapter)).get(n)
        return doc.span_lines(sp)

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    _cover(fields, cover)

    # 第一章
    put("name_art1_zh", article(1), r"1\.本商品名稱[:：](.+?)（TDCC商品代號為")
    put("product_code_art1", article(1), r"1\.本商品名稱[:：].+?（TDCC商品代號為(\d{12})")
    put("isin_art1", article(1), r"1\.本商品名稱[:：].+?（TDCC商品代號為\d{12}，ISIN[:：]([A-Z]{2}[A-Z0-9]{10})\)")
    # 第一章第 3 項寫全名：共用規則的 issuer_name_ch1 是「只寫中文的出處」（比審查標準全名括號前的中文），交中文部分；
    # 全名另由 GS 規則和第 1 頁第 7 項比（英文名稱一字一行，忽略空白）
    full = RawText(article(3)).capture("issuer_name_ch1_full", r"本商品發行機構為\s*(.+?)。")
    fields["issuer_name_ch1_full"] = full
    fields["issuer_name_ch1"] = derived("issuer_name_ch1", full, re.split(r"[（(]", full.value)[0] if full.ok else None)
    put("currency_art5", article(5), rf"5\.計價幣別[:：]({ZH_CCY})（[A-Z]{{3}}）")
    put("currency_art5_iso", article(5), rf"5\.計價幣別[:：]{ZH_CCY}（([A-Z]{{3}})）")
    denom = rf"6\.每單位面額[:：]([\d,]+)({CCY})"
    put("denomination", article(6), denom, number)
    fields["denomination_currency"] = capture_group(article(6), denom, 2, "denomination_currency")
    fields["art10_tickers"] = _tickers(article(10))

    art13 = article(13)
    put("tenor_months", art13, r"\(1\)年期[:：].*?則為(\d+)個月", int)
    put("trade_date", art13, r"\(2\)交易日[:：]" + D, parse_date)
    put("issue_date", art13, r"\(3\)發行日[:：]" + D, parse_date)
    put("final_valuation_date", art13, r"\(4\)評價日[:：]" + D, parse_date)
    put("maturity_date", art13, r"\(5\)到期日[:：]評價日後的第\d+個營業日，現預定為" + D, parse_date)
    fields["date_table"] = date_table(art13)

    art14 = article(14)
    put("coupon_pa_pct", art14, COUPON, Decimal)
    fields["monthly_coupon_pct"] = capture_group(art14, COUPON, 2, "monthly_coupon_pct", Decimal)

    _article15(fields, article(15))
    _article16(fields, article(16))
    _schedule(fields)
    scenario_coupon = _article17(fields, article(17))

    _chapter2(fields, doc)
    _chapter4(fields, doc, put)
    _occurrences(fields)
    mentions = [
        Mention.of_field("第一章第 15 項(2) 最低保證收益率", fields["minimum_return_pct"]),
        Mention.of_field("第一章第 17 項情境假設配息率", scenario_coupon),
    ]
    return GsTermSheet(fields, TextIndex(doc.lines), doc, mentions)


def _cover(fields: dict[str, ParsedField], cover: list[Line]) -> None:
    """第 1 頁：受託機構商品代號、項目 1.～13.、第 2 頁末刊印日期。"""
    ti = TextIndex(cover)
    fields["product_code"] = capture("product_code", ti, r"^中文產品說明書受託機構商品代號為(\d{12})", whole_match=True)
    zh = r"1\.商品中文名稱[:：](.+?)（TDCC商品代號為(\d{12})，ISIN[:：]([A-Z]{2}[A-Z0-9]{10})\)"
    fields["name_zh"] = capture("name_zh", ti, zh, whole_match=True)
    fields["product_code_zh"] = capture_group(cover, zh, 2, "product_code_zh")
    fields["isin"] = capture_group(cover, zh, 3, "isin")
    raw = RawText(cover)
    en = r"2\.\s*商品英文名稱\s*[:：]\s*(.+?)\s*\(TDCC Product Code:\s*(\d{12})\s*，\s*ISIN:\s*([A-Z]{2}[A-Z0-9]{10})\s*\)"
    fields["name_en"] = raw.capture("name_en", en)
    fields["product_code_en"] = raw.capture("product_code_en", en, group=2)
    fields["isin_en"] = raw.capture("isin_en", en, group=3)
    fields["product_type"] = capture("product_type", ti, r"3\.商品種類[:：](.+?)4\.發行機構註冊地", whole_match=True)
    fields["currency_zh"] = capture("currency_zh", ti, rf"6\.計價幣別[:：]({ZH_CCY})7\.發行機構", whole_match=True)
    fields["issuer_name_cover"] = raw.capture(
        "issuer_name_cover", r"7\.\s*發行機構之名稱、電話及地址\s*[:：]\s*(.+?)\s*，\s*電話"
    )
    dist = (
        r"10\.\s*受託機構之名稱、電話及地址\s*[:：]\s*(.+?)\s*，\s*電話\s*[:：]\s*(.+?)\s*，\s*地址\s*[:：]\s*(.+?)"
        r"\s*(?:\(營業活動所在地\))?\s*11\."
    )
    fields["distributor_name_cover"] = raw.capture("distributor_name_cover", dist)
    fields["distributor_phone_cover"] = raw.capture("distributor_phone_cover", dist, group=2)
    fields["distributor_address_cover"] = raw.capture("distributor_address_cover", dist, group=3)
    approval = capture("approval_date", ti, r"審查通過之日期[:：]" + D, parse_date, whole_match=True)
    fields["approval_date"] = approval
    fields["print_date"] = capture("print_date", ti, r"本中文產品說明書刊印日期[:：]" + D, parse_date, whole_match=True)


def _tickers(art10: list[Line]) -> ParsedField:
    """第 10 項(1) 連結標的資產表的彭博代碼（依表上順序）。"""
    raw = RawText(art10)
    start, stop = re.search(r"\(1\)\s*連結標的資產", raw.text), re.search(r"\(2\)\s*相對權重", raw.text)
    a, b = (start.start(), stop.start()) if start and stop else (-1, -1)
    if a < 0 or b <= a:
        return ParsedField.missing("art10_tickers", "第 10 項找不到「連結標的資產」到「相對權重」")
    found = list(TICKER.finditer(raw.text, a, b))
    if not found:
        return ParsedField.invalid(
            "art10_tickers", raw.lines_for(a, b), "第 10 項讀不到彭博代碼（代號 交易所碼 Equity）"
        )
    return ParsedField.present("art10_tickers", [f"{m[1]} {m[2]}" for m in found], raw.lines_for(a, b))


# ---------------------------------------------------------------- 第 13 項觀察時程表


SCHEDULE_ROW = re.compile(r"(\d{1,2})" + r"(?:西元)?(\d{4}年\d{1,2}月\d{1,2}日)" * 3)


def date_table(art13: list[Line]) -> ParsedField:
    """觀察時程表：表頭「t 觀察期間起始日(含) 觀察期間結束日(含) 預定付息日」（標題可能寫「觀察時程表（西元年）」），每列期別＋三個日期。

    值：{"rows": [{"period", "start", "end", "payment"}]}。表格以去空白的文字讀（期別與年份相黏時以日期格式分開）。"""
    name = "date_table"
    ti = TextIndex(art13)
    head = re.search(r"觀察時程表(?:（西元年）)?t觀察期間起始日\(含\)觀察期間結束日\(含\)預定付息日", ti.text)
    b = ti.text.find("投資人應注意本中文產品說明書所述之付息日", head.end()) if head else -1
    if head is None or b < 0:
        return ParsedField.missing(name, "第 13 項找不到「觀察時程表」（表頭 t／觀察期間起始日／結束日／預定付息日）")
    a = head.start()
    body = ti.text[head.end() : b]
    rows, pos = [], 0
    for m in SCHEDULE_ROW.finditer(body):
        if m.start() != pos:
            break
        pos = m.end()
        dates = [parse_date(x) for x in m.groups()[1:]]
        if None in dates:
            return ParsedField.invalid(name, ti.lines_for(a, b), f"觀察時程表第 {m[1]} 列日期不合法")
        rows.append({"period": int(m[1]), "start": dates[0], "end": dates[1], "payment": dates[2]})
    lines = ti.lines_for(a, b)
    if pos != len(body) or not rows:
        return ParsedField.invalid(name, lines, "觀察時程表的列不是「期別＋三個日期」")
    if [r["period"] for r in rows] != list(range(1, len(rows) + 1)):
        return ParsedField.invalid(name, lines, "觀察時程表期數不是從 1 起連續")
    return ParsedField.present(name, {"rows": rows}, lines)


# ---------------------------------------------------------------- 第 15 項


def _article15(fields: dict[str, ParsedField], art15: list[Line]) -> None:
    """定義（執行價格／下限觸發價格之 %、初始參考價格）、KI 型態、連結標的價格表、最低保證收益率。"""
    ti = TextIndex(art15)
    for key, label in (("strike_def_pct", "執行價格"), ("ki_def_pct", "下限觸發價格")):
        fields[key] = capture(
            key, ti, rf"「{label}{OPT_I}」[:：]初始參考價格{OPT_I}之" + PCT, Decimal, whole_match=True
        )
    fields["ki_type"] = ki_type(ti, art15)
    fields["initial_pricing"] = initial_pricing(ti, art15)
    a = next((i for i, ln in enumerate(art15) if squash(ln.text) == "連結標的價格表"), None)
    b = next(
        (
            i
            for i, ln in enumerate(art15)
            if a is not None and i > a and squash(ln.text).startswith(("(2)", "連結標的匯率表"))
        ),
        None,
    )  # 人民幣商品價格表後接「連結標的匯率表」
    table = price_table.read(
        "underlying_table", art15[a + 1 : b] if a is not None and b is not None else [], PRICE_TABLE
    )
    fields["underlying_table"] = table
    _table_fields(fields, table)
    m = r"\(2\)最低保證收益率[:：]首(\d*)個觀察期間年利率" + PCT
    put_k = capture("minimum_return_periods", ti, m, lambda x: int(x) if x else 1, whole_match=True)
    fields["minimum_return_periods"] = put_k
    fields["minimum_return_pct"] = capture_group(art15, m, 2, "minimum_return_pct", Decimal)


def _table_fields(fields: dict[str, ParsedField], table: ParsedField) -> None:
    """價格表 → 標準欄位：標的、各標的價格、K／KO／KI %（K、KI 主來源是定義，表頭 % 由規則交叉驗證；KO 只在表頭）。"""
    fields["underlyings"] = underlyings_field(table)
    fields["underlying_prices"] = price_table.price_rows(table)
    fields["strike_pct"] = fields["strike_def_pct"]
    ki_t = fields["ki_type"]
    if ki_t.ok and ki_t.value == "none":
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki_t.evidence), note="說明書無 KI（沒有下限觸發價格）"
        )
    else:
        fields["ki_pct"] = fields["ki_def_pct"]
    if table.ok and "ko" in table.value.pcts:
        fields["ko_pct"] = ParsedField("ko_pct", FieldStatus.PRESENT, table.value.pcts["ko"], list(table.evidence))
    elif table.ok:
        fields["ko_pct"] = ParsedField.invalid(
            "ko_pct", list(table.value.header_lines), "價格表表頭讀不到提前出場價格的 %"
        )
    else:
        fields["ko_pct"] = derived("ko_pct", table)


def underlyings_field(table: ParsedField) -> ParsedField:
    """價格表 → 標準欄位 `underlyings`（彭博代號，依表上順序）。"""
    if not table.ok:
        return derived("underlyings", table)
    return ParsedField("underlyings", FieldStatus.PRESENT, list(table.value.tickers), list(table.evidence))


def ki_type(ti: TextIndex, lns: list[Line], where: str = "第一章第 15 項") -> ParsedField:
    """KI 型態（範本規格 §4.4）：下限觸發事件＋下限觸發觀察期間 → D；只在評價日比下限觸發價格 → AM；
    沒有「下限觸發」且到期比執行價格 → none（以 present「none」交出）。投資人須知句型同，名詞沒有「(i)」。"""
    name, text = "ki_type", ti.text
    event, daily = re.search(KI_EVENT, text), KI_DAILY in text
    at_maturity, plain = re.search(KI_AT_MATURITY, text), re.search(NO_KI, text)
    if event and daily:
        m = event
        return ParsedField.present(name, "D", ti.lines_for(m.start(), m.end()))
    if event or daily:
        return ParsedField.invalid(name, lns, f"{where}下限觸發事件與下限觸發觀察期間要同時出現")
    if at_maturity:
        return ParsedField.present(name, "AM", ti.lines_for(at_maturity.start(), at_maturity.end()))
    if "下限觸發" not in text and plain:
        return ParsedField.present(name, "none", ti.lines_for(plain.start(), plain.end()))
    return ParsedField.invalid(name, lns, f"{where}到期買回的下限觸發寫法不在範本規格內")


def initial_pricing(ti: TextIndex, lns: list[Line]) -> ParsedField:
    """期初定價（範本規格 §4.7）：「初始參考價格…為…。」一句 → open／vwap；「交易日之參考價格」且「參考價格」定義為
    收盤價格 → close（間接寫法）。值為參考條件表格式 [values.initial_pricing] 的對照值。"""
    name = "initial_pricing"
    found = list(ti.finditer(INITIAL_SENTENCE))
    if len(found) != 1:
        return ParsedField.invalid(name, lns, "找不到或有多句「初始參考價格…為…」，期初定價寫法不在範本規格內")
    m = found[0]
    ev = ti.lines_for(m.start(), m.end())
    body = m[1]
    if re.fullmatch(INITIAL_OPEN, body):
        return ParsedField.present(name, "open", ev)
    if re.fullmatch(INITIAL_VWAP, body):
        return ParsedField.present(name, "vwap", ev)
    close = re.search(REFERENCE_CLOSE, ti.text)
    if re.fullmatch(INITIAL_REFERENCE, body) and close:
        return ParsedField.present(name, "close", ev + ti.lines_for(close.start(), close.end()))
    return ParsedField.invalid(name, ev, f"期初定價寫法「{body}」不在範本規格內")


# ---------------------------------------------------------------- 第 16 項、KO 排程


def ko_terms(
    ti: TextIndex, lns: list[Line], where: str = "第一章第 16 項"
) -> tuple[ParsedField, ParsedField, ParsedField]:
    """KO 觀察（只有每日觀察，回傳起始期 k）、記憶式（多標的記憶式／單一標的兩種句型）與句型名稱（範本規格 §4.1、§4.2）。

    投資人須知寫在「二. 3. 買回價金之計算」，名詞沒有「(i)」（先移掉再交進來）。"""
    text = ti.text
    starts = list(ti.finditer(KO_DAILY))
    if len(starts) == 1:
        m = starts[0]
        first = ParsedField.present("first_callable_period", int(m[1]), ti.lines_for(m.start(), m.end()))
    else:
        first = ParsedField.invalid(
            "first_callable_period", lns, f"{where}「提前出場觀察日」的寫法缺漏、重複或不在範本規格內"
        )
    memory = EVENT_MEMORY in text and MEMORY_EVENT in text
    single = EVENT_SINGLE in text
    if memory == single:
        note = f"{where}自動提前出場事件與記憶提前出場事件的句型缺漏、重複或不在範本規格內"
        return first, ParsedField.invalid("ko_memory", lns, note), ParsedField.invalid("memory_style", lns, note)
    anchor = EVENT_MEMORY if memory else EVENT_SINGLE
    a = text.find(anchor)
    ev = ti.lines_for(a, a + len(anchor))
    style = "memory" if memory else "single"
    return first, ParsedField.present("ko_memory", memory, ev), ParsedField.present("memory_style", style, ev)


def _article16(fields: dict[str, ParsedField], art16: list[Line]) -> None:
    first, memory, style = ko_terms(TextIndex(art16), art16)
    fields["ko_first_period"] = first
    fields["ko_memory"], fields["memory_style"] = memory, style


def _schedule(fields: dict[str, ParsedField]) -> None:
    """KO 觀察方式（D）、第一個可提前出場期 k 與提前出場排程：k 須在觀察時程表期數內；比價日為第 k 期與末期的觀察期間結束日。"""
    first, table = fields["ko_first_period"], fields["date_table"]
    keys = ("ko_observation", "first_callable_period", "autocall_schedule")
    bad = next((p for p in (first, table) if not p.ok), None)
    if bad is not None:
        for key in keys:
            fields[key] = derived(key, bad)
        return
    rows, k = table.value["rows"], first.value
    ev = list(first.evidence) + list(table.evidence)
    n = len(rows)
    if not 1 <= k <= n:
        for key in keys:
            fields[key] = ParsedField(
                key, FieldStatus.INVALID, evidence=ev, note=f"第 {k} 個觀察期間不在觀察時程表（{n} 期）內"
            )
        return
    dates = {k: rows[k - 1]["end"], n: rows[-1]["end"]}
    fields["ko_observation"] = ParsedField("ko_observation", FieldStatus.PRESENT, "D", list(first.evidence))
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, k, list(first.evidence))
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule", FieldStatus.PRESENT, standard_fields.AutocallSchedule("D", k, n, dates), ev
    )


# ---------------------------------------------------------------- 第 17 項情境


def _article17(fields: dict[str, ParsedField], art17: list[Line]) -> ParsedField:
    """情境假設配息率、情境 1 的期別、情境 4 第 1 檔的初始參考價格／執行價格／K %（範本規格 §4.9）；回傳情境假設配息率。"""
    ti = TextIndex(art17)
    coupon = capture("scenario_coupon_pct", ti, r"\(2\)配息率[:：]年利率" + PCT, Decimal, whole_match=True)
    fields["scenario_units"] = capture("scenario_units", ti, r"×最低申購單位數（(\d+)單位）", int, whole_match=True)
    fields["scenario_first_period"] = capture(
        "scenario_first_period",
        ti,
        r"情境1[:：]本情境假設自動提前出場事件已於第一個適用之提前出場觀察日（且落在第(\d+)個觀察期間結束日）發生",
        int,
        whole_match=True,
    )
    p = r"(?:\(1\))?"
    scen4 = (
        rf"初始參考價格{p}[:：]({PRICE})[^\d（]*?最終參考價格{p}[:：]{PRICE}[^\d（]*?（初始參考價格{p}之{PCT}）"
        rf"執行價格{p}[:：]({PRICE})[^\d（]*?（初始參考價格{p}之{PCT}）"
    )
    found = list(ti.finditer(scen4))
    if len(found) == 1:
        m = found[0]
        value = {"initial": dec(m[1]), "strike": dec(m[3]), "strike_pct": Decimal(m[4])}
        fields["scenario_first_underlying"] = ParsedField.present(
            "scenario_first_underlying", value, ti.lines_for(m.start(), m.end())
        )
    else:
        fields["scenario_first_underlying"] = ParsedField.missing(
            "scenario_first_underlying", "情境 4 找不到或有多處「初始參考價格(1)…執行價格(1)…（初始參考價格(1)之X%）」"
        )
    return coupon


# ---------------------------------------------------------------- 第二章、第四章


def _chapter2(fields: dict[str, ParsedField], doc: Document) -> None:
    ch2 = doc.articles(2)
    raw1 = RawText(doc.span_lines(ch2.get(1)))
    fields["issuer_name_ch2"] = raw1.capture("issuer_name_ch2", r"\(1\)\s*事業名稱\s*[:：]\s*(.+?)\s*\(2\)\s*設立日期")
    raw3 = RawText(doc.span_lines(ch2.get(3)))
    trustee = (
        r"\(2\)\s*受託機構\s*\(i\)\s*事業名稱\s*[:：]\s*(.+?)\s*\(ii\)\s*設立日期.*?\(iii\)\s*營業所在地\s*[:：]\s*(.+?)"
        r"\s*\(iv\)\s*負責人\s*[:：]\s*(.+?)\s*\(3\)"
    )
    fields["distributor_name_ch2"] = raw3.capture("distributor_name_ch2", trustee)
    fields["distributor_address_ch2"] = raw3.capture("distributor_address_ch2", trustee, group=2)
    fields["chairman"] = raw3.capture("chairman", trustee, group=3)


def _chapter4(fields: dict[str, ParsedField], doc: Document, put: Callable) -> None:
    ch4 = doc.articles(4)

    def art(n: int) -> list[Line]:
        return doc.span_lines(ch4.get(n))

    put("subscription_start", art(1), r"\(1\)本商品開始受理申購日期[:：]" + D, parse_date)
    put("subscription_end", art(1), r"\(2\)本商品結束受理申購日期[:：]" + D, parse_date)
    fields.update(fee_rates(art(2)))
    subscribe = rf"最低申購為(\d+)單位且其後增加單位應為一單位之倍數。每一單位為([\d,]+)({CCY})。"
    put("minimum_subscription_units", art(4), subscribe, int)
    fields["unit_amount"] = capture_group(art(4), subscribe, 2, "unit_amount", number)
    fields["unit_amount_currency"] = capture_group(art(4), subscribe, 3, "unit_amount_currency")
    price = rf"申購價金之計算[:：]申購單位數×每單位面額（([\d,]+)({CCY})）×" + PCT
    fields["issue_price_pct"] = capture_group(art(5), price, 3, "issue_price_pct", Decimal)
    fields["subscription_price_currency"] = capture_group(art(5), price, 2, "subscription_price_currency")
    put("minimum_redemption_units", art(8), r"每筆交易之最低贖回單位數為(\d+)單位，後續為一單位之倍數", int)


# ---------------------------------------------------------------- 出處清單


def _times(units: ParsedField, amount: ParsedField, name: str, count: int | None = None) -> ParsedField:
    """單位數 × 每單位金額（最低申購、最低贖回寫單位數，換算成金額）；`count` 給定時用它代替讀出的單位數
    （「其後增加單位應為一單位之倍數」：增加單位是一單位，證據沿用同一句）。"""
    bad = next((p for p in (units, amount) if not p.ok), None)
    if bad is not None:
        return derived(name, bad)
    n = units.value if count is None else count
    return ParsedField(name, FieldStatus.PRESENT, n * amount.value, list(units.evidence))


def _occurrences(fields: dict[str, ParsedField]) -> None:
    occ = Occurrence
    unit, denom = fields["unit_amount"], fields["denomination"]
    subscribe, redeem = fields["minimum_subscription_units"], fields["minimum_redemption_units"]
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ(
                "minimum_subscription",
                "最低申購金額",
                "第四章第 4 項最低申購單位數 × 每一單位金額",
                _times(subscribe, unit, "minimum_subscription"),
            ),
            occ(
                "minimum_additional",
                "最低加購金額",
                "第四章第 4 項其後增加單位（一單位）",
                _times(subscribe, unit, "minimum_additional", count=1),
            ),
            occ(
                "minimum_redemption",
                "最低贖回金額",
                "第四章第 8 項最低贖回單位數 × 每單位面額",
                _times(redeem, denom, "minimum_redemption"),
            ),
            occ(
                "redemption_increment",
                "增加贖回單位",
                "第四章第 8 項後續贖回（一單位）",
                _times(redeem, denom, "redemption_increment", count=1),
            ),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ(
                "subscription_start",
                "開始受理申購日",
                "第四章第 1 項(1) 本商品開始受理申購日期",
                fields["subscription_start"],
            ),
            occ(
                "subscription_end",
                "結束受理申購日",
                "第四章第 1 項(2) 本商品結束受理申購日期",
                fields["subscription_end"],
            ),
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "第 2 頁本中文產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.absent(
        "issue_price_others", "發行價格的其他出處（只有第四章申購價金）"
    )
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")
    currencies = (  # 金額旁的幣別字（Issue #170）：價格表價格、情境算式金額不列入
        ("denomination_currency", "每單位面額幣別", "第一章第 6 項每單位面額"),
        ("unit_amount_currency", "每一單位金額幣別", "第四章第 4 項每一單位"),
        ("subscription_price_currency", "申購價金面額幣別", "第四章第 5 項申購價金之計算"),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [occ(key, label, where, fields[key]) for key, label, where in currencies]
    )
