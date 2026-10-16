"""JPM 中文產品說明書（docs/templates/jpm-zh-product-description.md）：部分＋條號＋子項＋標籤定位，保留原文證據。

章名「I. 商品基本資料」（羅馬數字＋句點，五部分；`III.` 後沒有空白）；條號「N.」在左邊界（常獨立一行、標題在下一行）；
封面是兩欄表（標籤在左、值在右）。頁碼「N/M」不算內文。頁尾註腳的上標數字會夾在本文中（例：`中斷日效果` / `2 」條款`、
日期表表頭 `評價日(t)` / `3`），所有欄位都在去空白的全文索引上比對，句型容許這些數字。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence, ScenarioParam, ScenarioPrice
from ..text import squash
from . import money, price_table, schedule
from .common import NUM, Mention, dec, derived, number
from .layout import DATE_RE, Document, LayoutSpec, TextIndex, capture, join_text, parse_date

TEMPLATE_ID = "jpm-zh-pd"
# 2：價格表改由 parsers/price_table.py 讀（Issue #287）；3：情境重複寫出的值（Issue #315）；4：日期表標準欄位與共用排程推導（Issue #289）；
# 5：情境價格表、假設值與執行價交成共用情境規則的標準欄位（Issue #294）
PARSER_VERSION = "5"
CHAPTER_NAMES = {
    1: ("I", "商品基本資料"),
    2: ("II", "相關機構事業概況"),
    3: ("III", "商品風險揭露"),
    4: ("IV", "一般交易事項"),
    5: ("V", "特別記載事項"),
}
# 條號「N.」x≈14（多半獨立一行，標題在下一行；10. 以後常同一行）；子項「(n)」x≈24
LAYOUT = LayoutSpec(
    r"{zh}\.\s?{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    20,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    30,
    CHAPTER_NAMES,
)
COVER_TITLE = "中文產品說明書"
ISSUER = "荷蘭商J.P. Morgan Structured Products B.V."
NAME_ZH_HEAD = "摩根大通"
NAME_EN_HEAD = "JPMorgan"
NAME_EN_BODY = "Link to Equity Securities Callable Fixed Coupon Note"
ARTICLES = 26  # 第 I 部分條數
PAGE_MARK = re.compile(r"^(\d+)\s*/\s*(\d+)$")  # 頁碼「N/M」（頁面底部；第 2 組為總頁數）
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
FN = r"\d?"  # 夾在本文中的註腳上標數字（去空白後緊接在標籤後）
FEES = ("申購費用", "提前贖回費用", "分銷費用")
RATE = r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)"
FEE_PATTERNS = {  # 第 IV 部分 2. 費用表（去空白）：費用項目 → 該列「費率（百分比）」欄
    "申購費用": r"申購費用申購價金的" + RATE,
    "提前贖回費用": r"提前贖回費用投資人提前贖回價金的" + RATE,
    "分銷費用": r"分銷費用[(（].*?[)）]申購價金的" + RATE,
}
COVER_LABELS = (
    "商品代號",
    "國際證券編碼(ISIN)",
    "商品中文名稱",
    "商品英文名稱",
    "商品種類",
    "發行機構註冊地",
    "計價幣別",
    "總代理人之名稱、電話及地址",
    "保證機構之名稱及地址",
    "受託機構之名稱、電話及地址",
    "受託機構審查通過之日期及文號",
    "公會審查通過之日期及文號",
)
VALUE_X = 150.0  # 封面兩欄表：標籤在左（x < 150）、值在右
WARNING_HEAD = "商品之投資風險警語"
NAME_TAIL = re.compile(r"\s*[(（]下稱「本商品」[)）]\s*$")
TRAILING_PLACE = re.compile(r"\s*[(（]營業活動所在地[)）]\s*$")  # 地址後的「(營業活動所在地)」不屬於地址
# 價格表：第 9 項價格表（以彭博代碼切列，價格下一行是幣別字）、第 15 項假設價格表（「連結標的n」各一列，以價格切列）
PRICE_TABLE = price_table.TableSpec(
    where="第 9 項價格表",
    initial="期初價格",
    columns=(
        price_table.Column("strike", "執行價"),
        price_table.Column("ko", "自動提前出場價", required=False),
        price_table.Column("ki", "觸碰價", required=False),
    ),
    anchor="ticker",
    code="彭博代碼",
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="第 15 項假設價格表", anchor="price", code=None)
DATE_HEADS = {  # 日期表表頭（去空白）→ 欄
    "評價日(t)": "valuation",
    "配息支付日(t)": "payment",
    "觀察期間起始日(t)": "start",
    "觀察期間終止日(t)": "end",
}
TABLE_KINDS = {  # 表頭欄（依序）→ 日期表型態（範本規格 §5.1）
    ("valuation", "payment", "start", "end"): "D-4",
    ("start", "end", "payment"): "D-3",
    ("valuation", "payment"): "P",
}
# 第 8 項「自動提前出場評價日：」（範本規格 §4.1）：D 型起始欄名與 k；P 型 k 與最後一期
KO_DAILY = re.compile(r"於(評價日|觀察期間終止日)\(t\)\(t=(\d+)\)\(含\)起至最終評價日\(含\)止之每一個預定交易日")
KO_PERIODIC = re.compile(r"於任一評價日\(t\)\(t=(\d+)(?:-(\d+))?\)")
KO_COLUMN = {"評價日": "valuation", "觀察期間終止日": "end"}
# 第 8 項「自動提前出場事件：」句型（範本規格 §4.2）
MEMORY_YES = "所有連結標的(i)同時於該自動提前出場評價日或分別於不同自動提前出場評價日"
MEMORY_NO = "所有連結標的(i)於該自動提前出場評價日之股票收盤價大於或等於"
MEMORY_SINGLE = "連結標的於該自動提前出場評價日之股票收盤價大於或等於自動提前出場價"
NO_KI_CLAUSE = r"\(1\)若於最後評價日時(?:最差表現)?連結標的之股票收盤價大於或等於其執行價"  # 無 KI 的到期贖回第 (1) 款
KI_CLAUSES = {  # 有 KI 的到期贖回款（範本規格 §4.4）
    "AM": ("(1)若觸碰事件未發生：", "(2)若觸碰事件已發生，則"),
    "D": ("(1)若觸碰事件未發生：", "(2)若觸碰事件已發生，且"),
}
PRO_RATA = "自動提前出場配息金額："  # D 型才有的按日計息配息
PRO_RATA_RATE = r"自動提前出場配息金額：每單位面額x" + PCT + r"x1/12xn/N"  # 其中的年利率 Y
WORST_DEFINITION = "最差表現連結標的："  # 多標的才有的定義句
# 第 15 項情境 (i)～(iii) 裡重複寫出的值（Issue #315；去空白比對）：情境到「請注意：情境分析結果不保證」為止
SCENARIOS = (("(i)最佳情境", "情境 (i)"), ("(ii)一般情境", "情境 (ii)"), ("(iii)最差情境", "情境 (iii)"))
SCENARIO_END = "請注意：情境分析結果不保證"
SCENARIO_COUPON = r"每次配息金額=" + money.NUMBER + CCY + "x" + PCT + "x1/12="  # 年利率 Y
SCENARIO_PRINCIPAL = {  # 情境裡重複寫出的本金（= 每單位面額）：寫法 → （出處代號, 位置, 樣式）；出現在哪些情境見 PRINCIPAL_CASES
    "每次配息金額的本金": ("scenario_principal", "每次配息金額的", r"每次配息金額=(" + money.NUMBER + ")" + CCY),
    "到期贖回金額": ("scenario_principal", "到期贖回金額的", r"到期贖回金額=(" + money.NUMBER + ")" + CCY),
    "交割股數算式的每單位面額": (
        "scenario_denomination",
        "交割股數算式的",
        r"每單位面額/執行價x適用匯率=(" + money.NUMBER + ")" + CCY,
    ),
}
PRINCIPAL_CASES = {  # 情境 (i)～(iii) 各自一定有的本金句（27 份樣本都有，沒有就轉人工覆核）
    "每次配息金額的本金": (1, 2, 3),
    "到期贖回金額": (1, 2),
    "交割股數算式的每單位面額": (3,),
}
SCENARIO_STRIKE = r"每單位面額/執行價x適用匯率=" + money.NUMBER + CCY + "/(" + money.NUMBER + ")" + CCY
SCENARIO_WORST = r"此假設最差表現連結標的為連結標的(\d+)"


class KoWording(NamedTuple):
    """第 8 項「自動提前出場評價日：」讀出的寫法（範本規格 §4.1）。"""

    observation: str  # D 期間每日觀察／P 每期定日觀察
    first: int  # 起始期 k（Non-Call）
    column: str | None  # D 型起始那期取日期表的哪一欄（valuation 評價日／end 觀察期間終止日）；P 型為 None
    last: int | None  # P 型 t=k-N 的 N（只有一期時同 k）；D 型為 None


def document(lines: Sequence[Line]) -> Document:
    """版面結構；頁碼「N/M」不算內文。"""
    return Document([ln for ln in lines if not PAGE_MARK.match(ln.text.strip())], LAYOUT)


# ---------------------------------------------------------------- 封面兩欄表


def cover_items(lines: Sequence[Line]) -> dict[str, list[Line]]:
    """封面「商品代號：」到「公會審查通過之日期及文號：」的兩欄表：標籤（去空白、去冒號）→ 右欄的值行。"""
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
        elif current is not None and ln.x0 >= VALUE_X and s:
            items[current].append(ln)
    return items


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 JPM 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    items = cover_items(cover)
    first = squash(cover[0].text) if cover else ""
    codes = squash(join_text(items.get("商品代號") or []))
    seat = squash(join_text(items.get("發行機構註冊地") or []))
    zh = squash(join_text(items.get("商品中文名稱") or []))
    en = squash(join_text(items.get("商品英文名稱") or []))
    arts = doc.articles(1)
    failed = []
    for test, message in (
        (
            first == COVER_TITLE and codes.startswith("TDCC商品代號為"),
            "封面不是「中文產品說明書」接「商品代號：TDCC商品代號為…」",
        ),
        (seat.startswith("發行機構為" + squash(ISSUER)), f"封面發行機構註冊地不是「發行機構為{ISSUER}…」"),
        (zh.startswith(NAME_ZH_HEAD), f"商品中文名稱不是 {NAME_ZH_HEAD} 開頭"),
        (
            en.startswith(NAME_EN_HEAD) and squash(NAME_EN_BODY) in en,
            f"商品英文名稱不是 {NAME_EN_HEAD} 開頭、含「{NAME_EN_BODY}」",
        ),
        (
            all(n in doc.chapters for n in range(1, 6)),
            "章名不是「I. 商品基本資料」…「V. 特別記載事項」寫法或五部分不完整",
        ),
        (set(range(1, ARTICLES + 1)) <= set(arts), f"第 I 部分條號 1–{ARTICLES} 不連續"),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class JpmTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document
    annual_mentions: list[
        Mention
    ]  # 年利率的其他出處（D 型第 8 項自動提前出場配息金額；第 15 項情境的交給共用情境規則）

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def _item(items: dict[str, list[Line]], label: str, name: str, pattern: str | None = None) -> ParsedField:
    """封面兩欄表某項的值（跨行合併）；`pattern` 取其中一段（第 1 組，在原文上比對，保留英文空白）。"""
    lns = items.get(label) or []
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    text = join_text(lns).strip()
    if pattern is None:
        return ParsedField.present(name, text, lns)
    m = re.fullmatch(pattern, text, re.S)
    if not m:
        return ParsedField.invalid(name, lns, f"封面「{label}」的值格式未知")
    return ParsedField.present(name, m[1].strip(), lns)


def _first_value(items: dict[str, list[Line]], label: str, name: str, pattern: str) -> ParsedField:
    """封面兩欄表某項的第一行（機構名稱與電話在同一行）以 `pattern` 取第 1 組。"""
    lns = (items.get(label) or [])[:1]
    if not lns:
        return ParsedField.missing(name, f"找不到封面「{label}」")
    m = re.fullmatch(pattern, lns[0].text.strip())
    if not m:
        return ParsedField.invalid(name, lns, f"封面「{label}」的值格式未知")
    return ParsedField.present(name, m[1].strip(), lns)


def _labelled(name: str, lines: Sequence[Line], label: str, strip: re.Pattern[str] | None = None) -> ParsedField:
    """以「標籤：值」開頭的那一行（去掉前面的子項標號）的值；標籤比對去空白，`strip` 去掉值結尾的說明。"""
    hits = []
    for ln in lines:
        text = re.sub(r"^\s*(?:[(（]\d{1,2}[)）]\s*)?", "", ln.text)
        if squash(text).startswith(squash(label)):
            m = re.match(r"^\s*" + "".join(re.escape(c) + r"\s*" for c in squash(label)) + r"(.*)$", text)
            value = m[1].strip() if m else ""
            hits.append((strip.sub("", value) if strip else value, [ln]))
    return ParsedField.from_hits(name, hits, missing_note=f"找不到「{label}」")


def read(lines: Sequence[Line]) -> JpmTermSheet:
    """讀出標準欄位與 JPM 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
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
    code_lines = items.get("商品代號") or []
    codes = re.fullmatch(r"TDCC商品代號為(\d{12})、受託機構商品代號為(\d{12}|)", squash(join_text(code_lines)))
    for key, group in (("product_code", 1), ("trustee_product_code", 2)):
        if codes:
            fields[key] = ParsedField.present(key, codes[group], code_lines)
        elif code_lines:
            fields[key] = ParsedField.invalid(
                key, code_lines, "封面商品代號的寫法未知（TDCC商品代號為…、受託機構商品代號為…）"
            )
        else:
            fields[key] = ParsedField.missing(key, "找不到封面「商品代號」")
    fields["isin"] = _item(items, "國際證券編碼(ISIN)", "isin", r"([A-Z]{2}[A-Z0-9]{10})")
    zh_lines = items.get("商品中文名稱") or []
    zh = join_text(zh_lines).strip()
    fields["name_zh"] = (
        ParsedField.present("name_zh", NAME_TAIL.sub("", zh), zh_lines)
        if NAME_TAIL.search(zh)
        else ParsedField.invalid("name_zh", zh_lines, "商品中文名稱找不到「(下稱「本商品」)」")
        if zh_lines
        else ParsedField.missing("name_zh", "找不到封面「商品中文名稱」")
    )
    fields["name_en"] = _item(items, "商品英文名稱", "name_en")
    fields["product_type"] = _item(items, "商品種類", "product_type")
    fields["currency_zh"] = _item(items, "計價幣別", "currency_zh")
    fields["issuer_name_cover"] = _item(items, "發行機構註冊地", "issuer_name_cover", r"發行機構為(.+?)，.*")
    fields["agent_name_cover"] = _first_value(
        items, "總代理人之名稱、電話及地址", "agent_name_cover", r"(.+?)、電話：.*"
    )
    guarantor = [ln for ln in items.get("保證機構之名稱及地址") or [] if not squash(ln.text).startswith("地址：")]
    fields["guarantor_name_cover"] = (
        ParsedField.present("guarantor_name_cover", join_text(guarantor).strip(), guarantor)
        if guarantor
        else ParsedField.missing("guarantor_name_cover", "找不到封面「保證機構之名稱及地址」")
    )
    dist = "受託機構之名稱、電話及地址"
    fields["distributor_name_cover"] = _first_value(items, dist, "distributor_name_cover", r"(.+?)、電話：.*")
    fields["distributor_phone_cover"] = _first_value(items, dist, "distributor_phone_cover", r".+?、電話：(.+)")
    fields["distributor_address_cover"] = _labelled(
        "distributor_address_cover", items.get(dist) or [], "地址：", TRAILING_PLACE
    )
    approval = _item(items, "受託機構審查通過之日期及文號", "approval_date")
    value = parse_date(approval.value) if approval.ok else None
    fields["approval_date"] = (
        ParsedField("approval_date", FieldStatus.INVALID, evidence=approval.evidence, note="審查通過日期不是日期")
        if approval.ok and value is None
        else derived("approval_date", approval, value)
    )
    put("print_date", cover, r"產品說明書刊印日期：" + D, parse_date)
    fields["issuer_name_ch1"] = standard_fields.absent("issuer_name_ch1", "第 I 部分只寫中文的發行機構名稱")

    # 第 I 部分
    put("name_art1", article(1), r"商品名稱：(.+)")
    put("guarantor_name_ch1", article(3), r"保證機構名稱及其長期債務信用評等：(.+?)，長期債務信用評等")
    art5 = article(5)
    put("issue_price_pct", art5, r"發行價格：每單位面額之" + PCT, Decimal)
    put("denomination", art5, r"每單位面額：" + NUM + CCY, number)
    put("denomination_currency", art5, rf"每單位面額：{money.NUMBER}({CCY})")
    put("currency_art5", art5, rf"計價幣別：({CCY})$")

    art8 = article(8)
    t8 = TextIndex(art8)
    put("coupon_pa_pct", art8, r"(?<!出場)配息金額：每單位面額x" + PCT + r"x1/12", Decimal)
    put("strike_pct", art8, r"執行價：" + PCT + r"x期初價格", Decimal)
    put("ko_pct", art8, r"自動提前出場價：" + PCT + r"x期初價格", Decimal)
    ki_raw = capture("ki_pct", t8, r"觸碰價：" + PCT + r"x期初價格", Decimal, whole_match=True)
    fields["ki_type"] = ki_type(t8, art8)
    ki = fields["ki_type"]
    if ki.ok and ki.value == "none" and not ki_raw.ok:
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence), note="說明書無 KI（第 8 項沒有觸碰價）"
        )
    else:
        fields["ki_pct"] = ki_raw
    put(
        "maturity_redemption_pct",
        art8,
        r"\(1\)若(?:於最後評價日時(?:最差表現)?連結標的之股票收盤價大於或等於其執行價|觸碰事件未發生)：每單位面額x"
        + PCT,
        Decimal,
    )
    fields["ko_wording"] = ko_wording(t8, art8)
    fields["ko_memory"] = ko_memory(t8, art8)
    fields["pro_rata"] = pro_rata(t8, art8)
    worst = t8.text.find(WORST_DEFINITION)
    fields["worst_of_wording"] = ParsedField.present(
        "worst_of_wording", worst >= 0, t8.lines_for(worst, worst + len(WORST_DEFINITION)) if worst >= 0 else art8[:1]
    )
    annual = [
        Mention(
            f"第 8 項自動提前出場配息金額（第 {i} 處）",
            Decimal(m[1]),
            tuple(Evidence.of(x) for x in t8.lines_for(m.start(), m.end())),
        )
        for i, m in enumerate(t8.finditer(PRO_RATA_RATE), start=1)
    ]

    # 第 9 項價格表
    table = price_table.read(
        "underlying_table", table_region(article(9), "連結標的資產、相對權重", "相對權重"), PRICE_TABLE
    )
    fields["underlying_table"] = table
    fields["underlyings"] = price_table.underlyings(table)
    fields["underlying_prices"] = price_rows(table)
    fields["initial_pricing"] = standard_fields.absent("initial_pricing", "期初定價（收盤價／VWAP）字樣")

    # 第 12 項日期
    art12 = article(12)
    put("tenor_months", art12, r"\(1\)商品期間：(\d+)個月", int)
    put("trade_date", art12, r"\(2\)交易日/期初評價日" + FN + "：" + D, parse_date)
    put("issue_date", art12, r"\(3\)發行日：" + D, parse_date)
    put("final_valuation_date", art12, r"\(4\)最後評價日：" + D, parse_date)
    put("maturity_date", art12, r"\(6\)到期日：" + D, parse_date)
    fields["date_table"] = date_table(art12)
    _schedule(fields)

    # 第 15 項假設價格表
    art15 = article(15)
    put("scenario_principal", art15, r"假設原計價幣別本金為" + NUM + CCY, number)
    put("scenario_principal_currency", art15, rf"假設原計價幣別本金為{money.NUMBER}({CCY})")
    scenario_table = price_table.read(
        "scenario_table", table_region(art15, "假設原計價幣別本金為", "(i)"), SCENARIO_TABLE
    )
    fields["main_price_table"] = price_table.at("main_price_table", table, PRICE_TABLE)
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", scenario_table, SCENARIO_TABLE)
    scenario_currencies = _scenarios(fields, art15)

    # 第 II 部分
    ch2 = doc.articles(2)
    fields["issuer_name_ch2"] = _labelled("issuer_name_ch2", doc.span_lines(ch2.get(1)), "事業名稱：")
    fields["guarantor_name_ch2"] = _labelled("guarantor_name_ch2", doc.span_lines(ch2.get(2)), "事業名稱：")
    blocks = doc.subitems(ch2.get(3))
    agent = _block(doc, blocks, "總代理人")
    fields["agent_name_ch2"] = _labelled("agent_name_ch2", agent, "事業名稱：")
    trustee = _block(doc, blocks, "受託機構")
    fields["distributor_name_ch2"] = _labelled("distributor_name_ch2", trustee, "事業名稱：")
    fields["distributor_address_ch2"] = _labelled("distributor_address_ch2", trustee, "營業所在地：", TRAILING_PLACE)
    fields["chairman"] = _labelled("chairman", trustee, "負責人姓名：")
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第 IV 部分受託機構電話")
    fields.update(standard_fields.absent_all(standard_fields.DISTRIBUTOR_OTHERS, "受託機構的其他出處"))

    # 第 IV 部分
    ch4 = doc.articles(4)
    put("subscription_start", doc.span_lines(ch4.get(1)), r"開始受理申購日期：" + D, parse_date)
    fee_ti = TextIndex(doc.span_lines(ch4.get(2)))
    for label in FEES:
        key = standard_fields.fee_field(label)
        fields[key] = capture(key, fee_ti, FEE_PATTERNS[label], whole_match=True)
    art4 = doc.span_lines(ch4.get(4))
    put("minimum_subscription", art4, r"最低申購金額：" + NUM + CCY, number)
    put("minimum_additional", art4, r"最低加購金額：" + NUM + CCY, number)
    put("minimum_subscription_currency", art4, rf"最低申購金額：{money.NUMBER}({CCY})")
    put("minimum_additional_currency", art4, rf"最低加購金額：{money.NUMBER}({CCY})")
    put("issue_price_ch4", doc.span_lines(ch4.get(5)), r"申購單位數x每單位面額x" + PCT, Decimal)
    fields["minimum_issue_size"] = standard_fields.absent("minimum_issue_size", "發行不成立的金額門檻")
    part4_item8 = doc.span_lines(ch4.get(8))
    put("minimum_redemption", part4_item8, r"最低贖回金額或單位數：" + NUM + CCY, number)
    put("minimum_redemption_currency", part4_item8, rf"最低贖回金額或單位數：{money.NUMBER}({CCY})")

    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ(
                "minimum_subscription",
                "minimum_subscription",
                "第 IV 部分第 4 項最低申購金額",
                fields["minimum_subscription"],
            ),
            occ(
                "minimum_additional",
                "minimum_additional",
                "第 IV 部分第 4 項最低加購金額",
                fields["minimum_additional"],
            ),
            occ(
                "minimum_redemption",
                "minimum_redemption",
                "第 IV 部分第 8 項最低贖回金額",
                fields["minimum_redemption"],
            ),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ(
                "subscription_start",
                "subscription_start",
                "第 IV 部分第 1 項開始受理申購日期",
                fields["subscription_start"],
            )
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "print_date", "封面產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.occurrences(
        "issue_price_others",
        [
            occ(
                "issue_price_ch4",
                "issue_price_subscription",
                "第 IV 部分第 5 項申購價金之計算",
                fields["issue_price_ch4"],
            )
        ],
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others",
        [
            occ(
                "denomination_currency",
                "denomination_currency",
                "第 I 部分第 5 項每單位面額",
                fields["denomination_currency"],
            ),
            occ(
                "scenario_principal_currency",
                "scenario_principal_currency",
                "第 15 項假設原計價幣別本金",
                fields["scenario_principal_currency"],
            ),
            occ(
                "minimum_subscription_currency",
                "minimum_subscription_currency",
                "第 IV 部分第 4 項最低申購金額",
                fields["minimum_subscription_currency"],
            ),
            occ(
                "minimum_additional_currency",
                "minimum_additional_currency",
                "第 IV 部分第 4 項最低加購金額",
                fields["minimum_additional_currency"],
            ),
            occ(
                "minimum_redemption_currency",
                "minimum_redemption_currency",
                "第 IV 部分第 8 項最低贖回金額",
                fields["minimum_redemption_currency"],
            ),
            *scenario_currencies,
        ],
    )
    return JpmTermSheet(fields, TextIndex(doc.lines), doc, annual)


