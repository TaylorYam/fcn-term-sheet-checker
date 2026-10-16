"""CA（東方匯理）中文產品說明書（docs/templates/ca-zh-product-description.md）：章＋條號＋標籤定位，保留原文證據。

沒有獨立封面：第 1–2 頁是「● 標籤：值」清單（值可能跨行）、警語 1～12 與刊印日期。章名「第一章、商品基本資料」～
「第七章、股權連結商品的額外條款及條件」（全形頓號；第七章章名可能拆兩行，不用它定位）；條號「N.」（x≈35–41）。
每頁頁底「第N 頁，共M頁」（擷取時排在該頁第一行）不算內文。說明書有 CJK 相容字（見、若、例；投資人須知另有「年」），
一律先做 NFKC。K／KI／KO % 只在價格表表頭；價格表只有中文名稱，依列序對到第 10 項的彭博代碼。
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
from .common import RawText, capture_group, compat_normalized, derived, fee_rates, number
from .layout import Document, LayoutSpec, TextIndex, capture, join_wrapped_tickers, parse_date

TEMPLATE_ID = "ca-zh-pd"
# 2：價格表幣別字換行的那格仍歸到同一列（Issue #278）；3：價格表改由 parsers/price_table.py 讀（Issue #287）；
# 4：重複寫出處（情境、贖回 %、說明文字幣別、警語的受託機構名稱；Issue #319）；5：日期表標準欄位與共用排程推導（Issue #289）；
# 6：情境價格表與假設值交成共用情境規則的標準欄位（Issue #294）
PARSER_VERSION = "6"
# 第七章章名在部分文件拆成兩行（「第七章、股權連結商」／「品的額外條款及條件」），只用前六章定位；辨識另查第七章全文
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
    6: ("六", "通知"),
}
CHAPTER7 = "第七章、股權連結商品的額外條款及條件"
# 條號「N.」x≈35–41（第 19 項起寫成「19.次級市場」，點後沒有空白）；子項「(n)」
LAYOUT = LayoutSpec(
    r"第{zh}章、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    50,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    60,
    CHAPTER_NAMES,
)
ISSUER = "Crédit Agricole CIB Financial Solutions"
NAME_ZH_MARK = "計價固定配息自動提前出場結構型商品(無擔保)(不保本)"
NAME_EN_MARK = "Fixed Coupon Autocall Note"
ARTICLES = 25  # 第一章條數
PAGE = re.compile(r"^第\d+ ?頁，共\d+頁$")
PAGE_Y = 740  # 頁碼在頁底（y≈765）
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
ISIN = r"\(ISIN：([A-Z]{2}[A-Z0-9]{10})\)"
TICKER = re.compile(r"^([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})$")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CN_NUM.update({f"十{k}": 10 + v for k, v in list(CN_NUM.items())[:9]})
CN_NUM.update({"二十": 20, **{f"二十{k}": 20 + v for k, v in list(CN_NUM.items())[:4]}})
# 第 9 項 (b) KO（範本規格 §4.1、§4.2）
KO_DAILY = r"自動提前出場觀察日：自第(\d{1,2})個配息期結束日\(含\)至期末訂價日\(含\)的每一個預定交易日。"
KO_TABLE_HEAD = "i自動提前出場觀察日自動提前出場日"
KO_EVENT = r"自動提前出場事件：(.+?)則發生自動提前出場事件。"
MEMORY_PRIOR = "或任何先前的自動提前出場觀察日"
MEMORY_SAME = "無須於同一自動提前出場觀察日"
# 第 9 項 (2) 界限事件（範本規格 §4.4）
KI_AM = "界限事件：若於期末訂價日"
KI_D = "界限事件：若於界限事件觀察期間之任何預定交易日"
KI_D_PERIOD = "界限事件觀察期間：從期初訂價日(含)至期末訂價日(含)"
KI_MATURITY = "若未發生界限事件"
# 第 9 項 (b)、(c) 贖回金額「商品面額x N%」（Issue #319；投資人須知二、2. 同句型）
KO_REDEMPTION = r"「自動提前出場贖回金額」：[^「]*?商品面額x" + PCT
MATURITY_REDEMPTION = r"「到期贖回金額」：[^「]*?商品面額x" + PCT
# 說明文字的幣別字（Issue #319）：到期交付股票時「以X支付剩餘金額」、提前贖回「可能低於100%X投資本金」
REMAINDER_CURRENCY = rf"以({CCY})支付剩餘金額"
PRINCIPAL_CURRENCY = rf"可能低於\d+(?:\.\d+)?%({CCY})投資本金"
INTEREST_CURRENCY = rf"(?:受|當)({CCY})利率"  # 利率風險「受X利率變動所影響，例如當X利率上漲時…當X利率下跌時」
# 價格表（第 9 項 (2)、第 16 項情境）：第 16 項沒有列號，兩張表都以價格切列；表內沒有代碼（第 10 項的代碼依列序
# 對到）。欄序 K → KI → KO，價格後接報價幣別字（可能換行）
PRICE_TABLE = price_table.TableSpec(
    where="第 9 項價格表",
    initial="期初訂價",
    columns=(
        price_table.Column("strike", "執行價"),
        price_table.Column("ki", "下限價", required=False),
        price_table.Column("ko", "自動提前出場價"),
    ),
    anchor="price",
    pct=r"之(\d+(?:\.\d+)?)%",
    pct_label="(期初訂價之X% )",
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="第 16 項情境價格表")


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁底頁碼不算內文。"""
    body = [ln for ln in compat_normalized(lines) if not (ln.y0 >= PAGE_Y and PAGE.match(ln.text.strip()))]
    return Document(body, LAYOUT)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 CA 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    ti = TextIndex(cover)
    head = [squash(x.text) for x in cover[:2]]
    zh = re.search(r"●商品中文名稱：(.+?)\(ISIN：", ti.text)
    en = re.search(r"●商品英文名稱：(.+?)\(ISIN：", ti.text)
    issuer = squash(ISSUER)
    arts = doc.articles(1)
    rest = TextIndex(doc.lines[doc.chapters[6].start :]).text if 6 in doc.chapters else ""
    failed = []
    for test, message in (
        (
            len(head) == 2 and head[0] == "中文產品說明書" and head[1].startswith("●TDCC商品代號："),
            "前兩行不是「中文產品說明書」「● TDCC 商品代號：…」",
        ),
        (
            zh is not None and zh[1].startswith(issuer) and NAME_ZH_MARK in zh[1],
            f"封面商品中文名稱不是以「{ISSUER}」開頭或沒有「{NAME_ZH_MARK}」",
        ),
        (
            en is not None and en[1].startswith(issuer) and squash(NAME_EN_MARK) in en[1],
            f"封面商品英文名稱不是以「{ISSUER}」開頭或沒有「{NAME_EN_MARK}」",
        ),
        (
            all(
                x in ti.text
                for x in ("●發行機構名稱及註冊辦公地點", "●總代理人之名稱、電話及地址：", "●保證機構之名稱及地址：")
            ),
            "封面沒有「發行機構名稱及註冊辦公地點」「總代理人之名稱、電話及地址」「保證機構之名稱及地址」",
        ),
        (
            all(n in doc.chapters for n in CHAPTER_NAMES) and CHAPTER7 in rest,
            "章名不是「第一章、商品基本資料」…「第七章、股權連結商品的額外條款及條件」或七章不完整",
        ),
        (set(range(1, ARTICLES + 1)) <= set(arts), f"第一章條號 1–{ARTICLES} 不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class CaTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def read(lines: Sequence[Line]) -> CaTermSheet:
    """讀出標準欄位與 CA 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    fields: dict[str, ParsedField] = {}

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    _cover(fields, doc.before_chapter1(), put)

    # 第一章
    art1 = article(1)
    fields["name_art1_zh"] = RawText(art1).capture("name_art1_zh", r"1\.\s*商品名稱\s*：\s*(.+?)\s*\(ISIN")
    put("isin_art1", art1, r"1\.商品名稱：.+?" + ISIN)
    fields["issuer_name_ch1"] = RawText(article(3)).capture(
        "issuer_name_ch1", r"3\.\s*發行機構\s*：\s*(.+?)\s*。\s*保證機構名稱"
    )
    art5 = article(5)
    put("currency_art5", art5, rf"5\.計價幣別：({ZH_CCY})\([A-Z]{{3}}\)")
    put("currency_art5_iso", art5, rf"5\.計價幣別：{ZH_CCY}\(([A-Z]{{3}})\)")
    art6 = article(6)
    denom = rf"每單位\(張\)商品面額為([\d,]+)({CCY})\([A-Z]{{3}}\)。"
    put("denomination", art6, denom, number)
    fields["denomination_currency"] = capture_group(art6, denom, 2, "denomination_currency")
    issue = rf"發行價格：每單位\(張\)商品面額的{PCT}，即[\d,]+({CCY})\([A-Z]{{3}}\)"
    put("issue_price_pct", art6, issue, Decimal)
    fields["issue_price_currency"] = capture_group(art6, issue, 2, "issue_price_currency")

    art9 = article(9)
    _article9(fields, art9)
    fields["art10_tickers"] = art10_tickers(article(10))
    put("tenor_months", article(13), r"\(1\)商品年期：(\d+)個月", int)
    art13 = article(13)
    put("trade_date", art13, r"\(2\)期初訂價日：" + D, parse_date)
    put("issue_date", art13, r"\(3\)發行日：" + D, parse_date)
    put("final_valuation_date", art13, r"\(4\)期末訂價日：" + D, parse_date)
    put("maturity_date", art13, r"\(5\)到期日：" + D, parse_date)
    fields.update(guaranteed_coupon(article(15)))
    _table_fields(fields)
    _schedule(fields)
    _article16(fields, article(16))

    _chapter2(fields, doc)
    _chapter4(fields, doc, put)
    _occurrences(fields)
    _repeated(fields, doc, article)
    fields["initial_pricing"] = standard_fields.absent(
        "initial_pricing", "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP）"
    )
    return CaTermSheet(fields, TextIndex(doc.lines), doc)


def _cover(fields: dict[str, ParsedField], cover: list[Line], put: Callable) -> None:
    """第 1–2 頁「● 標籤：值」清單與刊印日期。"""
    ti = TextIndex(cover)
    raw = RawText(cover)
    put("product_code", cover, r"●TDCC商品代號：(\d{12})")
    put("isin", cover, r"●商品中文名稱：.+?" + ISIN)
    put("isin_en", cover, r"●商品英文名稱：.+?" + ISIN)
    fields["name_zh"] = raw.capture("name_zh", r"●\s*商品中文名稱\s*：\s*(.+?)\s*\(ISIN")
    fields["name_en"] = raw.capture("name_en", r"●\s*商品英文名稱\s*：\s*(.+?)\s*\(ISIN")
    put("product_type", cover, r"●商品種類：(.+?)●")
    put("currency_zh", cover, rf"●計價幣別：({ZH_CCY})\([A-Z]{{3}}\)●")
    put("currency_cover_iso", cover, rf"●計價幣別：{ZH_CCY}\(([A-Z]{{3}})\)●")
    fields["issuer_name_cover"] = raw.capture(
        "issuer_name_cover", r"●\s*發行機構名稱及註冊辦公地點\s*(.+?)\s*，\s*註冊辦公地點"
    )
    dist = r"●\s*受託或銷售機構之名稱、電話及地址\s*：\s*(.+?)\s*，\s*電話\s*：\s*(.+?)\s*，\s*地址\s*：\s*(.+?)\s*●"
    fields["distributor_name_cover"] = raw.capture("distributor_name_cover", dist)
    fields["distributor_phone_cover"] = raw.capture("distributor_phone_cover", dist, group=2)
    fields["distributor_address_cover"] = raw.capture("distributor_address_cover", dist, group=3)
    put("approval_date", cover, r"●受託或銷售機構審查通過之日期：" + D, parse_date)
    fields["print_date"] = capture("print_date", ti, r"●中文產品說明書刊印日期：" + D, parse_date, whole_match=True)


# ---------------------------------------------------------------- 第 9 項：配息表、KO、到期贖回、價格表


ROW = r"(\d{1,2})\|?"


def _rows(text: str, pattern: str) -> list[tuple]:
    """表格的列；期別須從 1 起連續，遇到不連續就停（表後的文字不算列）。"""
    out = []
    for m in re.finditer(pattern, text):
        if int(m[1]) != len(out) + 1:
            break
        out.append(m.groups())
    return out


def _article9(fields: dict[str, ParsedField], art9: list[Line]) -> None:
    plain = TextIndex(art9)  # 贖回 %、(a) 舉例的月配息率（Issue #319）
    for name, pattern in (
        ("ko_redemption_pct", KO_REDEMPTION),
        ("maturity_redemption_pct", MATURITY_REDEMPTION),
        ("coupon_note_pct", r"月配息率為" + PCT),
    ):
        fields[name] = capture(name, plain, pattern, Decimal, whole_match=True)
    ti = TextIndex(art9, separate_digits=True)
    text = ti.text
    a = text.find("(a)「配息金額」")
    b = text.find("(b)「自動提前出場贖回金額」", a)
    c = text.find("(c)「到期贖回金額」", b)
    e = text.find("(2)連結標的：", c)
    if min(a, b, c, e) < 0:
        note = "第 9 項找不到 (a) 配息金額、(b) 自動提前出場贖回金額、(c) 到期贖回金額或 (2) 連結標的"
        for name in ("coupon_table", "monthly_coupon_pct", "ko_wording", "ko_memory", "ki_type", "underlying_table"):
            fields[name] = ParsedField.missing(name, note)
        return
    _coupon_table(fields, ti, a, b)
    _ko(fields, ti, b, c)
    table = price_table.read(
        "underlying_table", table_region(art9, "(2)連結標的：", "投資人請注意：本商品之收益"), PRICE_TABLE
    )
    fields["underlying_table"] = table
    fields["main_price_table"] = price_table.at("main_price_table", table, PRICE_TABLE)
    fields["ki_type"] = ki_type(ti, c, e, table)


def _coupon_table(fields: dict[str, ParsedField], ti: TextIndex, a: int, b: int) -> None:
    """配息表（範本規格 §5.2）：KO 每期「i／配息日／配息率」；KO 每日「配息期i／配息期結束日／配息日／配息率」。"""
    text = ti.text[a:b]
    ev = ti.lines_for(a, b)
    daily_head, periodic_head = "配息期i配息期結束日配息日配息率", "i配息日配息率"
    if daily_head in text:
        rows = _rows(text[text.find(daily_head) :], ROW + D + D + PCT)
        value = [
            {"period": int(p), "end": parse_date(x), "payment": parse_date(y), "rate": Decimal(r)}
            for p, x, y, r in rows
        ]
        kind = "D"
    elif periodic_head in text:
        rows = _rows(text[text.find(periodic_head) :], ROW + D + PCT)
        value = [{"period": int(p), "payment": parse_date(y), "rate": Decimal(r)} for p, y, r in rows]
        kind = "P"
    else:
        fields["coupon_table"] = ParsedField.missing("coupon_table", "第 9 項 (a) 找不到配息表")
        fields["monthly_coupon_pct"] = derived("monthly_coupon_pct", fields["coupon_table"])
        return
    if not value or any(None in r.values() for r in value):
        fields["coupon_table"] = ParsedField.invalid("coupon_table", ev, "配息表的列讀不到")
    else:
        fields["coupon_table"] = ParsedField.present("coupon_table", {"kind": kind, "rows": value}, ev)
    table = fields["coupon_table"]
    if table.ok:
        rates = sorted({r["rate"] for r in value})
        fields["monthly_coupon_pct"] = (
            ParsedField.present("monthly_coupon_pct", rates[0], ev)
            if len(rates) == 1
            else ParsedField.ambiguous("monthly_coupon_pct", rates, ev, "配息表各期配息率不同")
        )
    else:
        fields["monthly_coupon_pct"] = derived("monthly_coupon_pct", table)


def _ko(fields: dict[str, ParsedField], ti: TextIndex, b: int, c: int) -> None:
    """(b)：KO 觀察寫法（每日：起始期 k；每期：觀察日表）與記憶式（範本規格 §4.1、§4.2）。"""
    text = ti.text[b:c]
    ev = ti.lines_for(b, c)
    daily = list(re.finditer(KO_DAILY, text))
    has_table = KO_TABLE_HEAD in text
    if len(daily) == 1 and not has_table:
        m = daily[0]
        fields["ko_wording"] = ParsedField.present(
            "ko_wording", ("D", int(m[1]), []), ti.lines_for(b + m.start(), b + m.end())
        )
    elif has_table and not daily:
        rows = _rows(text[text.find(KO_TABLE_HEAD) :], ROW + D + D)
        ko_rows = [{"i": int(i), "observation": parse_date(x), "autocall": parse_date(y)} for i, x, y in rows]
        if not ko_rows or any(None in r.values() for r in ko_rows):
            fields["ko_wording"] = ParsedField.invalid("ko_wording", ev, "自動提前出場觀察日表的列讀不到")
        else:
            fields["ko_wording"] = ParsedField.present("ko_wording", ("P", None, ko_rows), ev)
    else:
        fields["ko_wording"] = ParsedField.invalid(
            "ko_wording", ev, "第 9 項 (b) 自動提前出場觀察日的寫法缺漏、重複或不在範本規格內"
        )
    events = list(re.finditer(KO_EVENT, text))
    if len(events) != 1:
        fields["ko_memory"] = ParsedField.invalid("ko_memory", ev, "第 9 項 (b) 自動提前出場事件的句型缺漏或重複")
        return
    m = events[0]
    tail = text[
        m.end() : m.end() + 40
    ]  # 「為避免疑義，所有連結標的無須於同一自動提前出場觀察日符合上開條件。」緊接在事件句後
    fields["ko_memory_wording"] = ParsedField.present(
        "ko_memory_wording",
        (MEMORY_PRIOR in m[1], MEMORY_SAME in tail),
        ti.lines_for(b + m.start(), b + m.end() + (len(tail) if MEMORY_SAME in tail else 0)),
    )
    fields["ko_memory"] = memory_of(fields["ko_memory_wording"])


def memory_of(wording: ParsedField) -> ParsedField:
    """兩句都有 → 記憶式；兩句都沒有 → 非記憶式；只有一句 → 不合法（多標的又沒有句型由規則轉人工覆核）。"""
    if not wording.ok:
        return derived("ko_memory", wording)
    prior, same = wording.value
    if prior != same:
        note = "自動提前出場事件只寫了「或任何先前的觀察日」與「無須於同一觀察日」其中一句"
        return ParsedField("ko_memory", FieldStatus.INVALID, evidence=list(wording.evidence), note=note)
    return ParsedField("ko_memory", FieldStatus.PRESENT, prior, list(wording.evidence))


def ki_type(ti: TextIndex, c: int, e: int, table: ParsedField) -> ParsedField:
    """界限事件（範本規格 §4.4）：(c) 有「若未發生界限事件」、(2) 有界限事件定義、價格表有下限價欄三者須一致。"""
    text = ti.text
    maturity = KI_MATURITY in text[c:e]
    after = text[e:]
    am, d = after.count(KI_AM), after.count(KI_D)
    period = KI_D_PERIOD in after
    lns = ti.lines_for(c, len(text))
    has_col = table.ok and "ki" in table.value.columns
    if not maturity and am + d == 0 and not period:
        if has_col:
            return ParsedField.invalid("ki_type", lns, "價格表有下限價欄，但第 9 項沒有界限事件")
        return ParsedField.present("ki_type", "none", ti.lines_for(c, e))
    if not maturity or am + d != 1 or (d == 1) != period:
        return ParsedField.invalid(
            "ki_type", lns, "第 9 項到期贖回、界限事件定義與界限事件觀察期間的寫法不一致或不在範本規格內"
        )
    if table.ok and not has_col:
        return ParsedField.invalid("ki_type", lns, "第 9 項有界限事件，但價格表沒有下限價欄")
    anchor = KI_AM if am else KI_D
    p = text.find(anchor, e)
    return ParsedField.present("ki_type", "AM" if am else "D", ti.lines_for(p, p + len(anchor)))


def table_region(lns: list[Line], start_marker: str, end_marker: str) -> list[Line]:
    """價格表的範圍：`start_marker` 那一行之後、`end_marker` 之前（去空白比對）。"""
    ti = TextIndex(lns)
    a = ti.text.find(start_marker)
    b = ti.text.find(end_marker, a + len(start_marker))
    if a < 0 or b < 0:
        return []
    return lns[ti.line_at(a + len(start_marker) - 1) + 1 : ti.line_at(b)]


def art10_tickers(art10: list[Line]) -> ParsedField:
    """第 10 項連結標的資產表的彭博代碼（`ABC UW`），依表上順序。"""
    ti = TextIndex(art10)
    a, b = ti.text.find("連結標的資產："), ti.text.find("相對權重：")
    if a < 0 or b < a:
        return ParsedField.missing("art10_tickers", "第 10 項找不到連結標的資產表")
    lines = join_wrapped_tickers(art10[ti.line_at(a) + 1 : ti.line_at(b)])
    hits = [(ln, TICKER.match(ln.text.strip())) for ln in lines]
    tickers = [(f"{m[1]} {m[2]}", ln) for ln, m in hits if m]
    if not tickers:
        return ParsedField.invalid("art10_tickers", lines, "第 10 項連結標的資產表讀不到彭博代碼")
    return ParsedField.present("art10_tickers", [t for t, _ in tickers], [ln for _, ln in tickers])


def _table_fields(fields: dict[str, ParsedField]) -> None:
    """價格表＋第 10 項代碼 → 標準欄位：標的、各標的價格、K／KI／KO %（表頭是唯一來源）。"""
    table, tickers = fields["underlying_table"], fields["art10_tickers"]
    if table.ok and tickers.ok and len(table.value.rows) == len(tickers.value):
        fields["underlyings"] = ParsedField(
            "underlyings", FieldStatus.PRESENT, list(tickers.value), list(tickers.evidence)
        )
        fields["underlying_prices"] = price_table.price_rows(table, tickers.value)
    elif table.ok and tickers.ok:
        note = f"價格表 {len(table.value.rows)} 列，第 10 項連結標的資產表 {len(tickers.value)} 檔，無法依列序對到代碼"
        ev = list(table.evidence) + list(tickers.evidence)
        for key in ("underlyings", "underlying_prices"):
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)
    else:
        bad = table if not table.ok else tickers
        fields["underlyings"] = derived("underlyings", bad)
        fields["underlying_prices"] = derived("underlying_prices", bad)
    # 價格表表頭一定有執行價與自動提前出場價（price_table 檢查過）；有無下限價欄與界限事件的一致性由 ki_type 檢查
    for key, col in (("strike_pct", "strike"), ("ko_pct", "ko")):
        fields[key] = derived(key, table, table.value.pcts[col] if table.ok else None)
    ki = fields["ki_type"]
    if ki.ok and ki.value == "none":
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence), note="說明書無 KI（沒有界限事件）"
        )
    else:
        bad = next((p for p in (ki, table) if not p.ok), None)
        fields["ki_pct"] = derived("ki_pct", bad or table, None if bad else table.value.pcts["ki"])


def guaranteed_coupon(art15: list[Line]) -> dict[str, ParsedField]:
    """第 15 項 (4)「最低保證配息率：第一個月／前k個月配息 x%」→ 月數（`guaranteed_coupon_months`）與 x（`guaranteed_coupon_pct`）。"""
    names = ("guaranteed_coupon_months", "guaranteed_coupon_pct")
    ti = TextIndex(art15)
    found = list(ti.finditer(r"\(4\)最低保證配息率：(?:第一個月|前([一二三四五六七八九十]{1,3})個月)配息" + PCT))
    if len(found) != 1:
        note = "第 15 項找不到或有多處「(4) 最低保證配息率：…配息 x%」"
        return {n: ParsedField.missing(n, note) for n in names}
    m = found[0]
    months = CN_NUM.get(m[1]) if m[1] else 1
    lns = ti.lines_for(m.start(), m.end())
    pct = ParsedField.present(names[1], Decimal(m[2]), lns)
    if months is None:
        return {
            names[0]: ParsedField.invalid(names[0], lns, f"最低保證配息率的月數「前{m[1]}個月」無法辨識"),
            names[1]: pct,
        }
    return {names[0]: ParsedField.present(names[0], months, lns), names[1]: pct}


# ---------------------------------------------------------------- KO 排程


SCHEDULE_WHERE = "第 9 項日期表"


def _schedule_table(fields: dict[str, ParsedField]) -> ParsedField:
    """日期表：D 為配息表（觀察日 = 配息期結束日）；P 為配息日表，觀察日取自動提前出場觀察日表裡自動提前出場日 = 該期
    配息日的那一列（對不到的期別沒有觀察日）。表上沒有開始日欄（首期自發行日開始），不查期始日。"""
    wording = fields["ko_wording"]

    def build(value: dict) -> standard_fields.DateTable | ParsedField:
        if value["kind"] == "D":
            return standard_fields.DateTable(SCHEDULE_WHERE, schedule.rows_of(value["rows"], "end"))
        if not wording.ok:
            return wording
        rows = schedule.mapped_rows(value["rows"], wording.value[2] or ())
        return standard_fields.DateTable(SCHEDULE_WHERE, rows)

    return schedule.table_field(fields["coupon_table"], build)


def _schedule(fields: dict[str, ParsedField]) -> None:
    """日期表與提前出場排程（`parsers/schedule.py`，範本規格 §4.1）。

    D：k = 「自第k 個配息期結束日」，比價日為第 k 期與末期的配息期結束日（配息表須是 D 型）。
    P：k = 觀察日表第 1 列自動提前出場日在配息日表的期別（觀察日表的 i 不是期別），各列須依序對到第 k～n 期；
    比價日為每期的觀察日。"""
    wording, table = fields["ko_wording"], fields["coupon_table"]
    fields["schedule_table"] = _schedule_table(fields)
    bad = next((p for p in (wording, table) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        return
    kind, k, ko_rows = wording.value
    rows = table.value["rows"]
    ev = list(wording.evidence) + list(table.evidence)
    note = ""
    if kind != table.value["kind"]:
        note = "第 9 項 (b) 的 KO 觀察寫法和配息表的種類不一致（每日型配息表要有「配息期結束日」欄）"
    elif kind == "P":
        period_of = {r["payment"]: r["period"] for r in rows}
        mapped = [period_of.get(r["autocall"]) for r in ko_rows]
        if any(p is None for p in mapped):
            note = "自動提前出場觀察日表的自動提前出場日不在配息日表內"
        elif mapped != list(range(mapped[0], len(rows) + 1)):
            note = f"自動提前出場觀察日表的期別不是第 {mapped[0]} 期到第 {len(rows)} 期連續"
        else:
            k = mapped[0]
    if note:
        fields.update(schedule.invalid(ev, note))
        return
    obs = ParsedField("ko_observation", FieldStatus.PRESENT, kind, list(wording.evidence))
    first = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    fields.update(
        schedule.autocall_schedule(obs, first, fields["schedule_table"], "end" if kind == "D" else "observation")
    )


# ---------------------------------------------------------------- 第 16 項情境分析


def _article16(fields: dict[str, ParsedField], art16: list[Line]) -> None:
    """基本假設（天期、面額、配息率與年化配息率）、情境價格表、情境一配息月數（範本規格 §4.9）。"""
    ti = TextIndex(art16)
    tenor = capture("scenario_tenor", ti, r"基本假設天期：(\d+)個月", int, whole_match=True)
    denom = rf"每單位面額：([\d,]+)({CCY})初始投資金額"
    denomination = capture("scenario_denomination", ti, denom, number, whole_match=True)
    fields["scenario_denomination_currency"] = capture_group(art16, denom, 2, "scenario_denomination_currency")
    invest = rf"初始投資金額：([\d,]+)({CCY})配息率"
    fields["scenario_investment_currency"] = capture_group(art16, invest, 2, "scenario_investment_currency")
    coupon = r"配息率：" + PCT + r"\(年化配息率為" + PCT + r"\)"
    monthly = capture_group(art16, coupon, 1, "scenario_monthly_pct", Decimal)
    fields["coupon_pa_pct"] = capture_group(art16, coupon, 2, "coupon_pa_pct", Decimal)
    scenario_table = price_table.read(
        "scenario_table", table_region(art16, "(年化配息率為", "情境分析一"), SCENARIO_TABLE
    )
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", scenario_table, SCENARIO_TABLE)
    # 情境三的執行價不核對（未涵蓋清單 doc.scenario_calculations）
    fields["scenario_prices"] = standard_fields.absent("scenario_prices", "要核對的較差情境價格")
    assumed = [
        ScenarioParam("scenario_tenor", "第 16 項基本假設", tenor),
        ScenarioParam("scenario_denomination", "第 16 項基本假設", denomination),
        ScenarioParam("scenario_monthly_coupon", "第 16 項基本假設", monthly),
    ]
    _scenarios(fields, ti, assumed)
    found = list(
        ti.finditer(
            r"情境分析一：較佳情況假設於第1個自動提前出場觀察日發生自動提前出場事件。[•‧]第1(?:~(\d+))?個月配息金額"
        )
    )
    if len(found) == 1:
        m = found[0]
        fields["scenario_first_months"] = ParsedField.present(
            "scenario_first_months", int(m[1]) if m[1] else 1, ti.lines_for(m.start(), m.end())
        )
    else:
        fields["scenario_first_months"] = ParsedField.missing(
            "scenario_first_months", "情境分析一找不到或有多處「假設於第1 個自動提前出場觀察日…第 1(~k) 個月配息金額」"
        )


SCENARIOS = ("一", "二", "三")
SCENARIO_END = "※請注意"
EQ = "[=＝]"
# 情境的配息金額、贖回金額行：「每單位面額 × x% ＝ 面額 × x% ＝ 金額」（Issue #319：x 兩處與面額是重複寫出的值）
SCENARIO_COUPON = rf"第1(?:~(\d+))?個月配息金額{EQ}每單位面額×{PCT}{EQ}([\d,]+)×{PCT}{EQ}"
SCENARIO_REDEMPTION = rf"{{}}{EQ}每單位面額×{PCT}{EQ}([\d,]+)×{PCT}{EQ}"
# 情境一自動提前出場、情境二到期的贖回金額行 → (情境序號, 句首, 出處清單)；% 對應第 9 項 (b)／(c)
SCENARIO_REDEMPTIONS = (
    (1, "自動提前出場贖回金額", "scenario_ko_redemption_mentions"),
    (2, "到期贖回金額", "scenario_maturity_redemption_mentions"),
)


def _scenarios(fields: dict[str, ParsedField], ti: TextIndex, assumed: list[ScenarioParam]) -> None:
    """情境分析一～三重複寫出的值（Issue #319，比照 BBVA #305）→ 出處清單，由規則逐處比對正式條款：

    - 共用情境規則的 `scenario_params`（Issue #294）：基本假設 `assumed`，加上配息金額行的配息率兩處（= 配息表配息率）、
      配息金額與贖回金額行代入的面額（= 第 6 項面額）、情境二、三「第 1~n 個月」的 n（= 天期；情境一的月數另由
      `scenario_first_months` 核對）
    - `scenario_ko_redemption_mentions`／`scenario_maturity_redemption_mentions`：贖回金額行的 % 兩處（= 第 9 項 (b)／(c)）
    - `scenario_currencies`：各情境金額旁的幣別（股價、股數與剩餘金額算式所在的行不列入）與情境三「以X支付剩餘金額」

    配息金額、累積配息、股數、剩餘金額、報酬率不重算；情境三的執行價、收盤價跟著標的走，不列入。
    """
    starts = [ti.text.find(f"情境分析{k}：") for k in SCENARIOS]
    bounds = [*starts, ti.text.find(SCENARIO_END, max(starts))]
    found = all(b >= 0 for b in bounds) and bounds == sorted(bounds)
    lists: dict[str, list[Occurrence]] = {
        name: []
        for name in (
            "scenario_coupon_mentions",
            "scenario_denomination_mentions",
            "scenario_months_mentions",
            "scenario_ko_redemption_mentions",
            "scenario_maturity_redemption_mentions",
            "scenario_currencies",
        )
    }
    for n, k in enumerate(SCENARIOS, start=1):
        where = f"第一章第 16 項情境分析{k}"
        lo, hi = (bounds[n - 1], bounds[n]) if found else (-1, -1)
        coupon = _one_match(ti, lo, hi, SCENARIO_COUPON, f"{where}配息金額行")
        mentions = [
            (
                "scenario_coupon_mentions",
                f"scenario{n}_coupon_pct",
                "配息率",
                "配息金額「每單位面額 × x%」",
                2,
                Decimal,
            ),
            (
                "scenario_coupon_mentions",
                f"scenario{n}_coupon_pct_calc",
                "配息率",
                "配息金額算式「面額 × x%」",
                4,
                Decimal,
            ),
            (
                "scenario_denomination_mentions",
                f"scenario{n}_coupon_denomination",
                "每單位面額",
                "配息金額算式",
                3,
                number,
            ),
        ]
        if n > 1:
            mentions.append(
                ("scenario_months_mentions", f"scenario{n}_months", "配息月數", "「第 1~n 個月配息金額」", 1, _months)
            )
        for container, field, label, place, group, convert in mentions:
            lists[container].append(Occurrence(field, label, f"{where}{place}", coupon(field, group, convert)))
        for m, label, container in SCENARIO_REDEMPTIONS:
            if m != n:
                continue
            line = _one_match(ti, lo, hi, SCENARIO_REDEMPTION.format(label), f"{where}{label}行")
            for field, place, group in (
                (f"scenario{n}_redemption_pct", f"{label}「每單位面額 × x%」", 1),
                (f"scenario{n}_redemption_pct_calc", f"{label}算式「面額 × x%」", 3),
            ):
                lists[container].append(Occurrence(field, "贖回 %", f"{where}{place}", line(field, group, Decimal)))
            field = f"scenario{n}_redemption_denomination"
            lists["scenario_denomination_mentions"].append(
                Occurrence(field, "每單位面額", f"{where}{label}算式", line(field, 2, number))
            )
        field = f"scenario{n}_currency"
        if found:
            amounts = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, start=lo, end=hi)
            occ = money.scenario_occurrence(field, "情境金額幣別", where, amounts, ti.lines_for(lo, lo + 1))
        else:
            occ = Occurrence(field, "情境金額幣別", where, ParsedField.missing(field, "第 16 項找不到情境分析一～三"))
        lists["scenario_currencies"].append(occ)
    lo = bounds[2] if found else -1
    lists["scenario_currencies"] += money.currency_words(
        ti.lines_for(lo, bounds[3]) if found else [],
        REMAINDER_CURRENCY,
        "scenario3_remainder_currency",
        "剩餘金額幣別",
        "第一章第 16 項情境分析三「以X支付剩餘金額」",
    )
    params = list(assumed)
    for name, code in SCENARIO_PARAMS.items():
        params += [ScenarioParam(code, f"{occ.where}的", occ.value) for occ in lists.pop(name)]
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    for name, items in lists.items():
        fields[name] = standard_fields.occurrences(name, items)


# 情境重複寫出的值交給共用情境規則的出處清單 → 出處代號
SCENARIO_PARAMS = {
    "scenario_coupon_mentions": "scenario_monthly_coupon",
    "scenario_denomination_mentions": "scenario_denomination",
    "scenario_months_mentions": "scenario_coupon_months",
}


def _months(x: str | None) -> int:
    """「第1~n個月」的 n；只寫「第1個月」時為 1。"""
    return int(x) if x else 1


def _one_match(ti: TextIndex, lo: int, hi: int, pattern: str, what: str) -> Callable[[str, int, Callable], ParsedField]:
    """`ti.text[lo:hi]` 內 `pattern` 恰好一處時，`get(name, group, convert)` 交出該組的值；沒有或有多處時每組都轉人工覆核。"""
    hits = [m for m in ti.finditer(pattern) if lo <= m.start() < hi] if lo >= 0 else []
    lns = [ln for m in hits for ln in ti.lines_for(m.start(), m.end())]

    def get(name: str, group: int, convert: Callable) -> ParsedField:
        if len(hits) != 1:
            note = f"{what}找不到已知寫法" if not hits else f"{what}有 {len(hits)} 處"
            return ParsedField.missing(name, note) if not hits else ParsedField.invalid(name, lns, note)
        try:
            return ParsedField.present(name, convert(hits[0][group]), lns)
        except (ValueError, TypeError, ArithmeticError):
            return ParsedField.invalid(name, lns, f"{what}「{hits[0][group]}」無法辨識")

    return get


# ---------------------------------------------------------------- 第二章、第四章


def _chapter2(fields: dict[str, ParsedField], doc: Document) -> None:
    ch2 = doc.articles(2)
    raw1 = RawText(doc.span_lines(ch2.get(1)))
    fields["issuer_name_ch2"] = raw1.capture("issuer_name_ch2", r"\(1\)\s*事業名稱\s*[:：]\s*(.+?)\s*\(2\)\s*設立日期")
    raw3 = RawText(doc.span_lines(ch2.get(3)))
    trustee = (
        r"\(5\)\s*受託或銷售機構\s*[:：]\s*\(a\)\s*事業名稱\s*[:：]\s*(.+?)\s*\(b\)\s*設立日期.*?\(c\)\s*營業所在地\s*[:：]\s*(.+?)"
        r"\s*\(d\)\s*負責人姓名\s*[:：]\s*(.+?)\s*\(6\)"
    )
    fields["distributor_name_ch2"] = raw3.capture("distributor_name_ch2", trustee)
    fields["distributor_address_ch2"] = raw3.capture("distributor_address_ch2", trustee, group=2)
    fields["chairman"] = raw3.capture("chairman", trustee, group=3)


def _chapter4(fields: dict[str, ParsedField], doc: Document, put: Callable) -> None:
    ch4 = doc.articles(4)

    def art(n: int) -> list[Line]:
        return doc.span_lines(ch4.get(n))

    put("subscription_start", art(1), r"◎開始受理申購日期：" + D, parse_date)
    put("subscription_end", art(1), r"◎結束受理申購日期：" + D, parse_date)
    fees = art(2)  # 表後的「附註：分銷費用…」換行後可能有一行以「分銷費用」開頭，不算費用項目
    fields.update(fee_rates(fees[: next((i for i, ln in enumerate(fees) if squash(ln.text).startswith("附註")), None)]))
    unit = r"\(相當於一單位\(張\)商品\)"
    for name, lns, pattern in (
        ("minimum_subscription", art(4), rf"最低申購金額：([\d,]+)({CCY}){unit}"),
        ("minimum_additional", art(4), rf"最低累加申購金額：商品面額([\d,]+)({CCY}){unit}"),
        ("minimum_redemption", art(8), rf"不得小於([\d,]+)({CCY}){unit}"),
    ):
        fields[name] = capture_group(lns, pattern, 1, name, number)
        fields[f"{name}_currency"] = capture_group(lns, pattern, 2, f"{name}_currency")


# ---------------------------------------------------------------- 出處清單


# 寫受託機構名稱的其他地方（Issue #319，比照投資人須知 #306）：(欄位, 位置, pattern)。第三章 6.(4) 第三處
# 「…依法不得承諾擔保」多「綜合」，依 #274 第 5 題特例放行，不列入
COVER_WARNING_NAMES = (
    ("distributor_name_w4a", "封面警語 4「本商品雖經…審查」", r"本商品雖經(.+?)審查"),
    ("distributor_name_w4b", "封面警語 4「…不負本商品投資盈虧之責」", r"之價值，(.+?)不負本商品投資盈虧之責"),
    ("distributor_name_w4c", "封面警語 4「…依法不得承諾擔保」", r"盈虧之責。(.+?)依法不得承諾"),
    ("distributor_name_w5", "封面警語 5「而非由…所保證」", r"而非由(.+?)所保證"),
    ("distributor_name_w6", "封面警語 6「(即…)負責」", r"受託或銷售機構[(（]即(.+?)[)）]負責外"),
)
CH3_WARNING_NAMES = (
    ("distributor_name_ch3_w4a", "第三章 6.(4)「本商品雖經…審查」", r"本商品雖經(.+?)審查"),
    ("distributor_name_ch3_w4b", "第三章 6.(4)「…不負本商品投資盈虧之責」", r"之價值，(.+?)不負本商品投資盈虧之責"),
    ("distributor_name_ch3_w5", "第三章 6.(5)「而非由…所保證」", r"而非由(.+?)所保證"),
)
STRUCTURE_NAMES = (
    (
        "distributor_name_structure_osu",
        "第二章 4. 交易架構（OSU）「投資人透過與…之受託買賣關係」",
        r"受託買賣時：本商品由發行機構發行，投資人透過與(.+?)之受託買賣關係",
    ),
    (
        "distributor_name_structure",
        "第二章 4. 交易架構「投資人透過與…之受託買賣關係」",
        r"總代理人為發行機構之總代理人。投資人透過與(.+?)之受託買賣關係",
    ),
)


def _occurrences(fields: dict[str, ParsedField]) -> None:
    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_subscription", "最低申購金額", "第四章第 4 項最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "第四章第 4 項最低累加申購金額", fields["minimum_additional"]),
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
        "print_dates", [occ("print_date", "刊印日期", "第 2 頁中文產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.absent("issue_price_others", "發行價格的其他出處")
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")
    fields.update(
        standard_fields.absent_all(
            [x for x in standard_fields.DISTRIBUTOR_OTHERS if x != "distributor_names_others"],
            "受託機構地址、負責人的其他出處",
        )
    )
    currencies = (  # 金額旁的幣別字（Issue #170）：價格表幣別字（標的報價幣別）、情境三股價與股數算式不列入
        ("denomination_currency", "每單位商品面額幣別", "第一章第 6 項商品面額"),
        ("issue_price_currency", "發行價格金額幣別", "第一章第 6 項發行價格"),
        ("minimum_subscription_currency", "最低申購金額幣別", "第四章第 4 項最低申購金額"),
        ("minimum_additional_currency", "累加申購金額幣別", "第四章第 4 項最低累加申購金額"),
        ("minimum_redemption_currency", "最低贖回金額幣別", "第四章第 8 項最低贖回金額"),
        ("scenario_denomination_currency", "情境假設每單位面額幣別", "第一章第 16 項基本假設每單位面額"),
        ("scenario_investment_currency", "情境假設初始投資金額幣別", "第一章第 16 項基本假設初始投資金額"),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others",
        [
            *(occ(key, label, where, fields[key]) for key, label, where in currencies),
            *fields["scenario_currencies"].value,
        ],
    )


def _repeated(fields: dict[str, ParsedField], doc: Document, article: Callable[[int], list[Line]]) -> None:
    """說明文字的幣別字併入 `currency_others`；警語與交易架構的受託機構名稱 → `distributor_names_others`（Issue #319）。"""
    words = money.currency_words
    ch3, ch4 = doc.articles(3), doc.articles(4)
    principal = "可能低於100%X投資本金"
    wording = (
        *words(
            article(9),
            REMAINDER_CURRENCY,
            "redemption_currency",
            "剩餘金額幣別",
            "第一章第 9 項 (c)「以X支付剩餘金額」",
        ),
        *words(
            article(15),
            PRINCIPAL_CURRENCY,
            "principal_currency_art15",
            "投資本金幣別",
            f"第一章第 15 項「{principal}」",
        ),
        *words(
            doc.chapter_lines(3),
            INTEREST_CURRENCY,
            "interest_rate_currency",
            "利率幣別",
            "第三章 1.(3) 利率風險",
        ),
        *words(
            doc.chapter_lines(3),
            rf"直接導致({CCY})投資本金損失之虞",
            "risk_principal_currency",
            "投資本金幣別",
            "第三章 3.、4.「直接導致X投資本金損失之虞」",
        ),
        *words(
            doc.span_lines(ch4.get(9)),
            PRINCIPAL_CURRENCY,
            "principal_currency_ch4",
            "投資本金幣別",
            f"第四章 9.「{principal}」",
        ),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [*fields["currency_others"].value, *wording]
    )
    names = [
        Occurrence(field, label, where, capture(field, TextIndex(lns), pattern))
        for lns, label, specs in (
            (doc.before_chapter1(), "受託機構名稱（警語）", COVER_WARNING_NAMES),
            (doc.span_lines(ch3.get(6)), "受託機構名稱（警語）", CH3_WARNING_NAMES),
            (doc.span_lines(doc.articles(2).get(4)), "受託機構名稱（交易架構）", STRUCTURE_NAMES),
        )
        for field, where, pattern in specs
    ]
    fields["distributor_names_others"] = standard_fields.occurrences("distributor_names_others", names)
