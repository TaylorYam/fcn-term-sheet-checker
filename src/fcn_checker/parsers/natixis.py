"""Natixis 中文產品說明書（docs/templates/natixis-zh-product-description.md）：章＋條號＋標籤定位，保留原文證據。

封面（第 1–2 頁）是「標籤：值」兩欄（標籤 x≈41、值 x≈241，值的行可能比標籤高或低），商品代號與 ISIN 只在封面
「TDCC 商品代號為…，ISIN 為…」一處；第 2 頁末是刊印日期。章名「第一章、商品基本資料」～「第五章、特別記載事項」
（全形頓號、五章）；條號「N.」（第一章 x≈54、其他章 x≈36）。每頁頁底有英文名稱兩行、「第N頁/共計M頁」與分類標記，
都不算內文（不可用「Natixis –」字首過濾：封面英文名稱也這樣開頭）。新舊兩版只差「受託機構」／「受託或銷售機構」的稱呼。
數字、日期與句型在去空白的全文索引上讀；名稱、地址等要保留空白的值在原文合併文字（`common.RawText`）上讀。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence, ScenarioParam, ScenarioPrice
from ..text import squash
from . import money, price_table, schedule
from .common import RawText, capture_group, derived, fee_rates, number
from .layout import Document, LayoutSpec, Span, TextIndex, capture, parse_date

TEMPLATE_ID = "natixis-zh-pd"
# 2：第一章條號中途重編、新版受託機構標籤（Issue #270）；3：價格表改由 parsers/price_table.py 讀（Issue #287）；
# 4：無 KI 要有到期贖回情況二只有實物交割的寫法（Issue #297）；5：重複寫出處（Issue #318）；6：日期表標準欄位與共用排程推導（Issue #289）；
# 7：情境價格表、假設值與實物交割執行價格交成共用情境規則的標準欄位（Issue #294）
PARSER_VERSION = "7"
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
}
# 條號「N.」第一章 x≈54、其他章 x≈36；子項「(n)」x≈63–90
LAYOUT = LayoutSpec(
    r"第{zh}章、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    60,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    100,
    CHAPTER_NAMES,
)
ISSUER = "法國外貿銀行"
NAME_EN_MARK = "Autocallable Fixed Coupon Note Linked to Basket of Equities"
ARTICLES = 26  # 第一章條數
FOOTER = re.compile(
    r"^(Natixis – .*|Protected, Unsecured and Non-Guaranteed\)|第\d+頁/共計?\d+頁|C[12] - (?:Inter ?nal|Public) Natixis)$"
)
FOOTER_Y = 760  # 頁尾（英文名稱、頁碼、分類標記）y≈785–827
STRAY_DIGIT = re.compile(r"^\d{1,2}$")  # 部分文件左上角單獨的數字（NX8 第 1、15 頁），不屬於內文
COVER_LABEL_MAX_X = 200  # 封面標籤欄 x≈41、值欄 x≈241
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
DISTRIBUTOR = r"受託(?:或銷售)?機構"  # 舊版「受託機構」、新版「受託或銷售機構」
BULLET = "[•‧]"  # 列項符號（PDF 字型不同時擷取成 U+2022 或 U+2027）
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CN_NUM.update({f"十{k}": 10 + v for k, v in list(CN_NUM.items())[:9]})
# 第 12 項 KO 觀察（範本規格 §4.1）
CN_PERIOD = r"([一二三四五六七八九十]{1,3})"  # 中文數字的期別（第一～第十二）
KO_DAILY_BODY = r"從第" + CN_PERIOD + r"個配息觀察期間結束日\(含\)到最終評價日\(不含\)之每一預定交易日每日觀察"
KO_DAILY = r"自動提前到期觀察日：" + KO_DAILY_BODY
# 封面「TDCC 商品代號為…，ISIN 為…」：第 1 組商品代號、第 2 組受託機構商品代號（舊版有標籤、可能空白；沒有時為 None）、第 3 組 ISIN
PRODUCT_CODES = rf"TDCC商品代號為(\d{{12}})，(?:{DISTRIBUTOR}商品代號為(.*?)，)?ISIN為([A-Z]{{2}}[A-Z0-9]{{10}})"
KO_PERIODIC = "自動提前到期觀察日：如下表所列"
# 第 14 項記憶式（範本規格 §4.2）
MEMORY = "收盤價皆曾經高於或等於其自動提前到期贖回價格"
NO_MEMORY = "收盤價皆高於或等於其自動提前到期贖回價格"
KO_DEF = r"自動提前到期贖回價格為期初進場價格之" + PCT
STRIKE_DEF = r"「執行價格」係指就任一連結標的而言，各該連結標的之期初進場價格之" + PCT
KI_DEF = r"「界限價格」係指就任一連結標的而言，各該連結標的之期初進場價格之" + PCT
KI_EVENT = "「觸及事件」係指"
# 無 KI 的到期贖回情況二：低於執行價格就實物交割（有 KI 時寫「依下列2.a或2.b所述情況」）；投資人須知貳、2. 同句
NO_KI_CASE = r"最終價格低於其執行價格，則於到期日發行機構將依下列所述情況贖回本商品：實物交割"
KI_PERIOD = r"「觸及事件判定期間」係指(最終評價日|期初進場日\(含\)起至最終評價日\(含\)止之期間)"
KI_PERIOD_WHOLE = "期初進場日(含)起至最終評價日(含)止之期間"
# 觸及事件判定日（第 12 項 `觸及事件判定日：…`；投資人須知舊版只有第 15 項定義 `「觸及事件判定日」係指於…`）
KI_DAY = {
    "最終評價日": "AM",
    "觸及事件判定期間之各預定交易日": "D",
    "觸及事件判定期間之各配息觀察期間結束日": "P",
}
COUPON = r"月配息率：" + PCT + r"\(即年利率" + PCT + r"\)"
# 第 9 項價格表（以左欄列號切列）與第 16 項情境價格表（沒有列號與代碼，以價格切列）：欄序 KO → KI → K，
# 表頭 %「(期初進場價格之X%)」；價格不帶幣別字（右邊另有「貨幣」欄）
PRICE_TABLE = price_table.TableSpec(
    where="第 9 項價格表",
    initial="期初進場價格",
    columns=(
        price_table.Column("ko", "自動提前到期"),
        price_table.Column("ki", "界限價格", required=False),
        price_table.Column("strike", "執行價格"),
    ),
    anchor="number",
    pct=r"之(\d+(?:\.\d+)?)%",
    pct_label="(期初進場價格之X%)",
    code="彭博代碼",
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="第 16 項情境價格表", anchor="price", code=None)
# 重複寫出處（Issue #318，比照 BBVA #305）
NUMBER = money.NUMBER
AUTOCALL_REDEMPTION = (
    r"自動提前到期贖回金額=發生自動提前到期事件之自動提前到期觀察日持有之商品單位數x每單位商品面額x" + PCT
)
MATURITY_REDEMPTION = r"則本商品於到期日將以每單位商品面額之" + PCT + "贖回"  # 第 15 項情況一
# 第 15 項情況一公式、2.a（有 KI 才有）的 %：(欄位, 每一處的位置, pattern, 每種型態都有)
MATURITY_REPEATS = (
    (
        "art15_formula",
        ("第一章第 15 項情況一到期贖回金額公式", "第一章第 15 項 2.a 到期贖回金額公式"),
        r"到期贖回金額=到期日持有之商品單位數x每單位商品面額x" + PCT,
        True,
    ),
    ("art15_case2a", ("第一章第 15 項 2.a",), r"則本商品將以每單位商品面額之" + PCT + "贖回", False),
)
SCENARIO_HEAD = r"情境([一二三四])[(（]"  # 情境一～四（無 KI 只有一～三）
SCENARIO_END = "關於上述情境分析之警語"
PHYSICAL_CALC = "實物交割之計算"  # 之後是實物交割算式：股價、匯率、股數混在一起，不列入幣別出處
# 各期配息「面額 x (年利率/12) =」：在原文行上讀（全文索引去掉空白後，左邊的期別會和面額黏在一起）
SCENARIO_COUPON_ROW = re.compile(rf"(?:^|\s)({NUMBER})\s*{CCY}\s*x\s*\(\s*\d+(?:\.\d+)?%\s*/\s*12\s*\)")
SCENARIO_PRINCIPAL = rf"收到({NUMBER})({CCY})的原始投資本金"
SCENARIO_AUTOCALL_PCT = r"自動提前到期贖回金額為原始投資本金乘以" + PCT
SCENARIO_MATURITY_PCT = r"(?<!提前)到期贖回金額為原始投資本金乘以" + PCT
# 實物交割之計算 (1)：「…=每單位商品面額(x 匯率)/績效表現最差之連結標的(名稱)執行價格 = 面額(x 匯率)/ 執行價格」
WORST_STRIKE = rf"每單位商品面額(?:x匯率)?/績效表現最差之連結標的\(([^()]+)\)執行價格=({NUMBER})({CCY})(?:x{NUMBER})?/({NUMBER}){CCY}"
SCENARIO_PRICE_WORDS = ("最終價格", "匯率")  # 情境假設的最終價格跟著標的走、匯率是換算，不是承作幣別的出處
# 說明文字的幣別字（Issue #318）：(欄位, 條, pattern, 位置, 依型態可有可無)
CURRENCY_WORDING = (
    ("principal_currency", 6, rf"低於\d+(?:\.\d+)?%({CCY})投資本金", "第一章第 6 項計價貨幣本金保本率", False),
    ("payment_currency", 8, rf"條款以({CCY})(?:計算及)?支付", "第一章第 8 項主要給付項目", False),
    ("coupon_rounding_currency", 13, rf"整數位之({CCY})金額", "第一章第 13 項配息金額", True),  # 日圓才有
    ("cash_remainder_currency", 15, rf"所得之({CCY})金額", "第一章第 15 項剩餘現金金額", False),
)
# 受託機構名稱的其他出處（Issue #318）：封面警語與第三章第 5 項警語 (5)、(6)，第四章第 10、14 項
WARNING_NAMES = (
    ("w5", "警語 (5)「而非由…所保證」", rf"而非由{DISTRIBUTOR}[(（](.+?)[)）]所保證"),
    ("w6a", "警語 (6)「由…另行訂定」", rf"及由{DISTRIBUTOR}[(（](.+?)[)）]另行訂定者"),
    ("w6b", "警語 (6)「係由…負責」", rf"係由{DISTRIBUTOR}[(（](.+?)[)）]負責外"),
)
CH4_NAMES = (
    (10, "distributor_name_ch4_10a", "第四章第 10 項贖回指示", rf"投資人應向{DISTRIBUTOR}[(（](.+?)[)）]提出贖回指示"),
    (10, "distributor_name_ch4_10b", "第四章第 10 項贖回價金", rf"受理其贖回申請，{DISTRIBUTOR}[(（](.+?)[)）]需俟"),
    (14, "distributor_name_ch4_14", "第四章第 14 項給付之方式", rf"給付之方式：{DISTRIBUTOR}[(（](.+?)[)）]於實際收到"),
)


def non_principal(text: str) -> str:
    """第一章第 1 項與投資人須知的英文名稱寫 `(Non Principal`（沒有連字號），視同封面的 `(Non-Principal`（2026-10-15 確認）。"""
    return re.sub(r"\(Non\s+Principal", "(Non-Principal", text)


def _is_noise(ln: Line) -> bool:
    text = ln.text.strip()
    if ln.y0 >= FOOTER_Y and FOOTER.match(text):
        return True
    return ln.x0 < 50 and ln.y0 < 60 and bool(STRAY_DIGIT.match(text))


class NatixisDocument(Document):
    """第一章條號可能中途重新從 1 編起（Word 自動編號跑掉，例：第 12 條之後接「1. 配息資料」…「14. 掛牌交易」；
    Issue #270）：依順序讀不滿 26 條時，改讀「編號中途從 1 重來一次、和第 1 條同一個 x、合計剛好 26 條」，依先後當成第 1～26 條。
    只容許重來一次，條內 x 相同的 `1.` 清單不會被當成條號一路接下去。"""

    def articles(self, chapter: int) -> dict[int, Span]:
        strict = super().articles(chapter)
        if chapter != 1 or len(strict) >= ARTICLES or 1 not in strict:
            return strict
        renumbered = self._renumbered_articles(chapter, self.lines[strict[1].start].x0)
        return renumbered if len(renumbered) == ARTICLES else strict

    def _renumbered_articles(self, chapter: int, x: float) -> dict[int, Span]:
        sp = self.chapters[chapter]
        marks: list[tuple[int, int]] = []
        printed, restarted = 1, False  # 下一個預期印出的編號；是否已重來過
        for i in range(sp.start + 1, sp.end):
            ln = self.lines[i]
            m = self.spec.article_re.match(ln.text)
            if not m or abs(ln.x0 - x) > 2:
                continue
            if int(m.group(1)) == printed:
                printed += 1
            elif int(m.group(1)) == 1 and marks and not restarted:  # 重新從 1 編起
                printed, restarted = 2, True
            else:
                continue
            marks.append((len(marks) + 1, i))
        return self._article_spans(marks, sp.end)


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁尾（英文名稱、頁碼、分類標記）與左上角單獨的數字不算內文。"""
    return NatixisDocument([ln for ln in lines if not _is_noise(ln)], LAYOUT)