# ---------------------------------------------------------------- 第 8 項：KO 與 KI 寫法（投資人須知贖回價金段同句型）


def ko_wording(ti: TextIndex, lines: list[Line], where: str = "第 8 項") -> ParsedField:
    """「自動提前出場評價日：」→ `KoWording`（KO 觀察方式、起始期、D 型起始欄或 P 型最後一期；範本規格 §4.1）。

    投資人須知贖回價金段的句型相同（parsers/jpm_iis.py），`where` 是錯訊裡的出處。"""
    name = "ko_wording"
    found = list(ti.finditer(r"自動提前出場評價日：(.{0,80}?)，並應"))
    if not found:
        return ParsedField.missing(name, f"{where}找不到「自動提前出場評價日：」")
    if len(found) > 1:
        return ParsedField.invalid(name, lines, f"{where}「自動提前出場評價日：」出現多次")
    body, lns = found[0][1], ti.lines_for(found[0].start(), found[0].end())
    daily = KO_DAILY.fullmatch(body)
    if daily:
        return ParsedField.present(name, KoWording("D", int(daily[2]), KO_COLUMN[daily[1]], None), lns)
    periodic = KO_PERIODIC.fullmatch(body)
    if periodic:
        k = int(periodic[1])
        return ParsedField.present(name, KoWording("P", k, None, int(periodic[2] or k)), lns)
    return ParsedField.invalid(name, lns, f"{where}自動提前出場評價日的寫法不在範本規格內")


