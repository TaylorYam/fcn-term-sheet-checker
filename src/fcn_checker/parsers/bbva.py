"""BBVA 中文產品說明書（docs/templates/bbva-zh-product-description.md）：章＋條號＋標籤定位，保留原文證據。

沒有獨立封面：第 1 頁是編號清單 1.～14.（商品代號、ISIN 只在這裡，沒有發行機構名稱欄），第 2 頁末是刊印日期。
章名「一、商品基本資料」～「六、存託憑證條款相關說明」（沒有「第N章」）；條號「N.」x≈28。每頁頁底只有「N/M」頁碼。
文字有 CJK 相容字（章名「揭露」的「露」U+F938 等），讀之前先轉成標準字（只轉 U+F900–U+FAFF，同 GS 投資人須知）。
數字、日期與句型在去空白的全文索引上讀；名稱、地址等要保留空白的值在原文合併文字（`common.RawText`）上讀。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import money, price_table
from .common import RawText, capture_group, compat_normalized, derived, fee_rates, number
from .layout import Document, LayoutSpec, TextIndex, capture, parse_date

TEMPLATE_ID = "bbva-zh-pd"
PARSER_VERSION = "2"  # 2：價格表改由 parsers/price_table.py 讀（Issue #287）
CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
    6: ("六", "存託憑證條款相關說明"),
}
# 條號「N.」x≈28（第二章、第四章的條內清單 x≈71，不算條號）；子項「(n)」x≈46–63
LAYOUT = LayoutSpec(
    r"{zh}、{name}",
    re.compile(r"^(\d{1,2})\.(?!\d)"),
    45,
    re.compile(r"^[(（](\d{1,2})[)）]\s*(.*)$"),
    80,
    CHAPTER_NAMES,
)
ISSUER_EN = "BBVA GLOBAL MARKETS, B.V."
ISSUER_PREFIX = "荷蘭商"  # 第二章與投資人須知的寫法；第一章 3.(1) 沒有（2026-10-15 確認也算一致）
NAME_ZH_MARK = "自動提前贖回連結一籃子股票結構型商品(無擔保及不保本)"
NAME_EN_MARK = "Autocallable Basket of Equity Linked Notes (Non Principal Guaranteed and Unsecured)"
ARTICLES = 25  # 第一章條數
PAGE_NO = re.compile(r"^\d+/\d+$")
FOOTER_Y = 800  # 頁底頁碼 y≈815
D = r"(\d{4}年\d{1,2}月\d{1,2}日)"
PCT = r"(\d+(?:\.\d+)?)%"
CCY = money.CURRENCY
ZH_CCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})\s+Equity(?![A-Za-z])")
# 價格表（第 14 項、第 16 項情境）：以彭博代碼切列，欄序 KO → K → KI，表頭 %「（期初價格的X%）」，價格後接幣別字
PRICE_TABLE = price_table.TableSpec(
    where="第 14 項價格表",
    initial="期初價格",
    columns=(
        price_table.Column("ko", "出場值"),
        price_table.Column("strike", "轉換比例"),
        price_table.Column("ki", "下限值", required=False),
    ),
    anchor="ticker",
    pct=r"期初價格的(\d+(?:\.\d+)?)%",
    pct_label="（期初價格的X%）",
    code="彭博代碼",
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="第 16 項情境價格表")
# 第 12 項 KO 觀察（範本規格 §4.1）
KO_DAILY = "自動提前贖回觀察日(AutomaticEarlyRedemptionValuationDate(s))：自動提前贖回觀察期間內之每個預定交易日"
KO_PERIOD = (
    r"自動提前贖回觀察期間[(（]AutomaticEarlyRedemptionValuationPeriod[)）][:：]自" + D + r"（含）起至(.+?)止之期間"
)
KO_TABLE_HEAD = "ij自動提前贖回觀察日自動提前贖回日j觀察日"
# 第 15 項自動提前贖回事件與記憶式（範本規格 §4.2）
KO_EVENT = r"「自動提前贖回事件\(AutomaticEarlyRedemptionEvent\)」：(.+?)大於或等於" + PCT + "。"
MEMORY = "不須出現在同一個自動提前贖回觀察日"
# 第 14 項到期條件（範本規格 §4.4）
KI_DEF = r"若於贖回評價日之最差表現值大於或等於" + PCT + r"（下限值）：100%"
NO_KI_BARRIER = r"若於贖回評價日之最差表現值大於或等於" + PCT + r"：100%"
KO_AT_MATURITY = r"最佳表現值大於或等於" + PCT + r"（出場值）：100%"
STRIKE_DEF = r"轉換比例\(PutStrikePercentage\)：" + PCT


def clean(lines: Sequence[Line]) -> list[Line]:
    """相容字轉成標準字；去掉頁底「N/M」頁碼。"""
    return compat_normalized([ln for ln in lines if not (ln.y0 >= FOOTER_Y and PAGE_NO.match(ln.text.strip()))])


def document(lines: Sequence[Line]) -> Document:
    return Document(clean(lines), LAYOUT)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：全部條件成立才是 BBVA 說明書。"""
    doc = document(lines)
    cover = doc.before_chapter1()
    head = [squash(x.text) for x in cover[:2]]
    ti = TextIndex(cover)
    raw = RawText(cover)
    zh = re.search(r"商品中文名稱\s*：\s*(.+?)\s*\(下稱", raw.text, re.S)
    en = re.search(r"商品英文名稱\s*：\s*(.+?)\s*\(the", raw.text, re.S)
    zh_s, en_s = squash(zh[1]) if zh else "", squash(en[1]) if en else ""
    failed = []
    for test, message in (
        (head == ["(僅適用於專業投資人)", "中文產品說明書"], "前兩行不是「(僅適用於專業投資人)」「中文產品說明書」"),
        (
            all(x in ti.text for x in ("商品代號：", "受託機構商品代號：", "國際證券編碼ISIN：")),
            "第 1 頁找不到「商品代號」「受託機構商品代號」「國際證券編碼ISIN」",
        ),
        (
            zh_s.startswith(squash(ISSUER_EN)) and NAME_ZH_MARK in zh_s,
            f"商品中文名稱不是以「{ISSUER_EN}」開頭或沒有「{NAME_ZH_MARK}」",
        ),
        (
            en_s.startswith(squash(ISSUER_EN)) and squash(NAME_EN_MARK) in en_s,
            f"商品英文名稱不是以「{ISSUER_EN}」開頭或沒有「{NAME_EN_MARK}」",
        ),
        (
            "總代理人之名稱、電話及地址：" in ti.text and "保證機構之名稱及地址：" in ti.text,
            "第 1 頁沒有「總代理人之名稱、電話及地址」與「保證機構之名稱及地址」",
        ),
        (
            all(n in doc.chapters for n in CHAPTER_NAMES),
            "章名不是「一、商品基本資料」…「六、存託憑證條款相關說明」或六章不完整",
        ),
    ):
        if not test:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in cover[:2]])