# ---------------------------------------------------------------- 封面「標籤：值」


@dataclass
class CoverRow:
    label: str  # 去空白後的標籤（含結尾冒號）
    label_lines: list[Line]
    value_lines: list[Line]

    @property
    def lines(self) -> list[Line]:
        return sorted(self.label_lines + self.value_lines, key=lambda x: (x.page, x.y0, x.x0))

    @property
    def raw(self) -> RawText:
        return RawText(sorted(self.value_lines, key=lambda x: (x.page, x.y0, x.x0)))


def cover_rows(cover: Sequence[Line]) -> list[CoverRow]:
    """封面「…ISIN 為…」之後、「本商品之投資風險警語」之前的兩欄表：標籤欄（x < 200）跨行時接起來，值欄每行依 y
    分給同一頁最近的標籤（值可能比標籤高或低）。"""
    start = next((i for i, ln in enumerate(cover) if "ISIN為" in squash(ln.text)), None)
    end = next((i for i, ln in enumerate(cover) if squash(ln.text).startswith("本商品之投資風險警語")), None)
    if start is None or end is None or end <= start:
        return []
    rows: list[CoverRow] = []
    values: list[Line] = []
    for ln in cover[start + 1 : end]:
        if ln.x0 < COVER_LABEL_MAX_X:
            if rows and not rows[-1].label.endswith(("：", ":")) and rows[-1].label_lines[-1].page == ln.page:
                rows[-1].label += squash(ln.text)
                rows[-1].label_lines.append(ln)
            else:
                rows.append(CoverRow(squash(ln.text), [ln], []))
        else:
            values.append(ln)
    for ln in values:
        same = [r for r in rows if r.label_lines[0].page == ln.page]
        if not same:
            continue
        mid = (ln.y0 + ln.y1) / 2
        best = min(
            same,
            key=lambda r: abs((min(x.y0 for x in r.label_lines) + max(x.y1 for x in r.label_lines)) / 2 - mid),
        )
        best.value_lines.append(ln)
    return rows