def ko_memory(ti: TextIndex, lines: list[Line], where: str = "第 8 項") -> ParsedField:
    """「自動提前出場事件：」句型 → 記憶式（範本規格 §4.2）；單一標的（沒有記憶條款）判非記憶式。"""
    name = "ko_memory"
    text = ti.text
    found = [(v, p, text.find(p)) for v, p in ((True, MEMORY_YES), (False, MEMORY_NO), (False, MEMORY_SINGLE))]
    hits = [(v, p, i) for v, p, i in found if i >= 0]
    if len(hits) != 1:
        return ParsedField.invalid(name, lines, f"{where}自動提前出場事件的句型不在範本規格內或出現多種")
    v, phrase, i = hits[0]
    return ParsedField.present(name, v, ti.lines_for(i, i + len(phrase)))


def pro_rata(ti: TextIndex, lines: list[Line], where: str = "第 8 項") -> ParsedField:
    """有沒有 D 型的「自動提前出場配息金額：每單位面額 x Y% x 1/12 x n/N」；有標籤但算式寫法不同 → 不合法（轉人工覆核）。"""
    name = "pro_rata"
    i = ti.text.find(PRO_RATA)
    if i < 0:
        return ParsedField.present(name, False, lines[:1])
    lns = ti.lines_for(i, i + len(PRO_RATA))
    if not re.search(PRO_RATA_RATE, ti.text):
        return ParsedField.invalid(name, lns, f"{where}自動提前出場配息金額的算式不在範本規格內")
    return ParsedField.present(name, True, lns)