# ---------------------------------------------------------------- 讀出


@dataclass
class BbvaTermSheet:
    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Document

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)


def read(lines: Sequence[Line]) -> BbvaTermSheet:
    """讀出標準欄位與 BBVA 規則需要的專屬資料（範本辨識另由 `detect` 負責，不在這裡重做）。"""
    doc = document(lines)
    arts = doc.articles(1)
    fields: dict[str, ParsedField] = {}

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    def put(name: str, lns: list[Line], pattern: str, convert: Callable = lambda x: x) -> None:
        fields[name] = capture(name, TextIndex(lns), pattern, convert, whole_match=True)

    _cover(fields, doc.before_chapter1(), put)

    # 第一章
    put("name_art1_zh", article(1), r"商品名稱：(.+)$")
    fields["issuer_name_ch1"] = _issuer_ch1(RawText(article(3)))
    put("currency_art5", article(5), rf"計價幣別：({ZH_CCY})$")
    fields["underlying_list"] = underlying_list("underlying_list", article(9))
    art12 = article(12)
    put("tenor_months", art12, r"\(1\)商品年期：(\d+)個月", int)
    put("trade_date", art12, r"\(2\)交易日：" + D, parse_date)
    put("issue_date", art12, r"\(3\)發行日：" + D, parse_date)
    put("maturity_date", art12, r"\(4\)到期日：" + D, parse_date)
    put("final_valuation_date", art12, r"贖回評價日：" + D, parse_date)
    _article12(fields, art12)
    art13 = article(13)
    put("coupon_annual_art13", art13, r"年利率：" + PCT, Decimal)
    put("coupon_m", art13, r"m：(\d+)", int)
    put("monthly_coupon_pct", art13, r"配息率（RateofInterest）：" + PCT, Decimal)
    put("day_count", art13, r"計息天數慣例（DayCountFraction）：「([^｣」（]+)")
    art14 = article(14)
    put("strike_def_pct", art14, STRIKE_DEF, Decimal)
    put("ki_def_pct", art14, KI_DEF, Decimal)
    put("no_ki_barrier_pct", art14, NO_KI_BARRIER, Decimal)
    put("ko_at_maturity_pct", art14, KO_AT_MATURITY, Decimal)
    fields["price_table"] = price_table.read(
        "price_table", table_region(art14, "交割營業日", "最低保證收益率"), PRICE_TABLE
    )
    fields["ki_type"] = ki_type(fields, art14)
    art15 = article(15)
    fields["ko_memory"], fields["ko_def_pct"] = ko_event(TextIndex(art15), art15)
    _table_fields(fields, fields["price_table"])
    # 年利率：KO 每日型在第 13 項；KO 每期型第 13 項只有每期配息率，由 _article16 改取第 16 項「年利率相當於約y%」
    fields["coupon_pa_pct"] = fields["coupon_annual_art13"]
    _schedule(fields)
    _article16(fields, article(16))

    _chapter2(fields, doc)
    _chapter4(fields, doc, put)
    _occurrences(fields)
    fields["initial_pricing"] = standard_fields.absent(
        "initial_pricing", "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP）"
    )
    return BbvaTermSheet(fields, TextIndex(doc.lines), doc)


