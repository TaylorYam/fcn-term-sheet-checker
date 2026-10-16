"""DBS 合成說明書（docs/templates/dbs-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：第 1 頁「中文產品說明書(最終版本)」、「• 標籤：值」清單、警語 1.～11. 與刊印日期；段落標題
（商品基本資料…特別記載事項）置中單獨一行，沒有章號；商品基本資料是兩欄表（標籤 x 56、值 x 170，標籤每 10 字換行）。
表1 表頭：欄名一行、「(期初價格的X%)」下一行，欄序 K → KO → KI；每格「美元 123.4567」。情境價格表表頭「（期初價格」／
「X%）」（全形、沒有「的」），每格幣別字與數字分兩行。費用表的分銷費用子列標籤（報酬／費用／折讓）佔費率欄、費率在右邊一欄。
每頁頁底「N / M頁」。改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`（第 1–2 頁）、`ts.warn`
（封面警語）、`ts.basic`（商品基本資料；表1 `ts.basic.table`、日期格 `ts.basic.dates`（表A 各列 `ts.basic.dates.table`）、配息 `ts.basic.coupon`、強制贖回
`ts.basic.ko`、到期贖回 `ts.basic.maturity`）、`ts.scenario`（情境；情境價格表 `ts.scenario.table`、情境三最終價格表 `ts.scenario.final`）、`ts.org`（相關機構
事業概況）、`ts.risk`、`ts.trading`（一般交易事項；費用表 `ts.trading.fees`）、`ts.rest`。
投資人須知（docs/templates/dbs-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
（真實文件寫異體字「視爲」，合成字型畫不出來，改寫「視為」；parser 兩種都收）段落代號 `iis.cover`、`iis.warn`、`iis.org`、`iis.summary`（商品簡介；價格表 `iis.summary.table`）、`iis.terms`（收益分配事項、
贖回價金之計算）、`iis.fees`、`iis.rest`；每頁頁首刊印日期 `iis.head`。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

import fitz

from harness import CONFIG, STANDARD, check_all, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row, weekday_on_or_after

NAME = STANDARD["product_name"]["dbs"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["dbs"]
ISSUER = STANDARD["issuer_name"]["dbs"]
PRODUCT_TYPE = STANDARD["product_type"]["dbs"]
DIST_NAME = "玉山綜合證券股份有限公司(含國際證券業務分公司)"  # 封面、投資人須知（審查標準等價寫法）
DIST_PHONE = "(02)5556-1313"
DIST_ADDRESS = "臺北市105民生東路三段158號6樓"
CHAIRMAN = STANDARD["distributor"]["chairman"]
ISIN = "XS1999900078"
APPROVAL = dt.date(2026, 6, 11)
Q4 = Decimal("0.0001")
TICKERS = ("ZDA UW", "ZDB UN", "ZDC UW", "ZDD UW", "ZDE UN")
EXCHANGE = {"UN": "紐約證券交易所", "UW": "納斯達克證券交易所"}
QUOTE = "美元"  # 標的都是美股
MIN_UNITS = {"USD": 5, "AUD": 7, "JPY": 8, "CNH": 4}  # 最低申購單位數（審查標準 min_amount_multiples.dbs）
# 人民幣：計價幣別括號後的定義（OOS 真實樣本的寫法，Issue #296）
RMB_NOTE = "。「人民幣」是指大陸地區(PRC)的法定貨幣，並可於大陸地區以外離岸交割者。就此定義而言，大陸地區不包括香港特別行政區、澳門特別行政區及台灣"
CN = ("一", "二", "三", "四", "五")


def underlyings(count: int) -> tuple[UL, ...]:
    """DBS 合成文件的標的：美股期初價 100n + 0.45（說明書一律 4 位小數）。"""
    return tuple(
        UL(
            f"虛構科技{CN[i - 1]}公司",
            EXCHANGE[TICKERS[i - 1].split()[1]],
            TICKERS[i - 1],
            Decimal(100 * i) + Decimal("0.45"),
        )
        for i in range(1, count + 1)
    )


@dataclass
class Spec(ProductSpec):
    """DBS 合成說明書的規格：商品規格加上 DBS 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    只支援 KO 每日觀察記憶式（`ko_obs`、`memory` 固定）；KI 為 none 或 AM。標的由 `count` 產生、最終比價日與到期日由表A 推得。
    """

    issuer: str = "DBS"
    product_code: str = "078199990001"
    tenor: int = 6
    first_callable: int = 1
    annual: Decimal = Decimal("10.00")  # ÷ 12 無條件進位 0.8334（四捨五入 0.8333）
    strike: Decimal = Decimal("70.00")
    ko: Decimal = Decimal("100.00")
    ki_pct: Decimal = Decimal("55.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 4  # 標的數
    approval: dt.date = APPROVAL
    iis_pages: int = 4
    split_iis_table: bool = False  # 投資人須知價格表表頭跨頁：欄名在前一頁、「(期初價格的X%)」在下一頁（DB1）
    split_iis_fee: bool = False  # 投資人須知費用表「提前贖回費用」列跨頁（DB2、DB4）

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count)
        self.final_date = self.ends[-1]
        self.maturity_date = self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期觀察期間結束日：每月 21 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 21)) for j in range(1, self.tenor + 1)]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=2)) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_CEILING)

    @property
    def quanto(self) -> bool:
        return self.ccy != "USD"

    @property
    def doc_ccy(self) -> str:
        """文件上的幣別代碼（名稱與計價幣別括號內；人民幣寫 RMB）。"""
        return NAME.get("ccy_aliases", {}).get(self.ccy, self.ccy)

    @property
    def currency_text(self) -> str:
        """計價幣別：之後的寫法（不含句尾）；人民幣多一段定義。"""
        return f"{self.currency_zh}({self.doc_ccy})" + (RMB_NOTE if self.ccy == "CNH" else "")

    def name_values(self, lang: str) -> dict[str, Any]:
        out = {"tenor": self.tenor, "ccy_zh": self.currency_zh, "ccy": self.doc_ccy}
        out[f"quanto_{lang}"] = NAME[f"quanto_{lang}"] if self.quanto else ""
        out[f"ki_{lang}"] = NAME[f"ki_{lang}"] if self.ki != "none" else ""
        out[f"underlying_{lang}"] = NAME[f"basket_{lang}"]
        return out

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(**self.name_values("zh"))

    @property
    def name_en(self) -> str:
        return NAME["en"].format(**self.name_values("en"))

    @staticmethod
    def date(d: dt.date) -> str:
        return f"{d.year}年{d.month:02d}月{d.day:02d}日"

    @staticmethod
    def iis_date(d: dt.date) -> str:
        return f"{d.year} 年{d.month:02d} 月{d.day:02d} 日"

    @property
    def print_date(self) -> dt.date:
        return self.trade_date + dt.timedelta(days=1)

    @property
    def min_units(self) -> int:
        return MIN_UNITS[self.ccy]

    @property
    def k_text(self) -> str:
        return "1" if self.first_callable == 1 else f"1~{self.first_callable}"


def _width(text: str, size: float) -> float:
    return sum(fitz.get_text_length(c, fontname="helv", fontsize=size) if ord(c) < 256 else size for c in text)


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（英數字不拆開）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts", right: float = 540):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"
        self.right = right

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def wrap(self, x: float, text: str, size: float = 10) -> list[str]:
        """依 `x` 到右邊界的寬度斷行（英數字串不拆開）。"""
        import re

        out, cur, used = [], "", 0.0
        for tok in re.findall(r"[\x21-\x7e]+|\s|.", text):
            tw = _width(tok, size)
            if used + tw > self.right - x and cur.strip():
                out.append(cur.strip())
                cur, used = "", 0.0
                if tok.isspace():
                    continue
            cur += tok
            used += tw
        if cur.strip():
            out.append(cur.strip())
        return out

    def text(self, x: float, text: str, gap: float = 13, size: float = 10) -> None:
        text = self.w.edit(text)
        if not text:
            return
        with self.w.verbatim():
            for part in self.wrap(x, text, size):
                self.w.line(x, part, size=size, gap=gap)

    def title(self, text: str, size: float = 10) -> None:
        self.w.need(20)
        self.w.line(297 - _width(text, size) / 2, text, size=size, gap=18)

    def cell(self, label: str, values: Sequence[str], *, lx: float = 56, vx: float = 170, chunk: int = 10) -> None:
        """兩欄表一格：標籤每 `chunk` 字一行（x `lx`）、值逐段（x `vx`），兩欄從同一個 y 開始。"""
        w = self.w
        label = w.edit(label)
        parts = [label[k : k + chunk] for k in range(0, len(label), chunk)]
        w.need(13 * len(parts) + 4)
        top = w.y
        with w.verbatim():
            for k, p in enumerate(parts):
                w.put(lx, top + 13 * k, p)
        for v in values:
            self.text(vx, v)
        w.y = max(w.y, top + 13 * len(parts)) + 5

    def centered_row(self, cells: Sequence[tuple[float, str]], gap: float, size: float = 9) -> None:
        self.w.row([(cx - _width(t, size) / 2, t) for cx, t in cells], gap=gap, size=size)


def _warnings(doc: str = "本中文產品說明書") -> list[str]:
    """封面警語 1.～11.：1. 為固定警語；7. 是允許片語「受託投資或受託買賣」。"""
    return [
        WARNING,
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息。",
        "本商品雖經受託或銷售機構審查，並不代表證實申請事項或保證該境外結構型商品之價值。",
        "本商品持有期間如有保證配息收益和保證保本率，係由發行機構保證，而非由受託或銷售機構保證。",
        f"{doc}之內容如有虛偽或隱匿之情事者，其餘內容應由發行人及其負責人依法負責。",
        "本商品係依境外結構型商品管理規則於中華民國境內受託投資或受託買賣，投資人申購前應詳閱本中文產品說明書。",
        "投資人未清楚瞭解中文產品說明書前，請勿於相關文件簽名或蓋章。",
        "受託或銷售機構應提供專業投資人相關契約審閱期間，其審閱期間不得低於三日。",
        "對於依OSU相關規範為OSU客戶受託買賣之特別風險警語：本商品僅得於OSU對OSU客戶為推介及交易對象。",
        "專業投資人及OSU客戶不適用「金融消費者保護法」之金融消費爭議處理機制。",
    ]


# ---------------------------------------------------------------- 說明書


def _cover(d: _Doc, s: Spec) -> None:
    w = d.w
    w.line(201, "中文產品說明書(最終版本)", size=14, gap=22)
    for label, value in (
        ("商品代號", s.product_code),
        ("受託或銷售機構商品代號", s.product_code),
        ("國際證券編碼ISIN", ISIN),
        ("商品中文名稱", f"{s.name_zh}(下稱「本商品」)"),
        ("商品英文名稱", s.name_en),
        ("商品種類", PRODUCT_TYPE),
        ("計價幣別", s.currency_text + ("。" if s.ccy == "CNH" else "")),
        ("發行機構之名稱", ISSUER),
        ("發行機構註冊地", "新加坡"),
        ("商品註冊地", "無(本商品之受託對象僅限於專業投資人及OSU客戶)"),
        (
            "發行人之名稱、電話及地址",
            "虛構銀行台北分公司(Fictional Bank, Taipei Branch)，電話：(02) 0000-0000，地址：台北市虛構路1號。",
        ),
        ("保證機構之名稱及地址", "本商品無保證機構。"),
        ("受託或銷售機構之名稱、電話及地址", f"{DIST_NAME}、電話：{DIST_PHONE}、地址：{DIST_ADDRESS}。"),
        ("公會審查通過之日期及文號", "不適用(本商品之受託對象僅限於專業投資人及OSU客戶)。"),
        ("受託或銷售機構審查通過之日期及文號", f"{s.approval.year}年{s.approval.month}月{s.approval.day}日；"),
    ):
        w.need(16)
        w.put(50, w.y, "•")
        d.text(68, f"{label}：{value}")
    d.text(50, "本商品之投資風險警語：")
    d.section = "warn"
    for k, t in enumerate(_warnings(), 1):
        d.text(50, f"{k}. {t}")
    d.section = "cover"
    d.text(50, "投資人應詳閱本中文產品說明書之內容，並應注意本商品之風險事項。 (請參閱第16-20頁)")
    d.text(50, f"中文產品說明書刊印日期：{s.date(s.print_date)}。")


TABLE_X = {"code": 98, "initial": 193, "strike": 288, "ko": 383, "ki": 478}  # 表1 各欄中心


def _price_columns(s: Spec) -> list[tuple[str, str, Decimal]]:
    cols = [("strike", "執行價格", s.strike), ("ko", "自動提前出場價格", s.ko)]
    if s.ki != "none":
        cols.append(("ki", "下限觸發價格", s.ki_pct))
    return cols


def _table1(d: _Doc, s: Spec, x: dict[str, float] = TABLE_X, *, split: bool = False) -> None:
    """表1（說明書、投資人須知）：欄名一行、「(期初價格的X%)」下一行；`split` 時兩行之間換頁（投資人須知 DB1）。"""
    cols = _price_columns(s)
    d.w.need(40 + 14 * s.count if not split else 0)
    d.centered_row([(x["code"], "彭博代碼"), (x["initial"], "期初價格"), *[(x[c], t) for c, t, _ in cols]], gap=14)
    if split:
        d.w.new_page()
    d.centered_row([(x[c], f"(期初價格的{p}%)") for c, _, p in cols], gap=14)
    for u in s.underlyings:
        prices = [u.initial] + [price(u.initial, p) for _, _, p in cols]
        d.centered_row(
            [
                (x["code"], u.ticker),
                *[
                    (x[c], f"{QUOTE} {v:.4f}")
                    for c, v in zip(["initial", *[c for c, _, _ in cols]], prices, strict=True)
                ],
            ],
            gap=13,
        )


def _scenario_table(d: _Doc, s: Spec) -> None:
    """情境價格表：表頭「（期初價格」／「X%）」，每格幣別字與數字分兩行。"""
    x = {"code": 207, "initial": 275, "strike": 343, "ko": 411, "ki": 479}
    cols = _price_columns(s)
    d.w.need(50 + 24 * s.count)
    names = {"strike": "執行價格", "ko": "自動提前出場", "ki": "下限觸發價格"}  # 欄窄：「自動提前出場」／「價格」兩行
    d.centered_row(
        [(x["code"], "彭博代碼"), (x["initial"], "期初價格"), *[(x[c], names[c]) for c, _, _ in cols]], gap=11
    )
    d.centered_row([(x["ko"], "價格")], gap=11)
    d.centered_row([(x[c], "（期初價格") for c, _, _ in cols], gap=11)
    d.centered_row([(x[c], f"{p}%）") for c, _, p in cols], gap=12)
    for u in s.underlyings:
        prices = [u.initial] + [price(u.initial, p) for _, _, p in cols]
        keys = ["initial", *[c for c, _, _ in cols]]
        d.centered_row([(x["code"], u.ticker), *[(x[c], QUOTE) for c in keys]], gap=11)
        d.centered_row([(x[c], f"{v:.4f}") for c, v in zip(keys, prices, strict=True)], gap=12)


def _basic(d: _Doc, s: Spec) -> None:
    d.w.new_page()
    d.section = "basic"
    d.title("商品基本資料")
    d.cell("商品名稱：", [s.name_zh.replace("(無擔保及無保證機構)(不保本)", "（無擔保及無保證機構)（不保本）")])
    d.cell("商品風險程度：", ["RR4"])
    d.cell(
        "發行機構名稱及長期債務信用評等：", ["本商品發行機構為新加坡商星展銀行股份有限公司 (DBS Bank Ltd)。虛構評等。"]
    )
    d.cell("計價幣別：", [s.currency_text + ("。" if s.ccy == "CNH" else "")])
    d.cell("商品面額：", [f"每單位{s.currency_zh}{s.denom:,}"])
    d.cell("發行價格：", ["商品面額的100.00%"])
    w = d.w
    top = w.y
    for k, p in enumerate(("連結標的資產及其相對", "權重與投資績效之關連", "情形：")):
        w.put(56, top + 13 * k, p)
    d.centered_row([(212, "連結標的"), (330, "彭博代碼"), (437, "交易所")], gap=13)
    for n, u in enumerate(s.underlyings, 1):
        w.row([(176, str(n), ""), (192, u.name), (309, u.ticker), (392, u.exchange)], gap=14, size=9)
    d.text(170, "相關交易所：所有交易所。")
    d.text(170, "相對權重：不適用。")
    w.y = max(w.y, top + 39) + 5
    d.section = "basic.dates"
    d.cell(
        "商品年期、發行日、到期日及其他依商品性質而定之日期：",
        [
            f"商品年期： {s.tenor}個月。",
            f"發行日：{s.date(s.issue_date)}",
            f"交易日：{s.date(s.trade_date)}",
            f"到期日：如未依下載之強制贖回條款發生自動提前出場事件之情形，為{s.date(s.maturity_date)}，如果定價日被延遲，"
            "則為延遲的定價日後的第二個支付日。",
            f"定價日：{s.date(s.final_date)}，如該日並非所有連結標的之規劃交易日則為其後第一個所有連結標的的規劃交易日"
            "(“預定規劃定價日”)。",
            "表A：",
        ],
    )
    d.section = "basic.dates.table"
    d.centered_row([(227, "觀察期間"), (340, "觀察期間結束日#"), (453, "利息支付日##")], gap=13)
    for j in range(s.tenor):
        d.centered_row([(227, str(j + 1)), (340, s.date(s.ends[j])), (453, s.date(s.payments[j]))], gap=13)
    d.section = "basic.dates"
    d.text(170, "#為避免疑問，觀察期間結束日不會因干擾日而被順延。")
    d.text(170, f"觀察起始日：{s.date(s.print_date)}")
    d.text(170, "觀察日：就一觀察期間而言，指該觀察期間內所有連結標的的所有交易所的每一個規劃交易日。")
    d.section = "basic.coupon"
    d.cell(
        "配息資料及計算公式：",
        [
            "適用，利息金額以計價幣別支付並根據下方公式計算(四捨五入至小數點後第二位數)：商品面額 X 適用之觀察期間的利率",
            f"利率：1) 對於第{s.k_text}個觀察期間而言，利率為最大利率值；及",
            "2) 對於其後每個觀察期間而言，利率將根據以下公式決定：最大利率值 X 累積日數 / 總日數",
            f"最大利率值：就每個觀察期間而言，為{s.monthly}%（即{s.annual}%/12），無條件進位至小數點後第四位。",
        ],
    )
    d.section = "basic.ko"
    d.cell(
        "強制贖回(含相關定義及強制贖回金額給付方式) ：",
        [
            "強制贖回金額：每一單位商品，為100%商品面額。",
            "記憶事件：就每一連結標的而言，如果該連結標的在任一自動提前出場決定日的參考價格大於或等於其自動提前出場價格，"
            "則視為發生記憶事件。",
            "自動提前出場事件：若所有連結標的皆被視為記憶標的，則視為發生自動提前出場事件。",
            f"自動提前出場決定日: 從第{s.first_callable}個觀察期間結束日（包含）開始至預定規劃定價日（包含）為止的期間內的"
            "每一個觀察日。",
            "強制贖回日：自動提前出場事件日後之第二個支付日。",
        ],
    )
    d.section = "basic.table"
    d.w.need(60 + 13 * s.count)
    d.centered_row([(295, "表1")], gap=14, size=10)
    _table1(d, s)
    d.section = "basic.maturity"
    if s.ki == "none":
        terms = [
            "(a) 若表現最差連結標的之最終價格等於或高於其執行價格，將適用現金結算；或者",
            "(b) 若表現最差連結標的之最終價格低於其執行價格，將適用實物結算。",
        ]
    else:
        terms = ["(a) 如果未發生下限觸發事件，將適用現金結算；或者", "(b) 如果發生下限觸發事件，將適用實物結算。"]
    ki = (
        []
        if s.ki == "none"
        else ["下限觸發事件：若任一連結標的於定價日的參考價格小於其下限觸發價格，則視為發生下限觸發事件。"]
    )
    d.cell(
        "到期贖回計算公式，含最終贖回金額、最低保證配息率及參與率：",
        [
            "若未發生自動提前出場事件或發行機構提前贖回之情形且投資人持有本商品至到期日，發行機構將於到期日依下列條款贖回所有商品。",
            *terms,
            "期初價格：就每一連結標的而言，為上述表1所示該連結標的每股或每單位(視情況而定)的價格。",
            *ki,
            "定價時間：指相關交易所的規劃收盤時間。",
            "最低保證配息率：不適用，本商品不保證最低收益率。",
        ],
    )


def _scenario(d: _Doc, s: Spec) -> None:
    d.section = "scenario"
    d.title("商品投資收益計算方法及其他資料")
    m = s.monthly
    d.cell(
        "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
        [
            "(3) 以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
            f"假設：商品年期：{s.tenor}個月",
            f"商品面額 = {s.currency_zh}{s.denom:,}",
            "發行價格 = 100.00%",
            "投資單位數 = 1 單位",
            f"投資金額 = {s.denom:,} X 100.00% X 1= {s.currency_zh}{s.denom:,}",
            f"利率：第{s.k_text}個觀察期間之利率為最大利率值，其後每個觀察期間之利率為最大利率值x累積日數/總日數",
            f"最大利率值：{m}%",
            f"自動提前出場決定日：從第{s.first_callable}個觀察期間結束日（包含）開始至預定規劃定價日（包含）為止的期間內的每一個觀察日",
            "各連結標的之相關價格：",
        ],
    )
    d.section = "scenario.table"
    _scenario_table(d, s)
    d.section = "scenario"
    _scenario_cases(d, s)


def _scenario_cases(d: _Doc, s: Spec) -> None:
    """情境一～四（真實樣本的寫法，Issue #321）：利息算式「面額 X 最大利率值% X 月數」、情境一「第1~k個觀察期間之利息
    金額」、情境三最終價格表與「表現最差連結標的為…其最終價格(x)低於其執行價格／下限觸發價格(y)」；金額寫承作幣別
    （數字是示意，規則不重算）。"""
    m, k, ccy, denom = s.monthly, s.first_callable, s.currency_zh, f"{s.denom:,}"
    d.text(
        172,
        f"1. 有利情況：假設本商品於第{k + 1}個觀察期間的第2個觀察日發生自動提前出場事件，該觀察期間總日數為20：",
    )
    d.text(
        172,
        f"(1) 投資人可取得之利息總計91.67{ccy}，(包括第{s.k_text}個觀察期間之利息金額為83.34{ccy}({denom} X {m}% X "
        f"{k}個月)，及第{k + 1}個觀察期間之利息金額為8.33{ccy}({denom} X {m}% X 2 ÷ 20) )；及",
    )
    d.text(
        172,
        f"(2) 於強制贖回日可取得之強制贖回金額為100%商品面額, 即{ccy}{denom}。較原始投資金額獲利約0.917%，"
        "即年化平均報酬率約為11.000%。",
    )
    d.text(
        172,
        "2. 於到期最佳情況：假設本商品未發生自動提前出場事件，且假設表現最差連結標的之最終價格等於或高於其執行價格 ：",
    )
    d.text(172, f"(1) 投資人可取得之利息總計500.04{ccy}({denom} X {m}% X {s.tenor}個月)；及")
    d.text(
        172,
        f"(2) 於到期日可取得之最終贖回金額為100%商品面額，即{ccy}{denom}。較原始投資金額獲利約5.000%，"
        f"即年化平均報酬率約為{s.annual}%。",
    )
    d.text(172, "3. 較差情況：假設本商品未發生自動提前出場事件，且假設表現最差連結標的之最終價格低於其執行價格。")
    d.text(172, "假設於定價日，各連結標的最終價格如下：")
    d.section = "scenario.final"
    x = {"code": 207, "initial": 275, "final": 343, "perf": 450}
    d.centered_row(
        [(x["code"], "彭博代碼"), (x["initial"], "期初價格"), (x["final"], "最終價格"), (x["perf"], "表現")], gap=12
    )
    finals = [int(u.initial / 2) if n == 0 else int(u.initial) + 10 for n, u in enumerate(s.underlyings)]
    for u, final in zip(s.underlyings, finals, strict=True):
        perf = (final / u.initial * 100).quantize(Q4)
        d.centered_row(
            [
                (x["code"], u.ticker),
                (x["initial"], f"{QUOTE} {u.initial:.4f}"),
                (x["final"], f"{QUOTE} {final}"),
                (x["perf"], f"=（{final}/{u.initial:.4f}）X 100% = {perf}%"),
            ],
            gap=12,
        )
    d.section = "scenario"
    worst = s.underlyings[0]
    word, pct = ("執行價格", s.strike) if s.ki == "none" else ("下限觸發價格", s.ki_pct)
    fx = f"，且假設{s.ccy}USD匯率為0.64" if s.quanto else ""
    d.text(
        172,
        f"依上所示，表現最差連結標的為{worst.ticker}，其最終價格({finals[0]})低於其{word}({price(worst.initial, pct)})"
        f"{fx}，則：",
    )
    d.text(172, f"(1) 投資人可取得之利息總計500.04{ccy}({denom} X {m}% X {s.tenor}個月)；及")
    d.text(
        172,
        f"(2) 發行機構將於到期日就每一單位商品交付99股({denom} ÷ 100.0000 = 99.554)之股票及支付27.70{ccy}"
        f"((0.554) X {finals[0]})。合計現金價值為5,477.74{ccy}(99 X {finals[0]} + 500.04 + 27.70)，較原始投資金額損失約"
        "45.223%，即年化平均報酬率約為-90.446%。",
    )
    d.text(172, "4. 最大損失情況：假設發行機構破產或無力償還本商品下的付款責任，投資人可能無法收到任何金額。")


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 DBS 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    _cover(d, s)
    _basic(d, s)
    _scenario(d, s)
    d.w.new_page()
    d.section = "org"
    d.title("相關機構事業概況")
    for t in (
        "1. 發行機構：",
        f"(1) 事業名稱：{ISSUER}",
        "(2) 設立日期：1968年07月16日。",
        "2. 保證機構：本商品無保證機構。",
        "3. 發行人：",
        "(1) 事業名稱：虛構銀行台北分公司(Fictional Bank, Taipei Branch)",
        "(2) 營業所在地：台北市虛構路1號。",
        "4. 受託或銷售機構：",
        "(1) 事業名稱：玉山綜合證券股份有限公司",
        f"(2) 營業所在地：{DIST_ADDRESS}",
        "(3) 設立日期：2000年11月20日",
        f"(4) 負責人姓名：{CHAIRMAN}",
        "5. 計算代理人：",
        f"(1) 事業名稱：{ISSUER}",
    ):
        d.text(50, t)
    d.section = "risk"
    d.title("商品主要風險揭露")
    d.text(50, "一、基本風險資訊：本商品並非傳統的存款。")
    d.text(50, "五、本商品之投資風險警語：")
    for k, t in enumerate(_warnings(), 1):
        d.text(50, f"{k}. {t}")
    d.w.new_page()
    d.section = "trading"
    d.title("一般交易事項")
    for t in (
        "1. 商品開始受理申購、開始受理贖回日期及後續受理贖回日期：",
        f"(1) 商品開始受理申購日期：{s.date(s.trade_date)}",
        "(2) 開始受理贖回日：發行日後的次一個規劃交易日(並須為臺北營業日)。",
        "2. 投資人應負擔的各項費用及金額或計算基準之表列：",
    ):
        d.text(50, t)
    d.section = "trading.fees"
    w = d.w
    w.need(260)
    top = w.y
    for x, dy, t in (
        (56, 0, "費用項目"),
        (147, 0, "費率（百"),
        (147, 13, "分比）"),
        (198, 0, "收取時點"),
        (283, 0, "收取方式"),
        (410, 0, "收取人"),
    ):
        w.put(x, top + dy, t, size=9)
    w.y = top + 30
    for cells in (
        [(56, "申購費用"), (147, "申購價金"), (198, "投資人向受託或"), (410, "受託或銷售機構")],
        [(147, "的")],
        [(147, "0%~5%")],
        [(56, "提前贖回費用"), (147, "投資人提"), (198, "投資人向受託或"), (410, "受託或銷售機構")],
        [(147, "前贖回價")],
        [(147, "金的")],
        [(147, "0%~5%")],
        [(56, "管理費用（信託"), (147, "無"), (198, "不適用")],
        [(56, "管理費或管銷費")],
        [(56, "分銷費用(如屬發"), (147, "報酬"), (198, "無"), (283, "不適用")],
        [(56, "行機構或發行人"), (147, "費用"), (198, "申購價金的"), (283, "於本商品發行時一次給付")],
        [(56, "給予受託或銷售"), (198, "0%~5%")],
        [(56, "利益應單獨列示)"), (147, "折讓"), (198, "無"), (283, "不適用")],
    ):
        w.row(cells, gap=13, size=9)
    d.section = "trading"
    unit = f"{s.currency_zh}{s.denom:,}"
    for t in (
        "3. 商品交易架構：參閱上述「相關機構事業概況」。",
        f"4. 最低申購金額及最低加購金額：最低申購金額為{s.min_units}單位商品面額，即{s.currency_zh}{s.min_units * s.denom:,}，"
        "並以1單位商品面額為最低加購金額。",
        "5. 申購價金之計算：申購金額(申購單位數 X 商品面額 X 發行價格) + 申購費用",
        "7. 發行或受託不成立之情形：受託投資或受託買賣本商品之總金額未達發行機構設定之最低申購總金額。",
        "8. 最低贖回金額及單位數：",
        f"(3) 投資人申請提前贖回：最低贖回金額為1單位商品面額，即{unit}，並以1單位商品面額為累加單位。",
    ):
        d.text(50, t)
    d.section = "rest"
    d.title("特別記載事項")
    d.text(50, "1. 發行機構有依據中文產品說明書之條款就本商品給付有關款項之義務。")
    doc = w.doc
    for p, page in enumerate(doc):
        page.insert_text((281, 782), f"{p + 1} / {len(doc)}頁", fontname=FONT, fontsize=9)
    w.flush()
    doc.save(path)
    doc.close()
    require_used(edits, used)
    return path


# ---------------------------------------------------------------- 投資人須知


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """DBS 中文投資人須知（docs/templates/dbs-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis", right=575)
    w = d.w
    w.line(226, "中文投資人須知(最終版本)", size=12, gap=16)
    d.text(
        18,
        f"{s.name_zh} (下稱「本商品」) (商品代號：{s.product_code} ) ( 受託或銷售機構商品代號：{s.product_code} ) "
        f"( 國際證券編碼ISIN：{ISIN})",
        size=9,
    )
    d.text(18, s.name_en, size=9)
    d.text(16, "※投資風險警語：", size=9)
    d.section = "warn"
    for k, t in enumerate(_warnings("本中文投資人須知"), 1):
        d.text(16, f"{k}. {t}", size=9)
    d.section = "org"
    d.title("相關機構事業概況", size=9)
    for t in (
        f"1. 發行機構：{ISSUER}；營業所在地：1 Fictional Boulevard, Singapore。",
        "2. 發行人：虛構銀行台北分公司(Fictional Bank, Taipei Branch)；營業所在地：台北市虛構路1號。",
        f"3. 受託或銷售機構：{DIST_NAME}；營業所在地：{DIST_ADDRESS}。",
    ):
        d.text(16, t, size=9)
    d.section = "summary"
    d.title("商品簡介", size=9)
    uls = "、".join(f"{u.name} (Fictional {i} Inc)，彭博代碼為{u.ticker}" for i, u in enumerate(s.underlyings, 1))
    for t in (
        "受託對象：專業投資人及OSU 客戶。",
        "本商品風險程度：RR4",
        f"計價幣別：{s.currency_text}；計價貨幣本金保本率：不適用，本商品為不保障投資本金之境外結構型商品。",
        f"連結標的資產：{uls}。",
    ):
        d.text(16, t, size=9)
    d.section = "summary.table"
    if s.split_iis_table:
        while w.y < 735:  # 表頭欄名排在頁底，「(期初價格的X%)」換到下一頁
            w.space(13)
    _table1(d, s, {"code": 60, "initial": 150, "strike": 262, "ko": 382, "ki": 502}, split=s.split_iis_table)
    d.section = "summary"
    for t in (
        f"商品面額：每單位{s.currency_zh}{s.denom:,}, 發行價格：商品面額的100.00%。",
        f"商品年期：{s.tenor} 個月。交易日：{s.iis_date(s.trade_date)}；發行日：{s.iis_date(s.issue_date)}；到期日：如未依下載之"
        f"強制贖回條款發生自動提前出場事件之情形，為{s.iis_date(s.maturity_date)} ，如果定價日被延遲，則為延遲的定價日後的"
        f"第二個支付日；定價日：{s.iis_date(s.final_date)}，如果被延遲，將根據中文產品說明書條款決定。",
        "開始受理贖回日：發行日後的次一個規劃交易日(並須為臺北營業日)。",
    ):
        d.text(16, t, size=9)
    d.section = "terms"
    d.title("收益分配事項", size=9)
    for label, value in (
        ("配息資料及計算公式：", "本商品每月配發利息。利息金額 = 商品面額 X 適用之觀察期間的利率"),
        (
            "利率：",
            f"對於第{s.k_text} 個觀察期間而言，利率為最大利率值。對於其後每個觀察期間而言，利率將根據以下公式決定。",
        ),
        ("最大利率值：", f"就每個觀察期間而言，為{s.monthly}% ({s.annual}%/12) ，無條件進位至小數點後第四位。"),
    ):
        d.cell(label, [value], lx=22, vx=118, chunk=8)
    d.title("贖回價金之計算", size=9)
    maturity = (
        "(a) 若表現最差連結標的之最終價格等於或高於其執行價格，將適用現金結算；或者"
        if s.ki == "none"
        else "(a) 如果未發生下限觸發事件，將適用現金結算；或者"
    )
    rows = [
        ("強制贖回金額：", "每一單位商品，為100%商品面額。"),
        ("自動提前出場事件：", "若所有連結標的皆被視為記憶標的，則視為發生自動提前出場事件。"),
        (
            "記憶事件：",
            "就每一連結標的而言，如果該連結標的在任一自動提前出場決定日的參考價格大於或等於其自動提前出場價格，則視為發生記憶事件。",
        ),
        ("到期贖回：", maturity),
    ]
    if s.ki != "none":
        rows.append(("下限觸發事件：", "若任一連結標的於定價日的參考價格小於其下限觸發價格，則視為發生下限觸發事件。"))
    for label, value in rows:
        d.cell(label, [value], lx=22, vx=118, chunk=8)
    d.section = "rest"
    d.title("發行或受託不成立之處理", size=9)
    d.text(16, "發行或受託不成立之情形：受託投資或受託買賣本商品之總金額未達發行機構設定之最低申購總金額。", size=9)
    d.title("商品主要風險揭露", size=9)
    d.text(16, "一、基本風險資訊：1.並非傳統的存款。", size=9)
    d.section = "fees"
    d.title("投資人應負擔的各項費用及金額或計算基準之表列", size=9)
    if s.split_iis_fee:
        while w.y < 700:  # 「提前贖回費用」那一列排在頁底，後半換到下一頁
            w.space(13)
    top = w.y
    for x, dy, t in (
        (22, 0, "費用項目"),
        (143, 0, "費率（百"),
        (143, 13, "分比）"),
        (211, 0, "收取時點"),
        (325, 0, "收取方式"),
        (495, 0, "收取人"),
    ):
        w.put(x, top + dy, t, size=9)
    w.y = top + 28
    fee_rows = [
        [(22, "申購費用"), (143, "申購價金"), (211, "投資人向受託或銷")],
        [(143, "的0%~5%")],
        [(22, "提前贖回費用"), (143, "投資人提"), (211, "投資人向受託或銷")],
        "break",
        [(143, "前贖回價"), (211, "售機構提前贖回時")],
        [(143, "金的")],
        [(143, "0%~5%")],
        [(22, "管理費用（信託管理"), (143, "無"), (211, "不適用")],
        [(22, "分銷費用(如"), (100, "報酬"), (143, "無"), (211, "不適用")],
        [(100, "費用"), (143, "申購價金"), (211, "於本商品發行時一")],
        [(143, "的0%~5%")],
        [(100, "折讓"), (143, "無"), (211, "不適用")],
    ]
    for cells in fee_rows:
        if cells == "break":
            if s.split_iis_fee:
                w.new_page()
            continue
        w.row(cells, gap=13, size=9)
    d.section = "rest"
    d.title("相關機構之權利、義務及責任", size=9)
    d.text(16, "1.發行機構有依據中文產品說明書之條款就本商品給付有關款項之義務。", size=9)
    d.title("發行人協助辦理投資人權益保護之方式", size=9)
    doc = w.doc
    while len(doc) < s.iis_pages:
        w.new_page()
        doc = w.doc
    while len(doc) > s.iis_pages:
        doc.delete_page(len(doc) - 1)
    head = w.edit(f"中文投資人須知刊印日期：{s.iis_date(s.print_date)}", "head")
    for n, page in enumerate(doc, 1):
        page.insert_text((304, 24), head, fontname=FONT, fontsize=9)
        page.insert_text((264, 826), f"第{n} 頁/共{s.iis_pages} 頁", fontname=FONT, fontsize=9)
    return w.save(path)


# ---------------------------------------------------------------- 參考條件表與核對


def row(spec: Spec, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列；`overrides` 以 Excel 欄名覆寫。"""
    return reference_row(spec, **overrides)


def check_item(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋投資人須知＋與 `spec` 一致的參考條件表一列，經批量入口核對，
    回傳說明書的批量核對項目（BatchItem，含回填決策）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
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
