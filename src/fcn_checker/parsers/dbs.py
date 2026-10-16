"""DBS（星展銀行）中文產品說明書（docs/templates/dbs-zh-product-description.md）：段落標題＋標籤定位，保留原文證據。

沒有章號：段落標題（`商品基本資料`、`商品投資收益計算方法及其他資料`、`相關機構事業概況`、`商品主要風險揭露`、
`一般交易事項`、`特別記載事項`）整行比對；商品基本資料是「標籤：值」兩欄表（標籤 x≈56、值 x≈170）。第 1–2 頁是
「• 標籤：值」清單（`•` 單獨一行）、警語 1.～11. 與刊印日期。每頁頁底「N / M頁」（擷取時排在該頁第一行）不算內文。
K／KO／KI % 只在表1 表頭（欄序 K → KO → KI），表1 有彭博代碼。最終比價日叫「定價日」；KI 叫「下限觸發事件」。
支援樣態只有 KO 每日觀察＋記憶式、KI 到期觀察或無 KI（config/issuer_prefixes.toml），其他寫法一律讀不到、轉人工覆核。
數字、日期與句型在去空白的全文索引上讀；名稱、地址等要保留空白的值在原文合併文字（`common.RawText`）上讀。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import full_brackets, squash
from . import money, price_table, schedule
from .common import RawText, capture_group, compat_normalized, derived, fee_rates, number
from .layout import TextIndex, capture, join_wrapped_tickers, parse_date

TEMPLATE_ID = "dbs-zh-pd"
# 2：價格表改由 parsers/price_table.py 讀（Issue #287）；3：情境重複寫出的值（Issue #321）；4：日期表標準欄位與共用排程推導（Issue #289）
PARSER_VERSION = "4"
TITLE = "中文產品說明書(最終版本)"
SECTIONS = (
    "商品基本資料",
    "商品投資收益計算方法及其他資料",
    "相關機構事業概況",
    "商品主要風險揭露",
    "一般交易事項",
    "特別記載事項",
)
ISSUER_ZH = "星展銀行"
ISSUER_EN = "DBS Bank Ltd"
NAME_ZH_MARK = "股權連結結構型商品(無擔保及無保證機構)(不保本)"
NAME_EN_MARK = "Equity Linked Notes"
PAGE = re.compile(r"^\d+ / \d+頁$")
PAGE_Y = 760  # 頁碼在頁底（y≈774）
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
# 人民幣商品在計價幣別括號後多一段定義（`。「人民幣」是指大陸地區(PRC)的法定貨幣…台灣。`），內容不核對（Issue #296）
CCY_NOTE = rf"(?:。「{ZH_CCY}」是指[^•；]+?)?"
TICKER = re.compile(r"^([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})$")
# 強制贖回、配息資料（範本規格 §4.1、§4.2、§4.6）
OBSERVATION_DAY = "觀察日：就一觀察期間而言，指該觀察期間內所有連結標的的所有交易所的每一個規劃交易日"
KO_START = r"自動提前出場決定日[:：]從第(\d{1,2})個觀察期間結束日（包含）開始至預定規劃定價日（包含）為止的期間內的每一個觀察日"
MEMORY_EVENT = (
    "記憶事件：就每一連結標的而言，如果該連結標的在任一自動提前出場決定日的參考價格大於或等於其自動提前出場價格"
)
MEMORY_ALL = r"自動提前出場事件：若所有連結標的皆被視[為爲]記憶標的"
# 說明書「為a%（即y%/12）」、投資人須知「為a% (y%/12)」
MAX_RATE = r"最大利率值：就每個觀察期間而言，為" + PCT + r"[（(]即?" + PCT + r"/12[）)]，無條件進位至小數點後第四位"
FULL_RATE = r"利率：1?\)?對於第1(?:~(\d{1,2}))?個觀察期間而言，利率為最大利率值"
# 到期贖回（範本規格 §4.4）
KI_EVENT = "下限觸發事件：若任一連結標的於定價日的參考價格小於其下限觸發價格，則視為發生下限觸發事件"
KI_MATURITY = r"\(a\)如果未發生下限觸發事件，將適用現金結算"
NO_KI_MATURITY = r"\(a\)若表現最差連結標的之最終價格等於或高於其執行價格，將適用現金結算"
# 情境分析（範本規格 §4.9）：各情境的起點（情境三的金額從最終價格表之後的「依上所示」起算）與重複寫出的值（Issue #321）
SCENARIO_MARKS = ("有利情況：", "於到期最佳情況：", "較差情況：", "依上所示", "最大損失情況：")
TIMES = "[Xx×]"
SCENARIO_COUPON = rf"[\d,]+{TIMES}(\d+(?:\.\d+)?)%{TIMES}"  # 利息算式「10,000 X 0.6667% X 1個月」
SCENARIO_K = r"第1(?:~(\d{1,2}))?個觀察期間之利息金額"
SCENARIO3_WORST = (
    r"表現最差連結標的為([A-Z0-9./-]+)，其最終價格\(("
    + money.NUMBER
    + r")\)低於其(執行價格|下限觸發價格)\(("
    + money.NUMBER
    + r")\)"
)
# 價格表（表1、情境、投資人須知商品簡介）：以彭博代碼切列（代碼在列首），欄序 K → KO → KI；
# 表頭 %「(期初價格的X%)」可能跨頁（欄名在前一頁）；每格「美元 123.4567」或分兩行「美元」／「123.4567」
PRICE_TABLE = price_table.TableSpec(
    where="表1 價格表",
    initial="期初價格",
    columns=(
        price_table.Column("strike", "執行價格"),
        price_table.Column("ko", "自動提前出場價格"),
        price_table.Column("ki", "下限觸發價格", required=False),
    ),
    anchor="ticker",
    pct=r"期初價格的?(\d+(?:\.\d+)?)%",
    pct_label="(期初價格的X%)",
    code="彭博代碼",
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="情境分析價格表")
IIS_TABLE = replace(PRICE_TABLE, where="商品簡介價格表")


def body(lines: Sequence[Line]) -> list[Line]:
    """CJK 相容字正規化（4 份樣本都沒有，照共用做法）；列項符號「‧」（U+2027，部分字型擷取成這個字）統一成「•」；
    頁底頁碼不算內文。"""
    out = []
    for ln in compat_normalized(lines):
        if ln.y0 >= PAGE_Y and PAGE.match(ln.text.strip()):
            continue
        out.append(replace(ln, text=ln.text.replace("‧", "•")) if "‧" in ln.text else ln)
    return out


def sections(lines: list[Line]) -> dict[str, list[Line]]:
    """段落標題（整行相等、各出現一次）→ 該段的行（到下一個段落標題為止）；第 1–2 頁封面以 `cover` 為鍵。"""
    starts = {}
    for name in SECTIONS:
        idx = [i for i, ln in enumerate(lines) if squash(ln.text) == name]
        if len(idx) == 1:
            starts[name] = idx[0]
    out: dict[str, list[Line]] = {}
    ordered = sorted((i, n) for n, i in starts.items())
    out["cover"] = lines[: ordered[0][0]] if ordered else list(lines)
    for k, (i, n) in enumerate(ordered):
        end = ordered[k + 1][0] if k + 1 < len(ordered) else len(lines)
        out[n] = lines[i:end]
    return out


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 DBS 說明書。"""
    text_lines = body(lines)
    secs = sections(text_lines)
    ti = TextIndex(secs["cover"])
    head = squash(text_lines[0].text) if text_lines else ""
    zh = re.search(r"•商品中文名稱：(.+?)\(下稱「本商品」\)", ti.text)
    en = re.search(r"•商品英文名稱：(.+?)•", ti.text)
    order = [i for n in SECTIONS for i, ln in enumerate(text_lines) if squash(ln.text) == n]
    failed = []
    for ok, message in (
        (head == TITLE, f"第一行不是「{TITLE}」"),
        (
            all(
                f"•{x}：" in ti.text
                for x in ("商品代號", "受託或銷售機構商品代號", "國際證券編碼ISIN", "商品中文名稱", "商品英文名稱")
            ),
            "封面沒有「• 商品代號、受託或銷售機構商品代號、國際證券編碼ISIN、商品中文名稱、商品英文名稱」",
        ),
        (
            zh is not None
            and zh[1].startswith(ISSUER_ZH)
            and full_brackets(squash(NAME_ZH_MARK)) in full_brackets(zh[1]),
            f"封面商品中文名稱不是以「{ISSUER_ZH}」開頭或沒有「{NAME_ZH_MARK}」",
        ),
        (
            en is not None and en[1].startswith(squash(ISSUER_EN)) and squash(NAME_EN_MARK) in en[1],
            f"封面商品英文名稱不是以「{ISSUER_EN}」開頭或沒有「{NAME_EN_MARK}」",
        ),
        (
            "•發行人之名稱、電話及地址：" in ti.text and "•保證機構之名稱及地址：" in ti.text,
            "封面沒有「發行人之名稱、電話及地址」「保證機構之名稱及地址」",
        ),
        (
            len(order) == len(SECTIONS) and order == sorted(order),
            "段落標題不是依序各一次：" + "、".join(SECTIONS),
        ),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in text_lines[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class DbsTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def read(lines: Sequence[Line]) -> DbsTermSheet:
    """讀出標準欄位與 DBS 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    text_lines = body(lines)
    secs = sections(text_lines)
    fields: dict[str, ParsedField] = {}

    def section(name: str) -> list[Line]:
        return secs.get(name, [])

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    _cover(fields, section("cover"), put)
    basic = section("商品基本資料")
    _basic(fields, basic, put)
    _schedule(fields)
    scenario_currencies = _scenario(fields, section("商品投資收益計算方法及其他資料"), put)
    _organisations(fields, section("相關機構事業概況"))
    _trading(fields, section("一般交易事項"), put)
    _occurrences(fields)
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [*fields["currency_others"].value, *scenario_currencies]
    )
    fields["initial_pricing"] = standard_fields.absent(
        "initial_pricing", "期初定價方式（說明書沒有寫開盤價／收盤價／VWAP）"
    )
    fields["issuer_name_ch1"] = standard_fields.absent("issuer_name_ch1", "只寫中文的發行機構名稱出處")
    return DbsTermSheet(fields, TextIndex(text_lines))


def _cover(fields: dict[str, ParsedField], cover: list[Line], put: Callable) -> None:
    """第 1–2 頁「• 標籤：值」清單與刊印日期。"""
    raw = RawText(cover)
    put("product_code", cover, r"•商品代號：(\d{12})")
    put("distributor_code", cover, r"•受託或銷售機構商品代號：(\d{12})")
    put("isin", cover, r"•國際證券編碼ISIN：([A-Z]{2}[A-Z0-9]{10})")
    fields["name_zh"] = raw.capture("name_zh", r"•\s*商品中文名稱\s*：\s*(.+?)\s*\(下稱「本商品」\)")
    fields["name_en"] = raw.capture("name_en", r"•\s*商品英文名稱\s*：\s*(.+?)\s*•")
    put("product_type", cover, r"•商品種類：(.+?)•")
    put("currency_zh", cover, rf"•計價幣別：({ZH_CCY})\([A-Z]{{3}}\){CCY_NOTE}•")
    put("currency_cover_iso", cover, rf"•計價幣別：{ZH_CCY}\(([A-Z]{{3}})\){CCY_NOTE}•")
    fields["issuer_name_cover"] = raw.capture("issuer_name_cover", r"•\s*發行機構之名稱\s*：\s*(.+?)\s*•")
    dist = (
        r"•\s*受託或銷售機構之名稱、電話及地址\s*：\s*(.+?)\s*、\s*電話\s*：\s*(.+?)\s*、\s*地址\s*：\s*(.+?)\s*。?\s*•"
    )
    fields["distributor_name_cover"] = raw.capture("distributor_name_cover", dist)
    fields["distributor_phone_cover"] = raw.capture("distributor_phone_cover", dist, group=2)
    fields["distributor_address_cover"] = raw.capture("distributor_address_cover", dist, group=3)
    put("approval_date", cover, r"•受託或銷售機構審查通過之日期及文號：" + D, parse_date)
    put("print_date", cover, r"中文產品說明書刊印日期：" + D, parse_date)


# ---------------------------------------------------------------- 商品基本資料


def _basic(fields: dict[str, ParsedField], basic: list[Line], put: Callable) -> None:
    ti = TextIndex(basic, separate_digits=True)
    fields["name_basic"] = RawText(basic).capture("name_basic", r"商品名稱\s*：\s*(.+?)\s*商品風險程度\s*：")
    put("currency_basic", basic, rf"計價幣別：({ZH_CCY})\([A-Z]{{3}}\){CCY_NOTE}商品面額：")
    put("currency_basic_iso", basic, rf"計價幣別：{ZH_CCY}\(([A-Z]{{3}})\){CCY_NOTE}商品面額：")
    denom = rf"商品面額：每單位({CCY})([\d,]+)發行價格："
    fields["denomination"] = capture_group(basic, denom, 2, "denomination", number)
    fields["denomination_currency"] = capture_group(basic, denom, 1, "denomination_currency")
    put("issue_price_pct", basic, r"發行價格：商品面額的" + PCT, Decimal)
    fields["basic_tickers"] = basic_tickers(basic)
    put("tenor_months", basic, r"商品年期：(\d+)個月。", int)
    put("issue_date", basic, r"發行日：" + D, parse_date)
    put("trade_date", basic, r"(?<!劃)(?<!定)交易日：" + D, parse_date)
    put("maturity_date", basic, r"到期日：如未依下載之強制贖回條款發生自動提前出場事件之情形，為" + D, parse_date)
    put("final_valuation_date", basic, r"(?<!規劃)定價日：" + D + r"，如該日並非", parse_date)
    fields["coupon_table"] = table_a(ti)
    fields["coupon_pa_pct"] = capture_group(basic, MAX_RATE, 2, "coupon_pa_pct", Decimal)
    fields["monthly_coupon_pct"] = capture_group(basic, MAX_RATE, 1, "monthly_coupon_pct", Decimal)
    fields["full_rate_periods"] = full_rate_periods("full_rate_periods", ti, FULL_RATE)
    fields["ko_wording"] = ko_wording(ti)
    fields["ko_memory"] = memory(ti, "ko_memory")
    table = price_table.read("underlying_table", table_region(basic, "表1", "到期贖回計算公式，含"), PRICE_TABLE)
    fields["underlying_table"] = table
    fields["ki_type"] = ki_type(ti, table)
    _table_fields(fields)


def basic_tickers(basic: list[Line]) -> ParsedField:
    """連結標的資產表的彭博代碼（`ABC UW`），依表上順序（`連結標的／彭博代碼／交易所` 到 `相關交易所：`）。"""
    ti = TextIndex(basic)
    a, b = ti.text.find("連結標的彭博代碼交易所"), ti.text.find("相關交易所：")
    if a < 0 or b < a:
        return ParsedField.missing("basic_tickers", "商品基本資料找不到連結標的資產表")
    lines = join_wrapped_tickers(basic[ti.line_at(a) + 1 : ti.line_at(b)])
    tickers = [(f"{m[1]} {m[2]}", ln) for ln in lines if (m := TICKER.match(ln.text.strip()))]
    if not tickers:
        return ParsedField.invalid("basic_tickers", lines, "連結標的資產表讀不到彭博代碼")
    return ParsedField.present("basic_tickers", [t for t, _ in tickers], [ln for _, ln in tickers])


def table_a(ti: TextIndex) -> ParsedField:
    """表A（範本規格 §5.2）：觀察期間／觀察期間結束日／利息支付日；期別須從 1 起連續。"""
    head = "表A：觀察期間觀察期間結束日#利息支付日##"
    a = ti.text.find(head)
    b = ti.text.find("#為避免疑問", a + len(head))
    if a < 0 or b < 0:
        return ParsedField.missing("coupon_table", "商品年期格找不到「表A：」觀察期間表")
    text = ti.text[a + len(head) : b]
    rows = [
        {"period": int(m[1]), "end": parse_date(m[2]), "payment": parse_date(m[3])}
        for m in re.finditer(r"(\d{1,2})\|?" + D + D, text)
    ]
    ev = ti.lines_for(a, b)
    if not rows or any(None in r.values() for r in rows):
        return ParsedField.invalid("coupon_table", ev, "表A 的列讀不到")
    if [r["period"] for r in rows] != list(range(1, len(rows) + 1)):
        return ParsedField.invalid("coupon_table", ev, "表A 的觀察期間不是從 1 起連續")
    return ParsedField.present("coupon_table", {"kind": "D", "rows": rows}, ev)


def full_rate_periods(name: str, ti: TextIndex, pattern: str) -> ParsedField:
    """「對於第1~k個觀察期間而言，利率為最大利率值」的 k（k = 1 時寫「第1個」）。"""
    found = list(ti.finditer(pattern))
    if len(found) != 1:
        return ParsedField.missing(name, "找不到或有多處「對於第1~k個觀察期間而言，利率為最大利率值」")
    m = found[0]
    return ParsedField.present(name, int(m[1]) if m[1] else 1, ti.lines_for(m.start(), m.end()))


def ko_wording(ti: TextIndex) -> ParsedField:
    """KO 每日觀察（範本規格 §4.1）：觀察日 = 每一個規劃交易日，且強制贖回的自動提前出場決定日從第 k 個觀察期間結束日起。"""
    starts = list(ti.finditer(KO_START))
    if OBSERVATION_DAY not in ti.text or len(starts) != 1:
        return ParsedField.invalid(
            "ko_wording", [], "觀察日或自動提前出場決定日的寫法缺漏、重複或不在範本規格內（只支援每日觀察）"
        )
    m = starts[0]
    return ParsedField.present("ko_wording", int(m[1]), ti.lines_for(m.start(), m.end()))


def memory(ti: TextIndex, name: str) -> ParsedField:
    """記憶式（範本規格 §4.2）：「記憶事件」定義＋「所有連結標的皆被視為記憶標的」兩句都有才是記憶式；其他寫法讀不到。"""
    events = list(ti.finditer(MEMORY_ALL))
    if MEMORY_EVENT not in ti.text or len(events) != 1:
        return ParsedField.invalid(name, [], "記憶事件與自動提前出場事件的寫法缺漏或不在範本規格內（只支援記憶式）")
    m = events[0]
    return ParsedField.present(name, True, ti.lines_for(m.start(), m.end()))


def ki_type(ti: TextIndex, table: ParsedField) -> ParsedField:
    """下限觸發事件（範本規格 §4.4）：到期 (a)、下限觸發事件定義、價格表下限觸發價格欄三者須一致（說明書、投資人須知共用）。"""
    am, none = list(ti.finditer(KI_MATURITY)), list(ti.finditer(NO_KI_MATURITY))
    event = ti.text.count(KI_EVENT)
    has_col = table.ok and "ki" in table.value.columns
    if len(am) == 1 and not none and event == 1:
        if table.ok and not has_col:
            return ParsedField.invalid(
                "ki_type", ti.lines_for(am[0].start(), am[0].end()), "有下限觸發事件，但價格表沒有下限觸發價格欄"
            )
        p = ti.text.find(KI_EVENT)
        return ParsedField.present("ki_type", "AM", ti.lines_for(p, p + len(KI_EVENT)))
    if len(none) == 1 and not am and event == 0:
        if has_col:
            return ParsedField.invalid(
                "ki_type", ti.lines_for(none[0].start(), none[0].end()), "價格表有下限觸發價格欄，但沒有下限觸發事件"
            )
        return ParsedField.present("ki_type", "none", ti.lines_for(none[0].start(), none[0].end()))
    return ParsedField.invalid("ki_type", [], "到期贖回 (a) 與下限觸發事件的寫法不一致或不在範本規格內")


def price_rows(table: ParsedField) -> ParsedField:
    """價格表 → 標準欄位 `underlying_prices`（代碼、各價格、證據、價格旁的幣別字）；價格表有問題時沿用它的狀態。"""
    return price_table.price_rows(table, currency=True)


def table_region(lns: list[Line], start_marker: str, end_marker: str) -> list[Line]:
    """價格表的範圍：開頭是 `start_marker` 的那一行之後、開頭是 `end_marker` 的那一行之前（去空白比對）。"""
    a = next((i for i, ln in enumerate(lns) if squash(ln.text).startswith(start_marker)), None)
    if a is None:
        return []
    b = next((i for i in range(a + 1, len(lns)) if squash(lns[i].text).startswith(end_marker)), None)
    return lns[a + 1 : b] if b is not None else []


def _table_fields(fields: dict[str, ParsedField]) -> None:
    """表1＋標的表代碼 → 標準欄位：標的、各標的價格、K／KO／KI %（表頭是唯一來源）。"""
    table, tickers = fields["underlying_table"], fields["basic_tickers"]
    if table.ok and tickers.ok and table.value.tickers == tickers.value:
        fields["underlyings"] = ParsedField(
            "underlyings", FieldStatus.PRESENT, list(tickers.value), list(tickers.evidence)
        )
        fields["underlying_prices"] = price_rows(table)
    elif table.ok and tickers.ok:
        note = f"表1 的彭博代碼 {table.value.tickers} 和連結標的資產表 {tickers.value} 不同（代碼、順序或檔數）"
        ev = list(table.evidence) + list(tickers.evidence)
        for key in ("underlyings", "underlying_prices"):
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)
    else:
        bad = table if not table.ok else tickers
        fields["underlyings"] = derived("underlyings", bad)
        fields["underlying_prices"] = derived("underlying_prices", bad)
    for key, col in (("strike_pct", "strike"), ("ko_pct", "ko")):
        fields[key] = derived(key, table, table.value.pcts[col] if table.ok else None)
    ki = fields["ki_type"]
    if ki.ok and ki.value == "none":
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki.evidence), note="說明書無 KI（沒有下限觸發事件）"
        )
    else:
        bad = next((p for p in (ki, table) if not p.ok), None)
        fields["ki_pct"] = derived("ki_pct", bad or table, None if bad else table.value.pcts["ki"])


SCHEDULE_WHERE = "商品基本資料日期表"


def _schedule(fields: dict[str, ParsedField]) -> None:
    """日期表（表A，觀察日 = 觀察期間結束日；沒有開始日欄，不查期始日）與提前出場排程（`parsers/schedule.py`）：
    KO 只有每日觀察，k 須早於最後一期，比價日為表A 第 k 期與末期的觀察期間結束日。"""
    wording, table = fields["ko_wording"], fields["coupon_table"]
    fields["schedule_table"] = schedule.table_field(
        table, lambda v: standard_fields.DateTable(SCHEDULE_WHERE, schedule.rows_of(v["rows"], "end"))
    )
    bad = next((p for p in (wording, table) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        return
    k, n = wording.value, len(table.value["rows"])
    ev = list(wording.evidence) + list(table.evidence)
    if not 1 <= k < n:
        note = f"自動提前出場決定日的起始期（第 {k} 個觀察期間）不在表A（{n} 期）內或等於最後一期"
        fields.update(
            {key: ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note) for key in schedule.SCHEDULE_KEYS}
        )
        return
    obs = ParsedField("ko_observation", FieldStatus.PRESENT, "D", list(wording.evidence))
    first = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    fields.update(schedule.autocall_schedule(obs, first, fields["schedule_table"], "end"))


# ---------------------------------------------------------------- 情境分析


def _scenario(fields: dict[str, ParsedField], sec: list[Line], put: Callable) -> list[Occurrence]:
    """基本假設（年期、面額、發行價格、投資金額、利率、KO 決定日）、情境價格表、情境一出場期、情境二年化報酬率
    （範本規格 §4.9）與情境一～三重複寫出的值（Issue #321）；回傳各情境金額旁的幣別出處（併入 `currency_others`）。"""
    ti = TextIndex(sec)
    put("scenario_tenor", sec, r"假設：商品年期：(\d+)個月", int)
    denom = rf"商品面額=({CCY})([\d,]+)發行價格=" + PCT
    fields["scenario_denomination"] = capture_group(sec, denom, 2, "scenario_denomination", number)
    fields["scenario_denomination_currency"] = capture_group(sec, denom, 1, "scenario_denomination_currency")
    fields["scenario_issue_price_pct"] = capture_group(sec, denom, 3, "scenario_issue_price_pct", Decimal)
    invest = rf"投資金額=([\d,]+){TIMES}(\d+(?:\.\d+)?)%{TIMES}(\d+)=({CCY})([\d,]+)利率："
    for group, key, convert in (
        (1, "scenario_investment_principal", number),
        (2, "scenario_investment_pct", Decimal),
        (3, "scenario_units", int),
        (5, "scenario_investment", number),
    ):
        fields[key] = capture_group(sec, invest, group, key, convert)
    # 幣別照舊寬鬆讀（Issue #170）：算式寫法變了只影響投資金額的核對，不連帶幣別出處讀不到
    fields["scenario_investment_currency"] = capture_group(
        sec, rf"投資金額=[^=]+=({CCY})[\d,]+利率：", 1, "scenario_investment_currency"
    )
    fields["scenario_full_rate_periods"] = full_rate_periods(
        "scenario_full_rate_periods", ti, r"利率：第1(?:~(\d{1,2}))?個觀察期間之利率為最大利率值"
    )
    put("scenario_monthly_pct", sec, r"最大利率值：" + PCT + r"自動提前出場決定日", Decimal)
    put("scenario_ko_start", sec, r"自動提前出場決定日：從第(\d{1,2})個觀察期間結束日", int)
    fields["scenario_table"] = price_table.read(
        "scenario_table", table_region(sec, "各連結標的之相關價格", "1."), SCENARIO_TABLE
    )
    put(
        "scenario_first_period",
        sec,
        r"有利情況：假設本商品於第(\d{1,2})個觀察期間的第\d+個觀察日發生自動提前出場事件",
        int,
    )
    a = ti.text.find("於到期最佳情況")
    b = ti.text.find("較差情況", a)
    best = ti.lines_for(a, b) if a >= 0 and b > a else []
    put("scenario_best_annual_pct", best, r"年化平均報酬率約為" + PCT, Decimal)
    return _scenario_mentions(fields, ti)


def _scenario_spans(ti: TextIndex) -> list[tuple[str, int, int]]:
    """情境一～三在全文索引上的範圍（Issue #321）：情境一「有利情況：」到「於到期最佳情況：」、情境二到「較差情況：」、
    情境三從「依上所示」（最終價格表之後）到「最大損失情況：」；任一處找不到或順序不對時為空清單。"""
    marks = [ti.text.find(x) for x in SCENARIO_MARKS]
    if min(marks) < 0 or marks != sorted(marks):
        return []
    one, two, worse, after, end = marks
    return [("情境一", one, two), ("情境二", two, worse), ("情境三", after, end)]


def _scenario_mentions(fields: dict[str, ParsedField], ti: TextIndex) -> list[Occurrence]:
    """情境一～三重複寫出的值（Issue #321，比照 BBVA #305）：利息算式 `面額 X a% X` 的 a（= 最大利率值）、情境一
    `第1~k個觀察期間之利息金額` 的 k、情境三最終價格表的期初價格與「表現最差連結標的為…其最終價格(x)低於其執行價格／
    下限觸發價格(y)」；回傳各情境金額旁的幣別出處。利息金額、股數、零股金額、報酬率與假設匯率不重算。"""
    spans = _scenario_spans(ti)
    if not spans:
        note = "情境分析找不到「有利情況：」「於到期最佳情況：」「較差情況：」「依上所示」「最大損失情況：」"
        for name in ("scenario_coupon_mentions", "scenario_k_mention", "scenario3_initial", "scenario3_worst"):
            fields[name] = ParsedField.missing(name, note)
        missing = ParsedField.missing("scenario_currency", note)
        return [Occurrence("scenario_currency", "情境金額幣別", "情境分析", missing)]
    items = []
    for n, (case, a, b) in enumerate(spans, start=1):  # 情境一～三都有利息算式（33 份成立），某一情境沒有就缺漏
        hits = [m for m in ti.finditer(SCENARIO_COUPON) if a <= m.start() < b]
        if not hits:
            missing = ParsedField.missing(f"scenario_coupon_mentions_{n}", f"{case}找不到利息算式「面額 X a% X」")
            items.append(Occurrence(missing.name, "利息算式最大利率值", f"情境分析{case}", missing))
        for k, m in enumerate(hits, start=1):
            pf = ParsedField.present(
                f"scenario_coupon_mentions_{n}_{k}", Decimal(m[1]), ti.lines_for(m.start(), m.end())
            )
            items.append(Occurrence(pf.name, "利息算式最大利率值", f"情境分析{case}", pf))
    fields["scenario_coupon_mentions"] = standard_fields.occurrences("scenario_coupon_mentions", items)
    _, a, b = spans[0]
    ks = [m for m in ti.finditer(SCENARIO_K) if a <= m.start() < b]
    fields["scenario_k_mention"] = (
        ParsedField.present("scenario_k_mention", int(ks[0][1] or 1), ti.lines_for(ks[0].start(), ks[0].end()))
        if len(ks) == 1
        else ParsedField.missing("scenario_k_mention", "情境一找不到或有多處「第1~k個觀察期間之利息金額」")
    )
    fields["scenario3_initial"], fields["scenario3_worst"] = _scenario3(
        fields["underlying_table"], ti, spans[1][2], *spans[2][1:]
    )
    out = []
    for n, (case, a, b) in enumerate(spans, start=1):
        found = [  # 情境一～三的金額都以承作幣別寫（股價、匯率在最終價格表與括號內，沒有幣別字）
            *money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, skip=lambda _: False, start=a, end=b),
            *money.amounts(ti, rf"即({CCY})({money.NUMBER})", currency_group=1, skip=lambda _: False, start=a, end=b),
        ]
        out.append(
            money.scenario_occurrence(
                f"scenario{n}_currency", f"{case}金額幣別", f"情境分析{case}", found, ti.lines_for(a, a + 1)
            )
        )
    return out


def _scenario3(table: ParsedField, ti: TextIndex, lo: int, end: int, hi: int) -> tuple[ParsedField, ParsedField]:
    """情境三（`ti.text[lo:hi]`，「較差情況：」到「最大損失情況：」）最終價格表（「假設於定價日」到 `end`「依上所示」）
    每列的期初價格（依表1 代碼順序）與最終價格，及 `end` 之後「表現最差連結標的為X，其最終價格(x)低於其執行價格／下限
    觸發價格(y)」（Issue #321）；價格欄名對成表1 的欄（`strike`／`ki`）。"""
    names = ("scenario3_initial", "scenario3_worst")
    if not table.ok:
        return derived(names[0], table), derived(names[1], table)
    start = ti.text.find("假設於定價日", lo, end)
    if start < 0:
        return tuple(ParsedField.missing(x, "情境三找不到「假設於定價日」最終價格表") for x in names)
    initial, final, ev = [], {}, []
    for code in table.value.tickers:
        tk = re.escape(squash(code or "")) or "(?!)"  # 表1 一定有代碼（以代碼切列）；保險起見不讓空字串配到每一列
        hits = [
            m
            for m in ti.finditer(rf"(?<![A-Z0-9]){tk}(?:{CCY})?({money.NUMBER})(?:{CCY})?({money.NUMBER})=")
            if start <= m.start() < end
        ]
        if len(hits) != 1:
            return (
                ParsedField.missing(names[0], f"情境三最終價格表找不到或有多列 {code}"),
                ParsedField.missing(names[1], "情境三最終價格表讀不到"),
            )
        m = hits[0]
        initial.append(number(m[1]))
        final[squash(code)] = (code, number(m[2]))
        ev += ti.lines_for(m.start(), m.end())
    worst = [m for m in ti.finditer(SCENARIO3_WORST) if end <= m.start() < hi]
    hit = worst[0] if len(worst) == 1 else None
    if hit is None or hit[1] not in final:
        return ParsedField.present(names[0], initial, ev), ParsedField.missing(
            names[1], "情境三找不到或有多處「表現最差連結標的為…其最終價格(x)低於其執行價格／下限觸發價格(y)」"
        )
    code, shown = final[hit[1]]
    column = {"執行價格": "strike", "下限觸發價格": "ki"}[hit[3]]
    value = {"ticker": code, "table_final": shown, "final": number(hit[2]), "column": column, "price": number(hit[4])}
    return ParsedField.present(names[0], initial, ev), ParsedField.present(
        names[1], value, ti.lines_for(hit.start(), hit.end())
    )


# ---------------------------------------------------------------- 相關機構事業概況、一般交易事項


def _organisations(fields: dict[str, ParsedField], sec: list[Line]) -> None:
    raw = RawText(sec)
    fields["issuer_name_ch2"] = raw.capture(
        "issuer_name_ch2", r"1\.\s*發行機構\s*：\s*\(1\)\s*事業名稱\s*：\s*(.+?)\s*\(2\)\s*設立日期"
    )
    trustee = (
        r"4\.\s*受託或銷售機構\s*：\s*\(1\)\s*事業名稱\s*：\s*(.+?)\s*\(2\)\s*營業所在地\s*：\s*(.+?)\s*\(3\)\s*設立日期"
        r".*?\(4\)\s*負責人姓名\s*：\s*(.+?)\s*5\."
    )
    fields["distributor_name_ch2"] = raw.capture("distributor_name_ch2", trustee)
    fields["distributor_address_ch2"] = raw.capture("distributor_address_ch2", trustee, group=2)
    fields["chairman"] = raw.capture("chairman", trustee, group=3)


def _trading(fields: dict[str, ParsedField], sec: list[Line], put: Callable) -> None:
    put("subscription_start", sec, r"\(1\)商品開始受理申購日期：" + D, parse_date)
    ti = TextIndex(sec)
    a, b = ti.text.find("投資人應負擔的各項費用"), ti.text.find("商品交易架構")
    fees = sec[ti.line_at(a) : ti.line_at(b)] if a >= 0 and b > a else []
    fields.update(fee_rates(fees))
    fields[standard_fields.fee_field("分銷費用")] = distribution_fee(
        fees, fields[standard_fields.fee_field("分銷費用")]
    )
    sub = rf"最低申購金額為(\d+)單位商品面額，即({CCY})([\d,]+)，並以(\d+)單位商品面額為最低加購金額"
    red = (
        rf"\(3\)投資人申請提前贖回：最低贖回金額為(\d+)單位商品面額，即({CCY})([\d,]+)，並以(\d+)單位商品面額為累加單位"
    )
    for name, pattern in (("subscription", sub), ("redemption", red)):
        fields[f"minimum_{name}_units"] = capture_group(sec, pattern, 1, f"minimum_{name}_units", int)
        fields[f"minimum_{name}_currency"] = capture_group(sec, pattern, 2, f"minimum_{name}_currency")
        fields[f"minimum_{name}_amount"] = capture_group(sec, pattern, 3, f"minimum_{name}_amount", number)
        fields[f"minimum_{name}_step"] = capture_group(sec, pattern, 4, f"minimum_{name}_step", int)


def distribution_fee(lines: list[Line], shared: ParsedField) -> ParsedField:
    """說明書分銷費用的子列標籤（報酬／費用／折讓）佔了「費率」欄，費率在右邊一欄：取「費用」子列、子列標籤右邊到
    「收取方式」欄之前的文字（範本規格 §5.3）；下一個子列可能在下一頁（Issue #295）。共用 `fee_rates` 讀得到（投資人
    須知的版面）時用它。"""
    if shared.ok:
        return shared
    name = shared.name
    head = next((ln for ln in lines if squash(ln.text).startswith("費率")), None)
    way = next((ln for ln in lines if squash(ln.text) == "收取方式"), None)
    if head is None or way is None:
        return shared
    subs = [ln for ln in lines if abs(ln.x0 - head.x0) < 5 and squash(ln.text) in ("報酬", "費用", "折讓")]
    fee = [ln for ln in subs if squash(ln.text) == "費用"]
    if len(fee) != 1:
        return shared
    top = fee[0]
    below = [ln for ln in subs if (ln.page, ln.y0) > (top.page, top.y0)]
    bottom = min(below, key=lambda x: (x.page, x.y0)) if below else None
    cells = sorted(
        (
            ln
            for ln in lines
            if ln.page == top.page
            and top.x1 < ln.x0 < way.x0 - 5
            and ln.y0 + 3 >= top.y0
            and (bottom is None or (ln.page, ln.y0 + 3) < (bottom.page, bottom.y0))
        ),
        key=lambda x: (x.y0, x.x0),
    )
    rates = re.findall(r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)", "".join(squash(ln.text) for ln in cells))
    if len(rates) != 1:
        return ParsedField.invalid(name, [top, *cells], "費用表「分銷費用」的「費用」子列沒有剛好一個「X%~Y%」")
    return ParsedField.present(name, rates[0], [top, *cells])


# ---------------------------------------------------------------- 出處清單


def _min_amount(fields: dict[str, ParsedField], name: str, units: str, amount: str | None) -> ParsedField:
    """最低金額：`amount`（「即」後的金額）須 = 單位數 × 面額；`amount` 為 None 時用單位數 × 面額（加購、累加單位）。"""
    denom, n = fields["denomination"], fields[units]
    bad = next((p for p in (denom, n) if not p.ok), None)
    if bad is not None:
        return derived(name, bad)
    value = n.value * denom.value
    if amount is None:
        return derived(name, n, value)
    shown = fields[amount]
    if not shown.ok:
        return derived(name, shown)
    if shown.value != value:
        note = f"「{n.value}單位商品面額」= {value}，和「即」後的金額 {shown.value} 不同"
        return ParsedField(name, FieldStatus.INVALID, evidence=list(shown.evidence), note=note)
    return derived(name, shown, value)


def _occurrences(fields: dict[str, ParsedField]) -> None:
    occ = Occurrence
    minimums = (
        (
            "minimum_subscription",
            "最低申購金額",
            "一般交易事項 4. 最低申購金額",
            "minimum_subscription_units",
            "minimum_subscription_amount",
        ),
        ("minimum_additional", "最低加購金額", "一般交易事項 4. 最低加購金額", "minimum_subscription_step", None),
        (
            "minimum_redemption",
            "最低贖回金額",
            "一般交易事項 8.(3) 最低贖回金額",
            "minimum_redemption_units",
            "minimum_redemption_amount",
        ),
        ("redemption_increment", "增加贖回單位", "一般交易事項 8.(3) 累加單位", "minimum_redemption_step", None),
    )
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [occ(f, label, where, _min_amount(fields, f, units, amount)) for f, label, where, units, amount in minimums],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ(
                "subscription_start",
                "開始受理申購日",
                "一般交易事項 1.(1) 商品開始受理申購日期",
                fields["subscription_start"],
            )
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "第 2 頁中文產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.absent("issue_price_others", "發行價格的其他出處")
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "一般交易事項受託機構電話")
    fields.update(standard_fields.absent_all(standard_fields.DISTRIBUTOR_OTHERS, "受託機構的其他出處"))
    currencies = (  # 金額旁的幣別字（Issue #170）：價格表幣別字（標的報價幣別）、情境股價不列入；情境一～三另在 read 加入
        ("denomination_currency", "每單位商品面額幣別", "商品基本資料商品面額"),
        ("minimum_subscription_currency", "最低申購金額幣別", "一般交易事項 4. 最低申購金額"),
        ("minimum_redemption_currency", "最低贖回金額幣別", "一般交易事項 8.(3) 最低贖回金額"),
        ("scenario_denomination_currency", "情境假設商品面額幣別", "情境分析假設商品面額"),
        ("scenario_investment_currency", "情境假設投資金額幣別", "情境分析假設投資金額"),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [occ(key, label, where, fields[key]) for key, label, where in currencies]
    )