def _cover(fields: dict[str, ParsedField], cover: list[Line], put: Callable) -> None:
    """第 1～2 頁編號清單（範本規格 §2.1）與第 2 頁末刊印日期。"""
    raw = RawText(cover)
    put("product_code", cover, r"\d\.商品代號：(\d{12})")
    put("distributor_code", cover, r"受託機構商品代號：(\d{12})")
    put("isin", cover, r"國際證券編碼ISIN：([A-Z]{2}[A-Z0-9]{10})")
    fields["name_zh"] = raw.capture("name_zh", r"商品中文名稱\s*：\s*(.+?)\s*\(下稱「本商品」\)", squash)
    fields["name_en"] = raw.capture("name_en", r"商品英文名稱\s*：\s*(.+?)\s*\(the \"Product\"\)")
    put("product_type", cover, r"\d\.商品種類：(.+?)\d+\.發行機構註冊地")
    put("currency_zh", cover, rf"\d\.計價幣別：({ZH_CCY})\d+\.總代理人")
    dist = (
        r"受託機構之名稱、電話及地址\s*：\s*(.+?)\s*，\s*電話\s*：\s*(.+?)\s*，\s*地址\s*：\s*(.+?)\s*\d+\.\s*公會審查"
    )
    fields["distributor_name_cover"] = raw.capture("distributor_name_cover", dist)
    fields["distributor_phone_cover"] = raw.capture("distributor_phone_cover", dist, group=2)
    fields["distributor_address_cover"] = raw.capture("distributor_address_cover", dist, group=3)
    put("approval_date", cover, r"受託機構審查通過之日期及文號：" + D, parse_date)
    put("print_date", cover, r"(?<!中文)產品說明書刊印日期" + D, parse_date)
    fields["issuer_name_cover"] = standard_fields.absent("issuer_name_cover", "第 1 頁發行機構名稱欄")


def _issuer_ch1(raw: RawText) -> ParsedField:
    """第一章 3.(1) 發行機構：寫 `BBVA GLOBAL MARKETS, B.V.`（沒有「荷蘭商」），補上前綴後交出，和第二章、投資人須知
    同一個固定值比（2026-10-15 確認也算一致；證據保留原文）。"""
    pf = raw.capture("issuer_name_ch1", r"\(1\)\s*發行機構\s*：\s*(.+?)\s*發行機構信用評等")
    if pf.ok and not pf.value.startswith(ISSUER_PREFIX):
        return replace(pf, value=ISSUER_PREFIX + pf.value)
    return pf


# ---------------------------------------------------------------- 第 9 項連結標的表、第 14／16 項價格表


