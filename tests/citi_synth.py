"""Citi 合成說明書（docs/templates/citi-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：封面 `‧` 兩欄表（標籤 x 60、‧ x 260、值 x 273）；章名「一、商品基本資料」x 42；第一章條號「N.」
x 42、標題同一列 x 67（`legacy=True` 舊文件：「N. 標題」同一行、價格固定 4 位、受託機構商品代號 `[ ]`、分銷費用單列）；
子項「(n)」x 42、內容同一列 x 67；表格一格一行。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`、第一章各條 `ts.art{n}`（標的表
`ts.art9.table`、日期表 `ts.art12.table`、假設價格表 `ts.art15.table`）、其他各章 `ts.ch二`～`ts.ch五`。
投資人須知（docs/templates/citi-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
每頁頁首「第N頁，共M頁」；段落代號 `iis.cover`、`iis.warn`（警語 1.～13.）、`iis.org`（一、相關機構）、`iis.summary`
（商品簡介）、`iis.coupon`（收益分配事項）、`iis.redeem`（贖回價金之計算、不成立之情形）、`iis.fees`、`iis.rest`。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import fitz

from harness import CONFIG, STANDARD, check_all, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used, zh_date
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row, weekday_on_or_after

NAME = STANDARD["product_name"]["citi"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["citi"]
ISSUER = STANDARD["issuer_name"]["citi"]
GUARANTOR = STANDARD["guarantor_name"]["citi"]
AGENT = STANDARD["agent_name"]["citi"]
PRODUCT_TYPE = STANDARD["product_type"]["citi"]
MINIMUM_ISSUE = STANDARD["minimum_issue_size"]["citi"]
DIST = STANDARD["distributor"]
IIS_GUARANTOR = STANDARD["guarantor_name_equivalents"]["citi"][0]  # 投資人須知相關機構 2. 只寫中文
PHONE = "(+886)2-5556-1313"  # 審查標準 phone_equivalents 的 Citi 封面寫法
APPROVAL = dt.date(2026, 6, 11)
ZH_NUM = "一二三四五六七八九十"
Q4 = Decimal("0.0001")
TICKERS = ("ZCA UW", "ZCB UN", "1234 JT", "ZCD UW", "ZCE UW")


def zh_number(n: int) -> str:
    """1～99 的中文數字（一、十一、二十…）。"""
    tens, ones = divmod(n, 10)
    head = "" if tens == 0 else ("十" if tens == 1 else ZH_NUM[tens - 1] + "十")
    return head + (ZH_NUM[ones - 1] if ones else "")


def underlyings(count: int) -> tuple[UL, ...]:
    """Citi 合成文件的標的：期初價 100n + 0.5（價格表示範去尾零）。"""
    return tuple(
        UL(f"虛構標的{i}公司", "那斯達克交易所", TICKERS[i - 1], Decimal(100 * i) + Decimal("0.5"))
        for i in range(1, count + 1)
    )


@dataclass
class Spec(ProductSpec):
    """Citi 合成說明書的規格：商品規格加上 Citi 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count` 產生、最終比價日與到期日由日期表推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    `autocall=False` 為無自動提前出場（Non-Call = 天期、表上 KO(Freq)=P、KO(%)=100）。
    """

    issuer: str = "CITI"
    product_code: str = "159199990001"
    ki: str = "AM"  # none／AM／D／P
    tenor: int = 6
    first_callable: int = 1
    annual: Decimal = Decimal("9.88")
    strike: Decimal = Decimal("65.00")
    ki_pct: Decimal = Decimal("55.00")
    ko: Decimal = Decimal("95.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    autocall: bool = True  # False → 無自動提前出場
    vwap: bool = False  # 標的表表頭寫「期初評價日之VWAP」
    pricing_text: str | None = None  # 標的表表頭「期初評價日之X」的 X；None 依 vwap（VWAP／收盤價格）
    legacy: bool = False  # 舊文件版面（2024）
    trustee_code: str | None = None  # 受託或銷售機構商品代號；None 同商品代號，"" 為 `[ ]`
    units: int = 10  # 情境假設的投資單位數
    iis_pages: int = 4  # 投資人須知頁數（不足補空白頁、多的刪掉最後幾頁）
    iis_ticker_break: int | None = (
        None  # 投資人須知第 n 檔（從 1 起）的彭博代碼在代號與交易所碼之間換行（OOS 實際寫法）
    )

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count)
        if not self.autocall:
            self.first_callable = self.tenor
            self.ko = Decimal("100.00")
            self.memory = False
            self.ko_obs = "P"
        self.final_date, self.maturity_date = self.ends[-1], self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期配息觀察期間結束日（P 型為觀察日）：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=3)) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    @property
    def tickers(self) -> list[str]:
        return [u.ticker for u in self.underlyings]

    @property
    def name_zh(self) -> str:
        auto = "" if not self.autocall else NAME["autocall_memory_zh"] if self.memory else NAME["autocall_zh"]
        under = NAME["basket_zh"] if self.count > 1 else NAME["single_zh"]
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh, autocall_zh=auto, underlying_zh=under)

    @property
    def name_en(self) -> str:
        under = NAME["basket_en"] if self.count > 1 else NAME["single_en"]
        auto = NAME["autocall_en"] if self.autocall else ""
        return NAME["en"].format(tenor=self.tenor, ccy=self.ccy, autocall_en=auto, underlying_en=under)

    def money(self, x: Decimal | int, ccy: str | None = None) -> str:
        """金額 + 幣別：價格新文件去尾零（最多 4 位）、舊文件固定 4 位；整數金額加千分位。"""
        x = Decimal(x)
        if x == x.to_integral_value():
            text = f"{x:,.0f}" if not self.legacy or ccy is None else f"{x:,.4f}"
        else:
            text = f"{x:,.4f}" if self.legacy else f"{x.normalize():,f}"
        return f"{text} {ccy or self.currency_zh}"

    def price_text(self, x: Decimal) -> str:
        return self.money(x, self.currency_zh)


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

    def chapter(self, zh: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{zh}"
        self.w.line(42, f"{zh}、{name}", gap=16)

    def numbered(self, mark: str, body: str, *, legacy: bool, x: float = 42, body_x: float = 67) -> None:
        """條號或子項：新文件標號 x 42、內容同一列 x 67；舊文件「N. 內容」同一行。"""
        if legacy:
            self.text(x, f"{mark} {body}", body_x)
            return
        self.w.need(13)
        with self.w.verbatim():
            self.w.put(x, self.w.y, mark)
        self.text(body_x, body)

    def article(self, n: int, title: str, s: Spec) -> None:
        if not (self.section.startswith("ch") and self.section != "ch一"):
            self.section = f"art{n}"
        self.numbered(f"{n}.", title, legacy=s.legacy)

    def pair(self, label: str, values: Sequence[str]) -> None:
        """封面兩欄表的一項：標籤 x 60 與 ‧、第一個值同一列，其餘值接在下面（x 273）。"""
        self.w.need(13)
        with self.w.verbatim():
            self.w.put(60, self.w.y, self.w.edit(label))
            self.w.put(260, self.w.y, "‧")
        for v in values:
            self.text(273, v)


def _price_table(d: _Doc, s: Spec, *, reprint: bool) -> None:
    """第 9 項標的表（`reprint` 為第 15 項假設價格表：沒有分類與交易所欄，執行價、下限價欄標「(假設)」）。"""
    hypo = "(假設)" if reprint else ""
    pricing = s.pricing_text or ("VWAP" if s.vwap else "收盤價格")
    cols: list[tuple[str, Decimal | None]] = [
        (f"期初股價 (期初評價日之{pricing})", None),
        (f"執行價 (期初股價的{s.strike:.2f}%){hypo}", s.strike),
    ]
    if s.ki != "none":
        cols.append((f"下限價 (期初股價的{s.ki_pct:.2f}%){hypo}", s.ki_pct))
    if s.autocall:
        cols.append((f"自動提前出場界限 (期初股價的{s.ko:.2f}%)", s.ko))
    heads = ["連結標的", "彭博代碼"] + ([] if reprint else ["連結標的分類", "交易所"])
    for t in heads:
        d.cell(60, t)
    for label, _ in cols:
        for k in range(0, len(label), 6):  # 表頭每格拆成多行
            d.cell(300, label[k : k + 6])
    for i, u in enumerate(s.underlyings, 1):
        d.cell(48, str(i))
        d.cell(60, u.name)
        code, exch = u.ticker.split(" ")
        d.cell(150, f"{code} {exch}")
        d.cell(150, "Equity")
        if not reprint:
            d.cell(200, "股份")
            d.cell(240, u.exchange)
        for _, pct in cols:
            value = u.initial if pct is None else price(u.initial, pct)
            d.cell(300, s.price_text(value))


def _article8(d: _Doc, s: Spec) -> None:
    unit, worst = s.currency_zh, ("表現最差連結標的" if s.count > 1 else "連結標的")
    d.text(67, "(1) 配息： 本商品每月固定配息，請見下述第一章第13 項")
    d.text(67, "(2) 投資人於次級市場申請提前贖回：提前贖回之商品單位數 ×（每單位商品面額 × 提前贖回價格百分比）")
    d.text(67, "(3) 到期日給付：（到期日應支付或交付者，下稱「最終給付金額」）")
    lead = (
        "本商品若未發生記憶式自動提前出場事件，"
        if s.memory and s.autocall
        else "本商品若未發生自動提前出場事件，"
        if s.autocall
        else "本商品若"
    )
    d.text(67, f"{lead}未經發行機構提前贖回或投資人提前贖回，本商品將於到期日以下列方式給付：")
    if s.ki == "none":
        d.text(
            67,
            f"(a) 若在期末評價日{worst}之最終收盤價等於或大於執行價，以{unit}支付本商品每單位商品面額的100%之金額 或",
        )
        d.text(67, f"(b) 若在期末評價日{worst}之最終收盤價小於執行價，則")
    elif s.ki == "AM":
        d.text(67, f"(a) 若觸及下限事件從未發生，以{unit}支付本商品每單位商品面額的100%之金額 或")
        d.text(67, "(b) 若觸及下限事件曾發生，則")
    else:
        d.text(
            67,
            f"(a) 若觸及下限事件從未發生或觸及下限事件曾發生但在期末評價日{worst}之最終收盤價等於或大於執行價，以{unit}支付本商品每單位商品面額的100%之金額 或",
        )
        d.text(67, f"(b) 若觸及下限事件曾發生且在期末評價日{worst}之最終收盤價小於執行價，則")
    d.text(67, f"將以實物交付{worst}之股數如下：每單位商品面額的100% ÷ {worst}之執行價")
    d.text(67, f"執行價係指相關連結標的之期初股價的{s.strike:.2f}%")
    if s.ki != "none":
        watch = {
            "AM": f"在期末評價日，若{worst}之收盤價低於其下限價",
            "D": f"於期初評價日（不含當日）起至期末評價日（含當日）止之期間內，每一個預定交易日（干擾日除外），若{worst}之收盤價曾低於其下限價",
            "P": f"在任t 個觀察日 (t=1-{s.tenor})，若{worst}之收盤價曾低於其下限價",
        }[s.ki]
        d.text(67, f"觸及下限事件：{watch}，則視同觸及下限事件曾經發生。")
        d.text(67, f"下限價：係指相關連結標的之期初股價的{s.ki_pct:.2f}%，列於下述第9 項")
    if s.autocall:
        event = "記憶式自動提前出場" if s.memory else "自動提前出場"
        d.text(
            67,
            f"(4) 發生{event}：若發生{event}事件，發行機構就每單位商品將支付相當於每單位商品面額100%之金額，請見下述第13 項",
        )


def _dates(d: _Doc, s: Spec) -> None:
    ends, payments, k = s.ends, s.payments, s.first_callable
    d.numbered(
        "(1)",
        f"商品年期：{s.tenor} 個月，惟發行機構得提前贖回本商品 (請見第一章第17 項)"
        + (
            "以及記憶式自動提前出場事件是否發生"
            if s.autocall and s.memory
            else "以及自動提前出場事件是否發生"
            if s.autocall
            else ""
        ),
        legacy=s.legacy,
    )
    d.numbered("(2)", f"交易日：{zh_date(s.trade_date)}", legacy=s.legacy)
    d.numbered(
        "(3)", f"期初評價日：{zh_date(s.trade_date)}，惟受限於依照下述第(8)點「附帶條件」所為之調整", legacy=s.legacy
    )
    d.numbered("(4)", f"發行日：{zh_date(s.issue_date)}", legacy=s.legacy)
    d.numbered(
        "(5)", f"期末評價日：{zh_date(s.final_date)}，惟受限於依照下述第(8)點「附帶條件」所為之調整", legacy=s.legacy
    )
    d.numbered("(6)", f"到期日：{zh_date(s.maturity_date)}", legacy=s.legacy)
    if not s.autocall:
        label, heads = "配息日：", ["配息日"]
    elif s.ko_obs == "D":
        label, heads = (
            "配息觀察期間及配息日：（惟觀察日受限於依照下述第(8)點「附帶條件」所為之調整）",
            [
                "配息觀察期間開始日（不含當日）",
                "配息觀察期間結束日（含當日）",
                "配息日",
            ],
        )
    else:
        label, heads = "觀察日及配息日：（惟觀察日受限於依照下述第(8)點「附帶條件」所為之調整）", ["觀察日", "配息日"]
    d.numbered("(7)", label, legacy=s.legacy)
    d.section = "art12.table"
    d.cell(48, "(t)")
    for h in heads:
        d.cell(120, h)
    for j in range(1, s.tenor + 1):
        d.cell(48, str(j))
        if s.autocall and s.ko_obs == "D":
            start = "不適用" if j <= k else zh_date(ends[j - 2])
            end = "不適用" if j < k else zh_date(ends[j - 1])
            d.cell(120, start)
            d.cell(240, end)
        elif s.autocall:
            d.cell(120, zh_date(ends[j - 1]))
        d.cell(360, zh_date(payments[j - 1]))
    d.section = "art12"
    d.text(67, "每一期配息日預期將如上表所列但受限於營業日調整順延慣例。")
    d.numbered(
        "(8)", "附帶條件：就任一連結標的而言，若期初評價日，期末評價日或觀察日是非預定交易日，則順延。", legacy=s.legacy
    )
    if s.autocall and s.ko_obs == "D":
        d.numbered("(9)", "自動提前出場觀察日和自動提前出場配息日：", legacy=s.legacy)
        d.text(
            67,
            f"就所有連結標的而言，於第{zh_number(k)}個配息觀察期間結束日（含當日）起至期末評價日（含當日）止之期間內，"
            "每一個預定交易日（干擾日除外），惟受限於依照上述第(8)點「附帶條件」所為之調整。",
        )


def _article13(d: _Doc, s: Spec) -> None:
    unit, m, a, k = s.currency_zh, s.monthly, s.annual, s.first_callable
    if s.autocall and s.ko_obs == "D":
        d.text(67, "固定配息金額")
        d.text(67, "(1) 第1 期配息日應支付下列固定配息金額：")
        d.text(67, f"每單位商品面額 × {m}% (等同年息為{a:.2f}%)")
        d.text(67, "(2) 第1 期配息日之後的固定配息金額，應於每一期配息日(t)支付，支付金額為：")
        d.text(67, f"每單位商品面額 × {m}% (等同年息為{a:.2f}%)")
        d.text(67, f"自動提前出場配息：於其他情況，每單位商品面額 × m ÷ M × {m}% (等同年息為{a:.2f}%)")
    else:
        d.text(67, f"固定配息金額：於每期配息日應以{unit}支付下列固定配息金額")
        d.text(67, f"每單位商品面額x {m}% (約等同年息為{a:.2f}%)(四捨五入計算至小數點後第2 位)")
    d.text(67, "收盤價格：係指連結標的於相關日之正式收盤價。")
    if s.autocall:
        d.text(67, f"自動提前出場界限：係指相關連結標的之期初股價的{s.ko:.2f}%")
    d.text(67, "最終收盤價：係指相關連結標的於期末評價日之收盤價格。")
    if s.autocall:
        days = (
            f"自動提前出場觀察日(即第{k}個配息觀察期間結束日（含當日）起至期末評價日（含當日）)"
            if s.ko_obs == "D"
            else "觀察日"
        )
        if s.memory:
            d.text(
                67,
                f"記憶式自動提前出場事件：於{days}，所有連結標的如發生或被視為發生「買回自動提前出場」，則視為已發生記憶式自動提前出場事件。",
            )
            d.text(
                67,
                "就任一連結標的而言，若在任何觀察日，相關連結標的之收盤價格等於或大於其自動提前出場界限，則發生「買回自動提前出場」；"
                "惟一旦連結標的發生「買回自動提前出場」，對於該連結標的，其將被視為已在該觀察日及其後的觀察日發生「買回自動提前出場」。",
            )
        else:
            d.text(
                67,
                f"自動提前出場事件：若於{days}，所有連結標的之收盤價格均同時等於或大於其各自的自動提前出場界限，即是發生自動提前出場事件。",
            )
    d.text(
        67,
        f"配息後本商品的市場價值會相對降低（例如：假設本商品配息日前市場價值為98.00%，配息率為{m:.4f}%（即年化配息率為{a:.2f}%））。",
    )


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """Citi 中文投資人須知（docs/templates/citi-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis")
    w = d.w
    unit, worst = s.currency_zh, ("表現最差連結標的" if s.count > 1 else "連結標的")
    trustee = (s.product_code if s.trustee_code is None else s.trustee_code) or "[ ]"
    memo = "記憶式" if s.autocall and s.memory else ""
    w.line(48, "中文投資人須知", gap=14)
    d.text(48, f"刊印日期：{zh_date(s.trade_date)}")
    d.text(48, s.name_zh)
    d.text(
        48,
        f"(下稱「商品」) {s.name_en} 商品種類：{PRODUCT_TYPE}；ISIN：XS1999900001；受託或銷售機構商品代號：{trustee}；"
        f"集保商品代號：{s.product_code}",
    )
    w.line(48, "本商品之投資風險警語：")
    d.section = "warn"
    for n, text in enumerate(_iis_warnings(), 1):
        d.text(48, f"{n}. {text}", 60)
    d.section = "org"
    w.line(48, "一、 相關機構事業概況")
    d.text(48, f"1. 發行機構：{ISSUER}(“CGMHI”) (營業所在地：1 Fictional Street, New York)")
    d.text(48, f"2. 保證機構：{IIS_GUARANTOR}(營業所在地：1 Fictional Street, New York)")
    d.text(48, f"3. 總代理人：{AGENT['cover']}（營業所在地：臺北市信義區虛構路1 號）")
    d.text(48, f"4. 受託或銷售機構：{DIST['name']}（營業所在地：{DIST['address']}）")
    d.section = "summary"
    w.line(48, "二、商品事項")
    w.line(48, "1. 商品簡介：")
    names = "； ".join(
        f"{i}. {u.name}（Fictional {i} Inc）(彭博代碼：{u.ticker})" for i, u in enumerate(s.underlyings, 1)
    )
    k = s.first_callable
    table = "配息日" if not s.autocall else "配息觀察期間及配息日" if s.ko_obs == "D" else "觀察日及配息日"
    tenor = f"商品年期：{s.tenor} 個月，惟受限於發行機構得提前贖回之條件" + (
        f"以及{memo}自動提前出場事件是否發生" if s.autocall else ""
    )
    lines = [
        "受託或銷售對象：專業投資人",
        "本商品之風險程度：RR4",
        f"計價幣別：{unit}",
        "原計價幣別本金保本率：不保本。",
        f"連結標的資產：{names}",
        tenor,
        f"交易日：{zh_date(s.trade_date)}；期初評價日：{zh_date(s.trade_date)}；發行日：{zh_date(s.issue_date)}",
        f"期末評價日：{zh_date(s.final_date)}；到期日：{zh_date(s.maturity_date)}；{table}：詳細日期請見中文產品說明書第一章第12 項。",
    ]
    if s.autocall and s.ko_obs == "D":
        lines.append(
            f"自動提前出場觀察日：就所有連結標的而言，於第{k} 個配息觀察期間結束日（含當日）起至期末評價日（含當日）"
            "止之期間內，每一個預定交易日（干擾日除外）。"
        )
    lines.append(f"開始受理贖回日期：{zh_date(weekday_on_or_after(s.issue_date + dt.timedelta(days=1)))}")
    for text in lines:
        if s.iis_ticker_break and text.startswith("連結標的資產："):  # 每檔一行，指定那檔在代號與交易所碼之間換行
            entries = text.removeprefix("連結標的資產：").split("； ")
            out = ["• 連結標的資產："]
            for n, entry in enumerate(entries, 1):
                sep = "" if n == len(entries) else "；"
                if n == s.iis_ticker_break:
                    code, exchange = entry.rsplit(" ", 1)
                    out[-1] += code
                    out.append(exchange + sep)
                else:
                    out[-1] += entry + sep
                    out.append("")
            for k, part in enumerate(x for x in out if x):
                w.line(48 if k == 0 else 60, part)
            continue
        d.text(48, f"• {text}", 60)
    d.section = "coupon"
    w.line(48, "2. 收益分配事項：")
    m, a = s.monthly, s.annual
    if s.autocall and s.ko_obs == "D":
        d.text(
            48,
            f"配息金額：第1 期配息日應支付下列固定配息金額：每單位商品面額× {m}%(約等同年息為{a:.2f}%)；第1 期配息日之後的"
            f"固定配息金額，應於每一期配息日(t)支付，支付金額為：每單位商品面額× {m}%，惟若發生{memo}自動提前出場事件，"
            f"則支付自動提前出場配息，為每單位商品面額× m/M × {m}%。",
        )
    else:
        d.text(
            48, f"配息金額：固定配息金額應於配息日，以{unit}支付下列金額：每單位商品面額x{m}%(約等同年息為{a:.2f}%)。"
        )
        if s.autocall:
            d.text(
                48,
                f"{memo}自動提前出場事件：(i)於任一觀察日，所有連結標的之收盤價格均等於或大於其各自的自動提前出場界限。",
            )
    d.section = "redeem"
    w.line(48, "3. 贖回價金之計算")
    lead = f"本商品若未發生{memo}自動提前出場事件，且未經" if s.autocall else "本商品若未經"
    if s.ki == "none":
        cases = (
            f"(a) 若在期末評價日{worst}之最終收盤價等於或大於執行價，以{unit}支付本商品每單位商品面額的100%之金額 或"
            f"(b) 若在期末評價日{worst}之最終收盤價小於執行價，將以實物交付{worst}之股數。"
        )
    elif s.ki == "AM":
        cases = f"(a) 若觸及下限事件從未發生，以{unit}支付本商品每單位商品面額的100%之金額 或(b) 若觸及下限事件曾發生，將以實物交付。"
    else:
        cases = (
            f"(a) 若觸及下限事件從未發生或觸及下限事件曾發生但在期末評價日{worst}之最終收盤價等於或大於執行價，"
            f"以{unit}支付本商品每單位商品面額的100%之金額 或(b) 若觸及下限事件曾發生且…小於執行價，將以實物交付。"
        )
    text = f"• 到期給付金額：{lead}發行機構提前贖回或投資人提前贖回，本商品將於到期日以下列方式給付：{cases}"
    text += f"執行價係指相關連結標的之期初股價的{s.strike:.2f}%。"
    if s.ki != "none":
        watch = {
            "AM": f"在期末評價日，若{worst}之收盤價低於其下限價",
            "D": f"於期初評價日（不含當日）起至期末評價日（含當日）止之期間內，每一個預定交易日（干擾日除外），若{worst}之收盤價曾低於其下限價",
            "P": f"在任t 個觀察日 (t=1-{s.tenor})，若{worst}之收盤價曾低於其下限價",
        }[s.ki]
        text += (
            f"觸及下限事件：{watch}，則視同觸及下限事件曾經發生。下限價：係指相關連結標的之期初股價的{s.ki_pct:.2f}%。"
        )
    d.text(48, text, 60)
    w.line(48, "4. 受託或發行不成立之情形：")
    d.text(48, f"(1) 需發行機構於交易截止日或之前收到本商品有效申購金額至少達{MINIMUM_ISSUE}之總申購價值。")
    d.section = "rest"
    w.line(48, "三、 投資風險（詳細風險資訊，請參閱中文產品說明書第三章「商品風險揭露」）")
    d.text(48, "1. 基本風險資訊 (1) 最低收益風險：本商品不保本。")
    d.section = "fees"
    w.line(48, "四、 投資人應負擔的各項費用（除通路服務費外，發行機構並無其他與本商品有關的費用。）")
    for cell in (
        "費用項目",
        "費率(百分比)",
        "申購費用",
        "申購價金的",
        "0%~5%",
        "提前贖回費用",
        "投資人提前贖",
        "回價金的",
        "0%~5%",
    ):
        d.cell(60, cell)
    for cell in (
        "分銷費用(如屬發行",
        "機構或發行人給予受",
        "託或銷售機構之報",
        "酬、費用、折讓等各",
        "項利益應單獨列示)",
        "報",
        "酬 無",
        "費",
        "用",
        "申購價金的",
        "0%~5%",
        "折",
        "讓 無",
    ):
        d.cell(60, cell)
    d.section = "rest"
    w.line(48, "五、 相關機構之權利、義務及責任")
    d.text(48, "1. 本商品係依境外結構型商品管理規則受託投資或受託買賣時，發行機構應依照中文產品說明書支付有關款項。")
    w.line(48, "六、 協助投資人權益保護之方式")
    w.line(48, "七 發行機構、總代理人及受託或銷售機構與投資人爭議之處理方式")
    while len(w.doc) < s.iis_pages:
        w.new_page()
    while len(w.doc) > s.iis_pages:
        w.doc.delete_page(len(w.doc) - 1)
    for n, page in enumerate(w.doc, 1):
        page.insert_text((250, 40), f"第{n}頁，共{s.iis_pages}頁", fontname=FONT, fontsize=10)
    return w.save(path)


def _iis_warnings() -> list[str]:
    """投資人須知警語 1.～13.：1. 為固定警語；4.、5. 有受託機構簡稱；5. 有發行機構與保證機構；7.、9. 含允許的「受託投資」片語。"""
    out = _warnings()
    out[8] = "當受託投資或銷售本商品予任何投資人時，受託或銷售機構將不會對美國人士為銷售之要約。"
    return out


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 Citi 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()  # 說明書與投資人須知共用：改字換到哪一份都算
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    w = d.w
    unit = s.currency_zh
    trustee = (s.product_code if s.trustee_code is None else s.trustee_code) or "[ ]"
    w.line(48, "中文產品說明書", gap=16)
    d.text(48, s.name_zh)
    d.text(48, f"受託或銷售機構商品代號為{trustee}，ISIN 為XS1999900001，集保商品代號為{s.product_code}")
    d.pair("商品中文名稱：", [f"{s.name_zh}（下稱「本商品」）"])
    d.pair("商品英文名稱：", [s.name_en])
    d.pair("商品種類：", [PRODUCT_TYPE])
    d.pair("發行機構註冊地：", ["美國"])
    d.pair("計價幣別：", [unit])
    d.pair(
        "發行機構之名稱、電話及地址：",
        [f"{ISSUER}(“CGMHI”)", "地址：1 Fictional Street, New York", "電話：+1 000 000-0000"],
    )
    d.pair("總代理人之名稱、電話及地址：", [AGENT["cover"], "地址：臺北市信義區虛構路1 號", "電話：886-2-0000-0000"])
    d.pair("保證機構之名稱及地址：", [GUARANTOR["cover"], "地址：1 Fictional Street, New York"])
    d.pair(
        "受託或銷售機構之名稱、電話及地址：",
        [f"{DIST['name']} (下稱「玉山證券」)", f"地址：{DIST['address']}", f"電話：{PHONE}"],
    )
    d.pair("受託或銷售機構審查通過之日期/文號：", [zh_date(APPROVAL)])
    w.line(42, "警語：")
    warnings = _warnings()
    for n, t in enumerate(warnings, 1):
        d.text(42, f"{n}. {t}", 60)
    d.text(42, f"產品說明書刊印日期：{zh_date(s.trade_date)}")

    d.chapter("一", "商品基本資料")
    titles = {
        1: f"商品名稱：{s.name_zh}（下稱「本商品」）",
        2: f"商品風險程度：{WARNING}",
        3: f"發行機構名稱及其長期債務信用評等：{ISSUER}；長期債務信用評等為虛構",
        4: "商品之發行評等：無",
        5: f"計價幣別：{unit}({s.ccy})",
        8: "主要給付項目及其計算方式：",
        9: "連結標的資產：",
        12: "商品年期、發行日、到期日及其他依個別商品性質而定之日期：",
        13: "配息資料及其計算公式（本商品項下之所有計算及判定將由計算代理機構以其單獨絕對裁量權為之）：",
        14: "發行機構提前贖回/提前買回及到期給付計算公式，包含最低保證配息率及參與率：",
        15: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之平均年化報酬率：",
    }
    for n in range(1, 25):
        d.article(n, titles.get(n, f"第{n}項說明（不核對）"), s)
        if n == 3:
            d.text(67, f"保證機構名稱及其長期債務信用評等：{GUARANTOR['ch1']}；長期債務信用評等為虛構")
        elif n == 5:
            d.numbered("(1)", f"每單位商品面額： {s.money(s.denom)}", legacy=s.legacy, x=67, body_x=90)
            d.numbered("(2)", "發行價格： 每單位商品面額的100%", legacy=s.legacy, x=67, body_x=90)
        elif n == 8:
            _article8(d, s)
        elif n == 9:
            d.section = "art9.table"
            _price_table(d, s, reprint=False)
            d.section = "art9"
            d.text(67, "（每一連結標的下稱「連結標的」或「連結標的資產」）")
            d.text(67, "相對權重： 不適用")
        elif n == 12:
            _dates(d, s)
        elif n == 13:
            _article13(d, s)
        elif n == 14:
            d.text(
                67,
                f"(2) 最低保證配息率：每一配息日之固定配息金額為每單位商品面額 × {s.monthly}% (年化配息率約為{s.annual:.2f}%)。",
            )
        elif n == 15:
            d.text(67, "假設：")
            d.text(67, f"a) 每單位商品面額： {s.money(s.denom)}")
            d.text(67, f"b) 投資單位數： {s.units} 單位")
            total = s.units * s.denom
            sep = "：" if s.legacy else " ="
            d.text(67, f"c) 初始投資金額{sep} {s.units} 單位 × {s.money(s.denom)} = {s.money(total)}")
            d.text(67, f"d) 固定配息金額為每單位商品面額 × {s.monthly}% (等同年息為{s.annual:.2f}%)")
            d.text(67, "e) 連結標的相關股價資訊：")
            d.section = "art15.table"
            _price_table(d, s, reprint=True)
            d.section = "art15"
            d.text(67, "情境 ： 本金全部損失之最差情況")
            d.text(67, "情境內容為虛構說明，不核對。")

    d.chapter("二", "相關機構事業概況")
    d.article(1, "發行機構", s)
    d.numbered("(1)", f"公司名稱：{ISSUER}", legacy=s.legacy, x=66, body_x=90)
    d.article(2, "保證機構：", s)
    d.numbered("(1)", f"公司名稱：{GUARANTOR['ch2']}", legacy=s.legacy, x=66, body_x=90)
    d.article(3, "總代理人、計算代理機構、付款代理機構、受託或銷售機構及結算機構：", s)
    for n, (title, name, place, head) in enumerate(
        (
            ("總代理人：", AGENT["ch2"], "臺北市信義區虛構路1 號", "虛構甲"),
            ("計算代理機構：", "Fictional Bank N.A.", "1 Fictional Street, New York", "虛構乙"),
            ("付款代理機構：", "Fictional Markets Ltd", "1 Fictional Street, London", "虛構丙"),
            ("財務代理機構：", "虛構銀行倫敦分行", "1 Fictional Street, London", "虛構丁"),
            ("受託或銷售機構：", DIST["name"], DIST["address"], DIST["chairman"]),
        ),
        1,
    ):
        d.numbered(f"({n})", title, legacy=s.legacy, x=66, body_x=90)
        for t in (f"公司名稱：{name}", f"營業所在地：{place}", f"負責人姓名：{head}"):
            d.w.line(92, "•", gap=0)
            d.text(110, t)

    d.chapter("三", "商品風險揭露")
    d.article(1, "基本風險資訊：本商品不保本。", s)
    d.article(2, "個別商品風險資訊：不核對。", s)
    d.article(3, "依境外結構型商品管理規則第二十三條第一項：不核對。", s)
    d.article(4, "本商品之投資風險警語。", s)
    for n, t in enumerate(warnings, 1):
        d.text(67, f"({n}) {t}", 85)

    d.chapter("四", "一般交易事項")
    d.article(1, "商品開始受理申購、結束受理申購日期、開始受理贖回日期及後續受理贖回日期：", s)
    for t in (
        f"開始受理申購日期：{zh_date(s.trade_date)}",
        f"結束受理申購日期：{zh_date(s.trade_date)}",
        f"開始受理贖回日期：{zh_date(weekday_on_or_after(s.issue_date + dt.timedelta(days=1)))}",
    ):
        d.w.line(67, "•", gap=0)
        d.text(80, t)
    d.article(2, "投資人應負擔的各項費用及金額或計算基準之列表：", s)
    for t in ("費用項目", "費率(百分比)", "收取時點", "收取方式", "收取人"):
        d.cell(60, t)
    for t in (
        "申購費用",
        "申購價金的0%~5%",
        "投資人向受託或銷售機構申購時",
        "提前贖回費用",
        "投資人提前贖回價",
        "金的0%~5%",
        "投資人提前贖回時",
    ):
        d.cell(60, t)
    for t in (
        "管理費用(信託管理費或",
        "管銷費用)",
        "無",
        "不適用",
        "分銷費用(如屬發",
        "行機構或發行人給",
        "予受託或銷售機構",
        "之報酬、費用、折",
        "讓等各項利益應單",
        "獨列示)",
    ):
        d.cell(60, t)
    if s.legacy:
        d.cell(60, "申購價金的0%~5%")
    else:
        for t in (
            "報",
            "酬",
            "無",
            "不適用",
            "不適用",
            "不適用",
            "費",
            "用",
            "申購價金的0%~5%",
            "於本商品發行時一次給付",
            "折",
            "讓",
            "無",
            "不適用",
        ):
            d.cell(60, t)
    for t in ("保費費用", "不適用", "解約費用", "不適用", "其他費用", "無"):
        d.cell(60, t)
    d.article(3, "申購方式：不核對。", s)
    d.article(4, "最低申購金額及最低加購金額：", s)
    d.text(67, "最低申購金額：")
    d.text(67, s.money(s.denom))
    d.text(67, "最低加購金額：")
    d.text(67, s.money(s.denom))
    d.article(5, "申購價金之計算：每單位商品面額 × 申購單位數 × 100%", s)
    d.article(6, "申購手續及資金給付方式：不核對。", s)
    d.article(7, "受託/發行不成立之情形：", s)
    d.text(67, f"需發行機構於交易截止日或之前收到本商品有效申購金額至少達{MINIMUM_ISSUE}之總申購價值。")
    d.article(8, f"最低贖回金額或單位數：{s.money(s.denom)}。本商品必須以面額倍數之整數贖回，不得分拆。", s)

    d.chapter("五", "特別記載事項")
    d.text(42, "一、 不核對。")
    path = w.save(path)
    require_used(edits, used)
    return path


def _warnings() -> list[str]:
    """封面警語 1.～13. 與第三章 §4 (1)～(13)：1. 為固定警語，5. 含發行機構與保證機構名稱，7.、9. 含允許的「受託投資」片語。"""
    return [
        WARNING,
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及配息(如有)。",
        "本商品雖經玉山證券審查，惟並不代表保證本商品之價值，且玉山證券對本商品投資盈虧不負任何責任。玉山證券依法不得承諾擔保原計價幣別投資本金或最低收益率。",
        f"本商品持有期間如有保證配息收益或保證保本率，係由{ISSUER}（發行機構）及{GUARANTOR['cover']}（保證機構）保證，"
        "而非由玉山證券（受託或銷售機構）所保證。",
        "本中文產品說明書之內容如有虛偽或隱匿之情事者，其餘內容應由發行機構、總代理人及其負責人依法負責。",
        "本商品係依境外結構型商品管理規則於中華民國境內受託投資、受託買賣之投資標的。",
        "本商品未曾亦不會根據美國證券法註冊。",
        "當受託投資或受託買賣本商品予任何投資人時，受託或銷售機構將不會對美國人士為銷售之要約。",
        "投資人未清楚瞭解本商品中文產品說明書前，請勿於相關文件簽名或蓋章。",
        "受託或銷售機構應提供投資人相關契約審閱期間，其審閱期間不得低於三日。",
        "委託投資本商品之專業投資人不適用「金融消費者保護法」之金融消費爭議處理機制。",
        "請注意，於交易日確認之各項條件取決於市場狀況，詳見第一章第13 項。",
    ]


def row(spec: Spec, *, order: Sequence[int] | None = None, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列；`order`（從 1 起）改變表上 UL_n 的排序（Citi 表上常和說明書不同）。"""
    out = reference_row(spec)
    if spec.vwap:
        out["期初定價"] = "VWAP"
    if order:
        cols = ("", "_進場價", "_執行價", "_下限價", "_KO價")
        moved = {f"UL_{new}{c}": out[f"UL_{old}{c}"] for new, old in enumerate(order, 1) for c in cols}
        out.update(moved)
    out.update(overrides)  # 以 Excel 欄名覆寫（排序之後：UL_n 指表上第 n 檔）
    return out


def check(
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
    經批量入口核對，回傳該說明書的 CheckReport。"""
    return check_item(tmp_path, spec, pdf_spec=pdf_spec, edits=edits, order=order, config=config, **overrides).report


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
    """同 `check`，回傳說明書的批量核對項目（BatchItem，含回填決策）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, order=order, **overrides)])
    outcome = check_all(sheet, [pdf], config)
    return next(i for i in outcome.items if i.term_sheet == pdf)
