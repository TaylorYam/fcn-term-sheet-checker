"""Natixis 合成說明書（docs/templates/natixis-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：封面「中文產品說明書」「(適用於專業投資人及OSU 客戶)」、標題名稱、「TDCC 商品代號為…，ISIN 為…」，
再來是「標籤：值」兩欄（標籤 x 41、值 x 241，標籤放在值的中間那一行，跨行的標籤拆兩行）；章名「第一章、商品基本資料」在左邊界，
條號「N.」第一章 x 54、其他章 x 36。每頁頁底有英文名稱兩行、「第N頁/共計M頁」與分類標記（真實文件的頁尾，擷取時排除）。
表格（連結標的表、情境價格表）的格子置中於欄中心，表頭一格拆成多行；`split_numbers` 時價格從小數點後拆行。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`（封面；標題名稱 `ts.cover.title`）、`ts.warn`（封面警語）、第一章各條
`ts.art{n}`（連結標的表 `ts.art9.table`、日期表 `ts.art12.table`（每期型的自動提前到期觀察日表 `ts.art12.table.ko`）、情境價格表 `ts.art16.table`）、其他各章 `ts.ch二`～`ts.ch五`。
投資人須知（docs/templates/natixis-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
段落代號 `iis.cover`、`iis.warn`、`iis.org`（壹）、`iis.summary`（貳 1.）、`iis.coupon`（貳 2.）、`iis.fees`（肆）、`iis.rest`（其他）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import fitz

from harness import CONFIG, STANDARD, check_all, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row, weekday_on_or_after

NAME = STANDARD["product_name"]["natixis"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["natixis"]
ISSUER = STANDARD["issuer_name"]["natixis"]
PRODUCT_TYPE = STANDARD["product_type"]["natixis"]
DIST = STANDARD["distributor"]
PHONE = "+886 2 5556 1313"  # 審查標準 phone_equivalents 的寫法
ADDRESS = "台北市民生東路三段158號6樓"  # 審查標準 address_equivalents（沒有「松山區」）
ISIN = "XS1999900061"
APPROVAL = dt.date(2026, 6, 11)
Q4 = Decimal("0.0001")
TICKERS = {
    "US": ("ZNA UW", "ZNB UN", "ZNC UW"),
    "JT": ("7101 JT", "7102 JT", "7103 JT"),
}
EXCHANGE = {"UN": "紐約證券交易所", "UW": "那斯達克股票交易所", "JT": "東京證券交易所"}
QUOTE = {"US": "美元", "JT": "日圓"}  # 標的報價幣別
PRICING = {"close": "收盤價", "open": "開盤價", "vwap": "VWAP"}  # 參考條件表「期初定價」
CN = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二")


def underlyings(count: int, market: str) -> tuple[UL, ...]:
    """Natixis 合成文件的標的：美股期初價 100n + 0.45（2 位小數，說明書照交易所報價不補 0），日股 1000n + 500（整數）。"""
    out = []
    for i in range(1, count + 1):
        ticker = TICKERS[market][i - 1]
        initial = Decimal(1000 * i + 500) if market == "JT" else Decimal(100 * i) + Decimal("0.45")
        out.append(UL(f"虛構控股{CN[i - 1]}(Fict{i} Corp)", EXCHANGE[ticker.split()[1]], ticker, initial))
    return tuple(out)


@dataclass
class Spec(ProductSpec):
    """Natixis 合成說明書的規格：商品規格加上 Natixis 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count`、`market` 產生、最終比價日與到期日由日期表推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    """

    issuer: str = "NATIXIS"
    product_code: str = "328199990001"
    ko_obs: str = "D"  # D 每日（配息觀察期間表）／P 每期（配息日表＋自動提前到期觀察日表）
    ki: str = "AM"  # none／AM／D／P
    tenor: int = 6
    first_callable: int = 2
    memory: bool = True
    annual: Decimal = Decimal("18.00")
    strike: Decimal = Decimal("75.00")
    ko: Decimal = Decimal("105.00")
    ki_pct: Decimal = Decimal("60.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    market: str = "US"  # 標的市場：US 美股、JT 日股
    pricing: str = "close"  # 參考條件表期初定價（說明書沒寫）
    old: bool = False  # 舊版：封面寫「受託機構」、TDCC 行有空白的「受託機構商品代號為」
    split_numbers: bool = False  # 連結標的表界限／執行價格從小數點後拆行（`1,234.56` / `00`）
    approval: dt.date = APPROVAL
    iis_pages: int = 4

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count, self.market)
        self.final_date = self.ends[-1]
        self.maturity_date = self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期配息觀察期間結束日（= 每期自動提前到期觀察日）：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def starts(self) -> list[dt.date]:
        """各期配息觀察期間開始日：第 1 期為發行日，其後為前期結束日。"""
        return [self.issue_date, *self.ends[:-1]]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=3)) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    @property
    def distributor(self) -> str:
        return "受託機構" if self.old else "受託或銷售機構"

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh)

    @property
    def name_en(self) -> str:
        return NAME["en"].format(tenor=self.tenor, ccy=self.ccy)

    @property
    def name_en_inner(self) -> str:
        """第一章第 1 項、投資人須知、頁尾的英文名稱（`Non Principal`，沒有連字號）。"""
        return self.name_en.replace("(Non-Principal", "(Non Principal")

    @staticmethod
    def date(d: dt.date) -> str:
        return f"{d.year} 年{d.month:02d} 月{d.day:02d} 日"

    @staticmethod
    def date_iis(d: dt.date) -> str:
        return f"{d.year}年{d.month:02d}月{d.day:02d}日"

    def money(self, x: int | Decimal) -> str:
        return f"{Decimal(x):,.0f} {self.currency_zh}"

    def initial_text(self, u: UL) -> str:
        """期初進場價格照交易所報價位數（日股整數、美股 2 位小數）。"""
        return f"{u.initial:,.0f}" if self.market == "JT" else f"{u.initial:,.2f}"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\x21-\x7e]+|\s|.", text)


def _width(text: str, size: float) -> float:
    return sum(fitz.get_text_length(c, fontname="helv", fontsize=size) if ord(c) < 256 else size for c in text)


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（英數字不拆開）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts", width: float = 565):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"
        self.width = width

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def text(self, x: float, text: str, cont: float | None = None, gap: float = 12, size: float = 10) -> None:
        text = self.w.edit(text)
        if not text:
            return
        nxt = cont if cont is not None else x
        cur, used, here = "", 0.0, x
        with self.w.verbatim():
            for tok in _tokens(text):
                tw = _width(tok, size)
                if used + tw > self.width - here and cur.strip():
                    self.w.line(here, cur.strip(), size=size, gap=gap)
                    cur, used, here = "", 0.0, nxt
                    if tok.isspace():
                        continue
                cur += tok
                used += tw
            self.w.line(here, cur.strip(), size=size, gap=gap)

    def chapter(self, zh: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{zh}"
        self.w.line(36, f"第{zh}章、{name}", gap=16)

    def article(self, n: int, title: str) -> None:
        if self.section == "cover" or self.section.startswith("art") or self.section == "ch一":
            self.section = f"art{n}"
        self.text(54, f"{n}. {title}", 72)

    def labeled(self, label: list[str], values: list[str]) -> None:
        """封面一列「標籤：值」：值在 x 241 逐行，標籤（可拆兩行）放在值的中間。"""
        lines = []
        for v in values:
            v = self.w.edit(v)
            cur = ""
            for tok in _tokens(v):
                if _width(cur + tok, 10) > 320 and cur.strip():
                    lines.append(cur.strip())
                    cur = "" if tok.isspace() else tok
                else:
                    cur += tok
            if cur.strip():
                lines.append(cur.strip())
        h = 12 * max(len(lines), len(label)) + 6
        self.w.need(h)
        top = self.w.y
        mid = top + 12 * (len(lines) - 1) / 2
        for k, t in enumerate(label):
            self.w.put(41, mid + 12 * (k - (len(label) - 1) / 2), t)
        with self.w.verbatim():
            for k, t in enumerate(lines):
                self.w.put(241, top + 12 * k, t)
        self.w.y = top + h

    def centered_row(self, cells: Sequence[tuple[float, str]], gap: float, size: float = 7) -> None:
        """一列表格，每格置中於欄中心 x。"""
        self.w.row([(cx - _width(t, size) / 2, t) for cx, t in cells], gap=gap, size=size)

    def furnish(self, s: Spec) -> fitz.Document:
        """每頁頁底：英文名稱兩行、頁碼「第N頁/共計M頁」、分類標記（真實文件的頁尾，擷取時排除）。"""
        doc = self.w.doc
        en = s.name_en_inner
        cut = en.index("(Non Principal") + len("(Non Principal")
        for p, page in enumerate(doc):
            page.insert_text((36, 796), en[:cut], fontname=FONT, fontsize=8)  # en dash 要用 CJK 字型才畫得出來
            page.insert_text((36, 808), en[cut:].strip(), fontname=FONT, fontsize=8)
            page.insert_text((486, 820), f"第{p + 1}頁/共計{len(doc)}頁", fontname=FONT, fontsize=8)
            page.insert_text((20, 832), "C2 - Inter nal Natixis", fontname="helv", fontsize=3)
        return doc


def _warnings(s: Spec) -> list[str]:
    """封面警語 (1)～(11) 與第三章 5.(1)～(11)：(1) 為固定警語。"""
    d = s.distributor
    return [
        WARNING,
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息。",
        f"本商品雖經{d}審查，並不代表證實申請事項或保證該境外結構型商品之價值。",
        f"本商品持有期間如有保證配息收益，係由發行機構({ISSUER})保證，而非由{d}({DIST['name']})所保證，且本商品並無保證機構。",
        f"本中文產品說明書之內容如有虛偽或隱匿之情事者，係由{d}({DIST['name']})負責外，其餘內容應由發行人(虛構台北分行)依法負責。",
        "本商品的條款和條件之準據法為英國法。本商品係依境外結構型商品管理規則規定，於中華民國境內受託買賣之投資標的。",
        f"本商品透過{d}而提供予專業投資人及OSU客戶。",
        "投資人未清楚瞭解本中文產品說明書、受託契約條款及所有銷售文件內容前，請勿於相關文件簽名或蓋章。",
        f"{d}應提供投資人相關契約審閱期間。",
        "本商品僅銷售予專業投資人及OSU 客戶。",
    ]


# ---------------------------------------------------------------- 說明書


def _cover(d: _Doc, s: Spec) -> None:
    w = d.w
    w.line(252, "中文產品說明書", gap=18)
    w.line(200, "(適用於專業投資人及OSU 客戶)", gap=18)
    d.section = "cover.title"
    d.text(70, s.name_zh, 210, gap=18)
    d.section = "cover"
    code = (
        f"TDCC 商品代號為 {s.product_code} ，受託機構商品代號為 ，ISIN 為 {ISIN}"
        if s.old
        else f"TDCC 商品代號為 {s.product_code}，ISIN 為 {ISIN}"
    )
    w.line(150, code, gap=24)
    dl = s.distributor
    for label, values in (
        (["商品中文名稱："], [f"{s.name_zh}(下稱「本商品」)"]),
        (["商品英文名稱："], [s.name_en]),
        (["商品種類："], [PRODUCT_TYPE]),
        (["發行機構名稱："], [ISSUER, "(長期債務信用評等虛構評等)(截至本中文產品說明書刊印日期)"]),
        (["發行機構註冊地："], ["法國"]),
        (["發行機構地址及電話："], ["電話：+33 1 00 00 00 00", "地址：1 Fictional Avenue, Paris, France"]),
        (["商品註冊地："], ["專業投資人及OSU 客戶不適用"]),
        (["計價幣別："], [f"{s.currency_zh} ({s.ccy})"]),
        (["支付方式："], ["實物交割或現金交割"]),
        (["發行人之名稱、電話及地址："], ["發行人：虛構台北分行", "電話：(02) 0000 0000", "地址：台北市虛構路1 號"]),
        (["保證機構之名稱及地址："], ["無保證機構"]),
        ([f"{dl}之名稱、電話及地", "址："], [f"{dl}：{DIST['name']}", f"電話： {PHONE}", f"地址：{ADDRESS}"]),
        ([f"{dl}審查通過之日期及", "文號："], [s.date(s.approval)]),
    ):
        d.labeled(label, values)
    w.line(36, "本商品之投資風險警語：", gap=16)
    d.section = "warn"
    for k, t in enumerate(_warnings(s), 1):
        d.text(72, f"({k}) {t}", 72)
    d.section = "cover"
    d.text(36, "投資人應詳閱本中文產品說明書內容，並應注意本商品之風險事項，(請參閱第三章之說明)")
    d.text(36, f"中文產品說明書刊印日期：{s.date(s.trade_date)}")


def _heads(s: Spec, *, codes: bool) -> list[tuple[float, list[str]]]:
    """表頭各欄（欄中心、拆行後的文字）；欄序 KO → KI → K。`codes` 為連結標的表（多彭博代碼、本交易所欄）。"""
    cols = [(60, ["i"]), (102, ["連結", "標的", "資產/", "發行", "公司(i)"])] if codes else [(80, ["連結標的(i)"])]
    if codes:
        cols += [(146, ["彭博", "代碼"]), (190, ["本交", "易所"])]
    cols += [(235, ["期初進", "場價格"]), (330, ["自動提前到期贖回價格(期", "初進場價格", f"之{s.ko}%)"])]
    if s.ki != "none":
        cols.append((425, ["界限價", "格 (期", "初進場", "價格之", f"{s.ki_pct}%", ")"]))
    cols += [(487, ["執行價格", "(期初進場", "價格", f"之{s.strike}%)"]), (545, ["貨幣"])]
    return cols


def _table(d: _Doc, s: Spec, *, codes: bool) -> None:
    """連結標的表（`codes`）或情境價格表：表頭每欄拆成多行置中；每檔一列，欄序 KO → KI → K。"""
    cols = _heads(s, codes=codes)
    d.w.need(90 + 40 * s.count)
    rows = max(len(t) for _, t in cols)
    for k in range(rows):
        d.centered_row([(cx, t[k]) for cx, t in cols if k < len(t)], gap=11)
    d.w.space(8)
    x = {k: cx for k, (cx, _) in zip(range(len(cols)), cols, strict=True)}
    xs = [cx for cx, _ in cols]
    num_x = xs[-4:-1] if s.ki != "none" else xs[-3:-1]
    for n, u in enumerate(s.underlyings, 1):
        prices = (
            [price(u.initial, s.ko)]
            + ([price(u.initial, s.ki_pct)] if s.ki != "none" else [])
            + [price(u.initial, s.strike)]
        )
        texts = [f"{p:,.4f}" for p in prices]
        name, en = u.name.split("(")
        first: list[tuple[float, str]] = []
        second: list[tuple[float, str]] = []
        if codes:
            sym, exch = u.ticker.split()
            first += [(x[0], str(n)), (x[1], name), (x[2], sym), (x[3], u.exchange[:3])]
            second += [(x[1], f"({en}"), (x[2], exch), (x[3], u.exchange[3:])]
            init_x = x[4]
        else:
            first += [(x[0], name)]
            init_x = x[1]
        first.append((init_x, s.initial_text(u)))
        for k, (cx, t) in enumerate(zip(num_x, texts, strict=True)):
            if s.split_numbers and codes and k > 0:
                first.append((cx, t[:-2]))
                second.append((cx, t[-2:]))
            else:
                first.append((cx, t))
        first.append((xs[-1], QUOTE[s.market]))
        d.centered_row(first, gap=11, size=8)
        d.centered_row(second, gap=16, size=8)


def _article12(d: _Doc, s: Spec) -> None:
    d.text(90, "(1) 商品年期：" + f"{s.tenor} 個月(惟受第一章第14、18 項及第三章第1(9)項所述事件之限制)", 117)
    d.text(90, f"(2) 交易日/期初進場日：{s.date(s.trade_date)}，並依「中斷日」進行調整。", 117)
    d.text(90, f"(3) 發行日：{s.date(s.issue_date)}", 117)
    d.text(90, f"(4) 到期日：最終評價日後的第三個營業日，即{s.date(s.maturity_date)}，並依營業日慣例調整", 117)
    d.text(
        90, "(5) 次級市場投資人提前贖回日：於正常之市場狀況下，自發行日次一營業日起受理投資人申請提前贖回本商品。", 117
    )
    d.text(36, "25 「被替代對價」指受影響股票或存託憑證原股之其他對價。", 50, size=8)
    if s.ko_obs == "D":
        d.text(90, "(6) 配息觀察期間：表示任一配息觀察期間(t)，請詳閱下表。", 117)
        d.text(90, "(7) 配息日：如未發生自動提前到期事件，配息日如下表所列，並依營業日慣例調整。", 117)
        d.section = "art12.table"
        d.w.row(
            [(60, "配息觀察期間(t)"), (160, "配息觀察期間開始日"), (290, "配息觀察期間結束日"), (420, "配息日")], gap=14
        )
        for j in range(s.tenor):
            d.w.row(
                [(80, str(j + 1)), (160, s.date(s.starts[j])), (290, s.date(s.ends[j])), (420, s.date(s.payments[j]))],
                gap=13,
            )
        d.section = "art12"
        d.text(
            90,
            f"(8) 自動提前到期觀察日：從第{CN[s.first_callable - 1]}個配息觀察期間結束日(含)到最終評價日(不含)之每一預定交易日每日觀察，並依「中斷日」進行調整。",
            117,
        )
        d.text(
            90,
            "(9) 自動提前到期日：若「自動提前到期事件」於任一自動提前到期觀察日發生，則為該觀察日後第三個營業日。",
            117,
        )
        n = 10
    else:
        d.text(90, "(6) 配息日(含到期日)：如下表所列，並依營業日慣例調整。", 117)
        d.section = "art12.table"
        d.w.row([(100, "(t)"), (200, "配息日")], gap=14)
        for j in range(s.tenor):
            d.w.row([(100, str(j + 1)), (200, s.date(s.payments[j]))], gap=13)
        d.section = "art12"
        d.text(90, "(7) 自動提前到期觀察日/自動提前到期日：", 117)
        d.text(117, "(a) 自動提前到期觀察日：如下表所列，並依「中斷日」進行調整。")
        d.text(117, "(b) 自動提前到期日：如下表所列，並依營業日慣例調整。")
        d.section = "art12.table.ko"
        d.w.row([(100, "(t)"), (200, "自動提前到期觀察日(t)"), (340, "自動提前到期日(t)")], gap=14)
        for t, j in enumerate(range(s.first_callable - 1, s.tenor), 1):
            d.w.row([(100, str(t)), (200, s.date(s.ends[j])), (340, s.date(s.payments[j]))], gap=13)
        d.section = "art12"
        n = 8
    d.text(90, f"({n}) 預定交易日：關於任一連結標的，本交易所預定開放常規性交易的日期。", 117)
    n += 1
    rule = {
        "AM": "最終評價日",
        "D": "觸及事件判定期間之各預定交易日",
        "P": "觸及事件判定期間之各配息觀察期間結束日",
    }.get(s.ki)
    if rule:
        d.text(90, f"({n}) 觸及事件判定日：{rule}，並依「中斷日」進行調整。", 117)
        n += 1
    d.text(90, f"({n}) 最終評價日：{s.date(s.final_date)}，並依「中斷日」進行調整。", 117)


def _article15(d: _Doc, s: Spec) -> None:
    d.text(72, "(1) 若自動提前到期事件未發生，則持有至到期日贖回之公式如下：")
    d.text(
        72,
        "情況一：現金交割：如績效表現最差之連結標的於最終評價日的最終價格高於或等於其執行價格，則以每單位商品面額之100%贖回。",
    )
    if s.ki == "none":
        d.text(
            72,
            "情況二：如績效表現最差之連結標的於最終評價日的最終價格低於其執行價格，則於到期日發行機構將依下列所述情況"
            "贖回本商品：實物交割(或稱實物交付)：將以實物交付「可交付連結標的整數」。",
        )
    else:
        d.text(
            72,
            "情況二：如績效表現最差之連結標的於最終評價日的最終價格低於其執行價格，則依下列2.a 或2.b 所述情況贖回本商品：",
        )
        d.text(72, "2.a：現金交割：若未發生觸及事件，則本商品將以每單位商品面額之100%贖回。")
        d.text(72, "2.b：實物交割(或稱實物交付)：若曾經發生觸及事件，將以實物交付「可交付連結標的整數」。")
    d.text(
        72,
        f"「執行價格」係指就任一連結標的而言，各該連結標的之期初進場價格之{s.strike}%，惟可能因第一章第11 項之規定而調整。",
    )
    if s.ki != "none":
        d.text(
            72,
            "「觸及事件」係指計算代理機構判定任一連結標的於觸及事件判定期間之任一觸及事件判定日之收盤價曾經低於其界限價格。",
        )
        period = "最終評價日。" if s.ki == "AM" else "期初進場日(含)起至最終評價日(含)止之期間"
        d.text(72, f"「觸及事件判定期間」係指{period}")
        day = "各配息觀察期間結束日" if s.ki == "P" else "各預定交易日"
        d.text(72, f"「觸及事件判定日」係指於觸及事件判定期間之{day}，並依「中斷日」進行調整。")
        d.text(
            72,
            f"「界限價格」係指就任一連結標的而言，各該連結標的之期初進場價格之{s.ki_pct}%，惟可能因第一章第11 項之規定而調整。",
        )


def _article16(d: _Doc, s: Spec) -> None:
    ko = (
        f"從第{CN[s.first_callable - 1]}個配息觀察期間結束日(含)到最終評價日(不含)之每一預定交易日每日觀察，並依「中斷日」進行調整"
        if s.ko_obs == "D"
        else "依第一章第12(7)項所列，並依「中斷日」進行調整"
    )
    for t in (
        "(1) 投資收益計算方法：請參閱第一章第13 項之說明。",
        "(3) 情境分析解說可能獲利、損失及其他狀況之年化平均報酬率：",
        "假設：",
        f"a. 每單位商品面額 = {s.money(s.denom)}",
        "b. 投資標的單位數 = 1 單位",
        f"c. 天期：{s.tenor} 個月",
        f"d. 配息觀察期間：每月(共{s.tenor} 個配息觀察期間)",
        f"e. 自動提前到期觀察日：{ko}",
        f"f. 年利率：每年{s.annual}%",
        "h. 投資人並未於次級市場申請提前贖回本商品",
    ):
        d.text(108, t, 121)
    d.section = "art16.table"
    _table(d, s, codes=False)
    d.section = "art16"
    d.text(36, "情境一 (較佳情況)：")
    coupon = (Decimal(s.denom) * s.annual / 100 / 12).quantize(Decimal("0.01"), ROUND_HALF_UP)
    for t in range(1, s.first_callable + 1):
        event = f"於第{CN[s.first_callable - 1]}個配息觀察期間結束日發生" if t == s.first_callable else "未發生"
        d.w.row(
            [
                (72, str(t)),
                (140, f"{s.money(s.denom)} x ({s.annual}%/12) = {coupon:,.2f} {s.currency_zh}"),
                (380, event),
            ],
            gap=13,
        )
    d.text(
        36,
        f"• 在較佳情況下，自動提前到期事件於第{CN[s.first_callable - 1]}個配息觀察期間結束日發生，自動提前到期贖回金額為原始投資本金乘以100%。",
    )
    d.text(36, "情境二 (一般情況)：")
    d.text(36, "• 在一般情況下，於商品存續期間從未發生自動提前到期事件。")


ARTICLE_TITLES = {
    1: "商品名稱：",
    2: "商品風險程度：",
    3: "發行機構/保證機構名稱及其長期債務信用評等：",
    5: "計價幣別、商品面額及發行價格：",
    9: "連結標的資產、相對權重與投資績效之關聯情形：",
    12: "本商品年期、交易日、期初進場日、發行日、到期日、配息日、最終評價日及其他期日(依本商品性質決定之)：",
    13: "配息資料及其計算公式：",
    14: "自動提前到期事件之贖回計算公式：",
    15: "到期日贖回之計算公式，包含最低保證配息及參與率：",
    16: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
}  # 第一章各條標題；沒列的條是「第n項說明（不核對）」


def article_title(n: int) -> str:
    return ARTICLE_TITLES.get(n, f"第{n}項說明（不核對）")


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 Natixis 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    _cover(d, s)

    d.chapter("一", "商品基本資料")
    d.text(36, "本商品係依據基本公開說明書以英國法下之歐洲中期債券之全球無記名債券形式，以面額100%發行。")
    for n in range(1, 27):
        d.article(n, article_title(n))
        if n == 1:
            d.text(72, f"{s.name_zh}(下稱「本商品」)")
            d.text(72, s.name_en_inner)
        elif n == 2:
            d.text(72, WARNING)
        elif n == 3:
            d.text(72, f"發行機構：{ISSUER}")
            d.text(72, "發行機構長期債務信用評等(截至本中文產品說明書刊印日期)：虛構評等。")
            d.text(72, "保證機構名稱及其長期債務信用評等：無保證機構")
        elif n == 5:
            d.text(90, f"(1) 計價幣別：{s.currency_zh} ({s.ccy})")
            d.text(90, f"(2) 每單位商品面額：{s.money(s.denom)}")
            d.text(90, "(3) 發行價格：100%")
        elif n == 9:
            d.text(72, "「投資組合」於本商品發行日將由下表所列連結標的所組成，惟於發生第一章第11 項之情形")
            d.text(72, "時可能會變更及調整；各連結標的之相關敘述列於第一章第10 項：")
            d.section = "art9.table"
            _table(d, s, codes=True)
            d.section = "art9"
            d.text(72, "相對權重：不適用")
        elif n == 12:
            _article12(d, s)
        elif n == 13:
            d.text(72, f"(1) 月配息率：{s.monthly}% (即年利率{s.annual}%)")
            d.text(72, "(2) 配息金額：配息金額 = 配息觀察期間結束日持有之商品單位數x 每單位商品面額 x 月配息率")
        elif n == 14:
            word = "皆曾經 高於或等於" if s.memory else "皆高於或等於"
            d.text(
                72,
                f"若所有連結標的在任一自動提前到期觀察日(含)之收盤價{word}其自動提前到期贖回價格，則「自動提前到期事件」視同發生。",
            )
            d.text(
                72, f"「自動提前到期贖回價格」，係指就任一連結標的而言，自動提前到期贖回價格為期初進場價格之{s.ko}%。"
            )
        elif n == 15:
            _article15(d, s)
        elif n == 16:
            _article16(d, s)

    d.chapter("二", "相關機構事業概況")
    d.w.line(36, "1.", gap=0)
    d.w.line(58, "發行機構：", gap=12)
    for t in (
        f"(1) 事業名稱：{ISSUER}",
        "(2) 設立日期：2006 年11 月17 日",
        "(3) 營業所在地：1 Fictional Avenue, Paris",
    ):
        d.text(63, t, 90)
    d.text(36, "2. 保證機構：無保證機構")
    d.text(36, f"3. 發行人、計算代理機構、{s.distributor}、保管機構及其他相關機構：")
    for t in (
        "3.1 發行人：",
        "• 事業名稱：虛構台北分行",
        "• 負責人姓名：虛構負責人",
        f"3.5 {s.distributor}：",
        f"• 事業名稱：{DIST['name']}",
        "• 設立日期：2000 年11 月20 日",
        f"• 營業所在地：{ADDRESS}",
        f"• 負責人姓名：{DIST['chairman']}",
        "3.6 保管機構：不適用",
    ):
        d.text(54, t, 63)
    d.text(36, "4. 交易架構說明：不核對。")

    d.chapter("三", "商品風險揭露")
    for n, title in (
        (1, "基本風險資訊："),
        (2, "個別商品風險資訊："),
        (3, "信用連結型商品：不適用。"),
        (4, "管理規則說明事項："),
        (5, "本商品之投資風險警語："),
    ):
        d.text(36, f"{n}. {title}", 54)
        if n == 5:
            for k, t in enumerate(_warnings(s), 1):
                d.text(54, f"({k}) {t}", 72)

    d.chapter("四", "一般交易事項")
    d.text(36, "1. 商品開始受理申購、開始受理贖回日期及後續受理贖回日期：")
    for t in (
        f"• 開始受理申購日期：{s.date(s.trade_date)}",
        f"• 結束受理申購日期：{s.date(s.trade_date)}",
        f"• 開始受理贖回日期：自發行日次一營業日起，向{s.distributor}申請贖回。",
    ):
        d.text(63, t, 64)
    d.text(36, "2. 投資人應負擔的各項費用及金額或計算基準之表列：")
    for cells in (
        [(54, "費用項目"), (158, "費率 (百分比)"), (285, "收取時點"), (362, "收取方式"), (506, "收取人")],
        [(29, "申購費用"), (137, "申購價金的 0%~5%"), (268, "投資人向受託")],
        [(29, "提前贖回費用"), (137, "投資人提前贖回價金的"), (268, "投資人向受託")],
        [(137, "0%~5%"), (268, "或銷售機構")],
        [(29, "管理費用（信託管"), (137, "無"), (268, "不適用")],
        [(29, "分銷費用（如屬發"), (137, "申購價金的 0%~5%"), (268, "於本商品發行")],
        [(29, "保費費用"), (137, "不適用"), (268, "不適用")],
        [(29, "解約費用"), (137, "不適用"), (268, "不適用")],
        [(29, "其他費用"), (137, "無"), (268, "不適用")],
    ):
        d.w.row(cells, gap=15, size=9)
    m = s.money(s.denom)
    d.text(36, "3. 商品交易架構：請參閱第二章第4 項。")
    d.text(
        36,
        f"4. 最低申購金額或單位數：本商品最低申購金額為1 單位商品面額，即{m}；並以1 單位商品面額，即{m}為累加申購金額。",
        54,
    )
    d.text(36, "5. 申購價金之計算：申購價金＝投資人申購單位數 x 每單位商品面額 x 發行價格(100%) + 申購費用。", 54)
    d.text(36, "6. 申購手續及資金給付方式：不核對。")
    d.text(36, "7. 發行不成立之情形：發行機構於交易截止日或之前收到之本商品總申購金額至少須達(等值)二十萬美元。", 54)
    d.text(
        36,
        f"8. 最低贖回金額或單位數：本商品最低贖回金額為1 單位商品面額，即{m}；並以1 單位商品面額，即{m}為累加贖回金額。",
        54,
    )
    for n in range(9, 18):
        d.text(36, f"{n}. 第{n}項說明（不核對）")

    d.chapter("五", "特別記載事項")
    d.text(36, "1. 本商品於任何時候皆不得直接或間接於美國境內募集或銷售。")
    doc = d.furnish(s)
    d.w.flush()
    doc.save(path)
    doc.close()
    require_used(edits, used)
    return path


# ---------------------------------------------------------------- 投資人須知


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """Natixis 中文投資人須知（docs/templates/natixis-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis", width=585)
    w = d.w
    dt_ = s.date_iis
    dl = s.distributor
    w.line(120, f"中文投資人須知 (適用於專業投資人及OSU客戶)           刊印日期：{dt_(s.trade_date)}", gap=12)
    d.text(11, f"{s.name_zh} (下稱「本商品」){s.name_en_inner}", 14, gap=12)
    code = (
        f"商品種類：{PRODUCT_TYPE}，TDCC商品代號為{s.product_code} ，受託機構商品代號為，ISIN為 {ISIN}"
        if s.old
        else f"商品種類：{PRODUCT_TYPE}，TDCC商品代號為{s.product_code}，ISIN為 {ISIN}"
    )
    w.line(86, code, gap=12)
    w.line(9, "本商品之投資風險警語", gap=12)
    d.section = "warn"
    for k, t in enumerate(_warnings(s), 1):
        d.text(9, f"({k}) {t.replace('本中文產品說明書', '本中文投資人須知')}", 36, gap=12)
    d.section = "org"
    w.line(9, "壹、相關機構", gap=12)
    for t in (
        f"1. 發行機構：{ISSUER} (地址：1 Fictional Avenue, Paris)",
        "2. 發行人：虛構台北分行(地址：台北市虛構路1號)",
        "3. 保證機構：無保證機構",
        f"4. {dl}：{DIST['name']}(地址：{ADDRESS})",
    ):
        d.text(9, t, 36, gap=12)
    d.section = "summary"
    w.line(9, "貳、商品事項", gap=12)
    w.line(9, "1. 商品簡介", gap=12)
    m = f"{s.denom:,} {s.currency_zh}"
    ko = (
        f"從第{CN[s.first_callable - 1]}個配息觀察期間結束日(含)到最終評價日(不含)之每一預定交易日每日觀察，並依「中斷日」進行調整。"
        if s.ko_obs == "D"
        else "如中文產品說明書第一章第12(7)項所列，並依「中斷日」進行調整。"
    )
    lines = [
        "• 受託或銷售對象：專業投資人及OSU客戶",
        f"• {WARNING}",
        f"• 計價幣別：{s.currency_zh}({s.ccy}) ●每單位商品面額：{m}●最低申購金額：1單位商品面額，即{s.denom:,}{s.currency_zh}",
        "• 發行價格：100%",
        "• 連結標的資產：" + "、".join(u.name for u in s.underlyings) + "。●相對權重：不適用。",
        f"• 商品年期：{s.tenor}個月(惟受中文產品說明書所述事件之限制)",
        f"• 交易日/期初進場日：{dt_(s.trade_date)}，並依「中斷日」進行調整●發行日：{dt_(s.issue_date)}●到期日：最終評價日後的第三個營業日，即{dt_(s.maturity_date)}，並依營業日慣例調整",
        f"• 自動提前到期觀察日：{ko}",
    ]
    if s.ki in ("AM", "D"):
        rule = "最終評價日" if s.ki == "AM" else "觸及事件判定期間之各預定交易日"
        lines.append(f"觸及事件判定日：{rule} ，並依「中斷日」進行調整")
    lines += [
        f"• 最終評價日：{dt_(s.final_date)}，並依「中斷日」進行調整",
        "• 開始受理贖回日期：自發行日次一營業日起。",
    ]
    for t in lines:
        d.text(9, t, 9, gap=12)
    d.section = "coupon"
    w.line(9, "2. 收益分配事項及贖回價金之計算", gap=12)
    word = "皆曾經高於或等於" if s.memory else "皆高於或等於"
    for t in (
        f"配息：月配息率：{s.monthly}% (即年利率{s.annual}%)",
        f"自動提前到期事件：若所有連結標的在任一自動提前到期觀察日(含)之收盤價{word}其自動提前到期贖回價格，則「自動提前到期事件」視同發生。"
        f"「自動提前到期贖回價格」，係指就任一連結標的而言，自動提前到期贖回價格為期初進場價格之{s.ko}%。",
        f"「執行價格」係指就任一連結標的而言，各該連結標的之期初進場價格之{s.strike}%",
    ):
        d.text(9, t, 9, gap=12)
    if s.ki == "none":
        d.text(
            9,
            "情況二：如績效表現最差之連結標的於最終評價日的最終價格低於其執行價格，則於到期日發行機構將依下列所述情況"
            "贖回本商品：實物交割(或稱實物交付)：將以實物交付「可交付連結標的整數」。",
            9,
            gap=12,
        )
    else:
        d.text(
            9,
            "情況二：如績效表現最差之連結標的於最終評價日的最終價格低於其執行價格，則於到期日發行機構將依下列2.a或2.b"
            "所述情況贖回本商品：2.a：現金交割：若未發生觸及事件，則本商品將以每單位商品面額之100%贖回。",
            9,
            gap=12,
        )
        period = "最終評價日。" if s.ki == "AM" else "期初進場日(含)起至最終評價日(含)止之期間。"
        text = f"「觸及事件」係指任一連結標的之收盤價曾經低於其界限價格。「觸及事件判定期間」係指{period}"
        if s.ki == "P":
            text += "「觸及事件判定日」係指於觸及事件判定期間之各配息觀察期間結束日，並依「中斷日」進行調整。"
        text += f"「界限價格」係指就任一連結標的而言，各該連結標的之期初進場價格之{s.ki_pct}%"
        d.text(9, text, 9, gap=12)
    w.line(9, "3. 發行不成立之處理", gap=12)
    d.text(9, "• 發行不成立之情形：總申購金額至少須達(等值) 二十萬美元。", 9, gap=12)
    d.section = "rest"
    w.line(9, "叁、投資風險(以下風險之詳細解說，請參閱中文產品說明書第三章「商品風險揭露」。)", gap=12)
    d.text(9, "• 最低收益風險：於最壞之情況，投資人有可能損失所有原始投資本金。", 9, gap=12)
    d.section = "fees"
    w.line(9, "肆、投資人應負擔的各項費用", gap=12)
    for cells in (
        [(20, "費用項目"), (110, "費率 (百分比)"), (200, "收取時點"), (330, "收取方式"), (500, "收取人")],
        [(9, "申購費用"), (95, "申購價金的"), (190, "投資人向受")],
        [(95, "0%~5%"), (190, "託或銷售機")],
        [(9, "提前贖回費用"), (95, "投資人提前贖"), (190, "投資人向受")],
        [(95, "回價金的0%~5%")],
        [(9, "管理費用（信託管理費或"), (95, "無"), (190, "不適用")],
        [(9, "分銷費用（如屬發行機構"), (95, "申購價金的"), (190, "於本商品發")],
        [(95, "0%~5%")],
        [(9, "保費費用"), (95, "不適用"), (190, "不適用")],
        [(9, "其他費用"), (95, "無"), (190, "不適用")],
    ):
        d.w.row(cells, gap=11, size=8)
    d.section = "rest"
    for t in (
        "伍、相關機構之權利、義務及責任",
        "陸、協助投資人權益保護之方式",
        f"柒、發行機構、發行人及{dl}與投資人爭議之處理方式",
    ):
        w.line(9, t, gap=12)
        d.text(9, "1. 不核對。", gap=12)
    doc = w.doc
    while len(doc) < s.iis_pages:
        w.new_page()
        doc = w.doc
    while len(doc) > s.iis_pages:
        doc.delete_page(len(doc) - 1)
    for n, page in enumerate(doc, 1):
        page.insert_text((252, 835), f"第{n}頁/共{s.iis_pages}頁", fontname=FONT, fontsize=8)
        page.insert_text((20, 830), "C1 - Public Natixis", fontname="helv", fontsize=3)
    return w.save(path)


# ---------------------------------------------------------------- 參考條件表與核對


def row(spec: Spec, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列：期初定價依 `pricing`（VWAP 時進場價空白）。`overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(spec)
    out["期初定價"] = PRICING[spec.pricing]
    if spec.pricing == "vwap":
        out.update({f"UL_{i}_進場價": None for i in range(1, spec.count + 1)})
    out.update(overrides)
    return out


def check(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋與 `spec` 一致的參考條件表一列，經批量入口核對，回傳說明書的 CheckReport。"""
    return check_item(tmp_path, spec, pdf_spec=pdf_spec, edits=edits, config=config, **overrides).report


def check_item(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """同 `check`，回傳說明書的批量核對項目（BatchItem，含回填決策）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf], config)
    return next(i for i in outcome.items if i.term_sheet == pdf)


def check_iis(tmp_path: Path, spec: Spec | None = None, *, edits: Sequence[Edit] = (), config=CONFIG, **overrides: Any):
    """同 `check_item`，回傳投資人須知的批量核對項目。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
    return next(i for i in outcome.items if i.term_sheet == iis_path(pdf))