def ki_type(ti: TextIndex, lines: list[Line], where: str = "第 8 項") -> ParsedField:
    """「觸碰事件：」寫法 → KI 型態（範本規格 §4.4）；沒有觸碰事件且到期只看執行價 → 無 KI（present「none」）。"""
    name = "ki_type"
    text = ti.text
    m = re.search(r"觸碰事件：若計算代理機構判定，(.{0,60}?)，則發生觸碰事件", text)
    if m is None:
        if "觸碰價" in text or "觸碰事件" in text:
            return ParsedField.invalid(name, lines, f"{where}有觸碰價，但觸碰事件的寫法不在範本規格內")
        n = re.search(NO_KI_CLAUSE, text)
        if n is None:
            return ParsedField.invalid(name, lines, f"{where}到期贖回的寫法不在範本規格內")
        return ParsedField.present(name, "none", ti.lines_for(n.start(), n.end()))
    body, lns = m[1], ti.lines_for(m.start(), m.end())
    if body.startswith("在最後評價日時"):
        kind = "AM"
    elif body.startswith("在觸碰事件評價日時") and "觸碰事件評價日：自交易日(含)起至最後評價日(含)止" in text:
        kind = "D"
    else:
        return ParsedField.invalid(name, lns, f"{where}觸碰事件的觀察寫法不在範本規格內")
    # 到期贖回款須與觸碰事件寫法一致（範本規格 §4.4）
    if re.search(NO_KI_CLAUSE, text) or not all(c in text for c in KI_CLAUSES[kind]):
        return ParsedField.invalid(name, lns, f"{where}到期贖回款與觸碰事件的寫法不一致")
    return ParsedField.present(name, kind, lns)


