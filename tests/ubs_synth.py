"""UBS 合成說明書（docs/templates/ubs-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：第 1 頁「中文產品說明書」、項號「一、」～「十二、」（x 36，值跨行時接在 x 60）、警語 1.～10.、
刊印日期；之後段名單獨一行（商品基本資料、相關機構事業概況、商品風險揭露、投資風險警語、一般交易事項、特別記載事項），
段內項號「一、」起重新起算。每頁頁底「第N頁，共M頁」。價格表（商品基本資料八、十五）表頭一格拆成兩行（欄名、
「（即期初價格的X%）」），欄序 K → KI → KO；價格後接標的報價幣別字。改字製造錯誤用 `edits`（tests/pdf_writer.py 的
`Edit`），段落代號：`ts.cover`（第 1～2 頁）、`ts.warn`（第 1 頁警語）、商品基本資料各項 `ts.art{n}`（配息表
`ts.art8.coupon`、價格表 `ts.art8.table`、第九項標的表 `ts.art9.table`、情境價格表 `ts.art15.table`）、`ts.org`
（相關機構事業概況）、`ts.risk`、`ts.warnings`（投資風險警語段）、`ts.general`（一般交易事項）、`ts.special`。
投資人須知（docs/templates/ubs-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
段落代號 `iis.cover`、`iis.warn`、`iis.org`（相關機構）、`iis.summary`（商品簡介）、`iis.fees`、`iis.rest`。
真實文件每頁的私用區字元 U+F020 合成字型畫不出來，改由 tests/test_iis_ubs.py 直接以擷取後的行測刪除（說明書與投資人須知
共用 `parsers.ubs.clean_lines`）。
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

NAME = STANDARD["product_name"]["ubs"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["ubs"]
ISSUER = STANDARD["issuer_name"]["ubs"]
PRODUCT_TYPE = STANDARD["product_type"]["ubs"]
DIST = STANDARD["distributor"]
PHONE = "+886-2-5556-1313"  # 受託機構電話的國際寫法（審查標準 phone_equivalents）
ISIN = "XS1999900092"
APPROVAL = dt.date(2026, 6, 11)
Q4 = Decimal("0.0001")
TICKERS = {
    "US": ("ZUA UW", "ZUB UN", "ZUC UW", "ZUD UN", "ZUE UW"),
    "JT": ("7301 JT", "7302 JT", "7303 JT", "7304 JT", "7305 JT"),
}
EXCHANGE = {
    "UN": "紐約證券交易所及其繼承者(如有)",
    "UW": "那斯達克證券交易所及其繼承者(如有)",
    "JT": "東京證券交易所及其繼承者(如有)",
}
QUOTE = {"US": "美元", "JT": "日圓"}  # 標的報價幣別
PRICING = {"close": "收盤價", "open": "開盤價"}  # 參考條件表「期初定價」（說明書沒寫；VWAP 是 UBS 不支援樣態）
CN = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
ZH_NUM = (
    "一 二 三 四 五 六 七 八 九 十 十一 十二 十三 十四 十五 十六 十七 十八 十九 二十 二十一 二十二 二十三 二十四 二十五 二十六"
).split()


def underlyings(count: int, market: str, long_name: bool = False) -> tuple[UL, ...]:
    """UBS 合成文件的標的：美股期初價 100n + 0.45，日股 1000n + 500（說明書一律 4 位小數）；`long_name` 時第 1 檔名稱
    長到價格表裡要拆兩行（真實樣本：`甲乙丙半導體公` ／ `司`）。"""
    out = []
    for i in range(1, count + 1):
        ticker = TICKERS[market][i - 1]
        initial = Decimal(1000 * i + 500) if market == "JT" else Decimal(100 * i) + Decimal("0.45")
        name = f"虛構控股{CN[i - 1]}半導體股份有限公司" if long_name and i == 1 else f"虛構控股{CN[i - 1]}公司"
        out.append(UL(name, EXCHANGE[ticker.split()[1]], ticker, initial))
    return tuple(out)


@dataclass
class Spec(ProductSpec):
    """UBS 合成說明書的規格：商品規格加上 UBS 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count`、`market` 產生、最終比價日與到期日由配息表推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    """

    issuer: str = "UBS"
    product_code: str = "092199990001"
    ko_obs: str = "D"  # D 每日（配息觀察期間表）／P 每期（配息觀察日表）
    ki: str = "none"  # none／D／P／AM
    tenor: int = 6
    first_callable: int = 1
    memory: bool = True
    annual: Decimal = Decimal("12.00")
    strike: Decimal = Decimal("70.00")
    ko: Decimal = Decimal("100.00")
    ki_pct: Decimal = Decimal("60.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    market: str = "US"  # 標的市場：US 美股、JT 日股
    pricing: str = "close"  # 參考條件表期初定價（說明書沒寫）
    approval: dt.date = APPROVAL
    iis_pages: int = 4
    trustee_label: str = "受託機構"  # 第 1 頁商品代號與受託機構名稱的標籤（新版另有「受託機構/銷售機構」）
    issue_price_amount: bool = True  # 發行價格「100%，即X幣別」；False 寫「每單位面額之100%」（沒有金額）
    ko_pct_in_clause: bool = False  # 非記憶式多標的的 KO 條款寫出「提前出場價（即期初價格的Y%）」
    long_name: bool = False  # 第 1 檔標的名稱在價格表拆兩行

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count, self.market, self.long_name)
        self.final_date = self.ends[-1]
        self.maturity_date = self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期配息觀察日（KO 每期）／配息觀察期間結束日（KO 每日）：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def starts(self) -> list[dt.date]:
        """KO 每日型配息觀察期間開始日：第 1 期是發行日後的平日，之後是上一期結束日的下一個平日。"""
        first = weekday_on_or_after(self.issue_date + dt.timedelta(days=1))
        return [first] + [weekday_on_or_after(d + dt.timedelta(days=1)) for d in self.ends[:-1]]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=3)) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    def flags(self, lang: str) -> dict[str, str]:
        return {
            f"ko_observation_{lang}": NAME[f"{'daily' if self.ko_obs == 'D' else 'periodic'}_{lang}"],
            f"memory_{lang}": NAME[f"memory_{lang}"] if self.memory else "",
            f"ki_{lang}": NAME[f"ki_{lang}"] if self.ki != "none" else "",
        }

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(**self.flags("zh"))

    @property
    def name_en(self) -> str:
        return NAME["en"].format(**self.flags("en"))

    @staticmethod
    def date(d: dt.date) -> str:
        return f"{d.year} 年{d.month} 月{d.day} 日"

    def money(self, units: int = 1) -> str:
        return f"{Decimal(self.denom * units):,.2f} {self.currency_zh}"

    @property
    def quote(self) -> str:
        return QUOTE[self.market]

    @property
    def print_date(self) -> dt.date:
        return self.trade_date + dt.timedelta(days=1)

    @property
    def basket(self) -> str:
        return "連結標的股票" if self.count == 1 else "籃子中連結標的股票"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\x21-\x7e]+|\s|.", text)


def _width(text: str, size: float) -> float:
    return sum(fitz.get_text_length(c, fontname="helv", fontsize=size) if ord(c) < 256 else size for c in text)


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（英數字不拆開）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts", width: float = 560):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"
        self.width = width

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def text(self, x: float, text: str, cont: float | None = None, gap: float = 13, size: float = 10) -> None:
        text = self.w.edit(text)
        if not text:
            return
        nxt = cont if cont is not None else x + 18
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

    def heading(self, name: str, section: str) -> None:
        """段名單獨一行（新頁）。"""
        self.w.new_page()
        self.section = section
        self.w.line(36, name, size=14, gap=22)

    def item(self, n: int, text: str) -> None:
        """段內項號「一、」…（x 36）。"""
        self.text(36, f"{ZH_NUM[n - 1]}、{text}", 60)

    def centered_row(self, cells: Sequence[tuple[float, str]], gap: float, size: float = 8) -> None:
        self.w.row([(cx - _width(t, size) / 2, t) for cx, t in cells], gap=gap, size=size)

    def furnish(self, label: str) -> fitz.Document:
        """每頁頁底頁碼（真實文件的頁碼，擷取時排除）。"""
        doc = self.w.doc
        for p, page in enumerate(doc):
            page.insert_text((268, 818), label.format(n=p + 1, m=len(doc)), fontname=FONT, fontsize=9)
        return doc


def _warnings(doc: str = "本中文產品說明書") -> list[str]:
    """第 1 頁警語 1.～10.：1. 為固定警語。"""
    return [
        WARNING,
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息/收益。",
        f"本商品雖經{DIST['name']}(受託機構)審查，並不代表證實申請事項或保證該境外結構型商品之價值。",
        f"本商品持有期間如有保證配息收益或保證保本率，係由{ISSUER}經由倫敦分行(發行機構)提供。",
        f"{doc}之內容如有虛偽或隱匿之情事者，其餘內容應由虛構商虛構銀行台北分行(發行人)及其負責人依法負責。",
        "發行機構(之發行分行)有權在其註冊地(英國)的法律規定下發行本商品，於中華民國境內受託買賣。",
        "投資人對本商品所列的標的資產、證券、指數、貨幣、交換、或商品項目，不享有任何利益或權利。",
        "投資人未清楚瞭解中文產品說明書、受託契約條款及所有銷售文件內容前，請勿於相關文件簽名或蓋章。",
        "受託機構應提供專業投資人及OSU 客戶相關契約審閱期間，其審閱期間不得低於三日。",
    ]


def _fee_table(d: _Doc, x0: float = 36) -> None:
    """費用表：表頭「費率（百分比）」一行，其他欄表頭與它同一行；分銷費用沒有子列。"""
    w = d.w
    w.need(40)
    top = w.y
    for x, t in (
        (x0 + 43, "費用項目"),
        (x0 + 150, "費率（百分比）"),
        (x0 + 246, "收取時點"),
        (x0 + 359, "收取方式"),
        (x0 + 478, "收取人"),
    ):
        w.put(x, top, t, size=9)
    w.y = top + 18
    for cells in (
        [(x0 + 5, "申購費用"), (x0 + 140, "申購價金的 0%~5%"), (x0 + 230, "投資人向受託")],
        [(x0 + 5, "提前贖回費用"), (x0 + 140, "投資人提前贖回價金的"), (x0 + 230, "投資人向受託")],
        [(x0 + 140, "0%~5%")],
        [(x0 + 5, "管理費用（信託管理費"), (x0 + 140, "無"), (x0 + 230, "不適用")],
        [(x0 + 5, "分銷費用（如屬發行機"), (x0 + 140, "申購價金的 0%~5%"), (x0 + 230, "於本商品發行時")],
        [(x0 + 5, "保費費用"), (x0 + 140, "不適用"), (x0 + 230, "不適用")],
        [(x0 + 5, "解約費用"), (x0 + 140, "不適用"), (x0 + 230, "不適用")],
        [(x0 + 5, "其他費用"), (x0 + 140, "無"), (x0 + 230, "不適用")],
    ):
        w.row([(x, t) for x, t in cells], gap=15, size=9)
    d.text(x0, "附註：通路服務費係由投資人負擔，投資人同意其列為受託機構之報酬。")


# ---------------------------------------------------------------- 說明書


def _cover(d: _Doc, s: Spec) -> None:
    w = d.w
    w.line(216, "中文產品說明書", size=14, gap=22)
    label = s.trustee_label
    d.item(1, f"商品代號：TDCC 商品代號為{s.product_code}，{label}商品代號為{s.product_code}。")
    d.text(60, f"商品中文名稱：{s.name_zh}（下稱“本商品”）。")
    d.item(2, f"商品英文名稱：{s.name_en}")
    d.item(3, f"商品種類：{PRODUCT_TYPE}。")
    d.item(4, f"發行機構：{ISSUER}（下稱“瑞士銀行”或“瑞銀”），經由瑞士境外之倫敦分行發行。")
    d.text(60, "發行機構（之發行分行）註冊地：英國；商品註冊地 : 專業投資人及OSU 客戶不適用。")
    d.item(5, f"計價幣別：{s.currency_zh}。")
    d.item(6, "發行人（即依「境外結構型商品管理規則」所定義之發行機構在台分行）名稱：虛構商虛構銀行台北分行")
    d.text(60, "發行人電話/地址：+886-2-0000-0000；地址：台北市虛構路1 號。")
    d.item(7, f"{label}名稱：{DIST['name']}；電話：{PHONE}；地址：{DIST['address']}(營業活動所在地)")
    d.item(8, "公會審查通過之日期及文號：專業投資人及OSU 客戶不適用。")
    d.item(9, f"{s.date(s.approval)}經受託機構({DIST['name']})審查通過。")
    d.item(10, "投資風險警語：")
    d.section = "warn"
    for k, t in enumerate(_warnings(), 1):
        d.text(36, f"{k}. {t}", 54)
    d.section = "cover"
    d.item(11, "投資人應詳閱本產品說明書之內容，並應注意本商品之風險事項。（請參閱本產品說明書8 至9 頁）")
    d.item(12, f"本產品說明書刋印日期：{s.date(s.print_date)}刋印。")


HEAD_X = {"name": 110, "initial": 215, "strike": 300, "ki": 395, "ko": 500}


def _price_table(d: _Doc, s: Spec) -> None:
    """價格表：表頭兩行（「轉換價（即期初價格的」、「X%）」），「期初價格」在兩行之間；欄序 K → KI → KO；
    每檔一列，價格後接報價幣別字。"""
    cols = [("strike", "轉換價", s.strike)]
    if s.ki != "none":
        cols.append(("ki", "下限價", s.ki_pct))
    cols.append(("ko", "提前出場價", s.ko))
    x = dict(HEAD_X)
    d.w.need(50 + 18 * s.count)
    d.centered_row([(x["name"], s.basket[:6]), *[(x[c], f"{t}（即期初價格的") for c, t, _ in cols]], gap=8)
    d.centered_row([(x["initial"], "期初價格")], gap=8)
    d.centered_row([(x["name"], s.basket[6:] + "名稱"), *[(x[c], f"{p}%）") for c, _, p in cols]], gap=16)
    for u in s.underlyings:
        prices = [u.initial] + [price(u.initial, p) for _, _, p in cols]
        cells = [
            (cx, f"{v:,.4f} {s.quote}")
            for cx, v in zip([x["initial"], *[x[c] for c, _, _ in cols]], prices, strict=True)
        ]
        if len(u.name) > 8:  # 名稱拆兩行，價格垂直置中在兩行之間
            d.w.need(28)
            top, cut = d.w.y, 8
            for dy, part in ((0, u.name[:cut]), (11, u.name[cut:])):
                d.w.put(x["name"] - _width(part, 8) / 2, top + dy, part, 8)
            d.w.y = top + 5.5
            d.centered_row(cells, gap=22.5)
            continue
        d.centered_row([(x["name"], u.name), *cells], gap=16)


def _coupon_table(d: _Doc, s: Spec) -> None:
    d.section = "art8.coupon"
    w = d.w
    if s.ko_obs == "D":
        w.row(
            [
                (72, "配息觀察期間"),
                (170, "配息觀察期間開始日"),
                (265, "配息觀察期間結束日"),
                (370, "配息給付日"),
                (450, "每單位面額配息金額公式"),
            ],
            gap=14,
            size=8,
        )
        for j in range(s.tenor):
            kind = "固定" if j < s.first_callable else "變動"
            w.row(
                [
                    (72, f"第{j + 1} 個配息觀察期間 {s.date(s.starts[j])} {s.date(s.ends[j])} {s.date(s.payments[j])}"),
                    (450, "對應的配息金額="),
                ],
                gap=11,
                size=8,
            )
            w.row([(450, f"每單位面額x {kind}配息率")], gap=13, size=8)
    else:
        w.row([(150, "配息觀察日"), (270, "配息給付日")], gap=14, size=8)
        for j in range(s.tenor):
            w.row([(72, f"第{j + 1} 個月配息"), (150, s.date(s.ends[j])), (270, s.date(s.payments[j]))], gap=13, size=8)
    d.section = "art8"


def _ko_observation(s: Spec, *, art8: bool) -> str:
    if s.ko_obs == "D":
        head = "每日觀察，" if art8 else ""
        return f"{head}為自第{s.first_callable} 個配息觀察期間結束日（包含）至期末訂價日（含）的任一預定交易日。"
    return f"為自第{s.first_callable} 個配息觀察的每個配息觀察日。"


def _article8(d: _Doc, s: Spec) -> None:
    if s.issue_price_amount:
        d.text(54, f"• 發行價格：100%，即{s.money()}。（投資人請注意：實際購買價格可能跟發行價格不同。）")
    else:
        d.text(54, "• 發行價格：每單位面額之100%。（投資人請注意：實際購買價格可能跟發行價格不同。）")
    d.text(54, f"• 每單位面額：{s.money()}。")
    d.text(54, f"• 最低交易金額：{s.money(5)}。")
    d.text(54, "• 給付項目及其計算方式：")
    d.text(54, "• 每月固定配息：發行機構將在每月配息給付日給付對應的配息金額（請詳閱下表）。")
    _coupon_table(d, s)
    if s.ko_obs == "D":
        d.text(72, f"固定配息率：{s.monthly}%。（年化配息率為{s.annual}%）。")
        d.text(72, f"變動配息率：{s.monthly}%X（nt/Nt）（年化配息率為{s.annual}%）。")
        d.text(72, "nt：指在該配息觀察期間開始日至該配息觀察期間結束日或提前出場日孰先之預定交易日天數")
        if s.first_callable < s.tenor:
            k = s.first_callable + 1
            d.text(72, f"t 係為{k} 至{s.tenor} 的數字，代表第{k} 至第{s.tenor} 個月。")
    else:
        d.text(72, f"固定配息率：{s.monthly}%（年化配息率{s.annual}%）。")
    d.text(63, "• 提前出場給付：")
    obs = _ko_observation(s, art8=True)
    if s.memory:
        d.text(
            77,
            "若籃子中所有連結標的股票在任一提前出場觀察日*皆已成為鎖定股票，則提前出場事件視同在該提前出場觀察日發生。",
        )
        d.text(77, "為避免疑慮，即使各股票在不同提前出場觀察日成為鎖定股票，提前出場事件仍被視為發生。")
    elif s.count > 1:
        pct = f"（即期初價格的{s.ko}%）" if s.ko_pct_in_clause else ""
        d.text(
            77,
            f"若所有連結標的股票在任一提前出場觀察日*之收盤價皆高於或等於其提前出場價{pct}，則提前出場事件視同在該提前出場觀察日發生。",
        )
    else:
        d.text(
            77,
            "若連結標的股票在任一提前出場觀察日*的收盤價高於或等於提前出場價，則提前出場事件視同在該提前出場觀察日發生。",
        )
    d.text(63, f"• 提前出場觀察日：{obs}")
    if s.memory:
        d.text(
            63,
            "• 鎖定股票：係指籃子中任一連結標的股票在任一提前出場觀察日的收盤價高於或等於其提前出場價，則該股票被視為鎖定股票。",
        )
    who = "連結標的股票" if s.count == 1 else "籃子中任一連結標的股票"
    if s.ki != "none":
        when = {
            "D": "若在期初訂價日（含）至期末訂價日（含）間的任一預定交易日",
            "P": "若在任一配息觀察日",
            "AM": "若在期末訂價日",
        }[s.ki]
        d.text(63, "• 觸及下限事件：")
        d.text(77, f"{when}，{who}的收盤價低於下限價，則觸及下限事件視同發生。")
    d.text(63, "• 到期日給付：")
    if s.ki == "none":
        d.text(
            77,
            "（1）若於期末訂價日時最差表現的連結標的股票之收盤價高於或等於轉換價，則投資人可領回100%之每單位面額；否則，",
        )
        d.text(
            77,
            f"（2）若於期末訂價日時最差表現的連結標的股票之收盤價低於轉換價，則投資人將會收到股數，及以{s.currency_zh}支付剩餘金額（如有）。",
        )
    else:
        d.text(77, "（1）若觸及下限事件從未發生，則投資人可領回100%之每單位面額；否則，")
        d.text(77, f"（2）若觸及下限事件曾經發生，則投資人將會收到股數，及以{s.currency_zh}支付剩餘金額（如有）。")
    d.text(72, "其中：")
    d.section = "art8.table"
    _price_table(d, s)
    d.section = "art8"
    d.text(72, "上述轉換價、下限價、提前出場價將四捨五入至小數點後第4 位")
    d.text(72, "收盤價：就一預定交易日而言，交易所於當日公佈有關連結標的股票之官方收盤價。")


def _article9(d: _Doc, s: Spec) -> None:
    d.text(54, "• 籃子中連結標的資產：")
    d.section = "art9.table"
    d.centered_row([(110, s.basket + "名稱"), (250, "彭博代碼"), (400, "交易所")], gap=16)
    for u in s.underlyings:
        d.centered_row([(110, u.name), (250, u.ticker), (400, u.exchange)], gap=16)
    d.section = "art9"
    d.text(54, "• 權重：不適用；本商品之到期贖回收益將取決於最差表現的連結標的股票。")


def _article12(d: _Doc, s: Spec) -> None:
    for t in (
        f"• 天期/年期：{s.tenor} 個月。",
        f"• 交易日/期初訂價日：{s.date(s.trade_date)}。",
        f"• 發行日/交割日：{s.date(s.issue_date)}。",
        f"• 提前出場觀察日：{_ko_observation(s, art8=False)}",
        "• 提前出場給付日：即提前出場觀察日後的第三個營業日。",
        f"• 期末訂價日：{s.date(s.final_date)}。",
        f"• 期末交割日/到期日：若本商品期末以現金交割方式贖回，期末訂價日加三個營業日，即現預計為{s.date(s.maturity_date)}；"
        f"若本商品期末以證券交割方式贖回，期末訂價日加三個預定交易日，即現預計為{s.date(s.maturity_date)}。",
        "• 營業日：紐約的銀行和外匯市場對外開放營業日（不含星期六及星期日）。",
    ):
        d.text(54, t, 72)


def _article15(d: _Doc, s: Spec) -> None:
    d.text(54, "• 情境分析之解說：")
    d.text(54, "設定參數：")
    d.text(54, f"天期：{s.tenor} 個月")
    d.text(54, f"每單位面額：{s.money()} / 發行價格：100% / 每單位面額初始投資金額：{s.money()}。")
    d.section = "art15.table"
    _price_table(d, s)
    d.section = "art15"
    d.text(54, f"固定配息率：{s.monthly}%。（年化配息率為{s.annual}%）。")
    d.text(54, "情境分析一：在第1 個提前出場觀察日發生提前出場事件。")


ARTICLE_TITLES = {
    1: "商品名稱：",
    3: "發行機構為",
    5: "計價幣別：",
    8: "本商品重要條件、主要給付項目及其計算方式：",
    9: "連結標的資產（例如：指數或個股名稱等），及其相對權重、與投資績效之關連情形。",
    12: "商品相關日期",
    15: "投資收益計算方法–包含本金虧損之機率及以情境分析之解說最大可能獲利、損失及其他狀況之年化報酬率。",
    25: "ISIN 代碼：",
}


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 UBS 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    _cover(d, s)

    d.heading("商品基本資料", "art")
    for n in range(1, 27):
        d.section = f"art{n}"
        title = ARTICLE_TITLES.get(n, f"第{ZH_NUM[n - 1]}項說明（不核對）。")
        if n == 1:
            d.item(n, f"{title}{s.name_zh}。")
        elif n == 2:
            d.item(n, WARNING)
        elif n == 3:
            d.item(n, f"{title}{ISSUER}，經由倫敦分行發行。截至本產品說明書刋印日期，其長期債務信用評等為虛構評等。")
        elif n == 5:
            d.item(n, f"{title}{s.currency_zh}。")
        elif n == 8:
            d.item(n, title)
            _article8(d, s)
        elif n == 9:
            d.item(n, title)
            _article9(d, s)
        elif n == 12:
            d.item(n, title)
            _article12(d, s)
        elif n == 15:
            d.item(n, title)
            _article15(d, s)
        elif n == 25:
            d.item(n, f"{title}{ISIN}。")
        else:
            d.item(n, title)

    d.heading("相關機構事業概況", "org")
    for t in (
        "一、發行機構：",
        "（一）發行機構簡介：",
        f"• 事業名稱：{ISSUER}（UBS AG），透過其倫敦分行發行。",
        "• 發行機構（之發行分行）的主要營業地點的地址：1 Fictional Street, London。",
        "二、發行人（即依「境外結構型商品管理規則」所定義之發行機構在台分行）簡介：",
        "• 事業名稱/設立日期/營業所在地址：虛構商虛構銀行台北分行，於1998 年06 月25 日設立，地址：台北市虛構路1 號。",
        "• 負責人姓名：虛構人。",
        "三、其他相關機構簡介：",
        "（一）受託機構簡介：",
        f"• 事業名稱：{DIST['name']}，地址：{DIST['address']}(營業活動所在地)。",
        "• 設立日期：2000 年11 月20 日。",
        f"• 負責人姓名：{DIST['chairman']}。",
        "（二）計算代理機構簡介：",
        "• 事業名稱/營業所在地：虛構銀行倫敦分行，地址：1 Fictional Street, London。",
    ):
        d.text(36, t, 54)

    d.heading("商品風險揭露", "risk")
    d.text(36, "一、 潛在本金損失風險/最低收益風險 - 本商品並不保本。", 54)
    d.text(36, "二、 投資人提前贖回風險 - 本商品未必能在到期日前被變現/出售。", 54)

    d.heading("投資風險警語", "warnings")
    d.text(36, f"• {WARNING}", 54)
    d.text(36, "• 本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。", 54)

    d.heading("一般交易事項", "general")
    d.item(1, "申購與贖回相關日期：")
    d.text(54, f"商品申購開始受理日：{s.date(s.trade_date)}。")
    d.text(54, "商品申購結束受理日：發行日前第四個臺北營業日台北時間下午五點整。")
    d.item(2, "本商品係複委託平台項下之投資標的，投資人同意下列相關費用：")
    _fee_table(d)
    d.item(3, "商品交易架構：請參閱以上「相關機構事業概況」之”交易架構說明”。")
    d.item(4, "本商品最低申購金額及最低累加申購金額：")
    d.text(54, f"• 每單位面額：{s.money()}。")
    d.text(54, f"• 最低申購金額：{s.money(5)} （相當於五單位面額）。")
    d.text(54, f"• 最低累加申購金額：{s.money()} （相當於一單位面額）。")
    d.item(5, f"申購價金之計算：{s.money()} X 申購單位數X 購買價格")
    d.item(6, "申購手續及資金給付方式：不核對。")
    d.item(7, "發行不成立之情形：發行機構可根據其絕對裁量權，取消任何發行。")
    d.item(8, "最低贖回金額或單位數/最低累加贖回金額：")
    d.text(54, f"• 最低投資人提前贖回單位數：{s.money()}，即一單位面額。")
    d.text(54, f"• 最低累加投資人提前贖回單位數：{s.money()}，即一單位面額。")
    for n in range(9, 16):
        d.item(n, f"第{ZH_NUM[n - 1]}項說明（不核對）。")
    d.item(16, "發行人協助辦理投資人權益保護之方式。")
    d.text(36, f"受託機構聯絡方式：{DIST['name']}；電話：{PHONE}")
    d.item(17, "商品重要相關資料可至「境外結構型商品資訊觀測站」查詢。")

    d.heading("特別記載事項", "special")
    d.item(1, "於總代理人為發行機構或保證機構之子公司之情形：不適用。")
    doc = d.furnish("第{n}頁，共{m}頁")
    d.w.flush()
    doc.save(path)
    doc.close()
    require_used(edits, used)
    return path


# ---------------------------------------------------------------- 投資人須知


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """UBS 中文投資人須知（docs/templates/ubs-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis", width=565)
    w = d.w
    w.line(217, "中文投資人須知", size=14, gap=20)
    d.text(36, f"商品中文名稱：{s.name_zh}（下稱“本商品”）；商品英文名稱：{s.name_en}；商品種類：{PRODUCT_TYPE}。", 36)
    w.line(36, "警語")
    d.section = "warn"
    for k, t in enumerate(_warnings("本投資人須知"), 1):
        d.text(36, f"{k}. {t}", 54)
    d.section = "cover"
    d.text(36, f"本投資人須知刋印日期：{s.date(s.print_date)}刊印。")
    d.section = "org"
    w.line(36, "相關機構")
    for t in (
        "• 發行人（即依「境外結構型商品管理規則」定義之發行機構在台分行）名稱：虛構商虛構銀行台北分行，地址：台北市虛構路1 號。",
        f"• 發行機構：{ISSUER}，經由倫敦分行發行。",
        f"• 受託機構名稱：{DIST['name']}，地址為{DIST['address']}(營業活動所在地)。",
    ):
        d.text(36, t, 54)
    d.section = "summary"
    w.line(36, "商品事項")
    uls = "，".join(f"{u.name} （彭博代碼：{u.ticker}）" for u in s.underlyings)
    for t in (
        "• 商品簡介：",
        "1. 本商品銷售對象：專業投資人及OSU 客戶。",
        "3. 本商品風險等級：參考列載於本投資人須知封面的「警語」部份第一項。",
        f"6. 計價幣別：{s.currency_zh}。",
        f"9. 連結標的資產：{uls}。",
        f"10. 天期/年期：{s.tenor} 個月。",
        f"11. 發行日/交割日：{s.date(s.issue_date)}。",
        f"12. 期末交割日/到期日：若本商品期末以現金交割方式贖回，期末訂價日加三個營業日，即現預計{s.date(s.maturity_date)}；"
        f"若本商品期末以證券交割方式贖回，期末訂價日加三個預定交易日，即現預計為{s.date(s.maturity_date)}。",
        "13. 本商品開始受理投資人提前贖回日期：發行日後的次一個預定交易日（“受理投資人提前贖回開始日”）。",
        "• 收益分配事項：有關配息及提前出場計算，請參考產品說明書「商品基本資料」第八項。",
        "• 銷售不成立之處理：",
        "1. 銷售不成立之情形：發行機構可根據其絕對裁量權，取消任何發行。",
    ):
        d.text(41, t, 63)
    d.section = "rest"
    w.line(36, "商品風險揭露 （以下只是產品說明書商品風險揭露節錄）")
    d.text(36, "• 潛在本金損失風險/最低收益風險−本商品並不保本。", 54)
    d.section = "fees"
    d.text(36, "本商品係複委託平台項下之投資標的，投資人同意下列相關費用：")
    _fee_table(d)
    d.section = "rest"
    w.line(36, "相關機構之權利、義務及責任")
    d.text(36, "• 投資人之申購及贖回，須經境外發行機構確認後始生效力。", 54)
    d.text(36, f"受託機構聯絡方式：{DIST['name']}；電話：{PHONE}。")
    doc = w.doc
    while len(doc) < s.iis_pages:
        w.new_page()
        doc = w.doc
    while len(doc) > s.iis_pages:
        doc.delete_page(len(doc) - 1)
    for n, page in enumerate(doc, 1):
        page.insert_text((262, 830), f"第{n}頁，共{s.iis_pages}頁", fontname=FONT, fontsize=9)
    return w.save(path)


# ---------------------------------------------------------------- 參考條件表與核對


def row(spec: Spec, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列：期初定價依 `pricing`。`overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(spec)
    out["期初定價"] = PRICING[spec.pricing]
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