def underlying_list(name: str, art9: list[Line]) -> ParsedField:
    """第 9 項連結標的表的彭博代碼（`ABC UW Equity` 去掉 Equity），依表上順序；表上沒有價格。"""
    ti = TextIndex(art9)
    a = ti.text.find("(1)連結標的資產")
    b = ti.text.find("相對權重", a)
    if a < 0 or b < a:
        return ParsedField.missing(name, "第 9 項找不到連結標的表（「(1) 連結標的資產」到「相對權重」之間）")
    lines = art9[ti.line_at(a) : ti.line_at(b)]
    tickers = [f"{m[1]} {m[2]}" for m in TICKER.finditer(RawText(lines).text)]
    if not tickers:
        return ParsedField.invalid(name, lines, "連結標的表讀不到彭博代碼（代號 交易所碼 Equity）")
    return ParsedField.present(name, tickers, lines)


def table_region(lns: list[Line], start: str, end: str) -> list[Line]:
    """價格表的範圍：`start` 那一行之後、`end` 那一行之前（去空白比對）。"""
    ti = TextIndex(lns)
    a = ti.text.find(start)
    b = ti.text.find(end, a + 1) if a >= 0 else -1
    return lns[ti.line_at(a) + 1 : ti.line_at(b)] if a >= 0 and b >= 0 else []


def _table_fields(fields: dict[str, ParsedField], table: ParsedField) -> None:
    """價格表 → 標準欄位：標的、各標的價格；K／KO／KI % 主來源是第 14、15 項定義（表頭 % 由規則交叉驗證）。"""
    fields["underlyings"] = price_table.underlyings(table)
    fields["underlying_prices"] = price_table.price_rows(table)
    fields["strike_pct"] = fields["strike_def_pct"]
    fields["ko_pct"] = fields["ko_def_pct"]
    ki_t = fields["ki_type"]
    if ki_t.ok and ki_t.value == "none":
        fields["ki_pct"] = ParsedField(
            "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(ki_t.evidence), note="說明書無 KI（沒有下限值）"
        )
    else:
        fields["ki_pct"] = fields["ki_def_pct"]


# ---------------------------------------------------------------- KO、KI、記憶式


def ki_type(fields: dict[str, ParsedField], art14: list[Line]) -> ParsedField:
    """第 14 項到期條件：「…最差表現值大於或等於z%（下限值）：100%」→ AM；只有「…大於或等於y%：100%」（沒有「（下限值）」）
    → none（以 present「none」交出）；兩種都有或都沒有 → 不合法（範本規格 §4.4）。"""
    ki, barrier = fields["ki_def_pct"], fields["no_ki_barrier_pct"]
    if ki.ok and barrier.status == FieldStatus.MISSING:
        return ParsedField("ki_type", FieldStatus.PRESENT, "AM", list(ki.evidence))
    if barrier.ok and ki.status == FieldStatus.MISSING:
        return ParsedField("ki_type", FieldStatus.PRESENT, "none", list(barrier.evidence))
    return ParsedField.invalid("ki_type", art14, "第一章第 14 項到期條件的下限值寫法缺漏、重複或不在範本規格內")


def ko_event(ti: TextIndex, lns: list[Line]) -> tuple[ParsedField, ParsedField]:
    """第一章第 15 項自動提前贖回事件 → (ko_memory, ko_def_pct)：「最佳表現值」＋「不須出現在同一個…觀察日」→ 記憶式；
    「最差表現值」→ 非記憶式（範本規格 §4.2）。投資人須知二、 2.(3) 沒有「不須出現在同一個」那句，另在 bbva_iis 讀。"""
    where = "第一章第 15 項"
    found = list(ti.finditer(KO_EVENT))
    if len(found) != 1:
        bad = ParsedField.invalid("ko_memory", lns, f"{where}自動提前贖回事件的句型缺漏、重複或不在範本規格內")
        return bad, replace(bad, name="ko_def_pct")
    m = found[0]
    ev = ti.lines_for(m.start(), m.end())
    subject = m[1]
    pct = ParsedField.present("ko_def_pct", Decimal(m[2]), ev)
    if "最佳表現值" in subject and MEMORY in ti.text:
        return ParsedField.present("ko_memory", True, ev), pct
    if "最差表現值" in subject and MEMORY not in ti.text:
        return ParsedField.present("ko_memory", False, ev), pct
    return ParsedField.invalid("ko_memory", ev, f"{where}自動提前贖回事件的記憶式寫法不在範本規格內"), pct


