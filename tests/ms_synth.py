"""MS 合成說明書與參考條件表列（docs/templates/ms-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：封面「N. 標籤：值」、章名「第X章、」、第一章條號「N. 標題：」與內文同一行、子項「(n)」，
表格一格一行。`Spec.replace` 依段落替換文字製造錯誤（段落代號見 `_Doc.section`），`breaks` 在表格某列之後強制換頁。
`build_pdf` 另寫一份同商品的投資人須知（docs/templates/ms-zh-iis.md）：`Spec.iis_replace` 依投資人須知的段落代號
（cover、warn、org、summary、coupon、redeem、risk、fees、rest）替換文字，`iis_pages`／`iis_page_total` 改頁數與頁底總頁數。
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import fitz

from harness import CONFIG, REVIEW_STANDARD, check_all, iis_path
from pdf_writer import FONT, PdfWriter, zh_date
from reference_synth import build_reference_sheet, make_row

STD = tomllib.loads(REVIEW_STANDARD.read_text(encoding="utf-8"))
NAME = STD["product_name"]["ms"]
WARNING = STD["risk"]["fixed_warning_by_issuer"]["ms"]
WARNING_CH1 = STD["risk"]["fixed_warning_openings"]["ms"][0] + WARNING[WARNING.index("。") + 1 :] + "。"
ISSUER = STD["issuer_name"]["ms"]
DIST = STD["distributor"]
TRADE, ISSUE, APPROVAL = dt.date(2030, 1, 7), dt.date(2030, 1, 14), dt.date(2026, 6, 11)
DENOM = 10000
STRIKE, KI, KO = Decimal("70.00"), Decimal("60.00"), Decimal("100.00")
NUMERIC = set("0123456789,.")


def _weekday(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def money(x: Decimal | int) -> str:
    return f"{Decimal(x):,.2f}"


@dataclass
class Spec:
    code: str = "147199990001"
    obs: str = "D"  # KO 觀察方式：D 每日／P 每期定價日
    memory: bool = True
    ki: str = "AM"  # none／AM／D／P
    count: int = 2  # 標的數
    tenor: int = 4
    non_call: int = 1  # 第一個可提前出場期
    annual: Decimal = Decimal("12")  # 參考條件表年利率；說明書月配息率 = 年利率 ÷ 12
    trustee_code: str | None = None  # 受託機構商品代號；None 同商品代號，"" 空白
    ko_column: bool | None = None  # 價格表有無自動提前出場價欄；None 依 Non-Call < 天期
    replace: list[tuple[str, str, str]] = field(default_factory=list)  # (段落代號前綴, 原文, 新文字)
    breaks: dict[str, int] = field(default_factory=dict)  # 表格（date／price）第 n 列之後換頁
    iis_replace: list[tuple[str, str, str]] = field(default_factory=list)  # 投資人須知的 (段落代號前綴, 原文, 新文字)
    iis_pages: int = 4  # 投資人須知頁數（多的頁只有頁底）
    iis_page_total: int | None = None  # 投資人須知頁底「共 M頁」的 M；None 同實際頁數

    @property
    def ends(self) -> list[dt.date]:
        """各期配息週期終止日（P 型為定價日）：每月 14 日，遇週末順延。"""
        out = []
        for j in range(1, self.tenor + 1):
            m = 1 + j
            out.append(_weekday(dt.date(2030 + (m - 1) // 12, (m - 1) % 12 + 1, 14)))
        return out

    @property
    def payments(self) -> list[dt.date]:
        return [_weekday(d + dt.timedelta(days=3)) for d in self.ends]

    @property
    def starts(self) -> list[dt.date]:
        return [ISSUE] + [_weekday(d + dt.timedelta(days=1)) for d in self.ends[:-1]]

    @property
    def tickers(self) -> list[str]:
        return [f"ZZ{i + 1} UW" for i in range(self.count)]

    @property
    def initials(self) -> list[Decimal]:
        return [Decimal(100 * (i + 1)) for i in range(self.count)]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Decimal("0.0001"), ROUND_HALF_UP)

    @property
    def coupon(self) -> Decimal:
        return (DENOM * self.monthly / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)

    @property
    def has_ko(self) -> bool:
        return self.non_call < self.tenor if self.ko_column is None else self.ko_column

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(
            tenor=self.tenor,
            ccy_zh="美元",
            underlying_zh=NAME["basket_zh"] if self.count > 1 else NAME["single_zh"],
            memory_zh=NAME["memory_zh"] if self.memory else "",
        )

    @property
    def name_en(self) -> str:
        return NAME["en"].format(
            tenor=self.tenor, ccy="USD", underlying_en=NAME["basket_en"] if self.count > 1 else NAME["single_en"]
        )


def underlying_name(i: int) -> str:
    """第 i 檔標的的名稱（說明書第 11 項標的表與投資人須知連結標的資產同一寫法）。"""
    return f"虛構標的{i}公司(FICTIONAL {i})"


def price(initial: Decimal, pct: Decimal) -> Decimal:
    return (initial * pct / 100).quantize(Decimal("0.0001"), ROUND_HALF_UP)


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用 Spec.replace，超過寬度時在非數字處換行。"""

    def __init__(self, s: Spec, replace: list[tuple[str, str, str]] | None = None):
        self.s, self.w, self.section = s, PdfWriter(), "cover"
        self.replace = s.replace if replace is None else replace

    def edit(self, text: str) -> str:
        for sec, old, new in self.replace:
            if self.section.startswith(sec):
                text = text.replace(old, new)
        return text

    def text(self, x: float, text: str, cont: float | None = None) -> None:
        text = self.edit(text)
        if not text:
            return
        width, cur, used, first = 520 - (x - 36), "", 0.0, True
        for ch in text:
            cw = fitz.get_text_length(ch, fontname="helv", fontsize=10) if ord(ch) < 256 else 10
            if used + cw > width and cur and not (cur[-1] in NUMERIC and ch in NUMERIC):
                self.w.line(x if first else (cont if cont is not None else x), cur)
                cur, used, first = "", 0.0, False
            cur += ch
            used += cw
        self.w.line(x if first else (cont if cont is not None else x), cur)

    def cell(self, x: float, text: str) -> None:
        text = self.edit(text)
        if text:
            self.w.line(x, text, gap=14)

    def chapter(self, zh: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{zh}"
        self.w.line(36, f"第{zh}章、{name}", gap=16)

    def article(self, n: int, text: str) -> None:
        self.section = f"art{n}"
        self.text(54, f"{n}. {text}", 72)

    def sub(self, n: int, text: str) -> None:
        self.text(72, f"({n}) {text}", 72)


def _price_table(d: _Doc, s: Spec, section: str) -> None:
    d.section = section
    for t in ["連結", "標的", "(k)", "彭博代碼", "(Bloomberg)", "期初價格", "執行價 （期", "初價格的", f"{STRIKE}%）"]:
        d.cell(96, t)
    if s.ki != "none":
        for t in ["下限價格", "（期初價格的", f"{KI}%）"]:
            d.cell(371, t)
    if s.has_ko:
        for t in ["自動提前出場", "價 （期初價", "格的", f"{KO}%）"]:
            d.cell(437, t)
    for i, (ticker, initial) in enumerate(zip(s.tickers, s.initials, strict=True), 1):
        d.cell(96, f"k={i}")
        d.cell(141, ticker)
        d.cell(216, f"{initial:.4f}")
        d.cell(293, f"{price(initial, STRIKE):.4f}")
        if s.ki != "none":
            d.cell(371, f"{price(initial, KI):.4f}")
        if s.has_ko:
            d.cell(448, f"{price(initial, KO):.4f}")
        if section == "art16.table" and s.breaks.get("price") == i:
            d.w.new_page()


def _scenarios(d: _Doc, s: Spec) -> None:
    unit, a, n = "美元", s.coupon, s.tenor
    profit = []
    if s.non_call < n:
        profit.append(("較佳情況 A" if s.non_call + 1 < n else "較佳情況", s.non_call))
        if s.non_call + 1 < n:
            profit.append(("較佳情況B", s.non_call + 1))
    profit.append(("一般情況" if s.non_call < n else "較佳情況", n))
    zh = "一二三四五六"
    period = "配息週期終止日" if s.obs == "D" else "定價日"
    for idx, (title, k) in enumerate(profit):
        d.section = f"scen.{zh[idx]}"
        d.text(72, f"情境{zh[idx]}：{title}")
        if k < n:
            d.text(
                72,
                f"假設在第{k} 個{period}，所有連結標的之收盤價皆大於或等於其自動提前出場價，即發生自動提前出場事件：",
            )
        else:
            span = "配息觀察期間" if s.obs == "D" else "定價日"
            d.text(72, f"假設在第1 至第{n} 個{span}，不曾發生任何自動提前出場事件，到期日以現金結算收益：")
        d.text(72, "每單位商品面額×固定配息率")
        d.text(72, f"= 每單位商品面額×{s.monthly}%={money(DENOM)} {unit}×{s.monthly}%={money(a)} {unit}")
        if s.obs == "D" and 1 < k < n:
            d.text(
                72, f"第{k} 個配息觀察期間配息觀察日總數 ={money(DENOM)} {unit}×{s.monthly}%×100% ={money(a)} {unit}"
            )
        times = f"×{k}" if k > 1 else ""
        d.text(72, f"={money(DENOM)} {unit}+{money(a)} {unit}{times}-{money(DENOM)} {unit}={money(a * k)} {unit}")
        d.text(72, f"以簡單平均計算報酬率之方式以計算年化報酬率為{s.annual:.2f}%（四捨五入至百分位後第二位)")
    worse, fail = zh[len(profit)], zh[len(profit) + 1]
    d.section = f"scen.{worse}"
    d.text(72, f"情境{worse}：較差情況")
    d.text(72, "假設在所有配息觀察期間內，不曾發生任何自動提前出場事件。")
    strike = price(s.initials[0], STRIKE)
    d.text(
        72,
        f"於期末定價日籃子中表現最差之連結標的（{s.tickers[0]})的收盤價=42.0000 {unit}小於其執行價={strike:.4f} {unit}，"
        "到期日以實物交割結算收益：",
    )
    d.text(72, f"= 每單位商品面額×{s.monthly}%={money(DENOM)} {unit}×{s.monthly}%={money(a)} {unit}")
    d.text(72, f"=42.0000 {unit}×142 股/1+36.00 {unit}=6,000.00 {unit}")
    pnl = a * n + 6000 - DENOM
    d.text(72, f"= {money(a)} {unit}×{n}+6,000.00 {unit}-{money(DENOM)} {unit}={money(pnl)} {unit}")
    d.text(72, "以簡單平均計算報酬率之方式以計算年化報酬率為-40.00%（四捨五入至百分位後第二位)")
    d.section = f"scen.{fail}"
    d.text(72, f"情境{fail}：發行機構無法履約")
    d.text(72, "投資人要承擔發行機構之無擔保信用風險(報酬率則為-100%)。")


def build_pdf(path: Path, s: Spec, *, iis: bool = True) -> Path:
    """合成 MS 說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時旁邊另寫一份投資人須知（MS 為未支援上手）。"""
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s)
    d = _Doc(s)
    w = d.w
    ends, payments, starts = s.ends, s.payments, s.starts
    trustee = s.code if s.trustee_code is None else s.trustee_code
    w.line(258, "中文產品說明書", gap=16)
    for n, t in enumerate(
        [
            f"商品代號：{s.code}",
            f"受託機構商品代號：{trustee}",
            "國際證券編碼ISIN：XS1999900001",
            f"商品中文名稱：{s.name_zh}",
            f"商品英文名稱：{s.name_en}",
            "商品種類："
            + ("股票與/或指數股票型基金連結結構型債券" if s.count > 1 else "股票或指數股票型基金連結結構型債券"),
            "發行機構註冊地：英國",
            "商品註冊地：專業投資人與OSU 客戶不適用",
            "商品計價幣別：美元 (USD)",
            f"發行機構：{ISSUER}",
            "發行機構之地址： 25 Fictional Square, London",
            f"報價機構名稱及地址：{ISSUER}",
            "總代理人之名稱、電話及地址：台灣虛構證券股份有限公司，電話： (02)0000-0000",
            f"受託機構之名稱、電話及地址：{DIST['name']}, 電話: +886 2 5556 1313, 地址: {DIST['address']}(營業活動所在地)",
            f"受託機構審查通過之日期及文號：{zh_date(APPROVAL)}",
            "本商品之投資風險警語：",
        ],
        1,
    ):
        d.text(36, f"{n}. {t}", 54)
    d.text(54, "1) " + WARNING, 71)
    d.text(54, "2) 本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。", 71)
    d.text(72, f"中文產品說明書刊印日期：{zh_date(TRADE)}")

    d.chapter("一", "商品基本資料")
    short = s.name_zh.replace("(下稱「本商品」)", "")
    titles = {
        1: f"商品中文名稱：{short}",
        2: WARNING_CH1,
        3: f"發行機構名稱及其長期債務信用評等：本商品發行機構為{ISSUER.split('(')[0]}，截至刊印日期之信用評等不核對。",
        5: "計價幣別：美元 (USD)",
        6: f"每單位商品面額：美元 {money(DENOM)} 元",
        7: "發行價格：商品面額之100%",
        11: "連結標的資產，及其相對權重、與投資績效之關連情形：",
        14: "商品年期、發行日、到期日及其他依個別商品性質而定之日期：",
        15: "配息資料及其計算公式：",
        16: "到期贖回計算公式、最低保證贖回率及參與率：",
        17: "自動提前出場給付：",
        18: "投資收益計算方法、本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
    }
    for n in range(1, 30):
        d.article(n, titles.get(n, "範本說明。"))
        if n == 11:
            d.text(72, "連結標的資產：")
            if s.count > 1:  # 多標的另有列號欄
                d.cell(40, "連結標的")
            d.cell(85, "股票 / 指數股票型基金")
            d.cell(250, "彭博代碼(Bloomberg)")
            d.cell(441, "交易所")
            for i, ticker in enumerate(s.tickers, 1):
                if s.count > 1:
                    d.cell(44, str(i))
                d.cell(66, underlying_name(i))
                d.cell(283, ticker)
                d.cell(419, "那斯達克證交所")
            d.text(72, "相對權重：不適用。")
        elif n == 14:
            d.sub(1, f"商品年期：{s.tenor} 個月期")
            d.sub(2, f"交易日：{zh_date(TRADE)}")
            d.sub(3, f"發行日：{zh_date(ISSUE)}")
            d.sub(4, f"到期日：{zh_date(payments[-1])}。到期日為期末定價日後至少第三個營業日之日。")
            d.sub(5, f"期末定價日*：{zh_date(ends[-1])}，如當日非預定交易日，次一預定交易日為之。")
            if s.obs == "D":
                d.sub(6, "配息週期起始日、配息週期終止日與配息日：依下表所示")
                header = ["配息觀察期間(j)", "配息週期起始日（含）", "配息週期終止日（含）", "配息日"]
            else:
                d.sub(6, "定價日、配息日與自動提前出場日：依下表所示")
                header = ["(j)", "定價日(j)", "配息日", "自動提前出場日"]
            d.section = "art14.table"
            for x, t in zip((62, 175, 305, 473), header, strict=True):
                d.cell(x, t)
            for j in range(1, s.tenor + 1):
                if s.obs == "D":
                    cells = [starts[j - 1], ends[j - 1], payments[j - 1]]
                else:
                    # 自動提前出場日：第 Non-Call 期起 = 配息日；Non-Call = 天期時全部「無」
                    auto = payments[j - 1] if s.non_call <= j and s.non_call < s.tenor else None
                    cells = [ends[j - 1], payments[j - 1], auto]
                d.cell(98, str(j))
                for x, c in zip((188, 318, 450), cells, strict=True):
                    d.cell(x, zh_date(c) if c else "無")
                if s.breaks.get("date") == j:
                    w.new_page()
            d.section = "art14"
            d.text(72, "如配息日非營業日，則按經調整順延制之營業日慣例調整(但配息金額不變)。")
            d.sub(7, "次級市場投資人提前贖回日：自開始受理贖回日期至結束受理贖回日期止。")
        elif n == 15:
            d.sub(1, "配息頻率：每月，金額將根據以下條款所計算")
            if s.obs == "D":
                d.sub(
                    2,
                    f"於每月配息日，每單位配息金額 = 每單位商品面額 × 固定配息率 ({s.monthly}%) ，但如發生自動提前出場事件：",
                )
                d.text(72, f"每單位配息金額(j) = 每單位商品面額 ×{{{s.monthly}%×n(j)/N(j)}}")
                d.text(72, f"j 係為2 至{s.tenor} 的數字，代表第2 至{s.tenor} 個配息觀察期間")
            else:
                d.sub(2, "於每月配息日，發行機構將依下列計算公式給付配息金額：")
                d.text(72, f"每單位配息金額(j) = 每單位商品面額 ×固定配息率 ({s.monthly}%)")
                d.text(72, f"j 係為1 至{s.tenor} 的數字，代表第1 至{s.tenor} 個觀察期間")
        elif n == 16:
            if s.ki == "none":
                d.text(108, "(a)現金交割：若於期末定價日時籃子中表現最差的連結標的之收盤價高於或等於其執行價：")
                d.text(108, "(b)實物交割：若於期末定價日時籃子中表現最差的連結標的之收盤價低於其執行價，以實物交割。")
            else:
                d.text(
                    108, "(a)現金交割：若於期末定價日時觸及下限事件從未發生，發行機構將於到期日支付到期贖回金額；否則"
                )
                d.text(108, "(b)實物交割：若於期末定價日時觸及下限事件發生，以實物交割。")
                when = {
                    "AM": "期末定價日",
                    "D": "交易日（含）至期末定價日（含）間的任一共同預定交易日",
                    "P": "任一配息週期終止日(含期末定價日)",
                }[s.ki]
                d.text(
                    72, f"觸及下限事件：若在{when}，籃子中任一連結標的之收盤價低於其下限價格，則觸及下限事件視同發生。"
                )
            names = (
                ["期初價格", "執行價"]
                + (["下限價格"] if s.ki != "none" else [])
                + (["自動提前出場價"] if s.has_ko else [])
            )
            d.text(72, "、".join(names[:-1]) + "及" + names[-1] + "： 依下表所示")
            _price_table(d, s, "art16.table")
            d.section = "art16"
            d.sub(1, "最低保證贖回率：本商品不適用。")
            d.sub(2, "參與率：不適用。")
        elif n == 17:
            k, final = s.non_call, ends[-1]
            cond = "所有連結標的之收盤價皆等於或高於" if s.count > 1 else "該連結標的之收盤價大於或等於"
            if s.memory:
                d.text(
                    72,
                    "自動提前出場事件：若於記憶事件觀察日所有連結標的皆發生記憶事件成為自動提前出場標的，則發生自動提前出場事件。",
                )
                d.text(72, "記憶事件：任何連結標的於記憶事件觀察日之收盤價大於或等於其自動提前出場價，則發生記憶事件。")
                if s.obs == "D":
                    d.text(
                        72,
                        f"記憶事件觀察日：每日觀察，為自第{k} 個配息週期終止日（{zh_date(ends[k - 1])}）（包含）至"
                        f"期末定價日（{zh_date(final)}）（包含）的任一共同預定交易日。",
                    )
                else:
                    d.text(
                        72,
                        f"記憶事件觀察日：每一個定價日自第{k} 個定價日開始觀察，如當日非預定交易日，次一預定交易日為之。",
                    )
            elif s.obs == "D":
                d.text(72, f"自動提前出場事件：若於任一觀察日{cond}其自動提前出場價，則發生自動提前出場事件。")
                d.text(
                    72,
                    f"觀察日：每日觀察，為自第{k} 個配息週期終止日（{zh_date(ends[k - 1])}）（包含）至"
                    f"期末定價日（{zh_date(final)}）（包含）的任一預定交易日。",
                )
            else:
                d.text(
                    72, f"自動提前出場事件：自第{k} 個定價日（含）開始，若於任一定價日{cond}其自動提前出場價，則發生。"
                )
            d.text(72, "自動提前出場日：自動提前出場事件發生日後的第3 個營業日。")
        elif n == 18:
            d.sub(3, "以情境分析解說最大可能獲利、損失及其他狀況之平均年化報酬率：")
            d.text(72, "假設：")
            d.text(72, f"▪ 總投資金額 = 1 單位商品面額 = {money(DENOM)} 美元")
            d.text(72, f"▪ 年期：{s.tenor} 個月")
            d.text(72, f"▪ 月配息率={s.monthly}%")
            _price_table(d, s, "art18.table")
            _scenarios(d, s)

    d.chapter("二", "相關機構事業概況")
    d.text(54, "1. 發行機構")
    en = ISSUER.split("(")[1].rstrip(".)")
    d.text(72, f"(1) 事業名稱：{ISSUER.split('(')[0]}{en}，係依英格蘭及威爾士法律設立及存續之公司", 72)
    d.text(72, "(2) 設立日期：1900 年 1 月 1 日")
    d.text(54, "2. 保證機構：無")
    d.text(54, "3. 總代理人、決定代理機構、受託機構、保管機構及其他相關機構")
    for n, (who, name, addr, boss) in enumerate(
        [
            ("總代理人", "台灣虛構證券股份有限公司", "台北市虛構路1號", "虛構甲"),
            ("決定代理機構", ISSUER, "25 Fictional Square, London", "Fictional Person"),
            ("受託機構", DIST["name"], DIST["address"], DIST["chairman"]),
            ("保管機構", "虛構銀行倫敦分行", "1 Fictional Square, London", "Fictional Custodian"),
        ],
        1,
    ):
        d.text(72, f"({n}) {who}")
        d.text(72, f"◎ 事業名稱：{name}")
        d.text(72, "◎ 設立日期：2000 年11 月20 日")
        d.text(72, f"◎ 營業所在地：{addr}")
        d.text(72, f"◎ 負責人姓名：{boss}")
    d.text(54, "4. 交易架構說明：")

    d.chapter("三", "商品風險揭露")
    d.text(72, "4. 本商品之投資風險警語：")
    d.text(90, "1) " + WARNING, 108)
    d.text(90, "2) 本商品係複雜的金融商品。", 108)

    d.chapter("四", "一般交易事項")
    d.text(36, "1. 商品開始受理贖回日期及後續受理贖回日期，每營業日受理申購、贖回申請截止時間：")
    d.text(54, f"(1) 開始受理贖回日期：{zh_date(_weekday(ISSUE + dt.timedelta(days=1)))}")
    d.text(54, "(2) 結束受理贖回日期：期末定價日前三個營業日")
    d.text(36, "2. 投資人應負擔的各項費用及金額或計算基準之表列：")
    w.need(260)
    d.cell(103, "費用項目")
    d.cell(226, "費率 (百分")
    d.cell(310, "收取時點")
    for label, rate in [
        ("申購費用", "0%~5%"),
        ("提前贖回費用", "0%~5%"),
        ("管理費用（信託管理費或管銷", "無"),
        ("分銷費用（如屬發行機構或發", "0%~5%"),
        ("保費費用", "不適用"),
    ]:
        d.cell(42, label)
        d.cell(223, "價金的" if rate.endswith("%") else rate)
        if rate.endswith("%"):
            d.cell(223, rate)
    d.text(36, "3. 商品交易架構：參閱以上第二章。")
    d.text(
        36,
        f"4. 最低申購金額及最低加購金額：最低申購金額為 1 單位商品面額，即美元{DENOM:,} 元，並以美元"
        f"{money(DENOM)} 元(1 單位商品面額)為最低加購單位。",
    )
    d.text(36, "5. 申購價金之計算：申購價金＝投資人總申購單位數×每單位商品面額×發行價格(商品面額的 100%)+ 申購費用。")
    for n in (6, 7):
        d.text(36, f"{n}. 範本說明。")
    d.text(
        36,
        f"8. 最低贖回金額或單位數：最低贖回金額為1 單位商品面額，即美元{DENOM:,} 元，並以美元{money(DENOM)}"
        "元(1 單位商品面額)為累加贖回單位。",
    )
    d.chapter("五", "定義及其他條款")
    d.text(54, "1. 定義：範本說明。")
    d.chapter("六", "特別記載事項")
    d.text(54, "範本說明。")
    for i, page in enumerate(w.doc):
        page.insert_text((261, 818), f"第 {i + 1} 頁，共 {len(w.doc)}頁", fontname=FONT, fontsize=8)
    return w.save(path)