# ---------------------------------------------------------------- 價格表與假設價格表


def table_region(lines: list[Line], start: str, end: str) -> list[Line]:
    """`start` 那一行之後到 `end` 開頭的那一行之前（去空白比對開頭）。"""
    a = next((i for i, ln in enumerate(lines) if squash(ln.text).startswith(squash(start))), None)
    if a is None:
        return []
    b = next((i for i in range(a + 1, len(lines)) if squash(lines[i].text).startswith(squash(end))), len(lines))
    return lines[a + 1 : b]


def price_rows(table: ParsedField) -> ParsedField:
    """第 9 項價格表 → 標準欄位 `underlying_prices`（順序同 `underlyings`；列名「第 n 檔」）。"""
    names = [f"第 {i} 檔" for i in range(1, len(table.value.rows) + 1)] if table.ok else None
    return price_table.price_rows(table, names=names)


# ---------------------------------------------------------------- 第 15 項情境裡重複寫出的值（Issue #315）


def _scenario_spans(ti: TextIndex) -> list[tuple[str, int, int]]:
    """情境 (i)～(iii) 在 `ti.text` 的範圍（到下一個情境或「請注意：情境分析結果不保證」為止）；找不到或順序不對時為空。"""
    starts = [ti.text.find(head) for head, _ in SCENARIOS]
    end = ti.text.find(SCENARIO_END, max(starts))
    if min(starts) < 0 or starts != sorted(starts) or end < 0:
        return []
    return [(case, a, b) for (_, case), a, b in zip(SCENARIOS, starts, [*starts[1:], end], strict=True)]