# ---------------------------------------------------------------- 第 12 項日期表與 KO 排程


# 期別與日期之間：分行時有「|」（`separate_digits`），同一行時直接相接（`12030年…` 只能是期別 1 接 2030 年）
ROW3 = re.compile(r"(\d{1,2})\|?" + D * 3)
ROW1 = re.compile(r"(\d{1,2})\|?" + D)
KO_ROW = re.compile(r"(\d{1,2})\|?" + D + D + r"(\d{1,2})\|?" + D)


def _rows(text: str, pattern: re.Pattern[str]) -> list[tuple[int, list]]:
    """表格的列（期別＋日期）；期別須從 1 起連續，只取符合的列。"""
    out = []
    for m in pattern.finditer(text):
        if int(m[1]) != len(out) + 1:
            break
        out.append((int(m[1]), [parse_date(x) if "年" in x else int(x) for x in m.groups()[1:]]))
    return out


def _article12(fields: dict[str, ParsedField], art12: list[Line]) -> None:
    """日期表（KO 每日：配息觀察期間表；KO 每期：配息日表＋自動提前贖回觀察日表）、KO 觀察寫法與觀察期間（範本規格 §5.2）。"""
    ti = TextIndex(art12, separate_digits=True)
    text = ti.text
    period = list(ti.finditer(KO_PERIOD))
    if len(period) == 1:
        m = period[0]
        end = parse_date(m[2]) if "年" in m[2] else m[2]
        fields["ko_period"] = ParsedField.present(
            "ko_period", {"start": parse_date(m[1]), "end": end}, ti.lines_for(m.start(), m.end())
        )
    else:
        fields["ko_period"] = ParsedField.missing(
            "ko_period", "第 12 項找不到或有多處「自動提前贖回觀察期間：自…止之期間」"
        )
    daily, periodic = text.count(KO_DAILY), text.count(KO_TABLE_HEAD)
    if daily == 1 and periodic == 0:
        head = "配息觀察期間i起始日(含)終止日(含)配息日"
        a = text.find(head)
        b = text.find("一旦自動提前贖回事件發生", a)
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
        a = text.find(KO_DAILY)
        fields["ko_wording"] = ParsedField.present("ko_wording", "D", ti.lines_for(a, a + len(KO_DAILY)))
    elif periodic == 1 and daily == 0:
        a = text.find("i配息日")
        b = text.find(KO_TABLE_HEAD)
        if a < 0 or b < a:
            fields["date_table"] = ParsedField.missing("date_table", "第 12 項找不到配息日表")
        else:
            pays = _rows(text[a + len("i配息日") : b], ROW1)
            ko = _rows(text[b + len(KO_TABLE_HEAD) :], KO_ROW)
            ev = ti.lines_for(a, len(text))
            if not pays or not ko or any(None in d for _, d in pays + ko):
                fields["date_table"] = ParsedField.invalid("date_table", ev, "配息日表或自動提前贖回觀察日表的列讀不到")
            else:
                fields["date_table"] = ParsedField.present(
                    "date_table",
                    {
                        "kind": "P",
                        "rows": [{"period": p, "payment": d[0]} for p, d in pays],
                        "ko_rows": [
                            {"t": t, "observation": d[0], "autocall": d[1], "j": d[2], "observation_j": d[3]}
                            for t, d in ko
                        ],
                    },
                    ev,
                )
        fields["ko_wording"] = ParsedField.present("ko_wording", "P", ti.lines_for(b, b + len(KO_TABLE_HEAD)))
    else:
        note = "第 12 項自動提前贖回觀察日的寫法缺漏、重複或不在範本規格內"
        fields["ko_wording"] = ParsedField.invalid("ko_wording", art12, note)
        fields["date_table"] = ParsedField.invalid("date_table", art12, note)


