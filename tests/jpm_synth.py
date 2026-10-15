"""JPM 合成說明書（docs/templates/jpm-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：封面兩欄表（標籤 x 20、值 x 190）；章名「I. 商品基本資料」x 20；第 I 部分條號「N.」x 15
獨立一行、標題在下一行 x 35（10. 以後同一行）；子項「(n)」x 25；表格一格一行（價格下一行為幣別字）；每頁頁面底部頁碼「N/M」。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`、第 I 部分各條 `ts.art{n}`（標的表
`ts.art9.table`、日期表 `ts.art12.table`、假設價格表 `ts.art15.table`）、其他各部分 `ts.chII`～`ts.chV`。
投資人須知（docs/templates/jpm-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
段落代號 `iis.cover`、`iis.warn`（警語 1.～10.）、`iis.org`（一、相關機構）、`iis.summary`（商品簡介，含標的表）、
`iis.coupon`（收益分配事項）、`iis.redeem`（贖回價金之計算、不成立之處理）、`iis.fees`、`iis.rest`。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import fitz

from harness import CONFIG, STANDARD, check_all, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row, weekday_on_or_after

NAME = STANDARD["product_name"]["jpm"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["jpm"]
ISSUER = STANDARD["issuer_name"]["jpm"]
GUARANTOR = STANDARD["guarantor_name"]["jpm"]
AGENT = STANDARD["agent_name"]["jpm"]["cover"]
PRODUCT_TYPE = STANDARD["product_type"]["jpm"]
DIST = STANDARD["distributor"]
PHONE = "+886 2 5556 1313"  # 審查標準 phone_equivalents 的 JPM 封面寫法
APPROVAL = dt.date(2026, 6, 11)
REDEMPTION_START = "發行日後第一個預定交易日"
TICKERS = ("ZJA UW", "ZJB UN", "1234 JT", "ZJD UW", "ZJE UW")


def jdate(d: dt.date) -> str:
    """JPM 的日期寫法：月日補零、沒有空白（2030年01月07日）。"""
    return f"{d.year}年{d.month:02d}月{d.day:02d}日"


def underlyings(count: int) -> tuple[UL, ...]:
    """JPM 合成文件的標的：期初價 100n + 0.5（價格固定 4 位）。"""
    return tuple(
        UL(f"虛構標的{i}公司", "那斯達克證券交易所", TICKERS[i - 1], Decimal(100 * i) + Decimal("0.5"))
        for i in range(1, count + 1)
    )


@dataclass
class Spec(ProductSpec):
    """JPM 合成說明書的規格：商品規格加上 JPM 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count` 產生、最終比價日與到期日由日期表推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    `d4` 為每日觀察時日期表的排法：True 4 欄（評價日、配息支付日、觀察期間起始日／終止日，KO 評價日寫「評價日(t)」），
    False 3 欄（觀察期間起始日／終止日、配息支付日，寫「觀察期間終止日(t)」）。單一標的（`count=1`）沒有記憶條款。
    """

    issuer: str = "JPM"
    product_code: str = "076199990001"
    tenor: int = 6
    first_callable: int = 1
    annual: Decimal = Decimal("12.34")
    strike: Decimal = Decimal("70.00")
    ki_pct: Decimal = Decimal("60.00")
    ko: Decimal = Decimal("100.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    d4: bool = True  # 每日觀察時的日期表排法
    vwap: bool = False  # 參考條件表期初定價為 VWAP（說明書不寫期初定價方式）
    trustee_code: str | None = None  # 受託機構商品代號；None 同商品代號
    iis_pages: int = 4  # 投資人須知頁數（不足補空白頁、多的刪掉最後幾頁）

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count)
        if self.count == 1:
            self.memory = False
        self.final_date, self.maturity_date = self.valuations[-1], self.payments[-1]

    @property
    def valuations(self) -> list[dt.date]:
        """各期評價日（＝觀察期間終止日）：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=3)) for d in self.valuations]

    @property
    def tickers(self) -> list[str]:
        return [u.ticker for u in self.underlyings]

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh)

    @property
    def name_en(self) -> str:
        return NAME["en"].format(tenor=self.tenor, ccy=self.ccy)

    @property
    def worst(self) -> str:
        return "最差表現連結標的" if self.count > 1 else "連結標的"

    def money(self, x: Decimal | int) -> str:
        return f"{Decimal(x):,.0f}{self.currency_zh}"


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（數字不拆開）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts"):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def text(self, x: float, text: str, cont: float | None = None) -> None:
        text = self.w.edit(text)
        if not text:
            return
        width, cur, used, first = 555 - x, "", 0.0, True
        with self.w.verbatim():
            for ch in text:
                cw = fitz.get_text_length(ch, fontname="helv", fontsize=10) if ord(ch) < 256 else 10
                if used + cw > width and cur and not (cur[-1] in "0123456789,." and ch in "0123456789,."):
                    self.w.line(x if first else (cont if cont is not None else x), cur)
                    cur, used, first = "", 0.0, False
                cur += ch
                used += cw
            self.w.line(x if first else (cont if cont is not None else x), cur)

    def cell(self, x: float, text: str) -> None:
        self.w.line(x, text, gap=13)

    def bullet(self, text: str, x: float = 35) -> None:
        self.w.line(x, "●")
        self.text(x + 17, text)

    def chapter(self, roman: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{roman}"
        self.w.line(20, f"{roman}. {name}", gap=16)

    def article(self, n: int, title: str) -> None:
        """第 I 部分各條段落代號 art{n}；條號 x 15，9 以前獨立一行、標題下一行 x 35，10 以後同一行。"""
        if self.section.startswith("art") or self.section in ("cover", "chI"):
            self.section = f"art{n}"
        if n < 10:
            self.w.line(15, f"{n}.")
            self.text(35, title)
        else:
            self.text(15, f"{n}. {title}", 35)

    def pair(self, label: str, values: Sequence[str]) -> None:
        """封面兩欄表的一項：標籤 x 20、第一個值同一列 x 190，其餘值接在下面。"""
        self.w.need(13)
        with self.w.verbatim():
            self.w.put(20, self.w.y, self.w.edit(label))
        for v in values:
            self.text(190, v)


def _price_table(d: _Doc, s: Spec, *, iis: bool = False) -> None:
    """第 9 項標的表（投資人須知商品簡介同一張表，「自動提前出場價(i)」欄名拆成兩行）。"""
    cols: list[tuple[str, Decimal | None]] = [("期初價格(i)", None), ("執行價(i)", s.strike)]
    cols.append(("自動提前出場價(i)", s.ko))
    if s.ki != "none":
        cols.append(("觸碰價(i)", s.ki_pct))
    for t in ("i", "連結標的(i)(以下各稱「股票」)", "彭博代碼", "交易所"):
        d.cell(60, t)
    for label, _ in cols:
        if iis and label == "自動提前出場價(i)":
            d.cell(300, "自動提前")
            d.cell(300, "出場價(i)")
        else:
            d.cell(300, label)
    for i, u in enumerate(s.underlyings, 1):
        d.cell(48, str(i))
        d.cell(60, u.name)
        d.cell(150, u.ticker)
        d.cell(200, u.exchange)
        for _, pct in cols:
            value = u.initial if pct is None else price(u.initial, pct)
            d.cell(300, f"{value:,.4f}")
            d.cell(300, s.currency_zh)


def _payoff(d: _Doc, s: Spec, x: float, *, iis: bool = False) -> None:
    """第 8 項（投資人須知贖回價金之計算同句型）：自動提前出場與到期贖回；`iis` 的每日觀察一律寫「評價日(t)」。"""
    a, k, n, worst = s.annual, s.first_callable, s.tenor, s.worst
    d.bullet("於自動提前出場贖回金額付款日，發行機構將依下列計算公式以計價幣別給付：", x)
    if s.ko_obs == "D":
        d.text(x, "自動提前出場贖回金額：每單位面額 x 100% + 自動提前出場配息金額")
        d.text(x, f"自動提前出場配息金額：每單位面額 x {a:.2f}% x 1/12 x n/N")
    else:
        d.text(x, "自動提前出場贖回金額：每單位面額 x 100%")
    if s.count == 1:
        event = "連結標的於該自動提前出場評價日之股票收盤價大於或等於自動提前出場價"
    elif s.memory:
        event = (
            "所有連結標的(i)同時於該自動提前出場評價日或分別於不同自動提前出場評價日（包括之前自動提前出場評價日）"
            "之股票收盤價至少有一次大於或等於其自動提前出場價(i)"
        )
    else:
        event = "所有連結標的(i)於該自動提前出場評價日之股票收盤價大於或等於其自動提前出場價(i)"
    d.text(
        x, f"自動提前出場事件：如在任一自動提前出場評價日，{event}，則視為於該自動提前出場評價日發生自動提前出場事件。"
    )
    d.text(x, "自動提前出場日：發生自動提前出場事件之該自動提前出場評價日。")
    if s.ko_obs == "D":
        start = "評價日" if s.d4 or iis else "觀察期間終止日"
        when = f"於{start}(t)(t={k})(含)起至最終評價日(含)止之每一個預定交易日"
    else:
        when = f"於任一評價日(t)(t={k}-{n})" if k < n else f"於任一評價日(t)(t={k})"
    d.text(x, f"自動提前出場評價日：{when}，並應受中斷日效果條款之影響。")
    d.text(x, f"自動提前出場價：{s.ko:.2f}% x 期初價格")
    d.bullet("於到期日，發行機構將依下列計算公式以計價幣別給付：", x)
    d.text(x, "到期贖回金額：若未發生自動提前出場事件，到期日依下列方式贖回：")
    if s.ki == "none":
        d.text(x, f"(1) 若於最後評價日時{worst}之股票收盤價大於或等於其執行價：每單位面額 x 100%")
        d.text(x, f"(2) 若於最後評價日時{worst}之股票收盤價小於其執行價，則以實物交割之方式交付{worst}。")
    elif s.ki == "AM":
        d.text(x, "(1) 若觸碰事件未發生：每單位面額 x 100%")
        d.text(x, f"(2) 若觸碰事件已發生，則以實物交割之方式交付{worst}。")
    else:
        d.text(x, "(1) 若觸碰事件未發生：每單位面額 x 100%")
        d.text(x, f"(2) 若觸碰事件已發生，且於最後評價日時{worst}之股票收盤價大於或等於其執行價：每單位面額 x 100%")
        d.text(x, f"(3) 若觸碰事件已發生，且於最後評價日時{worst}之股票收盤價小於其執行價，則以實物交割之方式交付。")
    if s.ki != "none":
        watch = {"AM": "在最後評價日時", "D": "在觸碰事件評價日時"}[s.ki]
        d.text(x, f"觸碰事件：若計算代理機構判定，{watch}{worst}之股票收盤價低於觸碰價，則發生觸碰事件。")
        if s.ki == "D":
            d.text(x, "觸碰事件評價日：自交易日(含)起至最後評價日(含)止，對連結標的而言為預定交易日之日。")
    if s.count > 1:
        d.text(x, "最差表現連結標的：依計算代理機構計算得出績效表現最差之股票。")
    d.text(x, f"執行價：{s.strike:.2f}% x 期初價格")
    if s.ki != "none":
        d.text(x, f"觸碰價：{s.ki_pct:.2f}% x 期初價格")


def _dates(d: _Doc, s: Spec) -> None:
    d.text(25, f"(1) 商品期間：{s.tenor}個月")
    d.text(25, f"(2) 交易日/期初評價日：{jdate(s.trade_date)}")
    d.text(25, f"(3) 發行日：{jdate(s.issue_date)}，若該日非為相關結算系統之營業日，則發行日為次一營業日。")
    d.text(25, f"(4) 最後評價日：{jdate(s.final_date)}，若該日並非預定交易日，則為次一預定交易日。")
    d.text(25, "(5) 自動提前出場贖回金額付款日：自動提前出場日後第3個營業日。")
    d.text(25, f"(6) 到期日：{jdate(s.maturity_date)}，為最後評價日後第3個營業日。")
    d.section = "art12.table"
    k, vals, pays = s.first_callable, s.valuations, s.payments
    if s.ko_obs == "P":
        heads = ["評價日(t)", "配息支付日(t)"]
    elif s.d4:
        heads = ["評價日(t)", "配息支付日(t)", "觀察期間起始日(t)", "觀察期間終止日(t)"]
    else:
        heads = ["觀察期間起始日(t)", "觀察期間終止日(t)", "配息支付日(t)"]
    d.cell(50, "t")
    for h in heads:
        d.cell(100, h)
    for j in range(1, s.tenor + 1):
        d.cell(50, str(j))
        start = "N.A" if (j == 1 if s.d4 else j <= k) else jdate(vals[j - 2] + dt.timedelta(days=1))
        end = "N.A" if s.d4 and j == 1 else jdate(vals[j - 1])
        cells = {
            "評價日(t)": jdate(vals[j - 1]),
            "配息支付日(t)": jdate(pays[j - 1]),
            "觀察期間起始日(t)": start,
            "觀察期間終止日(t)": end,
        }
        for h in heads:
            d.cell(100, cells[h])
    d.section = "art12"


def _scenario(d: _Doc, s: Spec) -> None:
    d.text(25, "(3) 以情境分析解說最大可能獲利、損失及其他狀況之平均年化報酬率：")
    d.text(35, f"假設原計價幣別本金為{s.money(s.denom)}，若未發生第I部分第17項所定情形為前提：")
    d.section = "art15.table"
    heads = ["期初價格", "執行價", "自動提前出場價"] + (["觸碰價"] if s.ki != "none" else [])
    pcts = [None, s.strike, s.ko] + ([s.ki_pct] if s.ki != "none" else [])
    for h in heads:
        d.cell(150, h)
    for i, u in enumerate(s.underlyings, 1):
        d.cell(60, f"連結標的{i}")
        for pct in pcts:
            value = u.initial if pct is None else price(u.initial, pct)
            d.cell(150, f"{value:,.4f}{s.currency_zh}")
    d.section = "art15"
    d.text(35, "(i) 最佳情境：發生自動提前出場事件（情境內容為虛構說明，不核對）")


def _warnings(*, iis: bool = False) -> list[str]:
    """封面警語 1.～10.（第 III 部分 6. (1)～(10) 相同）：1. 固定警語；5. 保證機構簡稱與發行機構；6. 總代理人；
    7. 「受託投資/受託買賣」（允許片語）。投資人須知 6. 寫「本投資人須知」。"""
    short = GUARANTOR["ch1"]
    doc = "本投資人須知" if iis else "本產品說明書"
    return [
        WARNING,
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息/收益。",
        "本商品雖經受託機構審查，並不代表證實申請事項或保證該商品之價值，且受託機構不負本商品投資盈虧之責。",
        f"本商品持有期間如有保證配息收益或保證保本率，係由{short}保證，而非由受託機構所保證。{ISSUER}與{short}"
        "有財務會計準則公報第六號所定之關係人或實質關係人之情事。",
        f"{doc}之內容如有虛偽或隱匿之情事者，其餘內容應由{AGENT}及其負責人依法負責。",
        "本商品依境外結構型商品管理規則，於中華民國境內受託投資/受託買賣，投資人申購前應詳閱產品說明書。",
        "投資人未清楚瞭解產品說明書、受託契約條款及所有銷售文件內容前，請勿於相關文件簽名或蓋章。",
        "受託機構應提供專業投資人相關契約審閱期間，其審閱期間不得低於三日。",
        "本商品構成發行機構及保證機構無擔保之一般契約債務。",
    ]


def _fee_cells(d: _Doc) -> None:
    for t in (
        "費用項目",
        "費率（百分比）",
        "收取時點",
        "收取方式",
        "收取人",
        "申購費用",
        "申購價金的",
        "0%~5%",
        "投資人向受託或銷售機構申購時",
        "提前贖回費用",
        "投資人提前贖回",
        "價金的0%~5%",
        "投資人向受託或銷售機構提前贖回時",
        "管理費用（信託管理費或管銷費用）",
        "無",
        "不適用",
        "分銷費用（如屬發行機構或發行人給予受託或銷售機構之報酬、費用、折讓等各項利益應單獨列示）",
        "申購價金的",
        "0%~5%",
        "於本商品發行時一次給付",
        "保費費用",
        "不適用",
        "解約費用",
        "不適用",
        "其他費用",
        "無",
    ):
        d.cell(60, t)


def _page_marks(w: PdfWriter) -> None:
    total = len(w.doc)
    for n, page in enumerate(w.doc, 1):
        page.insert_text((290, 830), f"{n}/{total}", fontname=FONT, fontsize=10)


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """JPM 中文投資人須知（docs/templates/jpm-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis")
    w = d.w
    trustee = s.product_code if s.trustee_code is None else s.trustee_code
    w.line(250, "中文投資人須知", gap=14)
    d.text(70, s.name_zh)
    d.text(140, s.name_en)
    d.text(210, f"商品種類：{PRODUCT_TYPE}")
    d.text(70, f"TDCC商品代號{s.product_code}，受託機構商品代號{trustee}，ISIN為XS1999900002")
    w.line(15, "本商品之投資風險警語")
    d.text(380, f"投資人須知刊印日期：{jdate(s.trade_date)}")
    d.section = "warn"
    for n, t in enumerate(_warnings(iis=True), 1):
        d.text(15, f"{n}. {t}", 30)
    d.section = "org"
    w.line(15, "一、相關機構")
    d.text(15, f"1. 發行機構：{ISSUER}")
    d.text(30, "(地址：1 Fictional Street, Amsterdam)")
    d.text(15, f"2. 保證機構：{GUARANTOR['cover']}")
    d.text(30, "(地址：1 Fictional Street, Columbus)")
    d.text(15, f"3. 總代理人：{AGENT}(地址：臺北市信義區虛構路1號)")
    d.text(15, f"4. 受託機構：{DIST['name']}(地址：{DIST['address']}(營業活動所在地))")
    d.section = "summary"
    w.line(15, "二、境外結構型商品事項(詳細內容及名詞定義請見中文產品說明書)")
    w.line(15, "1. 商品簡介：")
    d.bullet("受託對象：專業投資人")
    d.bullet(f"計價幣別：{s.currency_zh}")
    d.bullet("原計價幣別本金保本率：不保本")
    d.bullet("連結標的資產：")
    _price_table(d, s, iis=True)
    d.bullet(
        f"商品年期：{s.tenor}個月；交易日/期初評價日：{jdate(s.trade_date)}；發行日：{jdate(s.issue_date)}，"
        f"若該日非為相關結算系統之營業日，則發行日為次一營業日；最後評價日：{jdate(s.final_date)}"
    )
    d.bullet(f"到期日：{jdate(s.maturity_date)}，為最後評價日後第3營業日。")
    d.bullet(f"開始受理贖回日期：{REDEMPTION_START}")
    d.section = "coupon"
    w.line(15, "2. 收益分配事項:(詳細說明及相關名詞定義請見中文產品說明書)")
    d.bullet("於配息支付日(t)，發行機構將依下列計算公式以計價幣別給付：")
    d.text(35, f"配息金額：每單位面額 x {s.annual:.2f}% x 1/12")
    d.section = "redeem"
    w.line(15, "3. 贖回價金之計算:(詳細說明及相關名詞定義請見中文產品說明書)")
    _payoff(d, s, 35, iis=True)
    d.text(
        15,
        "4. 受託或銷售不成立之處理:若境外結構型商品保證機構於交易日(含)前有實際信用評等未符合規定，則本商品將不成立。",
    )
    d.section = "rest"
    w.line(15, "三、投資風險")
    d.text(15, "1. 基本風險資訊：本商品不保本（以下為虛構說明，不核對）。")
    d.section = "fees"
    w.line(15, "四、費用")
    d.text(15, "本商品係特定金錢信託項/受託買賣下之投資標的，投資人同意下列相關費用：")
    _fee_cells(d)
    d.section = "rest"
    w.line(15, "五、相關機構之權利、義務及責任")
    d.text(15, "1. 投資人之申購及贖回，須經發行機構或其指定代理機構確認後始生效力。")
    w.line(15, "六、協助投資人權益之保護方式")
    w.line(15, "七、發行機構、總代理人及受託機構與投資人爭議之處理方式及受託機構與投資人爭議之處理方式")
    while len(w.doc) < s.iis_pages:
        w.new_page()
    while len(w.doc) > s.iis_pages:
        w.doc.delete_page(len(w.doc) - 1)
    _page_marks(w)
    return w.save(path)


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 JPM 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()  # 說明書與投資人須知共用：改字換到哪一份都算
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    w = d.w
    trustee = s.product_code if s.trustee_code is None else s.trustee_code
    w.line(250, "中文產品說明書", gap=16)
    d.pair("商品代號：", [f"TDCC商品代號為{s.product_code}、受託機構商品代號為{trustee}"])
    d.pair("國際證券編碼(ISIN)：", ["XS1999900002"])
    d.pair("商品中文名稱：", [f"{s.name_zh}(下稱「本商品」)"])
    d.pair("商品英文名稱：", [s.name_en])
    d.pair("商品種類：", [PRODUCT_TYPE])
    d.pair("發行機構註冊地：", [f"發行機構為{ISSUER}，依荷蘭法律設立登記之公司"])
    d.pair("計價幣別：", [s.currency_zh])
    d.pair("總代理人之名稱、電話及地址：", [f"{AGENT}、電話：(02) 0000-0000", "地址：臺北市信義區虛構路1號"])
    d.pair("保證機構之名稱及地址：", [GUARANTOR["cover"], "地址：1 Fictional Street, Columbus"])
    d.pair(
        "受託機構之名稱、電話及地址：",
        [f"{DIST['name']}、電話：{PHONE}", f"地址：{DIST['address']}(營業活動所在地)"],
    )
    d.pair("受託機構審查通過之日期及文號：", [jdate(APPROVAL)])
    d.pair("公會審查通過之日期及文號：", ["專業投資人不適用"])
    w.line(20, "商品之投資風險警語：")
    warnings = _warnings()
    for n, t in enumerate(warnings, 1):
        d.text(15, f"{n}. {t}", 30)
    d.text(15, "投資人應詳閱本產品說明書之內容，並應注意本商品之風險事項。")
    d.text(15, f"產品說明書刊印日期：{jdate(s.trade_date)}")

    d.chapter("I", "商品基本資料")
    titles = {
        1: f"商品名稱：{s.name_zh}",
        2: f"商品風險程度：{WARNING}",
        3: f"保證機構名稱及其長期債務信用評等：{GUARANTOR['ch1']}，長期債務信用評等為虛構",
        4: "商品之發行評等：專業投資人不適用",
        5: "發行價格：每單位面額之100%",
        8: "主要給付項目及其計算方式：",
        9: "連結標的資產、相對權重及與投資績效之關連情形：",
        12: "商品期間、發行日、到期日及其他依個別商品性質而定之日期",
        15: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之平均年化報酬率：",
    }
    for n in range(1, 27):
        d.article(n, titles.get(n, f"第{n}項說明（不核對）"))
        if n == 5:
            d.text(35, f"每單位面額：{s.money(s.denom)}")
            d.text(35, f"計價幣別：{s.currency_zh}")
        elif n == 8:
            d.bullet("於配息支付日(t)，發行機構將依下列計算公式以計價幣別給付：")
            d.text(35, f"配息金額：每單位面額 x {s.annual:.2f}% x 1/12")
            _payoff(d, s, 35)
        elif n == 9:
            d.section = "art9.table"
            _price_table(d, s)
            d.section = "art9"
            d.text(35, "相對權重：不適用。")
        elif n == 12:
            _dates(d, s)
        elif n == 15:
            _scenario(d, s)

    d.chapter("II", "相關機構事業概況")
    d.w.line(15, "1.")
    d.text(32, "發行機構")
    d.text(25, f"(1) 事業名稱：{ISSUER}")
    d.text(25, "(2) 設立日期：2006年11月6日")
    d.w.line(15, "2.")
    d.text(32, "保證機構：")
    d.text(25, "(1) 設立日期：1968年11月26日")
    d.text(25, f"(2) 事業名稱：{GUARANTOR['ch2']}")
    d.w.line(15, "3.")
    d.text(32, "總代理人、計算代理機構、受託機構：設立日期及簡介")
    for n, (title, name, place, head) in enumerate(
        (
            ("總代理人", AGENT, "臺北市信義區虛構路1號", "虛構甲"),
            ("計算代理機構", "Fictional Securities plc", "1 Fictional Street, London", "虛構乙"),
            ("受託機構", DIST["name"], f"{DIST['address']}(營業活動所在地)", DIST["chairman"]),
        ),
        1,
    ):
        d.text(25, f"({n}) {title}")
        for t in (f"事業名稱：{name}", "設立日期：2000年1月1日", f"營業所在地：{place}", f"負責人姓名：{head}"):
            d.bullet(t)
    d.w.line(15, "4.")
    d.text(32, "交易架構說明：不核對。")

    d.chapter("III", "商品風險揭露")
    for n in range(1, 6):
        d.w.line(15, f"{n}.")
        d.text(32, "風險說明（不核對）")
    d.text(15, "6. 商品之投資風險警語:", 35)
    for n, t in enumerate(warnings, 1):
        d.text(25, f"({n}) {t}", 40)

    d.chapter("IV", "一般交易事項")
    d.w.line(15, "1.")
    d.text(32, "商品開始受理申購、受理贖回日期及後續受理贖回日期")
    d.bullet(f"開始受理申購日期：{jdate(s.trade_date)}")
    d.bullet(f"開始受理贖回日期：{REDEMPTION_START}")
    d.w.line(15, "2.")
    d.text(32, "投資人應負擔的各項費用及金額或計算基準之表列")
    d.text(32, "本商品係特定金錢信託/受託買賣項下之投資標的，投資人同意下列相關費用：")
    _fee_cells(d)
    d.w.line(15, "3.")
    d.text(32, "商品交易架構：不核對。")
    d.w.line(15, "4.")
    d.text(32, "最低申購金額及最低加購金額")
    d.text(32, f"最低申購金額： {s.money(s.denom)}")
    d.text(32, f"最低加購金額： {s.money(s.denom)}")
    d.w.line(15, "5.")
    d.text(32, "申購價金之計算")
    d.text(32, "申購單位數 x 每單位面額 x 100%")
    d.w.line(15, "6.")
    d.text(32, "申購手續及資金給付方式：不核對。")
    d.w.line(15, "7.")
    d.text(32, "發行不成立之情形：若保證機構於交易日(含)前有實際信用評等未符合規定，則本商品將不成立。")
    d.text(15, f"8. 最低贖回金額或單位數： {s.money(s.denom)}(1單位面額)", 35)

    d.chapter("V", "特別記載事項")
    d.text(15, "1. 不核對。", 35)
    _page_marks(w)
    path = w.save(path)
    require_used(edits, used)
    return path


def row(spec: Spec, *, order: Sequence[int] | None = None, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列；`order`（從 1 起）改變表上 UL_n 的排序（JPM 表上常和說明書不同）。"""
    out = reference_row(spec)
    if spec.vwap:
        out["期初定價"] = "VWAP"
    if order:
        cols = ("", "_進場價", "_執行價", "_下限價", "_KO價")
        moved = {f"UL_{new}{c}": out[f"UL_{old}{c}"] for new, old in enumerate(order, 1) for c in cols}
        out.update(moved)
    out.update(overrides)  # 以 Excel 欄名覆寫（排序之後：UL_n 指表上第 n 檔）
    return out


def check_item(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    order: Sequence[int] | None = None,
    config=CONFIG,
    **overrides: Any,
):
    """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋與 `spec` 一致的參考條件表一列（`order` 改標的排序），
    經批量入口核對，回傳說明書的批量核對項目（BatchItem，含回填決策與 `partner` 投資人須知）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, order=order, **overrides)])
    outcome = check_all(sheet, [pdf], config)
    return next(i for i in outcome.items if i.term_sheet == pdf)


def check(tmp_path: Path, spec: Spec | None = None, **kw: Any):
    """同 `check_item`，回傳該說明書的 CheckReport。"""
    return check_item(tmp_path, spec, **kw).report