def _scenarios(fields: dict[str, ParsedField], art15: list[Line]) -> list[Occurrence]:
    """第 15 項情境 (i)～(iii) 重複寫出的值交成共用情境規則的出處（Issue #294）：假設原計價幣別本金、各情境的本金與
    每次配息金額算式的年利率（`scenario_params`）、情境 (iii) 交割股數算式的執行價（`scenario_prices`）；回傳各情境
    金額旁的幣別出處（併入 `currency_others`）。報酬率、配息金額、收盤價、適用匯率與股數不重算。"""
    ti = TextIndex(art15)
    spans = _scenario_spans(ti)
    params = [ScenarioParam("scenario_principal", "第 15 項假設原計價幣別", fields["scenario_principal"])]
    if not spans:
        note = "第 15 項找不到情境 (i)～(iii)（到「請注意：情境分析結果不保證」為止）"
        params += [
            ScenarioParam(
                "scenario_coupon_pa", "第 15 項情境每次配息金額的", ParsedField.missing("scenario_coupon_pa", note)
            ),
            ScenarioParam("scenario_principal", "第 15 項情境的", ParsedField.missing("scenario_principal", note)),
        ]
        fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
        fields["scenario_prices"] = ParsedField.missing("scenario_prices", note)
        return [
            Occurrence(
                "scenario_currency", "scenario_currency", "第 15 項情境", ParsedField.missing("scenario_currency", note)
            )
        ]
    for case, a, b in spans:
        coupons = [m for m in ti.finditer(SCENARIO_COUPON) if a <= m.start() < b]
        where = f"第 15 項{case}每次配息金額的"
        if not coupons:
            missing = ParsedField.missing("scenario_coupon_pa", f"第 15 項{case}找不到每次配息金額算式的年利率")
            params.append(ScenarioParam("scenario_coupon_pa", where, replace(missing, evidence=_ev(ti, a, a + 1))))
        for m in coupons:
            pf = ParsedField.present("scenario_coupon_pa", Decimal(m[1]), ti.lines_for(m.start(), m.end()))
            params.append(ScenarioParam("scenario_coupon_pa", where, pf))
    for i, (case, a, b) in enumerate(spans, start=1):
        hits = sorted(
            (m.start(), label, m)
            for label, (_, _, pattern) in SCENARIO_PRINCIPAL.items()
            for m in ti.finditer(pattern)
            if a <= m.start() < b
        )
        for _, label, m in hits:
            code, where, _ = SCENARIO_PRINCIPAL[label]
            pf = ParsedField.present(code, number(m[1]), ti.lines_for(m.start(), m.end()))
            params.append(ScenarioParam(code, f"第 15 項{case}{where}", pf))
        found = {label for _, label, _ in hits}
        for label in (x for x, cases in PRINCIPAL_CASES.items() if i in cases and x not in found):
            code, where, _ = SCENARIO_PRINCIPAL[label]
            pf = ParsedField.missing(code, f"第 15 項{case}找不到{label}")
            params.append(ScenarioParam(code, f"第 15 項{case}{where}", pf))
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    fields["scenario_prices"] = _scenario_strike(ti, spans[-1], fields["underlying_table"])
    out = []
    for n, (case, a, b) in enumerate(spans, start=1):
        found = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, start=a, end=b)
        out.append(
            money.scenario_occurrence(
                f"scenario{n}_currency", "scenario_currency", f"第 15 項{case}", found, ti.lines_for(a, a + 1)
            )
        )
    return out


