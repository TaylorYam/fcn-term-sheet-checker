"""BBVA 合成說明書（docs/templates/bbva-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：第 1 頁「(僅適用於專業投資人)」「中文產品說明書」、編號清單 1.～14.、第 2 頁末刊印日期；
章名「一、商品基本資料」～「六、存託憑證條款相關說明」在左邊界、各章新頁；條號「N.」x 28、子項「(n)」x 50。
每頁頁底「N/M」頁碼（真實文件的頁碼，擷取時排除）。價格表（第 14、16 項）每格一個位置，價格後接標的報價幣別字。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`（第 1～2 頁；警語 `ts.warn`）、第一章各條
`ts.art{n}`（連結標的表 `ts.art9.table`、日期表 `ts.art12.table`（每期型的自動提前贖回表 `ts.art12.table.ko`）、價格表
`ts.art14.table`、情境價格表 `ts.art16.table`）、其他各章 `ts.ch二`～`ts.ch六`。
投資人須知（docs/templates/bbva-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
段落代號 `iis.cover`、`iis.warn`、`iis.org`（一、）、`iis.summary`（二、 1.）、`iis.coupon`（二、 2.）、`iis.fees`（四、）、
`iis.rest`（其他）。
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
from pdf_writer import Edit, PdfWriter, require_used
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row, weekday_on_or_after

NAME = STANDARD["product_name"]["bbva"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["bbva"]
ISSUER = STANDARD["issuer_name"]["bbva"]  # 荷蘭商BBVA GLOBAL MARKETS, B.V.
ISSUER_EN = "BBVA GLOBAL MARKETS, B.V."
PRODUCT_TYPE = STANDARD["product_type"]["bbva"]
DIST = STANDARD["distributor"]
PHONE = "+886 2 5556 1313"  # 審查標準 phone_equivalents 的寫法
GUARANTOR = "西班牙商虛構銀行股份有限公司（Fictional Bank, S.A.）"
AGENT = '西班牙商虛構銀行股份有限公司臺北分公司("Fictional Taipei Branch")'
ISIN = "XS1999900071"
APPROVAL = dt.date(2026, 6, 11)
Q4 = Decimal("0.0001")
TICKERS = ("ZBA UW", "ZBB UN", "ZBC UW", "ZBD UW")
PRICING = {"close": "收盤價", "open": "開盤價", "vwap": "VWAP"}  # 參考條件表「期初定價」


def underlyings(count: int) -> tuple[UL, ...]:
    """BBVA 合成文件的標的：美股期初價 100n + 0.45。"""
    return tuple(
        UL(f"虛構控股{i}公司 (FICT{i} CORP)", "納斯達克交易所", TICKERS[i - 1], Decimal(100 * i) + Decimal("0.45"))
        for i in range(1, count + 1)
    )


def next_weekday(d: dt.date) -> dt.date:
    return weekday_on_or_after(d + dt.timedelta(days=1))


@dataclass
class Spec(ProductSpec):
    """BBVA 合成說明書的規格：商品規格加上 BBVA 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count` 產生、最終比價日與到期日由日期表推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    """

    issuer: str = "BBVA"
    product_code: str = "326199990001"
    ko_obs: str = "D"  # D 每日（配息觀察期間表＋觀察期間）／P 每期（配息日表＋自動提前贖回觀察日表，只有記憶式）
    ki: str = "AM"  # none／AM
    tenor: int = 6
    first_callable: int = 1
    memory: bool = True
    annual: Decimal = Decimal("18.00")
    strike: Decimal = Decimal("75.00")
    ko: Decimal = Decimal("100.00")
    ki_pct: Decimal = Decimal("60.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    pricing: str = "close"  # 參考條件表期初定價（說明書沒寫）
    trustee_detail: bool = True  # 第 1 頁 13. 審查日期後的受託機構明細 (i)～(iv)
    approval: dt.date = APPROVAL
    iis_pages: int = 4

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count)
        self.final_date = self.ends[-1]
        self.maturity_date = self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期配息觀察期間終止日（= 每期自動提前贖回觀察日）：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def starts(self) -> list[dt.date]:
        """各期配息觀察期間起始日：第 1 期為發行日，其後為前期終止日後的下一個平日。"""
        return [self.issue_date, *(next_weekday(d) for d in self.ends[:-1])]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=2)) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh)

    @property
    def name_en(self) -> str:
        return NAME["en"].format(tenor=self.tenor, ccy=self.ccy)

    @property
    def redemption_start(self) -> dt.date:
        return next_weekday(self.issue_date)

    @staticmethod
    def date(d: dt.date) -> str:
        return f"{d.year} 年{d.month} 月{d.day} 日"

    @staticmethod
    def date_iis(d: dt.date) -> str:
        return f"{d.year}年{d.month}月{d.day}日"

    def money(self, x: int | Decimal) -> str:
        return f"{Decimal(x):,.0f} {self.currency_zh}"


def _warnings() -> list[str]:
    """第 1 頁警語 (1)～(9) 與第三章 5.(1)～(9)：(1) 為固定警語。"""
    return [
        WARNING,
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息。",
        f"本商品雖經{DIST['name']}審查，並不代表證實申請事項或保證該境外結構型商品之價值。",
        f"本商品持有期間如有保證配息收益，係由{ISSUER_EN}（發行機構）或{GUARANTOR}（保證機構）保證。",
        f"本中文產品說明書之內容如有虛偽或隱匿之情事者，其餘內容應由{AGENT}及其負責人依法負責。",
        "本商品條款及條件之準據法為英國法，並無商品註冊地；本商品係依境外結構型商品管理規則於中華民國境內受託投資或受託買賣。",
        "投資人未清楚瞭解產品說明書、受託或銷售契約條款及所有銷售文件內容前，請勿於相關文件簽名或蓋章。",
        f"{DIST['name']}應提供專業投資人相關契約審閱期間。",
    ]


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（以字數近似）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts"):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def text(self, x: float, text: str, cont: float | None = None, size: float = 10, gap: float = 13) -> None:
        """一段文字：先整段改字，再依寬度（中文 1 字、英數半字）分行。"""
        text = self.w.edit(text)
        if not text:
            return
        limit = (560 - x) / size
        cur, used, here = "", 0.0, x
        with self.w.verbatim():
            for ch in text:
                wid = 0.55 if ord(ch) < 256 else 1.0
                if used + wid > limit and cur.strip() and not (ch.isalnum() and ord(ch) < 128 and cur[-1:].isalnum()):
                    self.w.line(here, cur.strip(), size=size, gap=gap)
                    cur, used, here = "", 0.0, cont if cont is not None else x
                    limit = (560 - here) / size
                cur += ch
                used += wid
            self.w.line(here, cur.strip(), size=size, gap=gap)

    def chapter(self, zh: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{zh}"
        self.w.line(28, f"{zh}、{name}", gap=18)

    def article(self, n: int, title: str) -> None:
        if self.section.startswith("art") or self.section == "ch一":
            self.section = f"art{n}"
        self.text(28, f"{n}. {title}", 50)

    def furnish(self) -> fitz.Document:
        """每頁頁底「N/M」頁碼（真實文件的頁碼，擷取時排除）。"""
        doc = self.w.doc
        for p, page in enumerate(doc):
            page.insert_text((288, 826), f"{p + 1}/{len(doc)}", fontname="helv", fontsize=9)
        return doc


# ---------------------------------------------------------------- 說明書


def _cover(d: _Doc, s: Spec) -> None:
    w = d.w
    w.line(220, "(僅適用於專業投資人)", gap=20)
    w.line(234, "中文產品說明書", gap=22)
    d.text(
        28, "本產品說明書所使用之條款係依據2030 年1 月1 日所補充之基本公開說明書所載之證券一般條件所使用之定義而訂定。"
    )
    w.space(8)
    items = [
        f"商品代號：{s.product_code}",
        f"受託機構商品代號：{s.product_code}",
        f"國際證券編碼ISIN：{ISIN}",
        "商品中文名稱：",
        "商品英文名稱：",
        f"商品種類：{PRODUCT_TYPE}",
        "發行機構註冊地：荷蘭",
        f"計價幣別：{s.currency_zh}",
        "總代理人之名稱、電話及地址：",
        "保證機構之名稱及地址：",
        f"受託機構之名稱、電話及地址：{DIST['name']}，電話：{PHONE}，地址：{DIST['address']}",
        "公會審查通過之日期及文號：不適用",
        f"受託機構審查通過之日期及文號：{s.date(s.approval)}",
        "商品之投資風險警語：",
    ]
    for n, item in enumerate(items, 1):
        d.text(28, f"{n}. {item}", 52)
        if n == 4:
            d.text(52, f"{s.name_zh} (下稱「本商品」)")
        elif n == 5:
            d.text(52, f'{s.name_en} (the "Product")')
        elif n == 9:
            d.text(52, f"{AGENT}，電話：Tel: 02-0000-0000，地址：臺北市虛構路1 號")
        elif n == 10:
            d.text(52, f"{GUARANTOR}，地址：1 Fictional Street, Madrid Spain")
        elif n == 13 and s.trustee_detail:
            for t in (
                f"(i) 事業名稱:{DIST['name']}",
                "(ii) 設立日期:西元2000 年11 月20 日",
                f"(iii) 營業所在地:{DIST['address']}",
                f"(iv) 負責人:{DIST['chairman']}",
            ):
                d.text(52, t)
    d.section = "warn"
    for k, t in enumerate(_warnings(), 1):
        d.text(56, f"({k}) {t}", 78)
    d.section = "cover"
    d.text(28, "投資人應詳閱本產品說明書之內容，並應注意本商品之風險事項。")
    d.text(364, f"產品說明書刊印日期 {s.date(s.trade_date)}")


def _underlying_table(d: _Doc, s: Spec) -> None:
    """第 9 項連結標的表：k／公司／股票幣別／國際證券編碼ISIN／螢幕頁面（彭博代碼）／交易所；沒有價格。"""
    w = d.w
    w.row(
        [
            (34, "k"),
            (80, "連結標的股票/"),
            (193, "股票幣別"),
            (259, "國際證券編碼"),
            (369, "螢幕頁面"),
            (487, "交易所"),
        ],
        gap=13,
        size=9,
    )
    w.row([(80, "標的股票公司"), (283, "ISIN"), (357, "（彭博代碼）")], gap=18, size=9)
    for n, u in enumerate(s.underlyings, 1):
        name, en = u.name.split(" (")
        w.row(
            [
                (34, str(n)),
                (52, name),
                (191, "美元"),
                (254, f"US000000000{n}"),
                (348, f"{u.ticker} Equity"),
                (449, u.exchange),
            ],
            gap=13,
            size=9,
        )
        w.row([(52, f"({en}"), (449, "（NASDAQ GS）")], gap=18, size=9)


def _price_table(d: _Doc, s: Spec) -> None:
    """第 14 項與第 16 項價格表：k／彭博代碼／期初價格／出場值／轉換比例／（下限值），欄序 KO → K → KI。"""
    w = d.w
    w.need(80 + 26 * s.count)
    cols = [
        (263, ["出場值X 期初價", "格（期初價格的", f"{s.ko}%）"]),
        (367, ["轉換比例X 期初", "價格（期初價格", f"的{s.strike}%）"]),
    ]
    if s.ki != "none":
        cols.append((471, ["下限值X 期初價", "格（期初價格的", f"{s.ki_pct}%）"]))
    w.row([(34, "k"), (73, "螢幕頁面"), (177, "期初價格"), *((x, t[0]) for x, t in cols)], gap=12, size=9)
    w.row([(61, "（彭博代碼）"), *((x, t[1]) for x, t in cols)], gap=12, size=9)
    w.row([(x + 15, t[2]) for x, t in cols], gap=20, size=9)
    for n, u in enumerate(s.underlyings, 1):
        prices = [price(u.initial, s.ko), price(u.initial, s.strike)]
        if s.ki != "none":
            prices.append(price(u.initial, s.ki_pct))
        cells = [(34, str(n)), (54, f"{u.ticker} Equity"), (165, f"{u.initial:,.4f} 美元")]
        cells += [(x + 7, f"{p:,.4f} 美元") for (x, _), p in zip(cols, prices, strict=True)]
        w.row(cells, gap=26, size=9)


def _article12(d: _Doc, s: Spec) -> None:
    d.text(50, f"(1) 商品年期：{s.tenor} 個月", 74)
    d.text(50, f"(2) 交易日：{s.date(s.trade_date)}", 74)
    d.text(50, f"(3) 發行日：{s.date(s.issue_date)}", 74)
    d.text(
        50,
        f"(4) 到期日：{s.date(s.maturity_date)}或若該日非營業日，則為緊接該日後之營業日（惟若該日將進入下一日曆月，則到期日將提前至該日前之營業日）",
        74,
    )
    k = s.first_callable
    if s.ko_obs == "D":
        d.text(50, "(5) 配息觀察期間：載列於下表。", 74)
        d.section = "art12.table"
        w = d.w
        w.row([(145, "配息觀察期間")], gap=20)
        w.row([(90, "i"), (153, "起始日(含)"), (305, "終止日(含)"), (468, "配息日")], gap=16)
        for j in range(s.tenor):
            w.row(
                [(89, str(j + 1)), (135, s.date(s.starts[j])), (288, s.date(s.ends[j])), (440, s.date(s.payments[j]))],
                gap=16,
            )
        d.section = "art12"
        d.text(78, "一旦自動提前贖回事件發生，配息金額將停止累積計算，並且於自動提前贖回日之後，將不再支付配息金額。")
        d.text(
            50,
            "(6) 配息日：下列(i)或(ii)（以較早發生者為準）：(i) 預定的配息日載列於上表；或(ii) 緊隨自動提前贖回事件發生後的自動提前贖回日。",
            74,
        )
        d.text(50, "(7) 配息日之營業日原則：「修正式次營業日」。", 74)
        d.text(
            50,
            "(8) 自動提前贖回日(Automatic Early Redemption Date(s))：發生自動提前贖回事件之自動提前贖回觀察日後的第2 個營業日。",
            74,
        )
        d.text(
            50,
            "(9) 自動提前贖回觀察日(Automatic Early Redemption Valuation Date(s))：自動提前贖回觀察期間內之每個預定交易日。",
            74,
        )
        d.text(
            50,
            f"(10) 自動提前贖回觀察期間 (Automatic Early Redemption Valuation Period)：自{s.date(s.ends[k - 1])}（含）起至贖回評價日（不含）止之期間。",
            74,
        )
    else:
        d.text(50, "(5) 配息期間：自一個配息期間結束日（含）起至下一個配息期間結束日（不含）止之每個期間。", 74)
        d.text(50, "(6) 配息期間結束日之營業日原則：不隨營業日原則作出調整。", 74)
        d.text(
            50, "(7) 配息日：下列(i)或(ii)（以較早發生者為準）：(i) 預定的配息日載列於下表；或(ii) 自動提前贖回日。", 74
        )
        d.section = "art12.table"
        w = d.w
        w.row([(87, "i"), (152, "配息日")], gap=16)
        for j in range(s.tenor):
            w.row([(86, str(j + 1)), (124, s.date(s.payments[j]))], gap=16)
        d.section = "art12"
        d.text(50, "(8) 配息日之營業日原則：「修正式次營業日」。", 74)
        d.text(
            50,
            "(9) 自動提前贖回觀察日(Automatic Early Redemption Valuation Date(s))、自動提前贖回日(Automatic Early Redemption Date(s))、觀察日 (Observation Date(s))：載列於下表。",
            74,
        )
        d.section = "art12.table.ko"
        w.row([(81, "ij"), (117, "自動提前贖回觀察日"), (280, "自動提前贖回日"), (431, "j"), (489, "觀察日")], gap=16)
        for t, j in enumerate(range(k - 1, s.tenor - 1), 1):
            w.row(
                [
                    (81, str(t)),
                    (125, s.date(s.ends[j])),
                    (276, s.date(s.payments[j])),
                    (429, str(t)),
                    (461, s.date(s.ends[j])),
                ],
                gap=16,
            )
        d.section = "art12"
        d.text(
            50,
            f"(10) 自動提前贖回觀察期間（Automatic Early Redemption Valuation Period）: 自{s.date(s.ends[k - 1])}（含）起至{s.date(s.ends[s.tenor - 2])}（含）止之期間。",
            74,
        )
    d.text(50, f"(11) 贖回評價日：{s.date(s.final_date)}", 74)
    d.text(
        50, "(12) 預定交易日 (Scheduled Trading Day)：各交易所預定開市買賣組成籃子中所有連結標的股票之任何一天。", 74
    )


def _article13(d: _Doc, s: Spec) -> None:
    if s.ko_obs == "D":
        d.text(50, "(1) 就每單位面額及配息日而言，配息金額＝每單位面額 X 配息率", 71)
        d.text(71, "配息率（Rate of Interest）：就各配息日而言，配息率將由計算代理機構依照下列公式計算而決定：")
        d.text(227, "年利率(i) ∗ 1/m ∗ n/N")
        d.text(71, f"年利率：{s.annual}%")
        d.text(71, "m：12")
        d.text(71, "n：就各配息觀察期間而言，配息觀察期間內之預定交易日總數", 88)
        d.text(71, "N：就各配息觀察期間而言，該配息觀察期間的預定交易日總數", 88)
    else:
        d.text(50, "(1) 就每單位面額、配息期間及配息日而言，配息金額＝每單位面額X 配息率X 計息天數慣例", 71)
        d.text(71, f"配息率（Rate of Interest）：{s.monthly}%")
        d.text(71, "計息天數慣例 （Day Count Fraction）：「1/1｣（如證券一般條件第4(k)條所定義）")


def _article14(d: _Doc, s: Spec) -> None:
    d.text(50, "(1) 到期贖回計算公式")
    d.text(50, "若「自動提前贖回事件」未曾發生，發行機構將於到期日支付到期贖回金額。")
    d.text(50, "到期乘數：")
    barrier = (
        f"若於贖回評價日之最差表現值大於或等於{s.ki_pct}%（下限值）：100%"
        if s.ki != "none"
        else f"若於贖回評價日之最差表現值大於或等於{s.strike}%：100%"
    )
    best = f"若所有連結標的之最佳表現值大於或等於{s.ko}%（出場值）：100%"
    conds = ([barrier] + ([best] if s.memory else [])) if s.ko_obs == "D" else [best, barrier]
    conds.append("否則，發行機構將不須支付到期贖回金額，且適用「實物交割」。")
    for letter, c in zip("ABC", conds, strict=False):
        d.text(50, f"({letter}) {c}")
    d.text(55, f"* 轉換比例 (Put Strike Percentage)：{s.strike}%")
    d.text(55, "* 交割營業日：紐約")
    d.section = "art14.table"
    _price_table(d, s)
    d.section = "art14"
    d.text(64, "(2) 最低保證收益率：本商品不保本，亦無最低收益之保證")


def _article15(d: _Doc, s: Spec) -> None:
    d.text(50, "自動提前贖回金額 = 每單位面額 X 100%")
    if s.memory:
        d.text(
            50,
            f"「自動提前贖回事件 (Automatic Early Redemption Event)」：就任一自動提前贖回觀察期間之自動提前贖回觀察日而言，籃子中每個連結標的之「最佳表現值｣均大於或等於{s.ko}%。",
        )
        d.text(
            50,
            "為避免疑義，為認定自動提前贖回事件是否發生，籃子中每個連結標的之「最佳表現值｣不須出現在同一個自動提前贖回觀察日。",
        )
    else:
        d.text(
            50,
            f"「自動提前贖回事件 (Automatic Early Redemption Event)」：就任一自動提前贖回觀察期間之自動提前贖回觀察日而言，「最差表現值｣大於或等於{s.ko}%。",
        )
    d.text(50, "* 期初價格 (RI Initial Value)：就任一連結標的而言，依第14 項所載列之價格。")


def _article16(d: _Doc, s: Spec) -> None:
    d.text(50, "假設：")
    d.text(50, f"• 總投資金額 = 10 單位面額 = {s.money(10 * s.denom)}")
    d.text(50, f"• 商品年期：{s.tenor} 個月")
    if s.ko_obs == "D":
        d.text(50, f"• 年利率：{s.annual}%")
    else:
        d.text(50, f"• 配息率：每月利率{s.monthly}%（年利率相當於約{s.annual}%（僅供參考，以每月利率為準））")
    d.section = "art16.table"
    _price_table(d, s)
    d.section = "art16"
    d.text(29, "情境一：較佳情況")
    d.text(29, "假設於自動提前贖回事件觀察期間的第一個自動提前贖回事件觀察日發生自動提前贖回事件：")
    k = s.first_callable
    periods = "第1 個" if k == 1 else f"第1至{k}個"
    if s.ko_obs == "D":
        d.text(29, f"{periods}配息觀察期間之配息金額＝10 單位面額X 配息率＝10 X {s.money(s.denom)} X {s.monthly}%")
    else:
        d.text(
            29,
            f"第1 個配息日之配息金額＝10 單位面額X 配息率X 計息天數慣例＝10X{s.denom * s.monthly / 100:,.2f} {s.currency_zh}",
        )
    d.text(29, "情境二：中間情況")
    d.text(29, "假設未曾發生自動提前贖回事件。")


ARTICLE_TITLES = {
    1: "商品名稱：",
    2: "商品風險程度：",
    3: "發行機構或保證機構名稱及其長期債務信用評等：",
    5: "計價幣別：",
    9: "連結標的資產(例如:指數或個股名稱等)，及其相對權重、與投資績效之關連情形",
    12: "商品年期、發行日、到期日及其他依個別商品性質而定之日期",
    13: "配息資料及其計算公式",
    14: "到期贖回計算公式，包含最低保證收益率及參與率",
    15: "自動提前贖回計算公式",
    16: "投資收益計算方法",
}


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 BBVA 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    _cover(d, s)

    d.chapter("一", "商品基本資料")
    for n in range(1, 26):
        title = ARTICLE_TITLES.get(n, f"第{n}項說明（不核對）")
        if n == 1:
            title += s.name_zh
        elif n == 2:
            title += WARNING
        elif n == 5:
            title += s.currency_zh
        d.article(n, title)
        if n == 3:
            d.text(50, f"(1) 發行機構：{ISSUER_EN}")
            d.text(50, "發行機構信用評等：虛構評等")
            d.text(50, f"(2) 保證機構：{GUARANTOR}")
        elif n == 9:
            d.text(53, "(1) 連結標的資產")
            d.section = "art9.table"
            _underlying_table(d, s)
            d.section = "art9"
            d.text(57, "(3) 相對權重：不適用。")
        elif n == 12:
            _article12(d, s)
        elif n == 13:
            _article13(d, s)
        elif n == 14:
            _article14(d, s)
        elif n == 15:
            _article15(d, s)
        elif n == 16:
            _article16(d, s)

    d.chapter("二", "相關機構事業概況")
    d.text(28, "1. 發行機構")
    for t in (
        "(1) 設立日期及簡介(包括但不限於事業名稱、營業所在地、負責人姓名)",
        f"(a) 事業名稱：{ISSUER}",
        "(b) 設立日期：西元2009 年10 月29 日",
    ):
        d.text(50, t)
    d.text(28, "2. 保證機構")
    d.text(50, f"(a) 事業名稱：{GUARANTOR}")
    d.text(28, "3. 總代理人、計算代理機構、受託機構、保管機構及其他相關機構:設立日期及簡介")
    for t in (
        "(1) 總代理人：",
        f"事業名稱：{AGENT}",
        "(4) 受託機構：",
        f"事業名稱：{DIST['name']}",
        "設立日期：2000年11月20日",
        f"營業所在地：{DIST['address']}",
        f"負責人姓名：{DIST['chairman']}",
        "(5) 發行及付款代理機構：",
        "事業名稱：Fictional Bank AG",
    ):
        d.text(46 if t.startswith("(") else 64, t)
    d.text(28, "4. 交易架構說明：不核對。")

    d.chapter("三", "商品風險揭露")
    d.text(28, "1. 基本風險資訊:")
    d.text(28, "5. 該商品之投資風險警語")
    for k, t in enumerate(_warnings(), 1):
        d.text(50, f"({k}) {t}", 72)

    d.chapter("四", "一般交易事項")
    d.text(28, "1. 商品開始受理申購、開始受理贖回日期及後續受理贖回日期", 50)
    d.text(50, f"(1) 商品開始受理申購日期：{s.date(s.trade_date)}")
    d.text(50, f"(2) 開始受理透過次級市場買賣之贖回日期：{s.date(s.redemption_start)}")
    d.text(28, "2. 投資人應負擔的各項費用及金額或計算基準之表列")
    for cells in (
        [(82, "費用項目"), (194, "費率(百分比)"), (295, "收取時點"), (408, "收取方式"), (517, "收取人")],
        [(33, "申購費用"), (189, "申購價金的"), (281, "投資人向受託")],
        [(189, "0%~5%")],
        [(33, "提前贖回費用"), (189, "投資人提前贖"), (281, "投資人向受託")],
        [(189, "回價金的0%~5%")],
        [(33, "管理費用(信託管理費或管"), (189, "無"), (281, "不適用")],
        [(33, "分銷費用"), (111, "報酬"), (189, "無"), (281, "不適用")],
        [(33, "（如屬發行"), (111, "費用"), (189, "申購價金的"), (281, "於本商品發行")],
        [(189, "0%~5%")],
        [(111, "折讓"), (189, "無"), (281, "不適用")],
        [(33, "保費費用"), (189, "不適用"), (281, "不適用")],
        [(33, "其他費用"), (189, "不適用"), (281, "不適用")],
    ):
        d.w.row(cells, gap=14, size=9)
    d.text(50, "分銷費用將反映於本商品之淨值。")
    d.text(28, "3. 商品交易架構：不核對。")
    d.text(28, "4. 最低申購金額及最低加購金額")
    m = s.money(s.denom)
    d.text(50, f"(1) 每單位面額：{m}")
    d.text(50, f"(2) 最低申購金額：{m}（相當於一單位面額）")
    d.text(50, f"(3) 最低加購金額：{m}（相當於一單位面額）")
    d.text(28, "5. 申購價金之計算")
    d.text(50, "商品申購價金＝總申購單位數 X 每單位商品面額 X 發行價格(商品面額的100%) + 申購費用（如適用）。")
    d.text(28, "6. 申購手續及資金給付方式：不核對。")
    d.text(28, "7. 發行不成立之情形：發行機構可全權決定取消本商品之發行。")
    d.text(28, "8. 最低贖回金額或單位數")
    d.text(50, f"最低贖回金額為{m}（每單位）。")
    for n in range(9, 18):
        d.text(28, f"{n}. 第{n}項說明（不核對）")

    d.chapter("五", "特別記載事項")
    d.text(28, "1. 投資人之申購及贖回，須經境外發行機構確認後始生效力")
    d.chapter("六", "存託憑證條款相關說明")
    d.text(28, "1. 「連結標的股票」應被視為包括發行條款中指定的相關存託憑證。")
    doc = d.furnish()
    d.w.flush()
    doc.save(path)
    doc.close()
    require_used(edits, used)
    return path


# ---------------------------------------------------------------- 投資人須知


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """BBVA 中文投資人須知（docs/templates/bbva-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis")
    w = d.w
    di = s.date_iis
    w.line(226, "(僅適用於專業投資人)", gap=16)
    w.line(238, "中文投資人須知", gap=16)
    d.text(18, f'{s.name_en} (the "Product")', 18, size=9)
    d.text(18, f"{s.name_zh} (ISIN: {ISIN}) (下稱「商品」或「本商品」)(商品種類：{PRODUCT_TYPE})", 18, size=9)
    w.line(122, f"TDCC 商品代號：{s.product_code} 受託機構商品代號：{s.product_code}", size=9)
    w.line(14, "商品之投資風險警語：", size=9)
    d.section = "warn"
    for k, t in enumerate(_warnings(), 1):
        d.text(14, f"{k}. {t.replace('本中文產品說明書', '本中文投資人須知')}", 36, size=9)
    d.section = "org"
    w.line(14, "一、 相關機構事業概況", size=9)
    for t in (
        f"1. 發行機構: {ISSUER} (營業所在地：1 Fictional Street, Madrid)",
        f"2. 保證機構：{GUARANTOR}(營業所在地：1 Fictional Street, Madrid)",
        f"3. 總代理人：{AGENT} (營業所在地：臺北市虛構路1號)",
        f"4. 受託機構：{DIST['name']}(電話: {PHONE}，營業所在地：{DIST['address']})",
    ):
        d.text(14, t, 32, size=9)
    d.section = "summary"
    w.line(14, "二、 本商品事項", size=9)
    w.line(14, "1. 商品簡介", size=9)
    for t in (
        "(1) 受託或銷售對象：專業投資人",
        "(2) 與國外相當之交易條件：專業投資人不適用",
        f"(3) 本商品風險程度：{WARNING}",
        "(4) 發行機構或保證機構之長期債務信用評等：虛構評等",
        "(5) 本商品之發行評等：不適用",
        f"(6) 計價幣別：{s.currency_zh}",
        "(7) 計價貨幣本金保本率：不適用。",
        "(8) 投資本金達成100%保本之各項條件：不適用",
        "(9) 連結標的資產：",
    ):
        d.text(17, t, 35, size=9)
    for n, u in enumerate(s.underlyings, 1):
        w.row(
            [
                (20, f"{n} {u.name}"),
                (202, "美元"),
                (262, f"US000000000{n}"),
                (351, f"{u.ticker} Equity"),
                (448, u.exchange),
            ],
            gap=13,
            size=8,
        )
    for t in (
        f"(10) 商品年期：{s.tenor}個月",
        f"(11) 發行日：{di(s.issue_date)}",
        f"(12) 到期日：{di(s.maturity_date)}或若該日非營業日，則為緊接該日後之營業日",
        "(13) 交易日、配息觀察期間、配息日、自動提前贖回日及贖回評價日等：請參見中文產品說明書第一章第12項。",
        f"(14) 開始受理透過次級市場買賣之贖回日期：{di(s.redemption_start)}",
        "(15) 後續受理透過次級市場買賣之贖回日期：自開始受理透過次級市場買賣之贖回日期起往後之每一個預定交易日。",
    ):
        d.text(14, t, 35, size=9)
    d.section = "coupon"
    w.line(14, "2. 收益分配事項：", size=9)
    w.line(17, "(1) 配息金額", size=9)
    if s.ko_obs == "D":
        d.text(
            38,
            "「配息率」：就各配息日而言，配息率將由計算代理機構依照下列公式計算而決定：年利率(i) ∗ 1/m ∗ n/N",
            size=9,
        )
        d.text(38, f"年利率：{s.annual}%", size=9)
        d.text(38, "m：12", size=9)
    else:
        d.text(38, "「配息金額＝每單位面額X 配息率X 計息天數｣。", size=9)
        d.text(38, f"配息率（Rate of Interest）：{s.monthly}%。", size=9)
        d.text(38, "計息天數慣例（Day Count Fraction）：「1/1｣（如證券一般條件第4(k)條所定義）", size=9)
    w.line(17, "(2) 到期贖回金額", size=9)
    barrier = (
        f"若於贖回評價日之最差表現值大於或等於{s.ki_pct}%（下限值）：100%"
        if s.ki != "none"
        else f"若於贖回評價日之最差表現值大於或等於{s.strike}%：100%"
    )
    best = f"若所有連結標的之最佳表現值大於或等於{s.ko}%（出場值）：100%"
    conds = ([barrier] + ([best] if s.memory else [])) if s.ko_obs == "D" else [best, barrier]
    conds.append("否則，發行機構將不須支付到期贖回金額，且適用「實物交割」。")
    for letter, c in zip("ABC", conds, strict=False):
        d.text(38, f"({letter}) {c}", size=9)
    w.line(17, "(3) 自動提前贖回金額", size=9)
    event = (
        f"「自動提前贖回事件」：就任一自動提前贖回觀察期間之自動提前贖回觀察日而言，籃子中每個連結標的之最佳表現值大於或等於{s.ko}%。"
        if s.memory
        else f"「自動提前贖回事件」：就任一自動提前贖回觀察期間之自動提前贖回觀察日而言，最差表現值大於或等於{s.ko}%。"
    )
    d.text(38, event, size=9)
    w.line(14, "3. 贖回價金之計算：請參考上述收益分配事項。", size=9)
    w.line(14, "4. 發行不成立之處理：發行機構可全權決定取消本商品之發行。", size=9)
    d.section = "rest"
    w.line(14, "三、 商品風險揭露", size=9)
    d.text(14, "1. 基本風險資訊：投資人可能損失其投資一部分或全部金額。", size=9)
    d.section = "fees"
    w.line(14, "四、 費用", size=9)
    for cells in (
        [(60, "費用項目"), (150, "費率(百分比)"), (250, "收取時點"), (360, "收取方式"), (500, "收取人")],
        [(14, "申購費用"), (140, "申購價金的0%~5%"), (240, "投資人向受託")],
        [(14, "提前贖回費用"), (140, "投資人提前贖回價金的0%~5%"), (240, "投資人向受託")],
        [(14, "管理費用"), (140, "無"), (240, "不適用")],
        [(14, "分銷費用"), (100, "報酬"), (140, "無"), (240, "不適用")],
        [(100, "費用"), (140, "申購價金的0%~5%"), (240, "於本商品發行")],
        [(14, "其他費用"), (140, "不適用"), (240, "不適用")],
    ):
        w.row(cells, gap=12, size=8)
    d.text(14, "分銷費用將反映於本商品之淨值。", size=9)
    d.section = "rest"
    for t in (
        "五、 相關機構之權利、義務及責任",
        "六、 協助投資人權益之保護方式",
        "七、 發行機構、總代理人及受託機構與投資人爭議之處理方式",
    ):
        w.line(14, t, size=9)
        d.text(14, "1. 不核對。", size=9)
    doc = w.doc
    while len(doc) < s.iis_pages:
        w.new_page()
        doc = w.doc
    while len(doc) > s.iis_pages:
        doc.delete_page(len(doc) - 1)
    for n, page in enumerate(doc, 1):
        page.insert_text((291, 838), f"{n}/{s.iis_pages}", fontname="helv", fontsize=9)
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


def check_item(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋與 `spec` 一致的參考條件表一列，經批量入口核對，回傳說明書的
    批量核對項目（BatchItem，含回填決策）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf], config)
    return next(i for i in outcome.items if i.term_sheet == pdf)


def check(tmp_path: Path, spec: Spec | None = None, **kw: Any):
    """同 `check_item`，回傳說明書的 CheckReport。"""
    return check_item(tmp_path, spec, **kw).report


def check_iis(tmp_path: Path, spec: Spec | None = None, *, edits: Sequence[Edit] = (), config=CONFIG, **overrides: Any):
    """同 `check_item`，回傳投資人須知的批量核對項目。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
    return next(i for i in outcome.items if i.term_sheet == iis_path(pdf))