def build_iis_pdf(path: Path, s: Spec) -> Path:
    """MS 中文投資人須知（新版，docs/templates/ms-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁。"""
    d = _Doc(s, s.iis_replace)
    w = d.w
    ends, payments, k, final = s.ends, s.payments, s.non_call, s.ends[-1]
    dist, zh_issuer = DIST["name"], ISSUER.split("(")[0]
    kind = "股票與/或指數股票型基金連結結構型債券" if s.count > 1 else "股票或指數股票型基金連結結構型債券"
    w.line(130, "中文投資人須知(專業投資人/國際證券業務分公司受託買賣客戶)")
    d.text(18, f"商品中文名稱：{s.name_zh} 商品英文名稱：{s.name_en} (ISIN:XS1999900001)")
    d.text(18, f"商品種類：{kind}")
    d.section = "warn"
    d.text(
        18,
        f"本商品之投資風險警語：1) {WARNING}2) 本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。"
        f"3) 本商品並非存款，最大損失為全部本金及利息。4) 商品雖經{dist}審查，並不代表證實申請事項或保證該商品之價值，"
        f"且{dist}不負本商品投資盈虧之責。{dist}依法不得承諾擔保投資本金或最低收益率。5) 本商品持有期間如有保證配息"
        f"或保證保本率，係由{zh_issuer}（發行機構）保證，而非由{dist}所保證。6) 本中文產品說明書之內容如有虛偽或隱匿"
        f"之情事者，係由受託機構(即{dist})負責外，其餘內容應由總代理人台灣虛構證券股份有限公司依法負責或由發行機構"
        f"{zh_issuer}依法負責(為OSU 客戶受託買賣時)。7) 範本說明。8) 範本說明。9) {dist}應提供專業投資人及OSU 客戶"
        "相關契約審閱期間。10) 範本說明。11) 範本說明。12) 投資人應詳閱本中文產品說明書之內容。",
    )
    d.section = "org"
    d.text(18, "一、 相關機構：")
    d.text(
        18,
        f"1. 發行機構：{ISSUER}；營業所在地：25 Fictional Square, London。2. 總代理人：台灣虛構證券股份有限公司；"
        f"營業所在地：台北市虛構路1號。3. 受託機構：{dist}；營業所在地：{DIST['address']}",
    )
    d.section = "summary"
    d.text(18, "二、 境外結構型商品事項：")
    d.text(18, "1. 商品簡介：")
    label = (lambda i: f"連結標的{i}") if s.count > 1 else (lambda i: "連結標的")
    uls = " ".join(f"({i}) {label(i)}: {underlying_name(i)}" for i in range(1, s.count + 1))
    redeem_from = _weekday(ISSUE + dt.timedelta(days=1))
    for n, t in enumerate(
        [
            "受託對象：專業投資人及OSU 客戶",
            "與國外相當之交易條件：專業投資人及OSU 客戶不適用。",
            "本商品風險程度： RR4",
            f"發行機構之長期債務信用評等：本商品發行機構為{zh_issuer}，截至本投資人須知刊印日期，評等不核對。",
            "商品之發行評等：無",
            "計價幣別：美元 (USD)",
            "計價貨幣本金保本率：無。",
            "投資本金達成100%保本率之各項條件：不適用。",
            f"連結標的資產：{uls}",
            f"商品年期：{s.tenor} 個月期；交易日：{zh_date(TRADE)}；發行日: {zh_date(ISSUE)}；到期日："
            f"{zh_date(payments[-1])}；期末定價日：{zh_date(final)} ，如當日非預定交易日，次一預定交易日為之。",
            "配息： 於存續期間每月配息。",
            f"開始受理贖回日期：{zh_date(redeem_from)}",
            "後續受理贖回日期：範本說明。",
        ],
        1,
    ):
        d.text(18, f"{n}) {t}", 47)
    w.new_page()
    d.section = "coupon"
    d.text(18, "2. 收益分配事項：於每月配息日，發行機構將依下列計算公式以美元為計價單位給付配息金額：")
    if s.obs == "D":
        d.text(
            49,
            "若於相對應配息觀察期間內未發生自動提前出場事件，則每單位配息金額 = 每單位商品面額 × 固定配息率 "
            f"({s.monthly}%)，但如發生自動提前出場事件，每單位配息金額(j) = 每單位商品面額×{{{s.monthly}%×n(j)/N(j)}}。"
            f"j 係為2 至第{s.tenor} 的數字。",
        )
    else:
        d.text(49, f"每單位配息金額(j) = 每單位商品面額 × 固定配息率 ({s.monthly}%)。j 係為1 至第{s.tenor} 的數字。")
    d.section = "redeem"
    d.text(18, "3. 贖回價金之計算：")
    if s.ki == "none":
        d.text(
            18,
            "1) 到期贖回：(a)現金交割：若於期末定價日時籃子中表現最差的連結標的之收盤價高於或等於其執行價，"
            "支付到期贖回金額；否則(b)實物交割：若於期末定價日時籃子中表現最差的連結標的之收盤價低於其執行價，以實物交割。",
            47,
        )
    else:
        when = {
            "AM": "期末定價日",
            "D": "交易日（含）至期末定價日（含）間的任一共同預定交易日",
            "P": "任一配息週期終止日(含期末定價日)",
        }[s.ki]
        d.text(
            18,
            "1) 到期贖回：(a)現金交割：若於期末定價日時觸及下限事件未發生，支付到期贖回金額；否則(b)實物交割："
            f"若於期末定價日時觸及下限事件發生，以實物交割。「觸及下限事件」：若在{when}，籃子中任一連結標的之收盤價"
            "低於其下限價格，則觸及下限事件視同發生。",
            47,
        )
    plain = "所有連結標的之收盤價皆等於或高於" if s.count > 1 else "該連結標的之收盤價大於或等於"
    if s.obs == "D":
        observed = (
            f"觀察日：每日觀察，為自第{k} 個配息週期終止日（{zh_date(ends[k - 1])}）（包含）至期末定價日"
            f"（{zh_date(final)}）（包含）的任一" + ("共同預定交易日。" if s.memory else "預定交易日。")
        )
        if s.memory:
            ko = (
                "(1)自動提前出場事件：若於記憶事件觀察日所有連結標的皆發生記憶事件成為自動提前出場標的，"
                "則發生自動提前出場事件。(2)記憶事件：任何連結標的於記憶事件觀察日之收盤價大於或等於其自動提前出場價，"
                "則發生記憶事件。(3)記憶事件" + observed
            )
        else:
            ko = f"(1)自動提前出場事件：若於任一觀察日{plain}其自動提前出場價，則發生自動提前出場事件。(2)" + observed
    elif s.memory:
        ko = (
            "若於記憶事件觀察日所有連結標的皆發生記憶事件成為自動提前出場標的，則發生自動提前出場事件。"
            f"記憶事件觀察日：每一個定價日自第{k} 期定價日（含）開始觀察，如當日非預定交易日，次一預定交易日為之。"
        )
    else:
        plain = "所有連結標的皆等於或高於" if s.count > 1 else plain
        ko = f"自動提前出場事件：自第{k} 個定價日（含）開始，若於任一定價日{plain}其自動提前出場價，則視同發生。"
    d.text(18, "2) 自動提前出場給付：自動提前出場贖回金額＝持有之商品單位數×每單位商品面額×100%。" + ko, 47)
    d.text(18, "4. 發行不成立之處理：範本說明。")
    d.section = "risk"
    d.text(18, "三、 主要投資風險：")
    d.text(
        18,
        "1. 基本風險資訊：範本說明。2. 個別商品風險資訊：(6)本金轉換風險：若於期末定價日時觸及下限事件發生或表現最差的"
        "連結標的之收盤價低於其執行價，投資人將收到連結標的。",
    )
    w.new_page()
    d.section = "fees"
    d.text(18, "四、 費用：")
    d.text(12, "投資人應負擔的各項費用及金額或計算基準之表列：")
    for x, t in ((94, "費用項目"), (236, "費率 (百分比)"), (328, "收取時點")):
        d.cell(x, t)
    w.new_page()
    for item, cells in [
        ("申購費用", ("申購價金的", "0%~5%")),
        ("提前贖回費用", ("投資人提前贖回價金的", "0%~5%")),
        ("管理費用（信託管理費或管銷費用）", ("無",)),
        (
            "分銷費用（如屬發行機構或發行人給予受託或銷售機構之報酬、費用、折讓等各項利益應單獨列示）",
            ("申購價金的", "0%~5%"),
        ),
        ("保費費用", ("不適用",)),
    ]:
        d.cell(18, item)
        for t in cells:
            d.cell(229, t)
    d.section = "rest"
    d.text(18, "五、 相關機構之權利、義務及責任")
    d.text(18, "1. 範本說明。")
    d.text(18, "六、 協助投資人權益之保護方式")
    d.text(18, "七、 總代理人及受託機構與投資人爭議之處理方式")
    while len(w.doc) < s.iis_pages:
        w.new_page()
    while len(w.doc) > s.iis_pages:
        w.doc.delete_page(-1)
    d.section = "cover"
    # 第 1 頁右上角的刊印日期寫在最後：擷取順序同真實樣本，排在第 1 頁最後
    w.doc[0].insert_text((374, 21), d.edit(f"中文投資人須知刊印日期:{zh_date(TRADE)}"), fontname=FONT, fontsize=8)
    total = s.iis_page_total or len(w.doc)
    for i, page in enumerate(w.doc):
        page.insert_text((262, 826), f"第{i + 1} 頁，共 {total}頁", fontname=FONT, fontsize=8)
    return w.save(path)