def _schedule(fields: dict[str, ParsedField]) -> None:
    """KO 觀察方式、第一個可提前贖回期與提前贖回排程（範本規格 §4.1）。

    D：觀察期間起日 = 日期表第 k 期終止日，k 即 Non-Call；比價日為第 k 期終止日與贖回評價日。
    P：k = 表第 1 列自動提前贖回日在配息日表的期別；表的期別須從 k 連續到天期 − 1，比價日為每列的自動提前贖回觀察日，
    最後一期為贖回評價日。"""
    wording, table, period, final = (
        fields["ko_wording"],
        fields["date_table"],
        fields["ko_period"],
        fields["final_valuation_date"],
    )
    keys = ("ko_observation", "first_callable_period", "autocall_schedule")
    bad = next((p for p in (wording, table, period, final) if not p.ok), None)
    if bad is not None:
        for key in keys:
            fields[key] = derived(key, bad)
        return
    kind = wording.value
    rows = table.value["rows"]
    n = len(rows)
    ev = list(wording.evidence) + list(period.evidence) + list(table.evidence)

    def invalid(note: str) -> None:
        for key in keys:
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)

    dates: dict[int, dt.date]
    if kind == "D":
        ends = [r["end"] for r in rows]
        if period.value["start"] not in ends:
            invalid("自動提前贖回觀察期間的起日不是日期表任何一期的終止日")
            return
        k = ends.index(period.value["start"]) + 1
        dates = {k: rows[k - 1]["end"], n: final.value}
    else:
        period_of = {r["payment"]: r["period"] for r in rows}
        mapped = [(period_of.get(r["autocall"]), r["observation"]) for r in table.value["ko_rows"]]
        if any(p is None for p, _ in mapped):
            invalid("自動提前贖回觀察日表的自動提前贖回日不在配息日表內")
            return
        k = mapped[0][0]
        if [p for p, _ in mapped] != list(range(k, n)):
            invalid(f"自動提前贖回觀察日表的期別不是第 {k} 期到第 {n - 1} 期連續")
            return
        dates = {p: d for p, d in mapped}
        dates[n] = final.value
    fields["ko_observation"] = ParsedField("ko_observation", FieldStatus.PRESENT, kind, list(wording.evidence))
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, k, ev)
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule", FieldStatus.PRESENT, standard_fields.AutocallSchedule(kind, k, n, dates), ev
    )


# ---------------------------------------------------------------- 第 16 項情境分析


def _article16(fields: dict[str, ParsedField], art16: list[Line]) -> None:
    """情境假設（總投資金額、商品年期、年利率或每月利率）、情境價格表、情境一第 1 期配息率（範本規格 §4.9）。"""
    ti = TextIndex(art16)
    total = rf"總投資金額=(\d+)單位面額=([\d,]+)({CCY})"
    fields["scenario_units"] = capture("scenario_units", ti, total, int, whole_match=True)
    fields["scenario_amount"] = capture_group(art16, total, 2, "scenario_amount", number)
    fields["scenario_tenor"] = capture("scenario_tenor", ti, r"[•‧]商品年期：(\d+)個月", int, whole_match=True)
    if fields["date_table"].ok and fields["date_table"].value["kind"] == "P":
        fields["scenario_monthly_pct"] = capture(
            "scenario_monthly_pct", ti, r"配息率：每月利率" + PCT, Decimal, whole_match=True
        )
        fields["scenario_coupon_pct"] = capture(
            "scenario_coupon_pct", ti, r"年利率相當於約" + PCT, Decimal, whole_match=True
        )
        fields["coupon_pa_pct"] = fields["scenario_coupon_pct"]
        fields["scenario_first_coupon_pct"] = standard_fields.absent(
            "scenario_first_coupon_pct", "KO 每期型情境一配息率（只寫每單位配息金額）"
        )
    else:
        fields["scenario_coupon_pct"] = capture(
            "scenario_coupon_pct", ti, r"[•‧]年利率：" + PCT, Decimal, whole_match=True
        )
        first = rf"情境一.*?配息金額＝\d+單位面額X配息率＝\d+X[\d,.]+{CCY}X" + PCT
        fields["scenario_first_coupon_pct"] = capture("scenario_first_coupon_pct", ti, first, Decimal, whole_match=True)
    fields["scenario_table"] = price_table.read(
        "scenario_table", table_region(art16, "假設：", "情境一"), SCENARIO_TABLE
    )


# ---------------------------------------------------------------- 第二章、第四章


