"""SG 合成說明書（docs/templates/sg-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：封面兩欄表（標籤 x 100、值 x 270）；章名「一、商品基本資料」；第一章條號「N.」x 28、標題在
同一列 x 50；子項「(n) …」x 50；表格一格一行。`legacy=True` 模擬舊版面（數字與單位、年月日之間有空白）。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`、第一章各條 `ts.art{n}`
（標的表 `ts.art8.table`、日期表 `ts.art12.table`、重印表 `ts.art15.table`、情境 `ts.scen.一`…）、其他各章 `ts.ch二`～`ts.ch五`。
SG 投資人須知尚無範本：`build_pdf` 旁邊只寫一份一頁的佔位 `_IIS.pdf`（未支援上手），讓說明書不因「這批缺投資人須知」
轉人工覆核；`iis=False` 時不寫。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import fitz

from harness import CONFIG, STANDARD, check_all, iis_path
from pdf_writer import Edit, PdfWriter, require_used
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row

NAME = STANDARD["product_name"]["sg"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["sg"]
WARNING_CH1 = STANDARD["risk"]["fixed_warning_variants"]["sg"][0]
ISSUER = STANDARD["issuer_name"]["sg"]
GUARANTOR = STANDARD["guarantor_name"]["sg"]
AGENT = STANDARD["agent_name"]["sg"]
PRODUCT_TYPE = STANDARD["product_type"]["sg"]
MULTIPLES = STANDARD["min_amount_multiples"]["sg"]
DIST = STANDARD["distributor"]
PHONE = "+886 2 5556 1313"  # 審查標準 phone_equivalents 的 SG 寫法
ADDRESS_COVER = "台北市松山區民生東路3段158號6樓"  # 審查標準 address_equivalents 的 SG 封面寫法
DIST_SHORT = "玉山證券"  # 審查標準 name_equivalents 的 SG 第二章寫法
APPROVAL = dt.date(2026, 6, 11)
ZH = "一二三四五六七八九十"
NUMERIC = set("0123456789,.")
Q2, Q4 = Decimal("0.01"), Decimal("0.0001")


def _weekday(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def underlyings(count: int) -> tuple[UL, ...]:
    """SG 合成文件的標的：ZSn UW、期初價 100n。"""
    return tuple(
        UL(f"虛構標的{i}公司", "那斯達克證券交易所", f"ZS{i} UW", Decimal(100 * i)) for i in range(1, count + 1)
    )


@dataclass
class Spec(ProductSpec):
    """SG 合成說明書的規格：商品規格加上 SG 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count` 產生、最終比價日與到期日由配息排程推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    `autocall=False` 為無自動提前出場（Non-Call = 天期、表上 KO(Freq)=P、KO(%)=100）。
    `fx` 給了就是 quanto（標的為日圓、商品為其他幣別）的寫法（§8 標的價格寫日圓、情境多一行最終匯率定價）。
    """

    issuer: str = "SG"
    product_code: str = "037199990001"
    ki: str = "AM"  # none／AM／D／P
    tenor: int = 6
    first_callable: int = 2
    annual: Decimal = Decimal("13.2")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    autocall: bool = True  # False → 無自動提前出場
    units: int = 10  # 情境假設的單位數
    legacy: bool = False  # 舊版面：數字與單位、年月日之間有空白
    trustee_code: str | None = None  # 受託或銷售機構商品代號；None 同商品代號，"" 空白
    fx: Decimal | None = None  # quanto：最終匯率定價（每一日圓兌商品幣別）
    agent_head: str = "虛構甲"  # 總代理人負責人（不核對）

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
        """各期配息週期終止日（P 型為評價日）：每月 9 日，遇週末順延。"""
        out = []
        for j in range(1, self.tenor + 1):
            m = 1 + j
            out.append(_weekday(dt.date(2030 + (m - 1) // 12, (m - 1) % 12 + 1, 9)))
        return out

    @property
    def payments(self) -> list[dt.date]:
        return [_weekday(d + dt.timedelta(days=5)) for d in self.ends]

    @property
    def obs_start(self) -> dt.date:
        """D 型觀察期間起始日 = 第 Non-Call 期終止日（第 Non-Call+1 期起始日）。"""
        return self.ends[self.first_callable - 1]

    @property
    def tickers(self) -> list[str]:
        return [u.ticker for u in self.underlyings]

    @property
    def initials(self) -> list[Decimal]:
        return [u.initial for u in self.underlyings]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    @property
    def total(self) -> Decimal:
        return Decimal(self.denom) * self.units

    @property
    def coupon(self) -> Decimal:
        return (self.total * self.monthly / 100).quantize(Q2, ROUND_HALF_UP)

    @property
    def underlying_ccy(self) -> str:
        return "日圓" if self.fx is not None else self.currency_zh

    @property
    def name_zh(self) -> str:
        if not self.autocall:
            auto = ""
        elif self.memory:
            auto = NAME["autocall_memory_zh"]
        else:
            auto = NAME["autocall_zh"]
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh, autocall_zh=auto)

    @property
    def name_en(self) -> str:
        return NAME["en"].format(
            tenor=self.tenor,
            ccy=NAME.get("ccy_aliases", {}).get(self.ccy, self.ccy),
            memory_en=NAME["memory_en"] if self.memory and self.autocall else "",
            autocall_en=NAME["autocall_en"] if self.autocall else "",
        )

    def date(self, d: dt.date) -> str:
        return (
            f"{d.year} 年{d.month:02d} 月{d.day:02d} 日" if self.legacy else f"{d.year}年{d.month:02d}月{d.day:02d}日"
        )

    def money(self, x: Decimal | int, ccy: str | None = None, decimals: int = 2) -> str:
        sep = " " if self.legacy else ""
        return f"{Decimal(x):,.{decimals}f}{sep}{ccy or self.currency_zh}"

    def amount(self, x: int) -> str:
        return self.money(x, decimals=0)

    def months(self, n: int) -> str:
        return f"{n} 個月" if self.legacy else f"{n}個月"


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時在非數字處換行。"""

    def __init__(self, edits: Sequence[Edit]):
        self.w = PdfWriter(edits, "ts")
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
                if used + cw > width and cur and not (cur[-1] in NUMERIC and ch in NUMERIC):
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
        self.w.line(28, f"{zh}、{name}", gap=16)

    def article(self, n: int, title: str, section: str | None = None) -> None:
        """條號「N.」x 28，標題同一列 x 50；第一章各條的段落代號為 art{n}，其他章沿用章的代號（ch二…）。"""
        other_chapter = self.section.startswith("ch") and self.section != "ch一"
        self.section = section or (self.section if other_chapter else f"art{n}")
        self.w.need(13)
        with self.w.verbatim():
            self.w.put(28, self.w.y, f"{n}.")
        self.text(50, title)

    def sub(self, n: int, text: str, x: float = 50) -> None:
        self.text(x, f"({n}) {text}", x + 20)

    def pair(self, label: str, values: Sequence[str]) -> None:
        """封面兩欄表的一項：標籤 x 100 與第一個值同一列，其餘值接在下面（x 270）。"""
        values = [v for v in values if v]
        self.w.need(13 * max(1, len(values)))
        with self.w.verbatim():
            self.w.put(60, self.w.y, self.w.edit(label))
        for v in values:
            self.text(270, v)
        if not values:
            self.w.y += 13


def _price_table(d: _Doc, s: Spec, section: str, *, reprint: bool) -> None:
    """第 8 項標的表（`reprint` 為第 15 項重印：表頭帶百分比、沒有名稱與代碼）。"""
    d.section = section
    cols: list[tuple[str, Decimal | None, str]] = [("期初價格", None, "")]
    if s.autocall:
        cols.append(("自動提前出場價", s.ko, f"(期初價格的{s.ko:.2f}%)"))
    if s.ki != "none":
        cols.append(("下限價", s.ki_pct, f"（期初價格的 {s.ki_pct:.2f}%)"))
    cols.append(("執行價", s.strike, f"(期初價格的{s.strike:.2f}%)"))
    if not reprint:
        for t in ("連結標的(k)", "連結標的資產", "彭博代碼", "連結標的資產種類"):
            d.cell(60, t)
    else:
        d.cell(55, "連結標的(k)")
    for label, _, pct in cols:
        d.cell(290, label)
        if reprint and pct:
            d.cell(280, pct)
    for i, u in enumerate(s.underlyings, 1):
        if s.count > 1 or reprint:
            d.cell(60, f"k={i}")
        if not reprint:
            d.cell(110, u.name)
            code, exch = u.ticker.split(" ")
            d.cell(200, code)
            d.cell(206, exch)
            d.cell(240, "股票")
        for _, pct, _ in cols:
            value = u.initial if pct is None else price(u.initial, pct)
            d.cell(290, s.money(value, s.underlying_ccy, 4))


def _scenarios(d: _Doc, s: Spec) -> None:
    a, n, k = s.coupon, s.tenor, s.first_callable
    kinds: list[tuple[str, str]] = [("best", "較佳情況")]
    if s.autocall:
        kinds.append(("middle", "中間情況"))
    kinds += [("worse", "較差情況"), ("worst", "最差情況")]
    worst = 1  # 表現最差標的：第 1 檔
    strike = price(s.initials[worst - 1], s.strike)
    worst_of = "最差表現之連結標的" if s.count > 1 else "連結標的"
    k_tag = f"(k={worst})" if s.count > 1 else ""
    barrier = "下限價" if s.ki != "none" else "執行價"
    for idx, (kind, title) in enumerate(kinds):
        number = ZH[idx]
        d.section = f"scen.{number}"
        d.text(28, f"情境{number}：{title}")
        autocall = kind == "best" and s.autocall
        periods = k if autocall else n
        span = f"第{periods}個配息金額" if periods == 1 else f"第1~{periods}個配息金額"
        if s.legacy:
            span = span.replace("個配息金額", " 個配息金額")
        cum = a * periods
        if autocall:
            when = (
                f"第{k}個評價日"
                if s.ko_obs == "P"
                else ("記憶事件" if s.memory else "自動提前出場事件") + "觀察期間起始日"
            )
            d.text(28, f"假設於{when}，發生自動提前出場事件，於自動提前出場日以現金結算收益：")
        elif kind in ("best", "middle"):
            lead = "假設不曾發生任何自動提前出場事件，" if s.autocall else ""
            d.text(28, f"{lead}於最終評價日{worst_of}收盤價大於或等於{barrier}，於到期日以現金結算收益：")
        else:
            close = Decimal(0) if kind == "worst" else (strike / 5).quantize(Decimal(1), ROUND_DOWN)
            lead = "假設不曾發生任何自動提前出場事件，" if s.autocall else ""
            d.text(
                28,
                f"{lead}於最終評價日{worst_of}{k_tag}收盤價= {s.money(close, s.underlying_ccy, 0)}小於{barrier}，於到期日以實體交付應實際交付數並支付剩餘金額的形式結算收益：",
            )
        d.text(28, span)
        d.text(136, f"= {s.units}單位面額x{s.amount(s.denom)}x{s.monthly}%")
        d.text(136, f"= {s.amount(int(s.total))}x{s.monthly}%")
        d.text(136, f"= {s.money(a)}")
        d.text(28, f"累積總配息金額 = {s.money(cum)}")
        if kind in ("best", "middle"):
            label = "自動提前出場贖回金額" if autocall else "到期贖回金額"
            d.text(28, f"{label} = {s.units}單位面額x{s.amount(s.denom)}x100%")
            d.text(150, f"= {s.amount(int(s.total))}x100%")
            d.text(150, f"= {s.amount(int(s.total))}")
            r = ((s.total + cum) / s.total * 100 - 100).quantize(Q4, ROUND_HALF_UP)
            d.text(28, f"總報酬率 = [({label}+累積總配息金額) / 總投資金額] -1")
            d.text(150, f"= ({s.money(s.total + cum)}/{s.amount(int(s.total))})-1")
            d.text(150, f"= {r}%(平均年化報酬率：{s.annual:.2f}%)")
            continue
        if s.fx is not None:
            d.text(28, f"最終匯率定價 = {s.fx}")
        divisor = strike * s.fx if s.fx is not None else strike
        quantity = (Decimal(s.denom) / divisor).quantize(Q4, ROUND_CEILING)
        whole = int(quantity.quantize(Decimal(1), ROUND_DOWN))
        frac = quantity - whole
        cash = (close * frac * (s.fx if s.fx is not None else 1)).quantize(Q2, ROUND_CEILING)
        d.text(28, f"數額計算 = 每單位面額 / 於最終評價日{worst_of}執行價")
        strike_text = s.money(strike, s.underlying_ccy, 4)
        if s.fx is None:
            d.text(136, f"= {s.amount(s.denom)} / {strike_text}")
        else:
            d.text(136, f"= {s.amount(s.denom)} / ({strike_text}x{s.fx})")
        d.text(136, f"= {quantity:,.4f}")
        d.text(28, f"應實際交付數 = 數額向下調整至最接近的整數位 = {whole:,}單位")
        close_text = s.money(close, s.underlying_ccy, 0)
        if s.fx is None:
            d.text(28, f"加上現金交割 = {close_text}x{frac:.4f}")
        else:
            d.text(28, f"加上現金交割 = {close_text}x{frac:.4f}x{s.fx}")
            d.text(136, f"= {s.money(close * frac, s.underlying_ccy)}x{s.fx}")
        d.text(136, f"= {s.money(cash)}")
        d.text(
            28,
            f"總投資金額 = {s.units}單位面額，因此，到期日將獲得最終評價日{worst_of}{whole * s.units:,}單位加上現金{s.money(cash * s.units)}",
        )
        value = s.total * close / strike + cum
        r = (value / s.total * 100 - 100).quantize(Q2, ROUND_HALF_UP)
        y = (r * 12 / n).quantize(Q2, ROUND_HALF_UP)
        d.text(28, "（如以現金結算到期日收益，本產品總報酬率")
        d.text(
            28,
            f"= {{[{s.amount(int(s.total))}x({close_text} / {strike_text}) + {s.money(cum)}]/ {s.amount(int(s.total))}}} -1 = {r}%（平均年化報酬率：{y}%))",
        )


def build_iis_stub(path: Path) -> Path:
    """SG 投資人須知的佔位檔（一頁；SG 投資人須知尚無範本，批量核對時為未支援上手）。"""
    w = PdfWriter()
    w.line(130, "中文投資人須知")
    w.line(18, "範本說明：SG 投資人須知尚未支援。")
    return w.save(path)


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 SG 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字；`iis` 時旁邊另寫一份佔位投資人須知。"""
    if iis and path.stem.endswith("_TS"):
        build_iis_stub(iis_path(path))
    d = _Doc(edits)
    w = d.w
    ends, payments = s.ends, s.payments
    trustee = s.product_code if s.trustee_code is None else s.trustee_code
    unit = s.currency_zh
    worst_of = "最差表現之連結標的" if s.count > 1 else "連結標的"
    w.line(32, "中文產品說明書", gap=16)
    d.text(32, s.name_zh)
    d.text(28, f"TDCC商品代號{s.product_code}，受託或銷售機構商品代號 {trustee}，ISIN為XS1999900001")
    d.pair("商品中文名稱", [s.name_zh, "（下稱「本商品」）"])
    d.pair("商品英文名稱", [s.name_en])
    d.pair("商品種類", [PRODUCT_TYPE])
    d.pair("發行機構註冊地", ["盧森堡"])
    d.pair("計價幣別", [unit])
    d.pair("發行機構之名稱、電話及地址", [ISSUER, "地址：1 Fictional Street, Luxembourg", "電話：+35 2 0000 0000"])
    d.pair("總代理人之名稱、電話及地址", [AGENT["cover"], "地址：台北市信義區虛構路1號", "電話：+886 2 0000 0000"])
    d.pair(
        "保證機構之名稱及地址",
        [f"{GUARANTOR['cover']}（下稱「法興」）", "地址：1 Fictional Boulevard, Paris", "電話：+33 1 0000 0000"],
    )
    d.pair(
        "受託或銷售機構之名稱、電話及地址",
        [f"{DIST['name']}（下稱「玉山證券」）", f"地址：{ADDRESS_COVER}", f"電話：{PHONE}"],
    )
    d.pair("受託或銷售機構審查通過之日期及文號", [s.date(APPROVAL)])
    d.text(28, "本商品之投資風險警語：")
    d.text(28, "一、" + WARNING, 58)
    d.text(28, "二、本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。", 58)
    d.text(
        28,
        f"五、本商品持有期間如有保證保本率或保證配息，係由{ISSUER}（發行機構）或{GUARANTOR['cover']}（保證機構）保證，而非由玉山證券所保證。",
        58,
    )
    d.text(
        28,
        "七、本商品的條款和條件之準據法為英國法。本商品係依境外結構型商品管理規則規定，為於中華民國境內受託買賣。投資人申購前應詳閱本商品中文產品說明書。",
        58,
    )
    d.text(28, "十、受託或銷售機構應提供專業投資人及OSU客戶相關契約審閱期間，其審閱期間不得低於三日。", 58)
    d.text(352, f"本產品說明書刊印日期：{s.date(s.trade_date)}")

    d.chapter("一", "商品基本資料")
    titles = {
        1: f"商品名稱：{s.name_zh}",
        2: f"商品風險程度：{WARNING_CH1}",
        3: f"保證機構名稱及其長期債務信用評等：{GUARANTOR['ch1']}，截至本中文產品說明書刊印日期，長期債務信用評等不核對。",
        4: "商品之發行評等：無",
        5: "計價幣別：",
        8: "主要給付項目及其計算方式：",
        9: "連結標的資產／及其相對權重、與投資績效之關連事情：",
        12: "商品年期、發行日、到期日及其他依個別商品性質而定之日期：",
        15: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之平均年化報酬率：",
        26: "ISIN 代碼：XS1999900001",
    }
    for n in range(1, 27):
        d.article(n, titles.get(n, "範本說明。"))
        if n == 5:
            d.text(194, unit)
            d.sub(1, f"每單位面額：{s.amount(s.denom)}")
            d.sub(2, "發行價格：每單位面額的100%")
        elif n == 8:
            _article8(d, s, unit, worst_of)
        elif n == 9:
            d.sub(1, "連結標的資產：")
            for t in ("連結標的(k)", "連結標的資產", "彭博代碼", "交易所"):
                d.cell(70, t)
            for i, u in enumerate(s.underlyings, 1):
                if s.count > 1:
                    d.cell(86, f"k={i}")
                d.cell(165, u.name)
                d.cell(320, u.ticker)
                d.cell(448, u.exchange)
            d.text(
                50,
                "（下稱每一「連結標的」或各「連結標的」，合稱「一籃子連結標的」）"
                if s.count > 1
                else "（下稱「連結標的」）",
            )
        elif n == 12:
            _dates(d, s, ends, payments)
        elif n == 15:
            d.sub(1, "投資收益計算方法：請參閱上述第一章第8項。")
            d.sub(2, "本金虧損之機率：本商品並無本金保障。")
            d.sub(3, "以情境分析解說最大可能獲利、損失及其他狀況之平均年化報酬率：")
            d.text(71, "假設：")
            d.text(86, "發行機構並無發生提前終止事件並履行本商品的所有義務且投資人並未申請提前贖回。")
            d.text(86, f"總投資金額 = {s.units}單位面額 = {s.amount(int(s.total))}")
            d.text(86, f"天期：{s.months(s.tenor)}")
            d.text(86, f"配息率 = {s.monthly} %")
            _price_table(d, s, "art15.table", reprint=True)
            _scenarios(d, s)

    d.chapter("二", "相關機構事業概況")
    d.article(1, "發行機構")
    d.sub(1, f"事業名稱：{ISSUER}")
    d.sub(2, "設立日期：2006年11月16日")
    d.sub(3, "營業所在地：1 Fictional Street, Luxembourg")
    d.article(2, "保證機構")
    d.sub(1, f"事業名稱：{GUARANTOR['ch2']}")
    d.sub(2, "設立日期：1864年5月4日")
    d.article(3, "總代理人、計算代理機構(Calculation Agent)、受託或銷售機構、保管機構及其他相關機構")
    d.sub(1, "總代理人：")
    d.text(71, f"‧ 事業名稱：{AGENT['ch2']}")
    d.text(71, "‧ 設立日期：1980年8月26日")
    d.text(71, "‧ 營業所在地：台北市信義區虛構路1號")
    d.text(71, f"‧ 負責人姓名：{s.agent_head}")
    d.sub(2, "計算代理機構：")
    d.text(71, f"‧ 事業名稱：{GUARANTOR['ch2']}。同保證機構，細節請見上述第2項。")
    d.sub(3, "財務代理、交換代理及付款代理：")
    d.text(71, "‧ 事業名稱：Fictional Agent Luxembourg")
    d.sub(4, "受託或銷售機構：")
    d.text(71, f"‧ 事業名稱：{DIST_SHORT}")
    d.text(71, "設立日期：2000年11月20日")
    d.text(71, f"營業所在地：{DIST['address']}")
    d.text(71, f"負責人姓名：{DIST['chairman']}")
    d.sub(5, "結算機構：")
    d.text(71, "‧ 事業名稱：Fictional Clearing S.A.")
    d.article(4, "交易架構說明：請見下一頁之圖表說明。")
    d.article(5, "利害關係人揭露：範本說明。")

    d.chapter("三", "商品風險揭露")
    d.article(1, "基本風險資訊：範本說明。")
    d.article(2, "個別商品風險資訊：範本說明。")
    d.article(3, "信用連結：不適用。")
    d.article(4, "依境外結構型商品管理規則第二十三條第一項：範本說明。")
    d.article(5, "本商品之投資風險警語：")
    d.text(28, "一、" + WARNING, 58)
    d.text(28, "二、本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。", 58)
    d.text(
        28, "七、本商品的條款和條件之準據法為英國法。本商品係依境外結構型商品管理規則規定，於中華民國境內受託買賣。", 58
    )
    d.article(6, "金融總會規定應行揭露事項：截至本產品說明書刊印日，無。")
    d.article(7, "其他為保護公益及投資人之必要應揭露事項：截至本產品說明書刊印日，無。")

    d.chapter("四", "一般交易事項")
    d.article(1, "商品開始受理申購、開始受理贖回日期及後續受理贖回日期，每營業日受理申購、贖回申請截止時間：")
    d.text(71, f"• 商品開始受理申購日期：自{s.date(s.trade_date)}起")
    d.text(71, "• 開始受理贖回日期：發行日後第一個營業日")
    d.article(2, "投資人應負擔的各項費用及金額或計算基準之表列：")
    d.text(28, "與本商品相關之費用：")
    w.need(220)
    for t in ("費用項目", "費率 (百分比)", "收取時點", "收取方式", "收取人"):
        d.cell(70, t)
    for label, cells in [
        ("申購費用", ("申購價金的", "0%~5%", "投資人向受託或銷售機構申購時")),
        ("提前贖回費用", ("投資人提", "前贖回價金", "的0%~5%", "投資人向受託或銷售機構提前贖回時")),
        ("管理費用（信託管理費或管銷費用）", ("無", "不適用")),
        (
            "分銷費用（如屬發行機構或發行人給予受託或銷售機構之報酬、費用、折讓等各項利益應單獨列示）",
            ("申購價金的", "0%~5%", "於本商品發行時一次給付"),
        ),
        ("保費費用", ("不適用", "不適用")),
    ]:
        d.cell(30, label)
        for t in cells:
            d.cell(153, t)
    d.article(3, "商品交易架構：本商品由發行機構發行、保證機構保證。")
    d.article(4, "最低申購金額及最低加購金額：")
    d.text(50, f"最低申購金額：{s.amount(s.denom * MULTIPLES['minimum_subscription'].get(s.ccy, 1))}")
    d.text(50, f"最低加購金額：{s.amount(s.denom * MULTIPLES['minimum_additional'])}")
    d.article(5, "申購金額之計算：每單位面額×申購單位數×100%")
    d.article(6, "申購手續及資金給付方式：範本說明。")
    d.article(7, "發行不成立之情形：")
    d.sub(1, f"總申購金額低於{s.amount(s.denom * 30)}、不利之市場情況；")
    d.article(
        8,
        f"最低贖回金額或單位數：{s.amount(s.denom * MULTIPLES['minimum_redemption'])}（即1單位）。本商品必須以面額倍數之整數贖回，不得分拆。",
    )
    for n in range(9, 18):
        d.article(n, "範本說明。")

    d.chapter("五", "特別記載事項")
    d.text(28, "一、依境外結構型商品管理規則第十四條第二項規定，受託或銷售機構應製作交易確認書予投資人。", 50)
    d.text(28, "二、投資人之申購及贖回，須經境外發行機構確認後始生效力。", 50)
    d.text(28, "三、其他依主管機關規定應於中文產品說明書記載之事項：", 50)
    d.text(51, "(i) 本產品說明書係根據發行機構2030年01月01日之基本公開說明書以及其相關補充文件作成。", 71)
    require_used(edits, w.used)
    return w.save(path)


def _article8(d: _Doc, s: Spec, unit: str, worst_of: str) -> None:
    kind = "自動提前出場贖回金額或到期贖回金額" if s.autocall else "到期贖回金額"
    d.text(50, f"主要給付項目為配息金額及{kind}：")
    d.text(68, f"於配息日，發行機構將依下列計算公式以{unit}為計價單位給付：")
    if s.autocall and s.ko_obs == "D":
        d.text(
            68,
            f"配息金額＝每單位面額×配息率 , 惟若自動提前出場事件發生於第{s.first_callable + 1}-{s.tenor}個配息週期之任一預定交易日，該配息金額將依該配息週期起始日至發生該自動提前出場事件之日（含）且為預定交易日之總日數，除以該週期內為預定交易日之總日數按比例計算之。",
        )
    else:
        d.text(68, "配息金額＝每單位面額×配息率")
    d.text(68, f"配息率 ＝{s.monthly}% (年率 {s.annual:.4f}%)")
    if s.autocall:
        d.text(68, f"於自動提前出場日，發行機構將依下列計算公式以 {unit}為計價單位給付：")
        d.text(68, "自動提前出場贖回金額＝每單位面額×100%")
        obs = "記憶事件觀察期間" if s.memory else "自動提前出場事件觀察期間"
        if s.ko_obs == "D":
            if s.memory:
                d.text(
                    68,
                    "自動提前出場事件：若於記憶事件觀察期間內之任一預定交易日，所有連結標的發生記憶事件成為自動提前出場標的，則視為於該日發生自動提前出場事件。",
                )
                d.text(
                    68,
                    "記憶事件：任何連結標的於記憶事件觀察期間任一預定交易日之收盤價大於或等於自動提前出場價，則該連結標的發生記憶事件，並成為自動提前出場標的。",
                )
            else:
                d.text(
                    68,
                    f"自動提前出場事件：若於自動提前出場事件觀察期間內之任一預定交易日，{'當日最差表現之連結標的' if s.count > 1 else '連結標的'}收盤價大於或等於自動提前出場價，則視為於該日發生自動提前出場事件。",
                )
            d.text(
                68,
                f"{obs}：自{s.date(s.obs_start)}（含）起至最終評價日（含）止之每一個預定交易日（應該視為評價日並受限於“連結標的中斷日之後果”條款(定義如後述)）。",
            )
        else:
            rng = f"(i={s.first_callable}-{s.tenor})"
            if s.memory:
                d.text(
                    68,
                    f"自動提前出場事件：若於任一評價日i {rng}，所有連結標的發生記憶事件成為自動提前出場標的，則視為於該日發生自動提前出場事件。",
                )
                d.text(
                    68,
                    f"記憶事件：任何連結標的於任一評價日i {rng}收盤價大於或等於自動提前出場價，則該連結標的發生記憶事件，並成為自動提前出場標的。",
                )
            else:
                d.text(
                    68,
                    f"自動提前出場事件：若於任一評價日i {rng}，{'當日最差表現之連結標的' if s.count > 1 else '連結標的'}收盤價大於或等於自動提前出場價，則視為於該日發生自動提前出場事件。",
                )
        d.text(68, f"自動提前出場價：連結標的之期初價格的{s.ko:.2f}%")
    d.text(68, f"於到期日，發行機構將依下列計算公式以 {unit}為計價單位給付：")
    lead = "若不曾發生任何自動提前出場事件，" if s.autocall else ""
    if s.ki in ("none", "AM"):
        barrier = "執行價" if s.ki == "none" else "下限價"
        d.text(68, f"到期贖回金額：{lead}發行機構將於到期日將依下列 (1) 或(2)方式贖回：")
        d.sub(1, f"若於最終評價日{worst_of}收盤價大於或等於{barrier}，到期贖回金額＝每單位面額×100%", 72)
        d.sub(
            2,
            f"若於最終評價日{worst_of}收盤價小於{barrier}，於到期日將實體交付該連結標的之應實際交付數並支付剩餘金額。",
            72,
        )
    else:
        d.text(68, f"到期贖回金額：{lead}發行機構將於到期日將依下列 (1)、(2) 或(3)方式贖回：")
        if s.ki == "D":
            obs1 = "訂價日（含）起至最終評價日（含）止之每一個預定交易日（「觀察期間」），每日"
            obs2 = "觀察期間內之任一預定交易日，當日"
        else:
            obs1 = f"每一個評價日i (i=1-{s.tenor})，每日"
            obs2 = f"任一評價日i (i=1-{s.tenor})，每日"
        d.sub(1, f"若於{obs1}{worst_of}收盤價不曾小於下限價，到期贖回金額＝每單位面額×100%", 72)
        d.sub(
            2,
            f"若於{obs2}{worst_of}收盤價小於下限價，且於最終評價日{worst_of}收盤價大於或等於執行價，到期贖回金額＝每單位面額×100%",
            72,
        )
        d.sub(
            3,
            f"若於{obs2}{worst_of}收盤價小於下限價，且於最終評價日{worst_of}收盤價小於執行價，於到期日將實體交付該連結標的之應實際交付數並支付剩餘金額。",
            72,
        )
    d.text(86, "應實際交付數 = 數額向下調整至最接近的整數位或交易單位")
    d.text(86, f"數額 = 每單位面額 / 於最終評價日{worst_of}執行價，並無條件進入至小數點第四位。")
    d.text(
        86,
        f"倘計算數額之結果有畸零數，則該畸零數應乘以於最終評價日{worst_of}收盤價，並無條件進入至小數點第二位，以現金支付（「剩餘金額」）。",
    )
    if s.fx is not None:
        d.text(50, "最終匯率定價：於最終評價日東京時間15:00於彭博頁面“BFIX”公佈之日圓兌商品幣別之匯率水準。")
    d.text(50, "收盤價：就股票及一預定交易日而言，交易所於當日公佈有關連結標的之官方收盤價。")
    if s.count > 1:
        ks = "、 ".join(str(i) for i in range(1, s.count + 1))
        d.text(
            50,
            f"最差表現之連結標的：指於一預定交易日，連結標的(k)(k={ks}) 表現最差者。惟若有兩個或以上之連結標的表現一樣，由計算代理機構全權決定何者為最差表現之連結標的。",
        )
    d.text(50, "連結標的表現：（連結標的之收盤價／連結標的期初價格）-1")
    d.text(50, "期初價格：連結標的於訂價日之價格")
    if s.ki != "none":
        d.text(50, f"下限價：連結標的期初價格的{s.ki_pct:.2f}%")
    d.text(50, f"執行價：連結標的期初價格的{s.strike:.2f}%")
    _price_table(d, s, "art8.table", reprint=False)


def _dates(d: _Doc, s: Spec, ends, payments) -> None:
    d.sub(1, f"商品年期：{s.months(s.tenor)}")
    d.sub(2, f"訂價日：{s.date(s.trade_date)}")
    d.sub(3, f"發行日／交割日：{s.date(s.issue_date)}")
    d.sub(4, "配息日：請參考下表相對應之日期（惟應遵守營業日順延慣例）")
    n = 5
    if s.autocall:
        if s.ko_obs == "D":
            d.sub(
                n,
                "自動提前出場日：為發生自動提前出場事件後第3個營業日（惟應遵守營業日順延慣例）（下稱「自動提前出場日」）",
            )
            d.sub(
                n + 1, "最終評價日：請參考下表相對應之日期；如當日非所有預定交易日，次一所有連結標的之預定交易日為之。"
            )
        else:
            d.sub(n, "自動提前出場日：請參考下表相對應之日期（惟應遵守營業日順延慣例）（下稱「自動提前出場日」）")
            d.sub(
                n + 1,
                "評價日、最終評價日：請參考下表相對應之日期；如當日非所有連結標的之預定交易日，次一所有連結標的之預定交易日為之。",
            )
        n += 2
    else:
        d.sub(
            n,
            "評價日、最終評價日：請參考下表相對應之日期；如當日非所有連結標的之預定交易日，次一所有連結標的之預定交易日為之。",
        )
        n += 1
    d.sub(n, f"到期日：{s.date(s.maturity_date)}（惟應遵守營業日順延慣例）")
    d.sub(n + 1, "預定交易日：指就連結標的而言，各交易所原定在其各自之正常交易時段開盤之任何日子。")
    if s.autocall and s.ko_obs == "D":
        d.sub(n + 2, "配息週期起始日、配息週期終止日與配息日：受限於自動提前出場事件，目前預定之日期如下:")
        header = ["配息週期起始日（不含）", "配息週期終止日（含）", "配息日"]
    elif s.autocall:
        d.sub(n + 2, "評價日與配息日或自動提前出場日：受限於自動提前出場事件，目前預定之日期如下：")
        header = ["評價日(i)", "配息日或自動提前出場日"]
    else:
        header = ["評價日(i)", "配息日"]
    d.section = "art12.table"
    d.cell(85, "配息週期(i)")
    for t in header:
        d.cell(160, t)
    k = s.first_callable
    for j in range(1, s.tenor + 1):
        d.cell(102, f"i={j}")
        if s.autocall and s.ko_obs == "D":
            if j <= k:
                cells = ["不適用", "不適用"]
            else:
                cells = [s.date(ends[j - 2]), s.date(ends[j - 1])]
        elif s.autocall:
            cells = [s.date(ends[j - 1]) if j >= k else "不適用"]
        else:
            cells = [s.date(ends[j - 1]) if j == s.tenor else "不適用"]
        for c in cells:
            d.cell(200, c)
        if j == s.tenor:
            d.cell(316, "（最終評價日）")
        d.cell(450, s.date(payments[j - 1]))
        if j == s.tenor:
            d.cell(463, "（到期日）")
    d.section = "art12"
    if s.autocall:
        d.text(71, "為避免疑慮，若發生自動提前出場事件，自動提前出場日將視為最後一個配息日。")
    d.text(50, "營業日：紐約之商業銀行和外匯市場於結算款項並為一般業務營業的日子。")


def check(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋與 `spec` 一致的參考條件表一列，經批量入口核對，
    回傳該說明書的 CheckReport。`config` 換核對設定（例：改過的審查標準）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **overrides)])
    outcome = check_all(sheet, [pdf], config)
    return next(i.report for i in outcome.items if i.term_sheet == pdf)