def _ev(ti: TextIndex, a: int, b: int) -> list[Evidence]:
    return [Evidence.of(x) for x in ti.lines_for(a, b)]


def _scenario_strike(ti: TextIndex, span: tuple[str, int, int], table: ParsedField) -> ParsedField:
    """情境 (iii) 交割股數算式「每單位面額/執行價 x 適用匯率 = 面額/執行價 x …」的執行價 → 共用情境規則 `scenario_prices`：
    對到「此假設最差表現連結標的為連結標的n」那一檔；單一標的沒寫時為第 1 檔，多標的沒寫時認不出是哪一檔。"""
    where = "第 15 項情境 (iii) 交割股數算式的"
    _, a, b = span
    found = [m for m in ti.finditer(SCENARIO_STRIKE) if a <= m.start() < b]
    worst = [m for m in ti.finditer(SCENARIO_WORST) if a <= m.start() < b]
    if len(found) != 1:
        value = ParsedField.missing("scenario_strike", "第 15 項情境 (iii) 找不到交割股數算式的執行價或出現多次")
        row = None
    else:
        m = found[0]
        lns = ti.lines_for(m.start(), m.end())
        value = (
            ParsedField.invalid("scenario_strike", lns, "第 15 項情境 (iii) 假設的最差表現連結標的出現多次")
            if len(worst) > 1
            else ParsedField.present("scenario_strike", dec(m[1]), lns)
        )
        single = table.ok and len(table.value.rows) == 1
        row = int(worst[0][1]) if len(worst) == 1 else (1 if single else None)
    return standard_fields.occurrences("scenario_prices", [ScenarioPrice(where, row, "strike", value)])


# ---------------------------------------------------------------- 第 12 項日期表


def _is_footnote(lines: list[Line], i: int) -> bool:
    """表頭欄名後面的註腳上標數字：一位數、緊接在前一行（同頁、x 相接）之後。"""
    ln, prev = lines[i], lines[i - 1]
    return (
        bool(re.fullmatch(r"\d", ln.text.strip()))
        and ln.page == prev.page
        and abs(ln.x0 - prev.x1) < 2
        and abs(ln.y0 - prev.y0) < 6
    )