def _row(rows: list[CoverRow], label: str) -> CoverRow | None:
    found = [r for r in rows if re.match(label, r.label)]
    return found[0] if len(found) == 1 else None


def _row_field(name: str, row: CoverRow | None, pattern: str, convert: Callable = lambda x: x.strip()) -> ParsedField:
    """封面一個標籤的值（原文合併）以 `pattern` 讀；證據含標籤與值的行。"""
    if row is None:
        return ParsedField.missing(name, "封面找不到這個標籤或出現多次")
    m = re.search(pattern, row.raw.text, re.S)
    if m is None:
        return ParsedField.invalid(name, row.lines, f"封面「{row.label}」的值不在範本規格內")
    try:
        value = convert(m[1])
    except (ValueError, TypeError, ArithmeticError):
        value = None
    if value is None:
        return ParsedField.invalid(name, row.lines, f"「{m[1]}」無法辨識")
    return ParsedField.present(name, value, row.lines)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 Natixis 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    head = [squash(x.text) for x in cover[:2]]
    rows = cover_rows(cover)
    zh = squash(_value(rows, "商品中文名稱"))
    en = squash(_value(rows, "商品英文名稱"))
    issuer = squash(_value(rows, "發行機構名稱"))
    ti = TextIndex(cover)
    arts = doc.articles(1)
    failed = []
    for test, message in (
        (
            len(head) == 2 and head[0] == "中文產品說明書" and head[1] == "(適用於專業投資人及OSU客戶)",
            "前兩行不是「中文產品說明書」「(適用於專業投資人及OSU客戶)」",
        ),
        ("TDCC商品代號為" in ti.text and "ISIN為" in ti.text, "封面找不到「TDCC 商品代號為…，ISIN 為…」"),
        (
            zh.startswith(ISSUER) and "(不保本、無擔保及無保證機構)" in zh,
            f"封面商品中文名稱不是以「{ISSUER}」開頭或沒有「(不保本、無擔保及無保證機構)」",
        ),
        (
            en.startswith("Natixis–") and squash(NAME_EN_MARK) in en,
            f"封面商品英文名稱沒有「Natixis –」與「{NAME_EN_MARK}」",
        ),
        (
            issuer.startswith(ISSUER) and _row(rows, "發行人之名稱、電話及地址") is not None,
            f"封面發行機構不是{ISSUER}或沒有「發行人之名稱、電話及地址」",
        ),
        (
            all(n in doc.chapters for n in CHAPTER_NAMES),
            "章名不是「第一章、商品基本資料」…「第五章、特別記載事項」或五章不完整",
        ),
        (set(range(1, ARTICLES + 1)) <= set(arts), f"第一章條號 1–{ARTICLES} 不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


def _value(rows: list[CoverRow], label: str) -> str:
    row = _row(rows, label)
    return row.raw.text if row else ""


# ---------------------------------------------------------------- 讀出


@dataclass
class NatixisTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def read(lines: Sequence[Line]) -> NatixisTermSheet:
    """讀出標準欄位與 Natixis 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    fields: dict[str, ParsedField] = {}

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    _cover(fields, doc.before_chapter1())

    # 第一章
    art1 = article(1)
    put("name_art1_zh", art1, r"1\.商品名稱：(.+?)\(下稱「本商品」\)")
    raw1 = RawText(art1)
    fields["name_art1_en"] = raw1.capture(
        "name_art1_en", r"\(下稱「本商品」\)\s*(Natixis.+?)\s*$", lambda x: non_principal(x.strip())
    )
    put("issuer_name_ch1", article(3), r"發行機構：(.+?)發行機構長期")
    art5 = article(5)
    put("currency_art5", art5, rf"\(1\)計價幣別：({ZH_CCY})\([A-Z]{{3}}\)")
    put("currency_art5_iso", art5, rf"\(1\)計價幣別：{ZH_CCY}\(([A-Z]{{3}})\)")
    denom = rf"\(2\)每單位商品面額：([\d,]+)({CCY})"
    put("denomination", art5, denom, number)
    fields["denomination_currency"] = capture_group(art5, denom, 2, "denomination_currency")
    put("issue_price_pct", art5, r"\(3\)發行價格：" + PCT, Decimal)

    table = underlying_table("underlying_table", article(9))
    fields["underlying_table"] = table
    fields["main_price_table"] = price_table.at("main_price_table", table, PRICE_TABLE)

    art12 = article(12)
    put("tenor_months", art12, r"商品年期：(\d+)個月", int)
    put("trade_date", art12, r"交易日/期初進場日：" + D, parse_date)
    put("issue_date", art12, r"\(3\)發行日：" + D, parse_date)
    put("maturity_date", art12, r"到期日：最終評價日後的第.{1,2}個營業日，即" + D, parse_date)
    put("final_valuation_date", art12, r"最終評價日：" + D, parse_date)
    _article12(fields, art12)

    fields["coupon_pa_pct"] = capture_group(article(13), COUPON, 2, "coupon_pa_pct", Decimal)
    fields["monthly_coupon_pct"] = capture_group(article(13), COUPON, 1, "monthly_coupon_pct", Decimal)

    art14 = article(14)
    fields["ko_memory"] = ko_memory(TextIndex(art14), art14)
    put("ko_def_pct", art14, KO_DEF, Decimal)
    put("autocall_redemption_pct", art14, AUTOCALL_REDEMPTION, Decimal)
    art15 = article(15)
    ti15 = TextIndex(art15)
    put("strike_def_pct", art15, STRIKE_DEF, Decimal)
    put("ki_def_pct", art15, KI_DEF, Decimal)
    fields["ki_type"] = ki_type(fields["ki_day_rule"], ti15, art15)
    put("maturity_redemption_pct", art15, MATURITY_REDEMPTION, Decimal)
    _table_fields(fields, table)
    _schedule(fields)
    assumed = _article16(fields, article(16))
    scenario_currencies = _scenarios(fields, article(16), art15, assumed)

    _chapter2(fields, doc)
    _chapter4(fields, doc, put)
    _occurrences(fields)
    wording = [  # 說明文字的幣別字（Issue #318）；依型態可有可無的句子沒有就不核對
        occ
        for field, n, pattern, where, optional in CURRENCY_WORDING
        if not optional or any(TextIndex(article(n)).finditer(pattern))
        for occ in money.currency_words(article(n), pattern, field, "說明文字幣別", where)
    ]
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [*fields["currency_others"].value, *wording, *scenario_currencies]
    )
    fields["distributor_names_others"] = _distributor_names(doc)
    fields["initial_pricing"] = standard_fields.absent(
        "initial_pricing", "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP）"
    )
    return NatixisTermSheet(fields, TextIndex(doc.lines), doc)


def _cover(fields: dict[str, ParsedField], cover: list[Line]) -> None:
    """封面：標題名稱、商品代號與 ISIN、「標籤：值」各欄、第 2 頁末刊印日期。"""
    ti = TextIndex(cover)
    code = PRODUCT_CODES
    fields["product_code"] = capture("product_code", ti, code, whole_match=True)
    fields["isin"] = capture_group(cover, code, 3, "isin")
    fields["distributor_code"] = distributor_code(ti, code)
    fields["name_title"] = capture(
        "name_title", ti, r"^中文產品說明書\(適用於專業投資人及OSU客戶\)(.+?)TDCC商品代號為", whole_match=True
    )
    rows = cover_rows(cover)
    fields["name_zh"] = _row_field("name_zh", _row(rows, "商品中文名稱"), r"^(.+?)\s*\(下稱「本商品」\)", squash)
    fields["name_en"] = _row_field("name_en", _row(rows, "商品英文名稱"), r"^(Natixis.+?)\s*$")
    fields["product_type"] = _row_field("product_type", _row(rows, "商品種類"), r"^(.+?)\s*$", squash)
    fields["issuer_name_cover"] = _row_field("issuer_name_cover", _row(rows, "發行機構名稱"), r"^(.+?)\s*\(長期")
    ccy = _row(rows, "計價幣別")
    fields["currency_zh"] = _row_field("currency_zh", ccy, rf"^({ZH_CCY})\s*\([A-Z]{{3}}\)\s*$")
    fields["currency_cover_iso"] = _row_field("currency_cover_iso", ccy, rf"^{ZH_CCY}\s*\(([A-Z]{{3}})\)\s*$")
    dist = _row(rows, rf"{DISTRIBUTOR}之名稱、電話及地址")
    pattern = rf"{DISTRIBUTOR}\s*[:：]\s*(.+?)\s*電話\s*[:：]\s*(.+?)\s*地址\s*[:：]\s*(.+?)\s*$"
    fields["distributor_name_cover"] = _row_field("distributor_name_cover", dist, pattern)
    fields["distributor_phone_cover"] = _row_field(
        "distributor_phone_cover", dist, pattern.replace("(.+?)", "(?:.+?)", 1)
    )
    fields["distributor_address_cover"] = _row_field(
        "distributor_address_cover", dist, pattern.replace("(.+?)", "(?:.+?)", 2)
    )
    fields["approval_date"] = _row_field(
        "approval_date",
        _row(rows, rf"{DISTRIBUTOR}審查通過之日期"),
        r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        parse_date,
    )
    fields["print_date"] = capture("print_date", ti, r"中文產品說明書刊印日期：" + D, parse_date, whole_match=True)


# ---------------------------------------------------------------- 第 9 項價格表、第 16 項情境價格表


def underlying_table(name: str, art9: list[Line]) -> ParsedField:
    """第 9 項價格表：「投資組合」那一句之後到「相對權重：」之前。"""
    ti = TextIndex(art9)
    a = ti.text.find("「投資組合」於本商品發行日")
    b = ti.text.find("相對權重：", a)
    region = art9[ti.line_at(a) + 1 : ti.line_at(b)] if 0 <= a < b else []
    return price_table.read(name, region, PRICE_TABLE)


def scenario_table(name: str, lns: list[Line]) -> ParsedField:
    """第 16 項情境假設的價格表：「連結標的(i)」那一行起到「情境一」之前。"""
    ti = TextIndex(lns)
    a = ti.text.find("連結標的(i)")
    b = ti.text.find("情境一", a)
    region = lns[ti.line_at(a) : ti.line_at(b)] if 0 <= a < b else []
    return price_table.read(name, region, SCENARIO_TABLE)


def distributor_code(ti: TextIndex, pattern: str) -> ParsedField:
    """「受託機構商品代號為」的值（NX2、NX8 舊版文件有這個標籤但空白；其他文件沒有這個標籤 → absent）。"""
    found = list(ti.finditer(pattern))
    if len(found) != 1:
        return ParsedField.missing("distributor_code", "找不到「TDCC 商品代號為…，ISIN 為…」或出現多次")
    m = found[0]
    if m[2] is None:
        return standard_fields.absent("distributor_code", "文件沒有「受託機構商品代號為」")
    return ParsedField.present("distributor_code", m[2], ti.lines_for(m.start(), m.end()))


def _table_fields(fields: dict[str, ParsedField], table: ParsedField) -> None:
    """第 9 項價格表 → 標準欄位：標的、各標的價格；K／KO／KI % 主來源是第 14、15 項定義（表頭 % 由規則交叉驗證）。"""
    fields["underlyings"] = price_table.underlyings(table)
    fields["underlying_prices"] = price_table.price_rows(table)
    fields["strike_pct"] = fields["strike_def_pct"]
    fields["ko_pct"] = fields["ko_def_pct"]
    ki_t = fields["ki_type"]
    if ki_t.ok and ki_t.value == "none":
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki_t.evidence), note="說明書無 KI（沒有界限價格）"
        )
    else:
        fields["ki_pct"] = fields["ki_def_pct"]


# ---------------------------------------------------------------- KO、KI、記憶式


def ko_memory(ti: TextIndex, lns: list[Line], where: str = "第一章第 14 項") -> ParsedField:
    """「收盤價皆曾經高於或等於」→ 記憶式；「收盤價皆高於或等於」→ 非記憶式（範本規格 §4.2）。"""
    memory, plain = ti.text.count(MEMORY), ti.text.count(NO_MEMORY)
    if memory + plain != 1:
        return ParsedField.invalid("ko_memory", lns, f"{where}自動提前到期事件的句型缺漏、重複或不在範本規格內")
    anchor = MEMORY if memory else NO_MEMORY
    a = ti.text.find(anchor)
    return ParsedField.present("ko_memory", bool(memory), ti.lines_for(a, a + len(anchor)))


def ki_type(day: ParsedField, ti: TextIndex, lns: list[Line], where: str = "第一章第 15 項") -> ParsedField:
    """KI 型態（範本規格 §4.4）：觸及事件判定日（`day`，值為 AM／D／P）與「觸及事件判定期間」須一致；沒有「觸及事件」
    與「界限價格」定義，且到期贖回情況二只有實物交割（`NO_KI_CASE`）→ none（以 present「none」交出）。"""
    name, text = "ki_type", ti.text
    event = KI_EVENT in text
    period = list(ti.finditer(KI_PERIOD))
    if not event and "「界限價格」" not in text:
        if day.status != FieldStatus.MISSING or period:
            return ParsedField.invalid(name, lns, f"{where}沒有「觸及事件」定義，但有觸及事件判定日或判定期間")
        case = re.search(NO_KI_CASE, text)
        if case is None:
            return ParsedField.invalid(
                name, lns, f"{where}沒有「觸及事件」定義，也找不到無觸及事件時的到期贖回寫法（情況二只有實物交割）"
            )
        return ParsedField.present(name, "none", ti.lines_for(case.start(), case.end()))
    if len(period) != 1:
        return ParsedField.invalid(name, lns, f"{where}「觸及事件判定期間」缺漏、重複或不在範本規格內")
    if not day.ok:
        return derived(name, day)
    whole = period[0][1] == KI_PERIOD_WHOLE
    expected = {"AM": not whole, "D": whole, "P": whole}[day.value]
    ev = list(day.evidence) + [Evidence.of(x) for x in ti.lines_for(period[0].start(), period[0].end())]
    if not expected:
        return ParsedField(
            name, FieldStatus.INVALID, evidence=ev, note=f"觸及事件判定日與{where}「觸及事件判定期間」不一致"
        )
    return ParsedField(name, FieldStatus.PRESENT, day.value, ev)


def ki_day(ti: TextIndex, lns: list[Line], pattern: str, where: str) -> ParsedField:
    """觸及事件判定日寫法 → AM／D／P；沒有這句 → missing（由 `ki_type` 判斷是不是無 KI）。"""
    found = list(ti.finditer(pattern))
    if not found:
        return ParsedField.missing("ki_day_rule", f"{where}沒有觸及事件判定日")
    values = {KI_DAY.get(m[1]) for m in found}
    if len(values) != 1 or None in values:
        return ParsedField.invalid("ki_day_rule", lns, f"{where}觸及事件判定日的寫法不在範本規格內或前後不一")
    m = found[0]
    return ParsedField.present("ki_day_rule", values.pop(), ti.lines_for(m.start(), m.end()))


# ---------------------------------------------------------------- 第 12 項日期表與 KO 排程


ROW3 = re.compile(r"(\d{1,2})" + D * 3)
ROW2 = re.compile(r"(\d{1,2})" + D * 2)
ROW1 = re.compile(r"(\d{1,2})" + D)


def _rows(text: str, pattern: re.Pattern[str]) -> list[tuple[int, list]]:
    """表格的列（期別＋日期）；期別須從 1 起連續。表格中間可能夾著頁底註腳等文字，只取符合的列。"""
    out = []
    for m in pattern.finditer(text):
        if int(m[1]) != len(out) + 1:
            break
        out.append((int(m[1]), [parse_date(x) for x in m.groups()[1:]]))
    return out


def _article12(fields: dict[str, ParsedField], art12: list[Line]) -> None:
    """日期表（KO 每日：配息觀察期間表；KO 每期：配息日表＋自動提前到期觀察日表）、KO 觀察寫法、觸及事件判定日。"""
    ti = TextIndex(art12)
    text = ti.text
    fields["ki_day_rule"] = ki_day(ti, art12, r"觸及事件判定日：(.+?)，並依「中斷日」", "第一章第 12 項")
    daily = list(ti.finditer(KO_DAILY))
    periodic = text.count(KO_PERIODIC)
    if len(daily) == 1 and periodic == 0:
        head = "配息觀察期間(t)配息觀察期間開始日配息觀察期間結束日配息日"
        a = text.find(head)
        b = text.find("自動提前到期觀察日：從第", a)
        if a < 0 or b < 0:
            fields["date_table"] = ParsedField.missing("date_table", "第 12 項找不到配息觀察期間表")
        else:
            rows = _rows(text[a + len(head) : b], ROW3)
            ev = ti.lines_for(a, b)
            if not rows or any(None in d for _, d in rows):
                fields["date_table"] = ParsedField.invalid("date_table", ev, "配息觀察期間表的列不是「期別＋三個日期」")
            else:
                fields["date_table"] = ParsedField.present(
                    "date_table",
                    {
                        "kind": "D",
                        "rows": [{"period": p, "start": d[0], "end": d[1], "payment": d[2]} for p, d in rows],
                    },
                    ev,
                )
        m = daily[0]
        fields["ko_wording"] = ParsedField.present(
            "ko_wording", ("D", CN_NUM.get(m[1])), ti.lines_for(m.start(), m.end())
        )
    elif periodic == 1 and not daily:
        head_pay = "(t)配息日"
        head_ko = "(t)自動提前到期觀察日(t)自動提前到期日(t)"
        a, c = text.find(head_pay), text.find(head_ko)
        b = text.find("自動提前到期觀察日/自動提前到期日", a)
        if min(a, b, c) < 0:
            fields["date_table"] = ParsedField.missing("date_table", "第 12 項找不到配息日表或自動提前到期觀察日表")
        else:
            pays = _rows(text[a + len(head_pay) : b], ROW1)
            ko = _rows(text[c + len(head_ko) :], ROW2)
            ev = ti.lines_for(a, len(text))
            if not pays or not ko or any(None in d for _, d in pays + ko):
                fields["date_table"] = ParsedField.invalid("date_table", ev, "配息日表或自動提前到期觀察日表的列讀不到")
            else:
                fields["date_table"] = ParsedField.present(
                    "date_table",
                    {
                        "kind": "P",
                        "rows": [{"period": p, "payment": d[0]} for p, d in pays],
                        "ko_rows": [{"t": t, "observation": d[0], "autocall": d[1]} for t, d in ko],
                    },
                    ev,
                )
        a = text.find(KO_PERIODIC)
        fields["ko_wording"] = ParsedField.present("ko_wording", ("P", None), ti.lines_for(a, a + len(KO_PERIODIC)))
    else:
        note = "第 12 項自動提前到期觀察日的寫法缺漏、重複或不在範本規格內"
        fields["ko_wording"] = ParsedField.invalid("ko_wording", art12, note)
        fields["date_table"] = ParsedField.invalid("date_table", art12, note)


SCHEDULE_WHERE = "第 12 項日期表"
# 配息觀察期間「始於開始日(不含)」：開始日 = 前一期結束日；第 1 期開始日 = 發行日（範本規格 §4.4）
SCHEDULE_START = standard_fields.PeriodStart("previous_end", "issue_date")


def _schedule_table(value: dict) -> standard_fields.DateTable:
    """日期表：D 為配息觀察期間表（觀察日 = 結束日）；P 為配息日表，觀察日取自動提前到期觀察日表裡自動提前到期日 = 該期
    配息日的那一列（對不到的期別沒有觀察日），沒有開始日欄。"""
    if value["kind"] == "D":
        return standard_fields.DateTable(SCHEDULE_WHERE, schedule.rows_of(value["rows"], "end"), SCHEDULE_START)
    return standard_fields.DateTable(SCHEDULE_WHERE, schedule.mapped_rows(value["rows"], value["ko_rows"]))


def _schedule(fields: dict[str, ParsedField]) -> None:
    """日期表與提前到期排程（`parsers/schedule.py`，範本規格 §4.1）。

    D：k = 第 12 項「從第k個配息觀察期間結束日」，比價日為第 k 期與末期的配息觀察期間結束日。
    P：k = 觀察日表第 1 列自動提前到期日在配息日表的期別（觀察日表的 t 不是期別），各列須依序對到第 k～n 期；
    比價日為每期的自動提前到期觀察日。"""
    wording, table = fields["ko_wording"], fields["date_table"]
    fields["schedule_table"] = schedule.table_field(table, _schedule_table)
    bad = next((p for p in (wording, table) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        return
    kind, k = wording.value
    ev = list(wording.evidence) + list(table.evidence)
    first = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    if kind == "D" and k is None:
        first = ParsedField(
            "first_callable_period", FieldStatus.INVALID, None, ev, note="自動提前到期觀察日的起始期讀不到"
        )
    elif kind == "P":
        rows = table.value["rows"]
        period_of = {r["payment"]: r["period"] for r in rows}
        mapped = [period_of.get(r["autocall"]) for r in table.value["ko_rows"]]
        if any(p is None for p in mapped):
            note = "自動提前到期觀察日表的自動提前到期日不在配息日表內"
        elif mapped != list(range(mapped[0], len(rows) + 1)):
            note = f"自動提前到期觀察日表的期別不是第 {mapped[0]} 期到第 {len(rows)} 期連續"
        else:
            note = ""
        first = (
            ParsedField("first_callable_period", FieldStatus.INVALID, None, ev, note=note)
            if note
            else ParsedField("first_callable_period", FieldStatus.PRESENT, mapped[0], ev)
        )
    obs = ParsedField("ko_observation", FieldStatus.PRESENT, kind, list(wording.evidence))
    fields.update(
        schedule.autocall_schedule(obs, first, fields["schedule_table"], "end" if kind == "D" else "observation")
    )


# ---------------------------------------------------------------- 第 16 項情境分析


def scenario_ko_kind(text: str) -> tuple[str, int | None] | None:
    """情境假設 e. 的 KO 觀察寫法 → (D, k)／(P, None)；不在範本規格內為 None（不合法）。"""
    m = re.match(KO_DAILY_BODY, text)
    if m:
        return "D", CN_NUM.get(m[1])
    return ("P", None) if text.startswith("依第一章第12(7)項所列") else None


def _article16(fields: dict[str, ParsedField], art16: list[Line]) -> list[ScenarioParam]:
    """情境假設（天期、期數、KO 觀察文字、年利率、面額）、情境價格表、情境一提前到期期別、各期配息的年利率（範本規格 §4.9）；
    回傳交給共用情境規則的情境假設與各期配息的年利率（Issue #294）。"""
    ti = TextIndex(art16)
    where = "第 16 項情境假設"
    assumed = [
        ScenarioParam(
            "scenario_denomination",
            f"{where} a. ",
            capture("scenario_denomination", ti, r"a\.每單位商品面額=([\d,]+)", number, whole_match=True),
        ),
        ScenarioParam(
            "scenario_tenor",
            f"{where} c. ",
            capture("scenario_tenor", ti, r"c\.天期：(\d+)個月", int, whole_match=True),
        ),
        ScenarioParam(
            "scenario_coupon_periods",
            f"{where} d. ",
            capture("scenario_periods", ti, r"共(\d+)個配息觀察期間\)", int, whole_match=True),
        ),
        ScenarioParam(
            "scenario_coupon_pa",
            f"{where} f. ",
            capture("scenario_coupon_pct", ti, r"f\.年利率：每年" + PCT, Decimal, whole_match=True),
        ),
    ]
    fields["scenario_ko_wording"] = capture(
        "scenario_ko_wording", ti, r"e\.自動提前到期觀察日：(.+?)f\.年利率", scenario_ko_kind, whole_match=True
    )
    terms: dict[Decimal, list[Line]] = {}  # 各期配息「x (y%/12)」的 y：同一個值一筆
    for m in ti.finditer(r"x\(" + PCT + r"/12\)"):
        terms.setdefault(Decimal(m[1]), []).extend(ti.lines_for(m.start(), m.end()))
    assumed += [
        ScenarioParam("scenario_coupon_pa", "第 16 項各期配息的", ParsedField.present("scenario_coupon_pa", v, lns))
        for v, lns in terms.items()
    ] or [
        ScenarioParam(
            "scenario_coupon_pa",
            "第 16 項各期配息的",
            ParsedField.missing("scenario_coupon_pa", "情境找不到各期配息「x (年利率/12)」"),
        )
    ]
    table = scenario_table("scenario_table", art16)
    fields["scenario_table"] = table
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", table, SCENARIO_TABLE)
    section1 = re.search(r"情境一(.+?)情境二", ti.text)
    first = (
        list(
            re.finditer(
                BULLET + r"在較佳情況下，自動提前到期事件於第" + CN_PERIOD + r"個配息觀察期間結束日發生",
                section1[1],
            )
        )
        if section1
        else []
    )
    if len(first) == 1 and CN_NUM.get(first[0][1]):
        m, base = first[0], section1.start(1)
        fields["scenario_first_period"] = ParsedField.present(
            "scenario_first_period", CN_NUM[m[1]], ti.lines_for(base + m.start(), base + m.end())
        )
    else:
        fields["scenario_first_period"] = ParsedField.missing(
            "scenario_first_period",
            "情境一找不到或有多處「在較佳情況下，自動提前到期事件於第k個配息觀察期間結束日發生」",
        )
    return assumed


def _scenario_spans(ti: TextIndex) -> list[tuple[str, int, int]]:
    """情境一～四（無 KI 為一～三）在 `ti.text` 的範圍（到下一個情境或「關於上述情境分析之警語」為止）；
    找不到、順序不對或不是從情境一連續時為空。"""
    heads = list(ti.finditer(SCENARIO_HEAD))
    end = ti.text.find(SCENARIO_END)
    names = [m[1] for m in heads]
    if len(names) < 3 or names != list("一二三四")[: len(names)] or end < heads[-1].start():
        return []
    starts = [m.start() for m in heads]
    return [(f"情境{n}", a, b) for n, a, b in zip(names, starts, [*starts[1:], end], strict=True)]


def _mentions(
    name: str,
    label: str,
    ti: TextIndex,
    spans: list[tuple[str, int, int]],
    pattern: str,
    convert: Callable,
    *,
    group: int = 1,
    required: bool = True,
) -> list[Occurrence]:
    """情境裡重複寫出的值（Issue #318）：每個情境一筆（欄位 `<name>_<第幾個情境>`），值是該情境裡每一處的值（依序）；
    沒有這句的情境不列（依情境可有可無，例：原始投資本金只在現金交割的情境）。每種型態都會寫的句子（`required`）
    在所有情境都找不到時，交出一筆缺漏（轉人工覆核）。"""
    out = []
    for n, (case, a, b) in enumerate(spans, start=1):
        hits = [m for m in ti.finditer(pattern) if a <= m.start() < b]
        if hits:
            lines = [ln for m in hits for ln in ti.lines_for(m.start(), m.end())]
            pf = ParsedField.present(f"{name}_{n}", tuple(convert(m[group]) for m in hits), lines)
            out.append(Occurrence(f"{name}_{n}", label, f"第 16 項{case}", pf))
    if required and not out:
        out.append(
            Occurrence(name, label, "第 16 項情境", ParsedField.missing(name, f"第 16 項情境找不到{label}的句子"))
        )
    return out


def _scenarios(
    fields: dict[str, ParsedField], art16: list[Line], art15: list[Line], assumed: list[ScenarioParam]
) -> list[Occurrence]:
    """第 16 項情境與第 15 項重複寫出的值（Issue #318，比照 BBVA #305）：各情境配息列數、自動提前到期與到期贖回的 %；
    配息算式／原始投資本金／實物交割算式的面額併入情境假設 `assumed` 交成共用情境規則的 `scenario_params`，實物交割算式的
    執行價格交成 `scenario_prices`（Issue #294）；回傳各情境金額旁的幣別出處（併入 `currency_others`）。"""
    ti, t15 = TextIndex(art16), TextIndex(art15)
    repeats = []  # 情況一公式、2.a（有 KI 才有）：每處一筆（欄位 `<field>_<第幾處>`）
    for field, places, pattern, required in MATURITY_REPEATS:
        hits = list(t15.finditer(pattern))
        if required and not hits:
            missing = ParsedField.missing(field, f"{places[0]}找不到「每單位商品面額 x X%」")
            repeats.append(Occurrence(field, "到期贖回 %", places[0], missing))
        for n, m in enumerate(hits, start=1):
            pf = ParsedField.present(f"{field}_{n}", (Decimal(m[1]),), t15.lines_for(m.start(), m.end()))
            repeats.append(Occurrence(f"{field}_{n}", "到期贖回 %", places[min(n, len(places)) - 1], pf))
    spans = _scenario_spans(ti)
    if not spans:
        missing = "第 16 項找不到情境一～三（或四）到「關於上述情境分析之警語」的段落"
        for name in ("scenario_row_counts", "autocall_redemption_mentions", "maturity_redemption_mentions"):
            fields[name] = ParsedField.missing(name, missing)
        denomination = ScenarioParam(
            "scenario_denomination", "第 16 項情境的", ParsedField.missing("scenario", missing)
        )
        fields["scenario_params"] = standard_fields.occurrences("scenario_params", [*assumed, denomination])
        fields["scenario_prices"] = _worst_strike(fields, ParsedField.missing("scenario_worst_strike", missing))
        pf = ParsedField.missing("scenario_currency", missing)
        return [Occurrence("scenario_currency", "情境金額幣別", "第 16 項情境", pf)]
    rows, coupon_denominations = [], []
    for n, (case, a, b) in enumerate(spans, start=1):
        hits = [(ln, m) for ln in ti.lines_for(a, b) if (m := SCENARIO_COUPON_ROW.search(ln.text))]
        field = f"scenario{n}_rows"
        if not hits:
            rows.append(
                Occurrence(
                    field,
                    "配息列數",
                    f"第 16 項{case}",
                    ParsedField.missing(field, f"第 16 項{case}找不到各期配息「面額 x (年利率/12)」"),
                )
            )
            continue
        lines = [ln for ln, _ in hits]
        rows.append(Occurrence(field, "配息列數", f"第 16 項{case}", ParsedField.present(field, len(hits), lines)))
        field = f"scenario_coupon_denomination_{n}"
        pf = ParsedField.present(field, tuple(number(m[1]) for _, m in hits), lines)
        coupon_denominations.append(Occurrence(field, "配息算式的面額", f"第 16 項{case}", pf))
    fields["scenario_row_counts"] = standard_fields.occurrences("scenario_row_counts", rows)
    denominations = [
        *coupon_denominations,
        *_mentions("scenario_principal", "原始投資本金", ti, spans, SCENARIO_PRINCIPAL, number),
        *_mentions(
            "scenario_physical_denomination",
            "實物交割算式的面額",
            ti,
            spans,
            WORST_STRIKE,
            number,
            group=2,
            required=False,
        ),  # 讀不到時由 `scenario_worst_strike` 轉人工覆核
    ]
    fields["scenario_params"] = standard_fields.occurrences(
        "scenario_params", [*assumed, *(p for occ in denominations for p in _denomination_params(occ))]
    )
    fields["autocall_redemption_mentions"] = standard_fields.occurrences(
        "autocall_redemption_mentions",
        _mentions("scenario_autocall_pct", "自動提前到期贖回 %", ti, spans, SCENARIO_AUTOCALL_PCT, Decimal),
    )
    fields["maturity_redemption_mentions"] = standard_fields.occurrences(
        "maturity_redemption_mentions",
        [*repeats, *_mentions("scenario_maturity_pct", "到期贖回 %", ti, spans, SCENARIO_MATURITY_PCT, Decimal)],
    )
    worst = list(ti.finditer(WORST_STRIKE))
    fields["scenario_prices"] = _worst_strike(
        fields,
        ParsedField.present(
            "scenario_worst_strike",
            (squash(worst[0][1]), number(worst[0][4])),
            ti.lines_for(worst[0].start(), worst[0].end()),
        )
        if len(worst) == 1
        else ParsedField.missing(
            "scenario_worst_strike", "情境找不到或有多處實物交割算式「…(標的)執行價格 = 面額 / 執行價格」"
        ),
    )

    def skip(text: str) -> bool:
        return money.stock_price_line(text) or any(w in text for w in SCENARIO_PRICE_WORDS)

    out = []
    for n, (case, a, b) in enumerate(spans, start=1):
        physical = ti.text.find(PHYSICAL_CALC, a, b)
        found = money.amounts(
            ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, skip=skip, start=a, end=b if physical < 0 else physical
        )
        out.append(
            money.scenario_occurrence(
                f"scenario{n}_currency", f"{case}金額幣別", f"第 16 項{case}", found, ti.lines_for(a, a + 1)
            )
        )
    return out


# 情境裡重複寫出的面額（`_mentions` 的標籤）→ 共用情境規則的（出處代號, 位置接在情境名稱後面的字）
DENOMINATION_PARAMS = {
    "配息算式的面額": ("scenario_denomination", "配息算式的"),
    "原始投資本金": ("scenario_principal", "原始投資"),
    "實物交割算式的面額": ("scenario_denomination", "實物交割算式的"),
}


def _denomination_params(occ: Occurrence) -> list[ScenarioParam]:
    """情境的一種面額句（每個情境一筆，值是該情境每一處的值）→ 共用情境規則的出處：同一個情境裡同一個值一筆。"""
    code, suffix = DENOMINATION_PARAMS[occ.name]
    pf = occ.value
    if not pf.ok:
        return [ScenarioParam(code, f"{occ.where}{suffix}", replace(pf, name=code))]
    return [
        ScenarioParam(code, f"{occ.where}{suffix}", ParsedField(code, FieldStatus.PRESENT, v, list(pf.evidence)))
        for v in dict.fromkeys(pf.value)
    ]


def _worst_strike(fields: dict[str, ParsedField], worst: ParsedField) -> ParsedField:
    """實物交割算式「…(標的)執行價格 = 面額 / 執行價格」→ 共用情境規則 `scenario_prices`：依標的名稱對到情境價格表
    那一列（= 第 9 項價格表同一列，由 `scenario.reprint` 核對）。情境價格表讀不到時不交（已由 `scenario.reprint` 轉
    人工覆核，不重複報）；找不到或有多列同名時認不出是哪一檔。"""
    table, where = fields["scenario_table"], "第 16 項實物交割算式的"
    if not table.ok:
        return standard_fields.occurrences("scenario_prices", [])
    if not worst.ok:
        return standard_fields.occurrences("scenario_prices", [ScenarioPrice(where, None, "strike", worst)])
    name, strike = worst.value
    # 情境價格表的名稱可能和期初進場價格擠在同一行（`特斯拉公司 195.92`），比名稱時去掉結尾的數字
    rows = [n for n, r in enumerate(table.value.rows, start=1) if squash(re.sub(r"[\d,.\s]+$", "", r.name)) == name]
    value = ParsedField("scenario_worst_strike", FieldStatus.PRESENT, strike, list(worst.evidence))
    return standard_fields.occurrences(
        "scenario_prices", [ScenarioPrice(where, rows[0] if len(rows) == 1 else None, "strike", value)]
    )


# ---------------------------------------------------------------- 第二章、第四章


def _chapter2(fields: dict[str, ParsedField], doc: Document) -> None:
    ch2 = doc.articles(2)
    raw1 = RawText(doc.span_lines(ch2.get(1)))
    fields["issuer_name_ch2"] = raw1.capture("issuer_name_ch2", r"\(1\)\s*事業名稱\s*[:：]\s*(.+?)\s*\(2\)\s*設立日期")
    raw3 = RawText(doc.span_lines(ch2.get(3)))
    trustee = (
        rf"3\.\d\s*{DISTRIBUTOR}\s*[:：]\s*{BULLET}\s*事業名稱\s*[:：]\s*(.+?)\s*{BULLET}\s*設立日期.*?{BULLET}\s*營業所在地\s*[:：]\s*(.+?)"
        rf"\s*{BULLET}\s*負責人姓名\s*[:：]\s*(.+?)\s*3\.\d"
    )
    fields["distributor_name_ch2"] = raw3.capture("distributor_name_ch2", trustee)
    fields["distributor_address_ch2"] = raw3.capture("distributor_address_ch2", trustee, group=2)
    fields["chairman"] = raw3.capture("chairman", trustee, group=3)


def _chapter4(fields: dict[str, ParsedField], doc: Document, put: Callable) -> None:
    ch4 = doc.articles(4)

    def art(n: int) -> list[Line]:
        return doc.span_lines(ch4.get(n))

    put("subscription_start", art(1), r"開始受理申購日期：" + D, parse_date)
    put("subscription_end", art(1), r"結束受理申購日期：" + D, parse_date)
    fields.update(fee_rates(art(2)))
    amount = rf"\d+單位商品面額，即([\d,]+)({CCY})"
    subscribe = rf"本商品最低申購金額為{amount}；並以{amount}為累加申購金額"
    redeem = rf"本商品最低贖回金額為{amount}；並以{amount}為累加贖回金額"
    for name, pattern, group in (
        ("minimum_subscription", subscribe, 1),
        ("minimum_subscription_currency", subscribe, 2),
        ("minimum_additional", subscribe, 3),
        ("minimum_additional_currency", subscribe, 4),
        ("minimum_redemption", redeem, 1),
        ("minimum_redemption_currency", redeem, 2),
        ("redemption_increment", redeem, 3),
        ("redemption_increment_currency", redeem, 4),
    ):
        lns = art(4) if "subscription" in name or "additional" in name else art(8)
        fields[name] = capture_group(
            lns, pattern, group, name, number if not name.endswith("currency") else lambda x: x
        )
    fields["issue_price_ch4"] = capture_group(
        art(5), r"申購價金＝投資人申購單位數x每單位商品面額x發行價格\(" + PCT + r"\)", 1, "issue_price_ch4", Decimal
    )


def _distributor_names(doc: Document) -> ParsedField:
    """受託機構名稱的其他出處（Issue #318）：封面警語與第三章第 5 項警語各 3 處、第四章第 10 項 2 處、第 14 項 1 處；
    每處都是固定寫法，找不到時為缺漏，由審查標準規則轉人工覆核。"""
    ch3, ch4 = doc.articles(3), doc.articles(4)
    places = (
        ("cover", "封面", TextIndex(doc.before_chapter1())),
        ("ch3", "第三章第 5 項", TextIndex(doc.span_lines(ch3.get(5)))),
    )
    items = [
        Occurrence(
            f"distributor_name_{tag}_{key}",
            "受託機構名稱（警語）",
            where + label,
            capture(f"distributor_name_{tag}_{key}", ti, pattern),
        )
        for tag, where, ti in places
        for key, label, pattern in WARNING_NAMES
    ]
    items += [
        Occurrence(field, "受託機構名稱", where, capture(field, TextIndex(doc.span_lines(ch4.get(n))), pattern))
        for n, field, where, pattern in CH4_NAMES
    ]
    return standard_fields.occurrences("distributor_names_others", items)


# ---------------------------------------------------------------- 出處清單


def _occurrences(fields: dict[str, ParsedField]) -> None:
    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_subscription", "最低申購金額", "第四章第 4 項最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "第四章第 4 項累加申購金額", fields["minimum_additional"]),
            occ("minimum_redemption", "最低贖回金額", "第四章第 8 項最低贖回金額", fields["minimum_redemption"]),
            occ("redemption_increment", "增加贖回單位", "第四章第 8 項累加贖回金額", fields["redemption_increment"]),
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
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [occ("issue_price_ch4", "發行價格（申購價金）", "第四章第 5 項申購價金之計算", fields["issue_price_ch4"])],
    )
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")
    fields.update(
        standard_fields.absent_all(
            [x for x in standard_fields.DISTRIBUTOR_OTHERS if x != "distributor_names_others"],
            "受託機構地址、負責人的其他出處",
        )
    )
    currencies = (  # 金額旁的幣別字（Issue #170）：價格表貨幣欄不列入；說明文字與情境另在 read 加入（Issue #318）
        ("denomination_currency", "每單位商品面額幣別", "第一章第 5 項每單位商品面額"),
        ("minimum_subscription_currency", "最低申購金額幣別", "第四章第 4 項最低申購金額"),
        ("minimum_additional_currency", "累加申購金額幣別", "第四章第 4 項累加申購金額"),
        ("minimum_redemption_currency", "最低贖回金額幣別", "第四章第 8 項最低贖回金額"),
        ("redemption_increment_currency", "累加贖回金額幣別", "第四章第 8 項累加贖回金額"),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [occ(key, label, where, fields[key]) for key, label, where in currencies]
    )