def _chapter2(fields: dict[str, ParsedField], doc: Document) -> None:
    """第二章依標籤讀（不依條號：BV1 第二章的條號 Word 自動編號跑掉，3. 寫成 1.）。"""
    raw = RawText(doc.chapter_lines(2))
    fields["issuer_name_ch2"] = raw.capture(
        "issuer_name_ch2", r"1\.\s*發行機構\s*\(1\)\s*設立日期及簡介.*?\(a\)\s*事業名稱\s*：\s*(.+?)\s*\(b\)\s*設立日期"
    )
    trustee = (
        r"\(4\)\s*受託機構\s*：\s*事業名稱\s*：\s*(.+?)\s*設立日期\s*：.*?營業所在地\s*：\s*(.+?)\s*"
        r"負責人姓名\s*：\s*(.+?)\s*\(5\)"
    )
    fields["distributor_name_ch2"] = raw.capture("distributor_name_ch2", trustee)
    fields["distributor_address_ch2"] = raw.capture("distributor_address_ch2", trustee, group=2)
    fields["chairman"] = raw.capture("chairman", trustee, group=3)


def fee_table(lns: list[Line]) -> list[Line]:
    """費用表（到表後說明「分銷費用將反映於本商品之淨值…」之前；那一行也以「分銷費」開頭，不是費用項目）。"""
    end = next((i for i, ln in enumerate(lns) if squash(ln.text).startswith("分銷費用將反映")), len(lns))
    return lns[:end]


def _chapter4(fields: dict[str, ParsedField], doc: Document, put: Callable) -> None:
    ch4 = doc.articles(4)

    def art(n: int) -> list[Line]:
        return doc.span_lines(ch4.get(n))

    put("subscription_start", art(1), r"商品開始受理申購日期：" + D, parse_date)
    put("redemption_start", art(1), r"開始受理透過次級市場買賣之贖回日期：" + D, parse_date)
    fields.update(fee_rates(fee_table(art(2))))
    amounts = (
        ("denomination", r"\(1\)每單位面額：", ""),
        ("minimum_subscription", r"\(2\)最低申購金額：", r"（相當於[一1]單位面額）"),
        ("minimum_additional", r"\(3\)最低加購金額：", r"（相當於[一1]單位面額）"),
    )
    for name, label, tail in amounts:
        pattern = label + rf"([\d,]+)({CCY}){tail}"
        put(name, art(4), pattern, number)
        fields[f"{name}_currency"] = capture_group(art(4), pattern, 2, f"{name}_currency")
    redeem = rf"最低贖回金額為([\d,]+)({CCY})（每單位）"
    put("minimum_redemption", art(8), redeem, number)
    fields["minimum_redemption_currency"] = capture_group(art(8), redeem, 2, "minimum_redemption_currency")
    put("issue_price_pct", art(5), r"發行價格\(商品面額的" + PCT + r"\)", Decimal)


# ---------------------------------------------------------------- 出處清單


def _occurrences(fields: dict[str, ParsedField]) -> None:
    occ = Occurrence
    fields["min_amounts"] = standard_fields.occurrences(
        "min_amounts",
        [
            occ("minimum_subscription", "最低申購金額", "第四章第 4 項最低申購金額", fields["minimum_subscription"]),
            occ("minimum_additional", "最低加購金額", "第四章第 4 項最低加購金額", fields["minimum_additional"]),
            occ("minimum_redemption", "最低贖回金額", "第四章第 8 項最低贖回金額", fields["minimum_redemption"]),
        ],
    )
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            occ(
                "subscription_start",
                "開始受理申購日",
                "第四章第 1 項商品開始受理申購日期",
                fields["subscription_start"],
            )
        ],
    )
    fields["print_dates"] = standard_fields.occurrences(
        "print_dates", [occ("print_date", "刊印日期", "第 2 頁產品說明書刊印日期", fields["print_date"])]
    )
    fields["issue_price_others"] = standard_fields.absent("issue_price_others", "發行價格的其他出處")
    fields["distributor_phone_ch4"] = standard_fields.absent("distributor_phone_ch4", "第四章受託機構電話")
    currencies = (  # 金額旁的幣別字（Issue #170）：價格表幣別字（標的報價幣別）、情境算式金額不列入
        ("denomination_currency", "每單位面額幣別", "第四章第 4 項每單位面額"),
        ("minimum_subscription_currency", "最低申購金額幣別", "第四章第 4 項最低申購金額"),
        ("minimum_additional_currency", "最低加購金額幣別", "第四章第 4 項最低加購金額"),
        ("minimum_redemption_currency", "最低贖回金額幣別", "第四章第 8 項最低贖回金額"),
    )
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [occ(key, label, where, fields[key]) for key, label, where in currencies]
    )