def date_table(lines: list[Line]) -> ParsedField:
    """第 12 項 (6) 到期日之後的日期表：「t」之後的表頭欄決定排法（D-4／D-3／P，範本規格 §5.1），資料列從「1」起，
    每列一個期別＋各欄日期（`N.A` 為 None），遇到不是日期的行結束。

    值：{"kind": "D-4"|"D-3"|"P", "columns": [...], "rows": [{"period", 欄: date|None}], "row_lines"}。
    """
    name = "date_table"
    heads = [i for i, ln in enumerate(lines) if squash(ln.text) == "t"]
    if len(heads) != 1:
        return ParsedField.missing(name, "找不到日期表表頭「t」" if not heads else "日期表表頭「t」出現多次")
    i = heads[0] + 1
    columns: list[str] = []
    while i < len(lines):
        s = squash(lines[i].text)
        if s in DATE_HEADS:
            columns.append(DATE_HEADS[s])
        elif not _is_footnote(lines, i):
            break
        i += 1
    kind = TABLE_KINDS.get(tuple(columns))
    if kind is None:
        return ParsedField.invalid(name, lines[heads[0] : i], "日期表表頭不是已知的三種寫法")
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    first, end = i, i
    for j in range(i, len(lines)):
        s = squash(lines[j].text)
        if re.fullmatch(r"\d{1,2}", s) and (not rows or len(rows[-1]) == len(columns) + 1):
            rows.append({"period": int(s)})
            row_lines.append([lines[j]])
        elif rows and len(rows[-1]) < len(columns) + 1 and (s == "N.A" or DATE_RE.fullmatch(s)):
            value = None if s == "N.A" else parse_date(s)
            if s != "N.A" and value is None:
                return ParsedField.invalid(name, [lines[j]], "日期表日期不合法")
            rows[-1][columns[len(rows[-1]) - 1]] = value
            row_lines[-1].append(lines[j])
        else:
            break
        end = j + 1
    for row, lns in zip(rows, row_lines, strict=True):
        if len(row) != len(columns) + 1 or row["payment"] is None:
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列欄位數或內容不符範本")
        if "valuation" in row and row["valuation"] is None:
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列評價日是「N.A」")
    if not rows or [r["period"] for r in rows] != list(range(1, len(rows) + 1)):
        return ParsedField.invalid(name, lines[first:end] or lines[heads[0] : i], "日期表期數不是從 1 起連續")
    return ParsedField.present(
        name, {"kind": kind, "columns": columns, "rows": rows, "row_lines": row_lines}, lines[first:end]
    )


SCHEDULE_WHERE = "第 12 項日期表"


def _schedule_table(value: dict) -> standard_fields.DateTable:
    """日期表：D-3 觀察日 = 觀察期間終止日；D-4、P 觀察日 = 評價日。每日觀察的表有觀察期間起始日：= 前一期終止日後的下一個
    一籃子預定交易日，開頭「N.A」的期別空白（D-3 為第 1～k 期，D-4 為第 1 期）；P 沒有起始日欄。"""
    kind = value["kind"]
    rows = schedule.rows_of(value["rows"], "end" if kind == "D-3" else "valuation", value["row_lines"])
    if kind == "P":
        return standard_fields.DateTable(SCHEDULE_WHERE, rows)
    start = standard_fields.PeriodStart("next_trading_day", None, schedule.blank_starts(rows))
    return standard_fields.DateTable(SCHEDULE_WHERE, rows, start)


def _schedule(fields: dict[str, ParsedField]) -> None:
    """日期表、KO 觀察方式、第一個可提前出場期與提前出場排程（`parsers/schedule.py`，範本規格 §4.1）：第 8 項寫法、日期表
    排法、D 型按日計息條款三者一致才交出；不一致時三個欄位都不合法（轉人工覆核）。D 的比價日為第 k 期第 8 項指定欄的日期與
    最後評價日，P 為第 k 期起每期的評價日。"""
    table, wording, pro, final = (fields[k] for k in ("date_table", "ko_wording", "pro_rata", "final_valuation_date"))
    fields["schedule_table"] = schedule.table_field(table, _schedule_table)
    bad = next((p for p in (table, wording, pro, final) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        return
    ev = list(wording.evidence) + list(table.evidence)
    kind, n = table.value["kind"], len(table.value["rows"])
    observation, k, column, last = wording.value
    note = ""
    if observation == "D":
        if not kind.startswith("D"):
            note = "第 8 項自動提前出場評價日是每日觀察，但日期表是每期觀察的排法"
        elif column not in table.value["columns"]:
            note = "第 8 項自動提前出場評價日的起始欄不在日期表上"
        elif not pro.value:
            note = "每日觀察的商品第 8 項沒有自動提前出場配息金額（x n/N）"
        elif not 1 <= k <= n or table.value["rows"][k - 1][column] is None:
            note = f"第一個可提前出場期 {k} 在日期表沒有日期"
    elif kind != "P":
        note = "第 8 項自動提前出場評價日是每期觀察，但日期表是每日觀察的排法"
    elif pro.value:
        note = "每期觀察的商品第 8 項卻有自動提前出場配息金額（x n/N）"
    elif not 1 <= k <= last == n:
        note = f"第 8 項自動提前出場評價日 t={k}–{last} 不是到最後一期（共 {n} 期）"
    if note:
        fields.update(schedule.invalid(ev, note))
        return
    obs = ParsedField("ko_observation", FieldStatus.PRESENT, observation, ev)
    first = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    if observation == "D":  # 第 k 期取第 8 項指定欄（起始日／終止日／評價日），末期取最後評價日
        start = ParsedField("ko_start", FieldStatus.PRESENT, table.value["rows"][k - 1][column], ev)
        fields.update(schedule.autocall_schedule(obs, first, fields["schedule_table"], "end", final, start))
    else:
        fields.update(schedule.autocall_schedule(obs, first, fields["schedule_table"]))


# ---------------------------------------------------------------- 第 II 部分


def _block(doc: Document, blocks, title: str) -> list[Line]:
    """第 II 部分第 3 項中標題以 `title` 開頭的子項（總代理人、受託機構）那一段。"""
    found = [sp for sp in blocks.values() if squash(sp.title).startswith(title)]
    return doc.span_lines(found[0]) if len(found) == 1 else []