def _xl(d: dt.date) -> dt.datetime:
    return dt.datetime.combine(d, dt.time())


def reference_row(s: Spec, **overrides: Any) -> dict[str, Any]:
    """與合成說明書一致的 MS 參考條件表列；回填欄位（TS、IIS、ISIN、發行日、比價日）預設空白。overrides 以 Excel 欄名覆寫。"""
    fields: dict[str, Any] = {
        "product_code": s.code,
        "denomination": DENOM,
        "currency": "USD",
        "trade_date": _xl(TRADE),
        "final_valuation_date": _xl(s.ends[-1]),
        "maturity_date": _xl(s.payments[-1]),
        "ko_pct": float(KO),
        "ko_observation": s.obs,
        "ko_memory": "Y" if s.memory else "N",
        "strike_pct": float(STRIKE),
        "ki_pct": float(KI) if s.ki != "none" else "-",
        "ki_type": s.ki if s.ki != "none" else "-",
        "coupon_pa_pct": float(s.annual),
        "tenor_months": s.tenor,
        "first_callable_period": s.non_call,
    }
    for i in range(1, 6):
        has = i <= s.count
        initial = s.initials[i - 1] if has else None
        fields[f"underlying_{i}"] = s.tickers[i - 1] if has else "-"
        fields[f"underlying_{i}_initial_price"] = float(initial) if has else "-"
        fields[f"underlying_{i}_strike_price"] = float(price(initial, STRIKE)) if has else "-"
        fields[f"underlying_{i}_ki_price"] = float(price(initial, KI)) if has and s.ki != "none" else "-"
        fields[f"underlying_{i}_ko_price"] = float(price(initial, KO)) if has else "-"
    return make_row("MS", fields, **overrides)


def check_pair(
    tmp_path: Path, spec: Spec | None = None, *, pdf_spec: Spec | None = None, config=CONFIG, **overrides: Any
):
    """同 `check`，回傳（說明書, 投資人須知）兩份的批量核對項目（BatchItem）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.code}_TS.pdf", pdf_spec or spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)


def check(tmp_path: Path, spec: Spec | None = None, *, pdf_spec: Spec | None = None, config=CONFIG, **overrides: Any):
    """合成說明書（`pdf_spec`，預設同 `spec`）＋與 `spec` 一致的參考條件表一列，經批量入口核對，回傳該說明書的 CheckReport。

    `config` 換核對設定（例：改過的審查標準）。
    """
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.code}_TS.pdf", pdf_spec or spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **overrides)])
    outcome = check_all(sheet, [pdf], config)
    return next(i.report for i in outcome.items if i.term_sheet == pdf)
