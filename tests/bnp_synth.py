"""BNP 合成說明書（docs/templates/bnp-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：封面項號「N.」獨立一行、內容在下一行；章名「第X章、」；第一章條號「N.」獨立一行、
標題在下一行；子項「(n)」；表格一格一行（標籤格與第一格有時同一行）。`breaks` 在表格某列之後強制換頁。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`、第一章各條 `ts.art{n}`
（標的表 `ts.art11.table`、日期表 `ts.art14.table`、重印表 `ts.art18.table`、情境 `ts.scen.一`…）、其他各章 `ts.ch二`～`ts.ch五`。
投資人須知（docs/templates/bnp-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
段落代號 `iis.cover`、`iis.warn`（警語 5 的「(發行機構)與法國巴黎銀行(保證機構)」一字一個文字單位，同真實樣本）、`iis.org`、
`iis.summary`（標的表 `iis.summary.table`）、`iis.coupon`、`iis.issue`、`iis.risk`、`iis.fees`、`iis.rest`。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import fitz

from harness import CONFIG, STANDARD, check_all, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used, zh_date
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row, weekday_on_or_after

NAME = STANDARD["product_name"]["bnp"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["bnp"]
ISSUER = STANDARD["issuer_name"]["bnp"]
GUARANTOR = STANDARD["guarantor_name"]["bnp"]
PRODUCT_TYPE = STANDARD["product_type"]["bnp"]
ISSUE_SIZE = STANDARD["minimum_issue_size"]["bnp"]
MULTIPLES = STANDARD["min_amount_multiples"]["bnp"]
DIST = STANDARD["distributor"]
PHONE = "+886 (2) 5556 1313"  # 審查標準 phone_equivalents 的 BNP 寫法
ADDRESS = "臺北市105 民生東路三段158 號6 樓"  # 審查標準 address_equivalents 的 BNP 寫法
APPROVAL = dt.date(2026, 6, 11)
PROSPECTUS = dt.date(2026, 5, 21)
ZH = "一二三四五六七八九十"
NUMERIC = set("0123456789,.")


def underlyings(count: int) -> tuple[UL, ...]:
    """BNP 合成文件的標的：ZBn UW、期初價 100n。"""
    return tuple(UL(f"虛構標的{i}公司", "那斯達克全球精選", f"ZB{i} UW", Decimal(100 * i)) for i in range(1, count + 1))


@dataclass
class Spec(ProductSpec):
    """BNP 合成說明書的規格：商品規格加上 BNP 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count` 產生、最終比價日與到期日由配息排程推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    `fx` 給了就是「標的為美元、商品為其他幣別」的寫法（§17 股數算式含匯率、§18 假設多一行匯率）。
    """

    issuer: str = "BNP"
    product_code: str = "082199990001"
    ki: str = "none"  # none／AM／D／P
    tenor: int = 6
    first_callable: int = 2
    annual: Decimal = Decimal("10")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    trustee_code: str | None = None  # 受託機構商品代號；None 同商品代號，"" 空白
    breaks: dict[str, int] = field(default_factory=dict)  # 表格（date／price）第 n 列之後換頁
    fx: Decimal | None = None  # 標的幣別（美元）≠ 商品幣別時的匯率（每一商品幣別兌美元）
    page_total: int | None = None  # 頁首「共M頁」的 M；None 同 PDF 頁數
    iis_pages: int = 4  # 投資人須知頁數（多的頁只有頁首）
    iis_page_total: int | None = None  # 投資人須知頁首「共M頁」的 M；None 同實際頁數
    blank_pages: int = 0  # 文末沒有文字層的附件頁數（真實樣本的附件是圖）

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count)
        self.final_date, self.maturity_date = self.ends[-1], self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期配息期結束日（P 型為自動提前贖回評價日）：每月 14 日，遇週末順延。"""
        out = []
        for j in range(1, self.tenor + 1):
            m = 1 + j
            out.append(weekday_on_or_after(dt.date(2030 + (m - 1) // 12, (m - 1) % 12 + 1, 14)))
        return out

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=3)) for d in self.ends]

    @property
    def starts(self) -> list[dt.date]:
        return [self.issue_date] + [weekday_on_or_after(d + dt.timedelta(days=1)) for d in self.ends[:-1]]

    @property
    def tickers(self) -> list[str]:
        return [u.ticker for u in self.underlyings]

    @property
    def initials(self) -> list[Decimal]:
        return [u.initial for u in self.underlyings]

    @property
    def jpy(self) -> bool:
        return self.ccy == "JPY"

    @property
    def rate_digits(self) -> int:
        return 6 if self.jpy else 4

    @property
    def decimals(self) -> int:
        return 0 if self.jpy else 2

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Decimal(1).scaleb(-self.rate_digits), ROUND_HALF_UP)

    @property
    def coupon(self) -> Decimal:
        return (self.denom * self.monthly / 100).quantize(Decimal(1).scaleb(-self.decimals), ROUND_HALF_UP)

    @property
    def autocall(self) -> bool:
        return self.first_callable < self.tenor

    @property
    def underlying_ccy(self) -> str:
        return "美元" if self.fx is not None else self.currency_zh

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(
            tenor=self.tenor,
            ccy_zh=self.currency_zh,
            underlying_zh=NAME["basket_zh"] if self.count > 1 else NAME["single_zh"],
            autocall_zh=NAME["autocall_zh"] if self.autocall else "",
        )

    @property
    def name_en(self) -> str:
        return NAME["en"].format(
            tenor=self.tenor,
            ccy=self.ccy,
            underlying_en=NAME["basket_en"] if self.count > 1 else NAME["single_en"],
            autocall_en=NAME["autocall_en"] if self.autocall else "",
        )

    def money(self, x: Decimal | int) -> str:
        return f"{Decimal(x):,.{self.decimals}f}"


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時在非數字處換行。"""

    def __init__(self, edits: Sequence[Edit], kind: str = "ts", used: set[Edit] | None = None):
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
        width, cur, used, first = 545 - x, "", 0.0, True
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
        self.w.line(x, text, gap=14)

    def chapter(self, zh: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{zh}"
        self.w.line(50, f"第{zh}章、{name}", gap=16)

    def number(self, n: int) -> None:
        """條號（封面項號、第一章與第二章條號）獨立一行。"""
        self.w.line(50, f"{n}.")

    def article(self, n: int, title: str, section: str | None = None) -> None:
        self.section = section or f"art{n}"
        self.number(n)
        self.text(80, title)

    def sub(self, n: int, text: str) -> None:
        self.text(55, f"({n}) {text}", 80)


def _price_cell(v: Decimal) -> str:
    return f"{v:,.4f}"


def _underlying_table(d: _Doc, s: Spec, section: str, *, reprint: bool, sep: str | None = None) -> None:
    """第 11 項標的表（`reprint` 為第 18 項重印：沒有彭博代碼與交易所列、價格格寫「N，即期初價格之X%」）。
    `sep` 另給價格與百分比之間的寫法（投資人須知「N,即期初價格之X%」）。"""
    d.section = section
    sep = sep or ("，即期初價格之" if reprint else " 即期初價格之")
    d.cell(50, "連結標的名稱：")
    for u in s.underlyings:
        d.cell(150, u.name)
    if not reprint:
        d.cell(50, "彭博代碼：")
        for u in s.underlyings:
            d.cell(150, u.ticker)
        d.cell(50, "交易所：")
        for u in s.underlyings:
            d.cell(150, u.exchange)
    d.cell(50, "期初價格(Initial Price)：")
    for v in s.initials:
        d.cell(150, _price_cell(v))
    rows = [("執行價格(Strike Price):", s.strike), ("自動提前贖回價格(Automatic Early Redemption Price)：", s.ko)]
    if s.ki != "none":
        rows.append(("下限價格(Knock-In Price)：", s.ki_pct))
    for i, (label, pct) in enumerate(rows):
        d.cell(50, label)
        for v in s.initials:
            d.cell(150, f"{_price_cell(price(v, pct))}{sep}{pct:.2f}%")
        if section == "art11.table" and s.breaks.get("price") == i + 1:
            d.w.new_page()
    d.cell(50, "貨幣(Currency)：")
    for _ in s.underlyings:
        d.cell(150, s.underlying_ccy)


def _scenarios(d: _Doc, s: Spec) -> None:
    unit, a, n, k = s.currency_zh, s.coupon, s.tenor, s.first_callable
    rounding = "四捨五入至整數位" if s.jpy else "四捨五入至小數點以下第二位"
    kinds: list[tuple[str, str]] = [("autocall", "自動提前贖回情況")]
    if s.ki in ("D", "P"):
        kinds += [("best", "最佳情況(A)"), ("best", "最佳情況(B)")]
    else:
        kinds.append(("best", "最佳情況"))
    kinds += [("worse", "較差情況"), ("worst", "最差情況"), ("extreme", "極端情況")]
    worst_idx = 0  # 表現最差標的：第 1 檔
    strike = price(s.initials[worst_idx], s.strike)
    for idx, (kind, title) in enumerate(kinds):
        number = ZH[idx]
        d.section = f"scen.{number}"
        d.text(80, f"情境{number}：{title}")
        if kind == "extreme":
            d.text(80, "如發行機構違約，投資人可能虧損100%原計價幣別投資本金。")
            continue
        periods = k if kind == "autocall" else n
        span = f"第{periods} 個月" if periods == 1 else f"第1~{periods} 個月"
        cum = a * periods
        if kind == "autocall":
            j = f"第1(j={n})個" if k == n else "第1 個"
            d.text(
                80,
                f"假設於{j}自動提前贖回評價日，該日所有連結標的之收盤價格皆大於或等於其各自自動提前贖回價格，發生自動提前贖回事件，於自動提前贖回日以現金結算收益：",
            )
        elif kind == "best":
            d.text(
                80,
                "假設不曾發生任何自動提前贖回事件，且表現最差連結標的於期末評價日之收盤價格大於其執行價格，於到期日以現金結算收益：",
            )
        else:
            close = Decimal("1.00") if kind == "worst" else (strike / 2).quantize(Decimal("0.01"), ROUND_HALF_UP)
            d.text(
                80,
                f"假設不曾發生任何自動提前贖回事件，且表現最差連結標的(假設為{s.underlyings[worst_idx].name})於期末評價日之收盤價格"
                f"{close} {s.underlying_ccy}小於其執行價格{_price_cell(strike)} {s.underlying_ccy}，於到期日以實體交付表現最差連結標的股數的形式結算收益：",
            )
        d.text(80, f"{span}配息金額 = 1 單位商品 × {s.denom:,} {unit} × {s.monthly}%")
        d.text(80, f"= {s.money(a)} {unit}({rounding})")
        d.text(80, f"累積總月配息金額 = {s.money(cum)} {unit}")
        if kind in ("autocall", "best"):
            label = "自動提前贖回金額" if kind == "autocall" else "到期贖回金額"
            d.text(80, f"{label} = 1 單位商品 × {s.denom:,} {unit} × 100%")
            d.text(80, f"= {s.denom:,} {unit}")
            r = ((s.denom + cum) / s.denom * 100 - 100).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            d.text(80, f"總報酬率 = [({label} + 累積總月配息金額) ÷ 總投資金額] - 1")
            d.text(80, f"= [({s.denom:,} {unit} + {s.money(cum)} {unit}) ÷ {s.denom:,} {unit}] - 1")
            d.text(80, f"= {r}% (年化報酬率：{s.annual:.3f}%)")
            continue
        numerator = s.denom * s.fx if s.fx is not None else Decimal(s.denom)
        shares = (numerator / strike).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        whole = int(shares.quantize(Decimal(1), ROUND_DOWN))
        frac = shares - whole
        per_share = close / s.fx if s.fx is not None else close
        remainder = (per_share * frac).quantize(Decimal(1).scaleb(-s.decimals), ROUND_HALF_UP)
        value = whole * per_share + remainder + cum
        r = (value / s.denom * 100 - 100).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        uccy = s.underlying_ccy
        if s.fx is None:
            d.text(80, "股數計算 = 每單位面額 ÷ 表現最差連結標的之執行價格")
            d.text(80, f"= {s.denom:,} {unit} ÷ {_price_cell(strike)} {uccy}")
        else:
            d.text(80, "股數計算 = (每單位面額 × 匯率) ÷ 表現最差連結標的之執行價格")
            d.text(80, f"= ({s.denom:,} {unit} × {s.fx}) ÷ {_price_cell(strike)} {uccy}")
        d.text(80, f"={shares} (四捨五入至小數點以下第四位)無條件捨去至整數位 = {whole} 股")
        if s.fx is None:
            d.text(80, f"剩餘金額 = {close} {uccy} × {frac}")
        else:
            d.text(80, f"剩餘金額 = ({close} {uccy} ÷ {s.fx}) × {frac}")
        d.text(80, f"= {s.money(remainder)} {unit}({rounding})")
        d.text(80, f"因此，到期日將獲得表現最差連結標的之股數{whole} 股加上現金{s.money(remainder)} {unit}")
        fx_div = " ÷ 匯率" if s.fx is not None else ""
        d.text(
            80,
            f"總報酬率 = [({whole} 股 × {s.underlyings[worst_idx].name}收盤價格{close} {uccy}{fx_div} + 現金交割{s.money(remainder)} {unit}"
            f" + 累積總月配息金額{s.money(cum)} {unit}) ÷ 總投資金額{s.denom:,} {unit}] - 1 = {r}% (年化報酬率：-50.000%)",
        )


SPLIT = "(發行機構)與法國巴黎銀行(保證機構)"  # 警語 5 第二句一字一個文字單位的那一段（真實樣本的版面）


def _split_line(d: _Doc, text: str) -> None:
    """警語 5 第二句：`SPLIT` 那一段每個字各寫成一個文字單位，排在同一行（真實樣本匯出成一字一行）。"""
    text = d.w.edit(text)
    before, split, after = text.partition(SPLIT)
    with d.w.verbatim():
        if not split:
            d.text(34, text)
            return
        d.w.need(13)
        x = 34.0
        for chunk in (before, *split):
            d.w.put(x, d.w.y, chunk)
            # 字間留空：太近時 PyMuPDF 會把同一基線的字併成一行
            x += sum(fitz.get_text_length(c, fontname="helv", fontsize=10) if ord(c) < 256 else 10 for c in chunk) + 12
        d.w.y += 13
        d.text(34, after)


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """BNP 中文投資人須知（docs/templates/bnp-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, "iis", used)
    w = d.w
    dist, unit, short = DIST["name"], s.currency_zh, GUARANTOR["ch1"]
    worst = "表現最差連結標的" if s.count > 1 else "連結標的"
    w.line(197, "中文投資人須知 (適用於專業投資人及OSU客戶)")
    w.line(400, f"刊印日期：{zh_date(s.trade_date)}")
    d.text(18, f"{s.name_zh}(下稱「商品」){s.name_en}")
    d.text(
        18,
        f"商品種類：{PRODUCT_TYPE}，TDCC 商品代號為{s.product_code}，受託機構商品代號為{s.product_code}，ISIN 為XS1999900001",
    )
    d.section = "warn"
    w.line(18, "本商品之投資風險警語：")
    for n, t in enumerate(
        [
            WARNING,
            "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
            "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息。",
            f"本商品雖經{dist}(受託機構)審查，並不代表或保證本商品之價值，且{dist}(受託機構)不負本商品投資盈虧之責。"
            f"{dist}(受託機構)依法不得承諾擔保原計價幣別投資本金或最低收益率。",
            f"本商品持有期間如有保證配息收益或保證保本率，係由{ISSUER}(發行機構)與{short}(保證機構)保證，"
            f"而非由{dist}(受託機構)所保證。",
        ],
        1,
    ):
        d.text(18, f"{n} {t}", 34)
    _split_line(d, f"{ISSUER}{SPLIT}間有財務會計準則公報第六號所定之關係人或實質關係人之情事。")
    for n, t in enumerate(
        [
            "本中文投資人須知之內容如有虛偽或隱匿之情事者，除商品風險程度、受託對象、受託機構收取之費用及由受託機構另行訂定者，"
            f"係由受託機構(即{dist})負責外，其餘內容應由虛構總代理人台北分行(總代理人)及其負責人依法負責。",
            "本商品係依中華民國境外結構型商品管理規則規定，於中華民國境內受託投資、受託買賣或為投資型保單之投資標的。"
            f"本商品乃依據發行機構於{zh_date(PROSPECTUS)}公告之基本公開說明書發行。",
            "本商品係依中華民國境外結構型商品管理規則規定於中華民國境內受託投資或受託買賣之投資標的。",
            "受託機構應提供專業投資人及OSU 客戶相關契約審閱期間。",
        ],
        6,
    ):
        d.text(18, f"{n} {t}", 34)
    d.section = "org"
    w.line(18, "壹、相關機構")
    for t in (
        f"1 發行機構：{ISSUER}(註冊辦公地點：1 Fictional Street, Amsterdam)",
        "2 總代理人：虛構總代理人台北分公司(下稱「虛構台北分行」)(地址：臺北市110 虛構路1 號)",
        f"3 保證機構：{GUARANTOR['cover']}(下稱「法國巴黎銀行」)(地址：1 Fictional Boulevard, Paris)",
        f"4 受託機構：{dist}(地址：{ADDRESS})",
    ):
        d.text(18, t, 34)
    d.section = "summary"
    w.line(18, "貳、商品事項")
    w.line(18, "1 商品簡介")
    for t in (
        "銷售對象：專業投資人及OSU 客戶",
        "發行機構之長期債務信用評等(截至本中文投資人須知刊印日期)：虛構評等。",
        f"商品風險程度：{WARNING}",
        f"商品之發行評等：無 ●計價幣別：{unit}",
        f"商品面額：每單位商品{s.denom:,} {unit} ●最低申購金額：{s.denom * MULTIPLES['minimum_subscription']:,}{unit}",
        "計價貨幣本金保本率：不保本。",
        "連結標的資產價格：",
    ):
        d.text(18, f"● {t}", 34)
    _underlying_table(d, s, "summary.table", reprint=False, sep=",即期初價格之")
    d.section = "summary"
    if s.count > 1:
        d.text(18, "籃子：指由上表列示的連結標的組成的一籃子。")
    if s.ko_obs == "D":
        evaluation = (
            f"自第{s.first_callable} 個配息期結束日(含該日)起至期末評價日(含該日)的每一個預定交易日"
            "(惟可於發生市場中斷事件時調整)，詳細日期請參閱中文產品說明書第一章第14 項之說明。"
        )
    else:
        evaluation = ("每1 個月一次，" if s.autocall else "") + "詳細日期請參閱中文產品說明書第一章第14 項之說明。"
    for t in (
        f"商品年期：{s.tenor} 個月期(商品依據中文產品說明書第一章第20 項被發行機構提前買回/投資人提前贖回除外)",
        f"商品開始受理申購日期：{zh_date(s.trade_date)}至{zh_date(s.trade_date)}",
        f"期初訂價日：{zh_date(s.trade_date)}，若該日並非所有連結標的之預定交易日，則遞延至下一個預定交易日。"
        f" ●發行日：{zh_date(s.issue_date)}，若該日並非營業日，則遞延至該日後之第一個營業日。"
        f" ●期末評價日：{zh_date(s.ends[-1])}，若該日並非所有連結標的之預定交易日，則遞延至下一個預定交易日。"
        f" ●到期日：最後出現的期末評價日後第三個營業日，預定為{zh_date(s.payments[-1])}(惟商品依據中文產品說明書"
        "第一章第20 項被提前買回/提前贖回除外)。●配息日：詳細日期請參閱中文產品說明書第一章第14 項之說明。",
        f"自動提前贖回評價日：{evaluation}自動提前贖回日：發生自動提前贖回事件之自動提前贖回評價日後第三個營業日。",
        "開始受理贖回日：投資人得於發行日之次一營業日起，向受託機構申請贖回。",
    ):
        d.text(18, f"● {t}", 34)
    w.new_page()
    d.section = "coupon"
    w.line(18, "2 收益分配事項、自動提前贖回金額及到期贖回金額之計算")
    d.text(18, f"● 配息：配息將於每個配息日支付。配息金額為商品面額 × C，其中C 為{s.monthly}%。", 34)
    d.text(
        18,
        "● 自動提前贖回事件計算公式：若發生自動提前贖回事件，就每單位商品，發行機構將於自動提前贖回日支付依下列公式計算之"
        f"{unit}金額(「自動提前贖回金額」) 提前贖回本商品：自動提前贖回金額 = 商品面額 × 100%。其中，",
        34,
    )
    d.text(
        18,
        "「自動提前贖回事件」：計算代理機構將依專業合理的方式釐定，若於任一自動提前贖回評價日，"
        f"{_ko_clause(s)}，則視為發生自動提前贖回事件。",
    )
    lead = (
        "● 到期贖回金額：若無自動提前贖回事件且商品並無投資人提前贖回或發行機構提前買回且註銷，就每單位商品，"
        f"發行機構將於到期日支付或交付依下列公式計算之{unit}金額或實體連結標的："
    )
    if s.ki == "none":
        d.text(
            18,
            lead
            + f"(1)若於期末評價日，{worst}之收盤價格大於或等於其執行價格，則投資人就每單位商品可領回：商品面額 × 100%；"
            f"或(2)若於期末評價日，{worst}之收盤價格小於其執行價格，則投資人將會收到發行機構以實體交付{worst}股數。",
            34,
        )
    else:
        when = {
            "AM": "期末評價日",
            "D": "自期初訂價日(不含)起至期末評價日(含)止之每一個預定交易日",
            "P": "每一個配息期結束日，若該日並非預定交易日，則遞延至下一個預定交易日",
        }[s.ki]
        d.text(
            18,
            lead + "(1)若不曾發生觸及生效事件，則投資人就每單位商品可領回：商品面額 × 100%；或(2)若曾發生觸及生效事件，"
            f"則投資人將會收到發行機構以實體交付{worst}股數。其中，",
            34,
        )
        d.text(18, f"「觸及生效事件觀察日」：指{when}。")
        d.text(
            18, "「觸及生效事件」：若任一連結標的收盤價格於觸及生效事件觀察日小於其下限價格，則被視為發生觸及生效事件。"
        )
    d.section = "issue"
    w.line(18, "3 發行不成立之處理")
    d.text(
        18,
        f"發行不成立之情形：需發行機構於受理申購截止日或之前收到之本商品有效申購金額至少達{ISSUE_SIZE}之總申購價值。",
    )
    d.section = "risk"
    w.line(18, "叁、投資風險")
    d.text(18, f"● 信用風險：本商品之保證機構為{short}。投資人要承擔發行機構及保證機構之信用風險。", 34)
    d.text(
        18, "● 本金轉換風險：若曾發生觸及生效事件或表現最差連結標的之收盤價格小於其執行價格，投資人將收到連結標的。", 34
    )
    w.new_page()
    d.section = "fees"
    w.line(18, "肆、投資人應負擔的各項費用")
    w.row([(18, "費用項目"), (109, "費率(百分比)"), (205, "收取時點"), (496, "收取人")], gap=14)
    tail = ("機構或發行人給予受", "託或銷售機構之報", "酬、費用、折讓等各", "項利益應單獨列示)")
    for label, rate, rest in (
        (("申購費用",), "申購價金的 0%~5%", ()),
        (("提前贖回費用",), "投資人提前贖回價金的0%~5%", ()),
        (("管理費用(信託管理", "費或管銷費用)"), "無", ()),
        (("分銷費用(如屬發行",), "申購價金的 0%~5%", tail),  # 列首的續行排在費率之後（同真實樣本的擷取順序）
        (("保費費用",), "不適用", ()),
    ):
        for t in label:
            d.cell(18, t)
        d.cell(109, rate)
        for t in rest:
            d.cell(18, t)
    d.section = "rest"
    w.line(18, "伍、相關機構之權利、義務及責任")
    d.text(18, "1 範本說明。")
    w.line(18, "陸、協助投資人權益保護之方式")
    d.text(18, "1 投資人與總代理人或受託機構發生爭議、訴訟之處理方式：")
    d.text(34, f"受託機構聯絡方式：{PHONE}")
    d.text(18, "2 投資人與總代理人發生爭議、國外訴訟之處理方式：請參見上述第一項之說明。")
    w.line(18, "柒、發行機構、總代理人及受託機構與投資人爭議之處理方式")
    while len(w.doc) < s.iis_pages:
        w.new_page()
    while len(w.doc) > s.iis_pages:
        w.doc.delete_page(-1)
    total = s.iis_page_total or len(w.doc)
    for i, page in enumerate(w.doc):
        page.insert_text((268, 40), f"第{i + 1}頁，共{total}頁", fontname=FONT, fontsize=8)
    if used is None:
        require_used(edits, w.used)
    return w.save(path)


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 BNP 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()  # 說明書與投資人須知共用：改字換到哪一份都算
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, "ts", used)
    w = d.w
    ends, payments, starts = s.ends, s.payments, s.starts
    trustee = s.product_code if s.trustee_code is None else s.trustee_code
    unit, basket = s.currency_zh, s.count > 1
    worst = "表現最差連結標的" if basket else "連結標的"
    w.line(104, "中文產品說明書(適用於專業投資人及OSU 客戶)", gap=16)
    w.line(220, "(下稱「本產品說明書」)", gap=16)
    d.text(55, s.name_zh)
    items = [
        f"TDCC 商品代號為{s.product_code} ，受託機構商品代號為{trustee} ，ISIN 為XS1999900001",
        f"商品中文名稱：{s.name_zh}(下稱「商品」)",
        f"英文名稱：{s.name_en}",
        f"商品種類：{PRODUCT_TYPE}",
        f"發行機構名稱：{ISSUER}",
        "發行機構註冊地：荷蘭",
        "發行機構註冊辦公地點：1 Fictional Street, Amsterdam，電話：+31 (0) 000 0000",
        "商品註冊地：不適用",
        f"計價幣別： {s.currency_zh}",
        "總代理人之名稱、電話及地址：虛構總代理人台北分公司(下稱「總代理人」)，電話：+886 (2) 0000 0000，地址：臺北市110 虛構路1 號",
        f"保證機構之名稱及地址：{GUARANTOR['cover']}(下稱「法國巴黎銀行」)，地址：1 Fictional Boulevard, Paris, France",
        f"受託機構之名稱、電話及地址：{DIST['name']}，電話：{PHONE}，地址：{ADDRESS}",
        f"玉山綜合證券股份有限公司(受託機構)審查通過之日期：{APPROVAL.year} 年{APPROVAL.month:02d} 月{APPROVAL.day:02d} 日",
        "本商品之投資風險警語：",
    ]
    for n, t in enumerate(items, 1):
        d.number(n)
        d.text(80, t)
    d.sub(1, WARNING)
    d.sub(2, "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。")
    d.sub(
        5,
        f"本商品持有期間如有保證配息收益或保證保本率，係由{ISSUER}(發行機構)與法國巴黎銀行(保證機構)保證，而非由受託機構所保證。",
    )
    d.sub(
        7,
        f"本商品係依中華民國境外結構型商品管理規則規定，於中華民國境內受託投資、受託買賣或為投資型保單之投資標的。本商品乃依據發行機構於{zh_date(PROSPECTUS)}公告之「債券、認股證與憑證發行計劃之基本公開說明書」及其增補(如有)(「基本公開說明書」)發行。",
    )
    d.sub(8, "本商品係依中華民國境外結構型商品管理規則規定於中華民國境內受託投資或受託買賣之投資標的。")
    d.number(15)
    d.text(80, "投資人應詳閱本產品說明書內容。")
    d.number(16)
    d.text(80, f"產品說明書刊印日期：{zh_date(s.trade_date)}。本產品說明書所載資料於刊印日期準確無誤。")

    d.chapter("一", "商品相關基本資料")
    titles = {
        1: f"商品名稱：{s.name_zh}",
        2: f"商品風險程度：{WARNING}",
        4: "商品之發行評等：無",
        5: f"計價幣別：{s.currency_zh}",
        6: f"商品面額：每單位商品{s.denom:,} {unit}",
        7: "發行價格：100.00%",
        11: "連結標的資產價格，及其相對權重、與投資績效之關連情形：",
        14: "商品年期、期初訂價日、發行日、期末評價日、到期日、配息日、自動提前贖回評價日、自動提前贖回日、及其他依商品性質而定之日期：",
        15: "配息資料及其計算公式：",
        16: "自動提前贖回事件計算公式：",
        17: "到期贖回計算公式，包含最低保證配息率及參與率：",
        18: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
        20: "發行機構得提前買回或投資人得提前贖回之條件及應注意事項：",
    }
    for n in range(1, 28):
        d.article(n, titles.get(n, "範本說明。"))
        if n == 3:
            d.text(80, f"發行機構：{ISSUER}")
            d.text(80, "發行機構長期債務信用評等(截至本產品說明書刊印日期)：不核對")
            d.text(80, f"保證機構：{GUARANTOR['ch1']}")
            d.text(80, "計算代理機構：BNP Paribas Financial Markets S.N.C.")
        elif n == 11:
            _underlying_table(d, s, "art11.table", reprint=False)
            d.section = "art11"
            if basket:
                d.text(80, "「籃子」指由上表列示的連結標的組成的一籃子。")
            d.text(80, "相對權重：不適用。")
        elif n == 14:
            _dates(d, s, ends, payments, starts)
        elif n == 15:
            d.text(87, f"月配息率：{s.monthly}% (即年利率{s.annual}%)")
            d.text(87, "配息將於各配息日依以下公式計算給付：")
            rounding = "四捨五入至整數位" if s.jpy else "四捨五入至小數點以下第二位"
            d.text(87, f"就每單位商品而言，由計算代理機構根據以下公式({rounding})計算以{unit}計值的配息款項：")
            d.text(87, f"固定配息金額 = 每單位面額 × {s.monthly}%。")
        elif n == 16:
            d.text(
                87,
                f"若發生自動提前贖回事件，就每單位商品，發行機構將於自動提前贖回日支付依下列公式計算之{unit}金額(「自動提前贖回金額」) 提前贖回本商品：",
            )
            d.text(87, "自動提前贖回金額 = 商品面額 × 100%")
            d.text(87, "其中，")
            d.text(
                87,
                f"「自動提前贖回事件」：計算代理機構將依專業合理的方式釐定，若於任一自動提前贖回評價日，{_ko_clause(s)}，則視為發生自動提前贖回事件。",
            )
        elif n == 17:
            d.text(
                87,
                f"若無自動提前贖回事件且商品並無投資人提前贖回或發行機構提前買回且註銷，就每單位商品，發行機構將於到期日支付或交付依下列公式計算之{unit}金額或實體連結標的：",
            )
            if s.ki == "none":
                d.sub(1, f"若於期末評價日，{worst}之收盤價格大於或等於其執行價格，則投資人就每單位商品可領回：")
                d.text(87, "商品面額 × 100%；或")
                d.sub(
                    2,
                    f"若於期末評價日，{worst}之收盤價格小於其執行價格，則投資人將會收到發行機構以實體交付{worst}股數及以現金交付剩餘金額(如有)。",
                )
            else:
                d.sub(1, "若不曾發生觸及生效事件，則投資人就每單位商品可領回：")
                d.text(87, "商品面額 × 100%；或")
                d.sub(
                    2,
                    f"若曾發生觸及生效事件，則投資人將會收到發行機構以實體交付{worst}股數及以現金交付剩餘金額(如有)。",
                )
                d.text(87, "其中，")
                when = {
                    "AM": "期末評價日",
                    "D": "自期初訂價日(不含)起至期末評價日(含)止之每一個預定交易日",
                    "P": "每一個配息期結束日，若該日並非預定交易日，則遞延至下一個預定交易日",
                }[s.ki]
                d.text(87, f"「觸及生效事件觀察日」：指{when}。")
                d.text(
                    87,
                    "「觸及生效事件」：若任一連結標的收盤價格於觸及生效事件觀察日小於其下限價格，則被視為發生觸及生效事件。",
                )
            d.text(
                87,
                f"最低保證配息率：若商品並無投資人提前贖回或發行機構提前買回且註銷，本商品最低配息率為{s.annual}%（年化）。",
            )
            d.text(87, "參與率：不適用。")
        elif n == 18:
            d.sub(1, "投資收益計算方法：請參閱上述本章第10 項之說明。")
            d.sub(2, "本金虧損之機率：範本說明。")
            d.sub(3, "以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：")
            d.text(87, "假設：")
            d.text(87, "投資單位數 = 1 單位商品")
            d.text(87, f"商品面額 = 每單位商品為{s.denom:,} {unit}")
            d.text(87, "發行價格：100.00%")
            d.text(87, f"投資本金 = 1 單位商品 × 商品面額 × 100.00% = {s.denom:,} {unit}")
            if s.fx is not None:
                d.text(87, f"匯率(每一{unit}兌美元的市場中間匯率) = {s.fx}")
            d.text(87, f"每月配息率：{s.monthly}%")
            d.text(87, "連結標的相關價格如下：")
            _underlying_table(d, s, "art18.table", reprint=True)
            _scenarios(d, s)
        elif n == 20:
            d.sub(
                1,
                f"自動提前贖回事件：若於任一自動提前贖回評價日，{_ko_clause(s)}，則視為發生自動提前贖回事件。發行機構依約定於自動提前贖回日支付自動提前贖回金額。",
            )
            d.sub(2, "當發生特殊事件後發行機構提前買回：範本說明。")

    d.chapter("二", "相關機構事業概況")
    d.number(1)
    d.text(80, "發行機構")
    d.sub(1, f"事業名稱：{ISSUER}")
    d.sub(2, "設立日期：1995 年3 月15 日")
    d.sub(3, "營業所在地：荷蘭 (1 Fictional Street, Amsterdam)")
    d.number(2)
    d.text(80, "保證機構")
    d.sub(1, f"事業名稱：{GUARANTOR['ch2']}")
    d.sub(2, "設立日期：1972 年8 月8 日")
    d.sub(3, "營業所在地：1 Fictional Boulevard, Paris, France")
    d.number(3)
    d.text(80, "總代理人、計算代理機構、商品主事務代理機構、受託機構、保管機構及其他相關機構")
    for n, (who, name, addr, boss) in enumerate(
        [
            ("總代理人", "虛構總代理人台北分公司", "臺北市110 虛構路1 號", "虛構甲"),
            (
                "計算代理機構",
                "BNP Paribas Financial Markets S.N.C.",
                "20 Fictional Boulevard, Paris",
                "Fictional Person",
            ),
            (
                "商品主事務代理機構",
                "BNP Paribas Financial Markets S.N.C.",
                "20 Fictional Boulevard, Paris",
                "Fictional Person",
            ),
            ("受託機構", DIST["name"], ADDRESS, DIST["chairman"]),
            ("結算系統：", "Euroclear Bank S.A./N.V.", "1 Fictional Square, Brussels", "Fictional Clearer"),
        ],
        1,
    ):
        d.sub(n, who)
        d.text(80, f"◎事業名稱：{name}")
        d.text(80, "◎設立日期：2000 年11 月20 日")
        d.text(80, f"◎營業所在地：{addr}")
        d.text(80, f"◎負責人姓名：{boss}")
    d.number(4)
    d.text(80, "交易架構說明：本商品由發行機構發行。")
    d.number(5)
    d.text(80, "利害關係人揭露：範本說明。")

    d.chapter("三", "商品風險揭露")
    d.number(1)
    d.text(80, "基本風險資訊：範本說明。")
    d.number(6)
    d.text(80, "本商品之投資風險警語。")
    d.sub(1, WARNING)
    d.sub(2, "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。")
    d.sub(
        7,
        f"本商品係依中華民國境外結構型商品管理規則規定，於中華民國境內受託投資、受託買賣或為投資型保單之投資標的。本商品乃依據發行機構於{zh_date(PROSPECTUS)}公告之「債券、認股證與憑證發行計劃之基本公開說明書」及其增補(如有)發行。",
    )

    d.chapter("四", "一般交易事項")
    d.number(1)
    d.text(80, "商品開始受理申購、開始受理贖回日期及後續受理贖回日期，每營業日受理申購、贖回申請截止時間：")
    d.text(80, f"◎商品開始受理申購日期：{zh_date(s.trade_date)}至{zh_date(s.trade_date)}")
    d.text(80, "◎開始受理贖回日期：投資人得於發行日之次一營業日起，向受託機構申請贖回。")
    d.number(2)
    d.text(80, "投資人應負擔的各項費用及金額或計算基準之表列：")
    w.need(200)
    for x, t in ((50, "費用項目"), (134, "費率(百分比) 收取時點"), (302, "收取方式"), (470, "收取人")):
        d.cell(x, t)
    for label, cells in [
        ("申購費用", ("申購價金的", "0%~5%", "投資人向受託或銷售機構申購時")),
        ("提前贖回費用", ("投資人提前贖回價金的", "0%~5%", "投資人向受託或銷售機構提前贖回時")),
        ("管理費用(信託管理費或管銷費用)", ("無", "不適用")),
        (
            "分銷費用(如屬發行機構或發行人給予受託或銷售機構之報酬、費用、折讓等各項利益應單獨列示)",
            ("申購價金的", "0%~5%", "於本商品發行時一次給付"),
        ),
        ("保費費用", ("不適用", "不適用")),
    ]:
        d.cell(50, label)
        for t in cells:
            d.cell(134, t)
    d.number(3)
    d.text(80, "商品交易架構：參閱以上第二章。")
    d.number(4)
    d.text(80, "商品最低申購金額及商品最低加購金額：")
    d.text(80, f"最低申購金額：{s.denom * MULTIPLES['minimum_subscription']:,} {unit}")
    d.text(80, f"最低加購金額：{s.denom * MULTIPLES['minimum_additional']:,} {unit}")
    d.number(5)
    d.text(80, "商品申購價金之計算：")
    d.text(80, "申購價金 = 申購商品單位數 × 每單位商品面額 × 100% + 申購費用")
    d.number(6)
    d.text(80, "申購手續及資金給付方式：範本說明。")
    d.number(7)
    d.text(80, "發行不成立之情形：")
    d.text(
        80,
        f"發行機構保留因任何原因而取消發行本商品之權利。尤其是本商品之發行附有條件，需發行機構於受理申購截止日或之前收到本商品有效申購金額至少達{ISSUE_SIZE}之總申購價值。",
    )
    d.number(8)
    d.text(
        80,
        f"最低贖回金額或單位數：最低贖回金額為{s.denom * MULTIPLES['minimum_redemption']:,} {unit}，並以{s.denom * MULTIPLES['redemption_increment']:,} {unit}為增加贖回之單位。",
    )
    d.number(9)
    d.text(80, "贖回價金之計算：請參見第一章之說明。")
    for n in range(10, 16):
        d.text(50, f"{n}. 範本說明。")
    d.text(50, "16. 總代理人協助辦理投資人權益保護之方式：")
    d.text(80, f"受託機構聯絡方式：{PHONE}")
    d.text(50, "17. 商品重要相關資料可至「境外結構型商品資訊觀測站」查詢。")

    d.chapter("五", "特別記載事項")
    d.number(1)
    d.text(80, "投資人之申購及贖回商品，須經境外發行機構確認後始生效力。")
    d.number(2)
    d.text(80, "其他依主管機關規定及總代理人認為對投資人權益有重大影響，而應於中文產品說明書記載之事項：")
    d.sub(
        1,
        f"本商品為依據發行機構{zh_date(PROSPECTUS)}公告之基本公開說明書及其增補(如有)及於發行日所確定有關本境外結構型商品之最終條款發行。",
    )
    d.sub(2, "自救權及吸收虧損權：範本說明。")
    text_pages = len(w.doc)
    for _ in range(s.blank_pages):
        w.doc.new_page(width=595, height=842)
    total = s.page_total or len(w.doc)
    for i, page in enumerate(w.doc):
        if i >= text_pages:
            continue
        page.insert_text((268, 40), f"第{i + 1}頁，共{total}頁", fontname=FONT, fontsize=8)
    require_used(edits, w.used)
    return w.save(path)


def _ko_clause(s: Spec) -> str:
    """第 16 項與第 20 項(1) 自動提前贖回事件的條件子句（範本規格 §4.2）。"""
    if s.memory:
        subject = "每一連結標的皆" if s.count > 1 else "連結標的"
        own = "其各自" if s.count > 1 else "其"
        return (
            f"{subject}曾經於該自動提前贖回評價日或之前任一自動提前贖回評價日之收盤價格大於或等於{own}自動提前贖回價格"
        )
    if s.count > 1:
        return "每一連結標的之收盤價格皆大於或等於其各自自動提前贖回價格"
    return "連結標的之收盤價格大於或等於其自動提前贖回價格"


def _dates(d: _Doc, s: Spec, ends, payments, starts) -> None:
    d.sub(1, f"商品年期：{s.tenor} 個月期(商品依據本章第20 項被發行機構提前買回/投資人提前贖回除外)")
    d.sub(2, f"期初訂價日：{zh_date(s.trade_date)}，若該日並非所有連結標的之預定交易日，則遞延至下一個預定交易日。")
    d.sub(3, f"發行日：{zh_date(s.issue_date)}，若該日並非營業日，則遞延至該日後之第一個營業日。")
    d.sub(4, f"期末評價日：{zh_date(ends[-1])}，若該日並非所有連結標的之預定交易日，則遞延至下一個預定交易日。")
    d.sub(
        5,
        f"到期日：最後出現的期末評價日後第三個營業日，預定為{zh_date(payments[-1])}(商品依據本章第20 項被提前買回/提前贖回除外)。",
    )
    if s.ko_obs == "D":
        d.sub(
            6,
            "配息期：由第(j-1)個配息期結束日(按下表所指明)(不包括該日)至緊接的第j 個配息期結束日(按下表所指明)(包括該日)。",
        )
        d.sub(7, "配息日：就各個配息期而言，於相關配息期結束日後第三個營業日（下表載列現時預期的日期）。")
        d.sub(
            8,
            f"自動提前贖回評價日：自第{s.first_callable} 個配息期結束日(含該日)起至期末評價日(含該日)的每一個預定交易日(惟可依據本章第13 項下於發生市場中斷事件時調整)。",
        )
        d.sub(9, "自動提前贖回日：發生自動提前贖回事件之自動提前贖回評價日後第三個營業日。")
        header = ["配息期開始日", "配息期結束日", "配息日"]
    else:
        d.sub(6, "配息日：如下表所列之日期，若該日並非營業日，則遞延至該日後之第一個營業日。")
        d.sub(
            7,
            "自動提前贖回評價日：如下表所列之日期，若當日不是預定交易日，則遞延至下一個預定交易日(惟可依據本章第13 項下於發生市場中斷事件時調整)。",
        )
        d.sub(8, "自動提前贖回日：發生自動提前贖回事件之自動提前贖回評價日後第三個營業日。")
        header = ["自動提前贖回評價日", "配息日"]
    d.section = "art14.table"
    d.cell(68, "j")
    for x, t in zip((135, 287, 456), header, strict=False):
        d.cell(x, t)
    for j in range(1, s.tenor + 1):
        if s.ko_obs == "D":
            cells = [zh_date(starts[j - 1]), zh_date(ends[j - 1]), zh_date(payments[j - 1])]
        else:
            evaluation = zh_date(ends[j - 1]) if j >= s.first_callable else "不適用"
            cells = [evaluation, zh_date(payments[j - 1])]
        d.cell(67, str(j))
        for x, c in zip((125, 276, 430), cells, strict=False):
            d.cell(x, c)
        if s.breaks.get("date") == j:
            d.w.new_page()
    d.section = "art14"
    next_n = 10 if s.ko_obs == "D" else 9
    d.sub(next_n, "營業日：紐約之營業日。")
    d.sub(next_n + 1, "營業日慣例：順延制；若任何一個日曆日並非營業日，則遞延至下一個營業日。")


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


def check_pair(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """同 `check`，回傳（說明書, 投資人須知）兩份的批量核對項目（BatchItem）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)
