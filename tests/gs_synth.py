"""GS 合成說明書（docs/templates/gs-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：第 1 頁「中文產品說明書」「受託機構商品代號為…」＋編號項目 1.～13.（x 72、續行 x 86）；
章名「第一章:商品基本資料」在左邊界 x 72，第一章起每頁右上角另有重複的章名頁首（x 458）、每頁頁底置中「第- N -頁，共M頁」；
條號「N.」與標題同一行。表格一格一個文字單位（同一列同一高度），表頭一格拆成多行。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`（第 1–2 頁）、`ts.warn`（第 1 頁警語）、
第一章各條 `ts.art{n}`（觀察時程表 `ts.art13.table`、價格表 `ts.art15.table`）、其他各章 `ts.ch二`～`ts.ch六`。
投資人須知（docs/templates/gs-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
段落代號 `iis.cover`、`iis.warn`、`iis.org`（一. 相關機構）、`iis.summary`（二. 1. 商品簡介，價格表 `iis.table`）、
`iis.coupon`（二. 2.）、`iis.redeem`（二. 3.、4.）、`iis.fees`（四.）、`iis.rest`（其他）。
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

NAME = STANDARD["product_name"]["gs"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["gs"]
ISSUER = STANDARD["issuer_name"]["gs"]
ISSUER_ZH = ISSUER.split("（")[0]
PRODUCT_TYPE = STANDARD["product_type"]["gs"]
MULTIPLES = STANDARD["min_amount_multiples"]["gs"]["minimum_subscription"]
DIST = STANDARD["distributor"]
PHONE = "+886 2 5556 1313"  # 審查標準 phone_equivalents 的 GS 寫法
ADDRESS = DIST["address"]
ISIN = "XS1999900048"
APPROVAL = dt.date(2026, 6, 11)
Q4 = Decimal("0.0001")
TICKERS = {  # 依代號排序（GS 說明書的順序）
    "US": ("ZGA UN", "ZGB UW", "ZGC UW", "ZGD UN", "ZGE UW"),
    "JT": ("7001 JT", "7002 JT", "7003 JT", "7004 JT", "7005 JT"),
}
EXCHANGE = {"UN": "紐約證券交易所", "UW": "納斯達克全球精選市場", "JT": "東京證券交易所"}
QUOTE = {"US": "美元", "JT": "日圓"}  # 標的報價幣別
PRICING = {"close": "收盤價", "open": "開盤價", "vwap": "VWAP"}  # 參考條件表「期初定價」
ZH_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九")


def underlyings(count: int, market: str) -> tuple[UL, ...]:
    """GS 合成文件的標的：美股期初價 100n + 0.45，日股 1000n + 500（價格表一律 4 位小數）。"""
    out = []
    for i in range(1, count + 1):
        ticker = TICKERS[market][i - 1]
        initial = Decimal(1000 * i + 500) if market == "JT" else Decimal(100 * i) + Decimal("0.45")
        out.append(UL(f"虛構控股{ZH_NUM[i - 1]}", EXCHANGE[ticker.split()[1]], ticker, initial))
    return tuple(out)


@dataclass
class Spec(ProductSpec):
    """GS 合成說明書的規格：商品規格加上 GS 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count`、`market` 產生、最終比價日與到期日由觀察時程表推得：`underlyings`、`final_date`、`maturity_date`
    不能直接給。KO 一律每日觀察（每期觀察是 GS 不支援樣態）；單一標的一律非記憶式、多標的一律記憶式（範本只有這兩種句型）。
    """

    issuer: str = "GS"
    product_code: str = "148199990001"
    ko_obs: str = "D"
    ki: str = "D"  # none／AM／D
    tenor: int = 6
    first_callable: int = 3
    annual: Decimal = Decimal("12.00")
    strike: Decimal = Decimal("65.00")
    ko: Decimal = Decimal("105.00")
    ki_pct: Decimal = Decimal("55.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    market: str = "US"  # 標的市場：US 美股、JT 日股
    pricing: str = "close"  # 期初定價：close（間接寫法）／open／vwap
    merged_prices: bool = False  # 價格表執行價格以右的價格擷取成同一行（以空白分開）
    split_initial: bool = False  # 價格表初始參考價格從小數點後拆行（`1,100.450` / `0`）
    approval: dt.date = APPROVAL
    iis_pages: int = 4

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count, self.market)
        self.memory = self.count > 1
        self.final_date, self.maturity_date = self.ends[-1], self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期觀察期間結束日：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def starts(self) -> list[dt.date]:
        """各期觀察期間起始日：第 1 期為交易日後一個平日，其後為前期結束日後一個平日。"""
        prev = [self.trade_date, *self.ends[:-1]]
        return [weekday_on_or_after(d + dt.timedelta(days=1)) for d in prev]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=2)) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    @property
    def basket(self) -> bool:
        return self.count > 1

    @property
    def units(self) -> int:
        """最低申購單位數（審查標準 min_amount_multiples.gs 依幣別）。"""
        return MULTIPLES[self.ccy]

    @property
    def name_zh(self) -> str:
        memo = NAME["memory_zh"] if self.memory else ""
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh, memory_zh=memo)

    @property
    def name_en(self) -> str:
        memo = NAME["memory_en"] if self.memory else ""
        ccy = NAME.get("ccy_aliases", {}).get(self.ccy, self.ccy)
        return NAME["en"].format(tenor=self.tenor, ccy=ccy, memory_en=memo)

    @property
    def iso_text(self) -> str:
        """第一章第 5 項括號裡的代碼（人民幣寫 CNY）。"""
        return NAME.get("ccy_aliases", {}).get(self.ccy, self.ccy)

    def i(self, text: str) -> str:
        """多標的寫「(i)」，單一標的去掉。"""
        return text if self.basket else text.replace("(i)", "")

    @staticmethod
    def date(d: dt.date) -> str:
        return f"{d.year}年{d.month}月{d.day}日"

    def money(self, x: int | Decimal, space: bool = False) -> str:
        return f"{Decimal(x):,.0f}{' ' if space else ''}{self.currency_zh}"


def _tokens(text: str) -> list[str]:
    """換行單位：連續的英數字（含符號）不拆開，中文一字一個。"""
    return re.findall(r"[\x21-\x7e]+|\s|.", text)


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（英數字不拆開）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts", width: float = 545):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"
        self.width = width
        self.chapters: list[tuple[int, str]] = []  # （頁索引, 章名）

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def text(self, x: float, text: str, cont: float | None = None, gap: float = 13) -> None:
        text = self.w.edit(text)
        if not text:
            return
        width, cur, used, first = self.width - x, "", 0.0, True
        with self.w.verbatim():
            for tok in _tokens(text):
                tw = sum(fitz.get_text_length(c, fontname="helv", fontsize=10) if ord(c) < 256 else 10 for c in tok)
                if used + tw > width and cur.strip():
                    self.w.line(x if first else (cont if cont is not None else x), cur.strip(), gap=gap)
                    cur, used, first = "", 0.0, False
                    if tok.isspace():
                        continue
                cur += tok
                used += tw
            self.w.line(x if first else (cont if cont is not None else x), cur.strip(), gap=gap)

    def row(self, cells: Sequence[tuple[float, str]], gap: float = 15, size: float = 10) -> None:
        self.w.row(cells, gap=gap, size=size)

    def chapter(self, zh: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{zh}"
        self.chapters.append((len(self.w.doc) - 1, f"第{zh}章:{name}"))
        self.w.line(72, f"第{zh}章:{name}", gap=20)

    def article(self, n: int, title: str) -> None:
        if self.section == "cover" or self.section.startswith("art") or self.section == "ch一":
            self.section = f"art{n}"
        self.text(72, f"{n}. {title}", 86)

    def furnish(self) -> fitz.Document:
        """第一章起每頁右上角的章名頁首、每頁頁底頁碼（真實文件的頁首頁尾，擷取時排除）。"""
        doc = self.w.doc
        for p, page in enumerate(doc):
            current = [name for start, name in self.chapters if start <= p]
            if current:
                page.insert_text((458, 70), current[-1], fontname=FONT, fontsize=8)
            page.insert_text((260, 800), f"第- {p + 1} -頁，共{len(doc)}頁", fontname=FONT, fontsize=8)
        return doc


def _warnings(s: Spec) -> list[str]:
    """第 1 頁警語 (1)～(10) 與第三章 5.(1)～(10)：(1) 為固定警語。"""
    return [
        f"本商品風險程度:{WARNING}",
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息。",
        "本商品雖經玉山證券（受託機構）審查，並不代表保證本商品之價值。",
        f"本商品持有期間如有保證配息收益，係由{ISSUER}（發行機構）及虛構保證機構保證，而非由玉山證券（受託機構）所保證。",
        "本中文產品說明書之內容如有虛偽或隱匿之情事者，除本商品風險程度、適合受託買賣風險等級外，其餘內容應由總代理人依法負責。",
        "本商品條款和條件之準據法為英國法。本商品係依境外結構型商品管理規則規定，於中華民國境內受託買賣。",
        "投資人應詳閱本中文產品說明書之內容，並應注意本商品之風險事項。",
        "本商品於發行日之價值可能遠低於原始發行價格。",
        "本商品之條款依發行機構虛構計畫之發行說明書規範。",
    ]


# ---------------------------------------------------------------- 說明書


def _cover(d: _Doc, s: Spec) -> None:
    w = d.w
    w.line(290, "中文產品說明書", gap=20)
    w.line(86, f"受託機構商品代號為 {s.product_code}", gap=16)
    d.text(
        72,
        f"1. 商品中文名稱:{s.name_zh}（TDCC商品代號為{s.product_code}，ISIN:{ISIN})（下稱「本商品」或「境外結構型商品」）",
        86,
    )
    d.text(72, f"2. 商品英文名稱: {s.name_en} (TDCC Product Code: {s.product_code}，ISIN: {ISIN})", 86)
    d.text(72, f"3. 商品種類:{PRODUCT_TYPE}", 86)
    d.text(72, "4. 發行機構註冊地:英國", 86)
    d.text(72, "5. 商品註冊地:不適用", 86)
    d.text(72, f"6. 計價幣別:{s.currency_zh}", 86)
    d.text(72, f"7. 發行機構之名稱、電話及地址:{ISSUER}，電話:+44 20 0000 0000，地址:1 Fictional Court, London", 86)
    d.text(72, "8. 總代理人之名稱、電話及地址:虛構總代理人臺北分公司，電話:+886 2 0000 0000，地址:臺北市虛構路1號", 86)
    d.text(
        72,
        "9. 各保證機構之名稱及地址:(1)虛構集團有限公司，地址:1 Fictional Street, New York，及(2)虛構證券有限公司，地址:香港虛構道1號",
        86,
    )
    d.text(72, f"10. 受託機構之名稱、電話及地址:{DIST['name']}，電話:{PHONE}，地址:{ADDRESS}(營業活動所在地)", 86)
    d.text(72, "11. 中華民國信託業商業同業公會審查通過之日期及文號：不適用", 86)
    d.text(72, f"12. {DIST['name']}（下稱「受託機構」）審查通過之日期:{s.date(s.approval)}", 86)
    d.text(72, "13. 本商品之投資風險警語:", 86)
    d.section = "warn"
    for k, t in enumerate(_warnings(s), 1):
        d.text(86, f"({k}) {t}", 100)
    d.section = "cover"
    d.text(86, f"本中文產品說明書刊印日期:{s.date(s.trade_date)}")


def _article10(d: _Doc, s: Spec) -> None:
    d.text(86, "(1) 連結標的資產:")
    d.row(
        [
            (104, s.i("連結標的(i)")),
            (144, s.i("連結標的股份發行人(i)")),
            (300, s.i("彭博代碼(i)")),
            (400, s.i("參考交易所(i)")),
        ]
    )
    for k, u in enumerate(s.underlyings, 1):
        d.row([(104, str(k)), (144, u.name), (300, f"{u.ticker} Equity"), (400, u.exchange)])
    d.text(86, "以下各稱「連結標的」，合稱「各連結標的」或「一籃子連結標的」。")
    d.text(86, "(2) 相對權重:不適用")
    d.text(86, "(3) 與投資績效的關連情形:於各付息日之應付配息金額於相關觀察期間依相關連結標的資產決定。")


def _article13(d: _Doc, s: Spec) -> None:
    for t in (
        f"(1) 年期:如未發生自動提前出場事件，或本商品未經其他方式提前買回者，則為{s.tenor}個月",
        f"(2) 交易日:{s.date(s.trade_date)}",
        f"(3) 發行日:{s.date(s.issue_date)}",
        f"(4) 評價日:{s.date(s.final_date)}。為本評價日條款之目的，應適用共同預定交易日及共同中斷日。",
        f"(5) 到期日:評價日後的第2個營業日，現預定為{s.date(s.maturity_date)}（下稱「預定到期日」）。",
        "(6) 觀察期間(t):自觀察期間起始日（含）起，至觀察期間結束日（含）止之各期間，如下開觀察時程表所載。",
        "(7) 付息日:就各觀察期間(t)而言，其觀察期間結束日後的第2個營業日。",
        "(8) 自動提前買回日:自動提前出場事件發生日後的第2個營業日。",
    ):
        d.text(86, t, 100)
    d.section = "art13.table"
    d.w.line(86, "觀察時程表", gap=14)
    d.row([(96, "t"), (130, "觀察期間起始日(含)"), (260, "觀察期間結束日(含)"), (390, "預定付息日")])
    for j in range(1, s.tenor + 1):
        d.row(
            [
                (96, str(j)),
                (130, s.date(s.starts[j - 1])),
                (260, s.date(s.ends[j - 1])),
                (390, s.date(s.payments[j - 1])),
            ]
        )
    d.section = "art13"
    d.text(86, "投資人應注意本中文產品說明書所述之付息日丶到期日及任何交割日期係指發行機構給付相關金額予受託機構之日。")


def _article15(d: _Doc, s: Spec) -> None:
    i = s.i
    worst = "於評價日表現最差之連結標的最終參考價格(i)" if s.basket else "最終參考價格"
    d.text(86, "(1) 到期買回金額:如未發生自動提前出場事件者，則本商品之各單位應依下開各段計算之到期買回金額買回:", 100)
    if s.ki == "D":
        d.text(100, "(a) 如已發生最終提前出場事件，或未發生下限觸發事件時:每單位面額 × 100%；或")
        d.text(
            100,
            i(
                f"(b) 如未發生最終提前出場事件，且已發生下限觸發事件，且{worst}等於或高於其執行價格(i)時:每單位面額 × 100%；或"
            ),
        )
        d.text(
            100,
            i(f"(c) 如未發生最終提前出場事件，且已發生下限觸發事件，且{worst}低於其執行價格(i)時:可交付資產之實物交割"),
        )
    elif s.ki == "AM":
        d.text(100, i(f"(a) 如已發生最終提前出場事件，或{worst}等於或高於其下限觸發價格(i)時:每單位面額 × 100%；或"))
        d.text(100, i(f"(b) 如未發生最終提前出場事件，且{worst}低於其下限觸發價格(i)時:可交付資產之實物交割"))
    else:
        d.text(100, "(a) 如最終參考價格等於或高於執行價格時:每單位面額 × 100%；或")
        d.text(100, "(b) 如最終參考價格低於執行價格時:可交付資產之實物交割")
    d.text(86, "本文中:")
    if s.basket:
        d.text(
            86,
            "「最終提前出場事件」指經計算機構之判定，當各連結標的(i)於任一提前出場觀察日或於評價日當日均至少已發生一次記憶提前出場事件(i)。",
        )
    if s.ki == "D":
        d.text(
            86,
            i(
                "「下限觸發事件」:指如任一連結標的(i)於其任一下限觸發觀察日之參考價格(i)低於其對應之下限觸發價格(i)，則視為已發生下限觸發事件。"
            ),
        )
        d.text(86, "「下限觸發觀察期間」指自交易日開始（不含）至評價日結束（含）之期間。")
    if s.ki != "none":
        d.text(86, i(f"「下限觸發價格(i)」:初始參考價格(i)之{s.ki_pct}%，詳見下方之連結標的價格表。"))
    d.text(86, i(f"「執行價格(i)」:初始參考價格(i)之{s.strike}%，詳見下方之連結標的價格表。"))
    d.text(
        86, i("「初始參考價格(i)」就各連結標的(i)而言，指下方之連結標的價格表「初始參考價格(i)」標題欄下列出之價格。")
    )
    sentence = {
        "close": "為該連結標的(i)於交易日之參考價格(i)。",
        "open": "為該連結標的(i)於交易日經參考交易所(i)官方決定及公布之開盤價格。",
        "vwap": "為該連結標的(i)於交易日經發行機構以誠信及商業上合理方式決定之成交量加權平均價格。",
    }[s.pricing]
    if s.basket:
        d.text(86, "初始參考價格(i)就各連結標的(i)而言，" + sentence)
    else:  # 單一標的寫法（GS5）
        d.text(
            86,
            "初始參考價格為"
            + {
                "close": "於交易日之參考價格。",
                "open": "於交易日經參考交易所官方決定及公布之連結標的之開盤價格。",
                "vwap": "於交易日經發行機構以誠信及商業上合理方式決定之成交量加權平均價格。",
            }[s.pricing],
        )
    d.text(86, i("「參考價格(i)」就各連結標的(i)而言，指經相關參考交易所官方決定及公布之該連結標的收盤價格。"))
    d.section = "art15.table"
    d.w.line(105, "連結標的價格表", gap=16)
    _price_table(d, s, tables_iis=False)
    d.section = "art15"
    first = "首個" if s.first_callable == 1 else f"首{s.first_callable}個"
    d.text(86, f"(2) 最低保證收益率:{first}觀察期間年利率{s.annual}%。")
    d.text(86, "(3) 參與率:不適用。")


def _price_table(d: _Doc, s: Spec, *, tables_iis: bool) -> None:
    """連結標的價格表（7 號字）：表頭一格拆成多行（K／KO／KI % 寫在表頭）；每檔一列，彭博代碼拆兩行（「代號 交易所碼」／
    「Equity」）。說明書有參考幣別欄、價格不帶幣別字；投資人須知沒有參考幣別欄、價格後接幣別字。"""
    i, ki = s.i, s.ki != "none"
    d.w.need(70 + 23 * s.count)  # 表格不跨頁
    cur = "" if not tables_iis else QUOTE[s.market]
    x_name, x_code, x_ccy, x_init = 144, 232, 274, 315

    def row(cells: list[tuple[float, str]], gap: float) -> None:
        d.row(cells, gap=gap, size=7)

    heads = [
        [
            (104, i("連結標")),
            (x_name, i("連結標的股份發")),
            (x_code, "彭博代"),
            (x_init, "初始參考"),
            (366, i("執行價格(i)")),
            (429, "提前出場價"),
        ],
        [
            (104, i("的(i)")),
            (x_name, i("行人(i)")),
            (x_code, i("碼(i)")),
            (x_init, i("價格(i)")),
            (366, "(初始參考價"),
            (429, i("格(i)(初始")),
        ],
        [(366, i(f"格(i)之{s.strike}")), (429, i("參考價格(i)"))],
        [(366, "%)"), (429, f"之{s.ko}%)")],
    ]
    if not tables_iis:
        heads[0].append((x_ccy, "參考幣"))
        heads[1].append((x_ccy, i("別(i)")))
    if ki:
        heads[0].append((491, "下限觸發價"))
        heads[1].append((491, i("格(i)(初始")))
        heads[2].append((491, i("參考價格(i)")))
        heads[3].append((491, f"之{s.ki_pct}%)"))
    for k, cells in enumerate(heads):
        row(cells, 9 if k < 3 else 14)
    for n, u in enumerate(s.underlyings, 1):
        prices = [price(u.initial, s.strike), price(u.initial, s.ko)] + ([price(u.initial, s.ki_pct)] if ki else [])
        initial = f"{u.initial:,.4f}"
        cells = [(104, str(n)), (x_name, u.name), (x_code, u.ticker)]
        if not tables_iis:
            cells.append((x_ccy, QUOTE[s.market]))
        cells.append((x_init, (initial[:-1] if s.split_initial else initial) + ("" if s.split_initial else cur)))
        texts = [f"{p:,.4f}{cur}" for p in prices]
        if s.merged_prices:
            cells.append((366, " ".join(texts)))
        else:
            cells += list(zip((366, 429, 491), texts, strict=False))
        row(cells, 9)
        rest = [(x_code, "Equity")]
        if s.split_initial:
            rest.append((x_init, initial[-1] + cur))
        row(rest, 14)


def _article16(d: _Doc, s: Spec) -> None:
    if s.basket:
        d.text(
            86,
            "如於任一提前出場觀察日，各連結標的(i)至少發生過一次記憶提前出場事件(i)，則視為已發生「自動提前出場事件」，且發行機構將於自動提前買回日給付自動提前買回金額。",
        )
    else:
        d.text(
            86,
            "如於一提前出場觀察日之參考價格等於或高於提前出場價格，則視為已發生「自動提前出場事件」，且發行機構將於自動提前買回日給付自動提前買回金額。",
        )
    d.text(86, "本文中:")
    if s.basket:
        d.text(
            86,
            "「記憶提前出場事件(i)」:就連結標的(i)而言，如該連結標的(i)於任一提前出場觀察日或評價日之參考價格(i)等於或高於其提前出場價格(i)，則該連結標的(i)應視為已發生記憶提前出場事件(i)。",
        )
    scope = (
        "一籃子連結標的之任何共同預定交易日（且非為任何連結標的之中斷日）"
        if s.basket
        else "非為連結標的之中斷日之任何預定交易日"
    )
    d.text(
        86,
        f"「提前出場觀察日」指自第{s.first_callable}個觀察期間結束日開始（含）至評價日結束（不含）之期間內，{scope}。",
    )


def _article17(d: _Doc, s: Spec) -> None:
    u = s.underlyings[0]
    one = "(1)" if s.basket else ""
    final = (u.initial / 10).quantize(Q4, ROUND_HALF_UP)
    ccy = QUOTE[s.market]
    for t in (
        "(1) 投資收益計算方法:請參見本章第14項及第15項。",
        "(3) 最大可能獲利、損失之情境分析解說以及其他狀況下之平均年化報酬率:",
        "下列情境假設如下:",
        "(1) 各觀察期間(t)之共同預定交易日日數為20日；",
        f"(2) 配息率:年利率{s.annual}%；及",
        f"(3) 初始投資金額 每單位面額（{s.money(s.denom, space=True)}）× 最低申購單位數（{s.units}單位）= {s.money(s.denom * s.units, space=True)}",
        "情境1:",
        f"本情境假設自動提前出場事件已於第一個適用之提前出場觀察日（且落在第{s.first_callable}個觀察期間結束日）發生。",
        "情境4:",
        f"初始參考價格{one}: {u.initial:,.4f} {ccy}",
        f"最終參考價格{one}: {final:,.4f} {ccy}（初始參考價格{one}之10.00%）",
        f"執行價格{one}: {price(u.initial, s.strike):,.4f} {ccy}（初始參考價格{one}之{s.strike}%）",
    ):
        d.text(86, t, 100)


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 GS 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    _cover(d, s)

    d.chapter("一", "商品基本資料")
    titles = {
        1: f"本商品名稱:{s.name_zh}（TDCC商品代號為{s.product_code}，ISIN:{ISIN})",
        2: f"本商品風險程度:{WARNING}",
        3: f"本商品發行機構及保證機構名稱及長期債務信用評等:本商品發行機構為{ISSUER}。截至本中文產品說明書刊印日期，發行機構之長期信用評等為虛構評等。",
        4: "本商品之發行評等:本商品未經評等。",
        5: f"計價幣別:{s.currency_zh}（{s.iso_text}）",
        6: f"每單位面額:{s.money(s.denom, space=True)}",
        10: "連結標的資產及其相對權重與投資績效的關連情形:",
        13: "本商品年期、發行日、到期日及其他依本商品性質而定之日期:",
        14: "配息資料及其計算公式:",
        15: "到期買回計算公式，包含最低到期保證收益率及參與率:",
        16: "發生自動提前出場事件之條件:",
        17: "投資收益計算方法–包含本金虧損之機率及以不同之情境分析解說之平均年化報酬率:",
    }
    for n in range(1, 37):
        d.article(n, titles.get(n, f"第{n}項說明（不核對）"))
        if n == 10:
            _article10(d, s)
        elif n == 13:
            _article13(d, s)
        elif n == 14:
            d.text(86, "本商品每單位於任一付息日應付之配息金額，應依相關觀察期間(t)按以下公式計算:")
            d.text(86, f"「配息率」就每個觀察期間而言，指年利率{s.annual}% / 12（約為{s.monthly}%）。")
        elif n == 15:
            _article15(d, s)
        elif n == 16:
            _article16(d, s)
        elif n == 17:
            _article17(d, s)

    d.chapter("二", "相關機構事業概況")
    d.text(72, "1. 發行機構")
    for t in (f"(1) 事業名稱: {ISSUER}", "(2) 設立日期:西元1988年6月2日", "(3) 營業所在地: 1 Fictional Court, London"):
        d.text(86, t, 100)
    d.text(72, "2. 保證機構")
    d.text(86, "(1) (i) 事業名稱:虛構集團有限公司（下稱「虛構集團」）")
    d.text(72, "3. 總代理人、計算機構、受託機構、結算系統及其他相關機構")
    for t in (
        "(1) 總代理人",
        "(i) 事業名稱:虛構總代理人臺北分公司",
        "(ii) 設立日期:西元1999年10月26日",
        "(2) 受託機構",
        f"(i) 事業名稱:{DIST['name']}",
        "(ii) 設立日期:西元2000年11月20日",
        f"(iii) 營業所在地:{ADDRESS}",
        f"(iv) 負責人:{DIST['chairman']}",
        "(3) 計算機構",
        f"(i) 事業名稱:{ISSUER}",
    ):
        d.text(86, t, 100)
    d.text(72, "4. 交易架構")

    d.chapter("三", "商品風險揭露")
    for n, title in (
        (1, "基本風險資訊"),
        (2, "個別商品風險資訊"),
        (3, "計算機構免責聲明:不核對。"),
        (4, "受託機構應揭露事項:不核對。"),
        (5, "本商品之投資風險警語:"),
    ):
        d.text(72, f"{n}. {title}", 86)
        if n == 5:
            for k, t in enumerate(_warnings(s), 1):
                d.text(86, f"({k}) {t}", 100)

    d.chapter("四", "一般交易事項")
    d.text(72, "1. 商品申購與贖回相關日期:")
    for t in (
        f"(1) 本商品開始受理申購日期:{s.date(s.trade_date)}",
        f"(2) 本商品結束受理申購日期:{s.date(s.trade_date)}",
        "(3) 開始受理投資人提前贖回日期:投資人得於發行日後次一臺北營業日向受託機構申請提前贖回本商品。",
        "(4) 後續受理提前贖回日期:投資人得於開始受理投資人提前贖回日期起至預定到期日前3個臺北營業日間提出贖回申請。",
    ):
        d.text(86, t, 100)
    d.text(72, "2. 投資人應負擔的各項費用及金額或計算基準之表列:")
    for cells in (
        [(73, "費用項目"), (160, "費率（百分比）"), (271, "收取時點"), (338, "收取方式"), (488, "收取人")],
        [(73, "申購費用"), (160, "申購價金的0%~5%"), (271, "投資人向受"), (488, "受託或銷售")],
        [(73, "提前贖回費用"), (160, "投資人提前贖回價金的"), (271, "投資人向受")],
        [(160, "0%~5%"), (271, "託或銷售機")],
        [(73, "管理費用（信託"), (160, "無"), (271, "不適用")],
        [(73, "分銷費"), (116, "報酬"), (160, "無"), (271, "不適用")],
        [(73, "用(如屬"), (116, "費用"), (160, "申購價金的0%~5%"), (271, "於本商品發")],
        [(73, "發行機"), (116, "折讓"), (160, "無"), (271, "不適用")],
        [(73, "保費費用"), (160, "不適用"), (271, "不適用")],
        [(73, "解約費用"), (160, "不適用"), (271, "不適用")],
        [(73, "其他費用"), (160, "無"), (271, "不適用")],
    ):
        d.row(cells, gap=14, size=9)
    d.text(72, "3. 商品交易架構:請參閱上述第二章第4項。")
    d.text(
        72,
        f"4. 最低申購金額及最低加購金額:最低申購為{s.units}單位且其後增加單位應為一單位之倍數。每一單位為{s.money(s.denom)}。本商品為封閉型。",
    )
    d.text(72, f"5. 申購價金之計算:申購單位數 × 每單位面額（{s.money(s.denom)}）× 100%")
    d.text(72, "6. 申購手續及資金給付方式:不核對。")
    d.text(
        72,
        "7. 發行或受託不成立之情形:發行機構有權於發行日前取消本商品之發行，包括總申購金額低於300,000美元或等值其他外幣；",
    )
    d.text(72, "8. 最低買回/贖回金額或單位數:")
    d.text(86, "(2) 投資人提前贖回：每筆交易之最低贖回單位數為1單位，後續為一單位之倍數，且須經發行機構同意。", 100)
    for n in range(9, 19):
        d.text(72, f"{n}. 第{n}項說明（不核對）")

    d.chapter("五", "特別記載事項")
    d.text(72, "1. 投資人之申購及贖回，須經發行機構確認後始生效力。")
    d.chapter("六", "定義")
    d.text(72, "1. 「共同預定交易日」指各連結標的之預定交易日。")
    doc = d.furnish()
    d.w.flush()
    doc.save(path)
    doc.close()
    require_used(edits, used)
    return path


# ---------------------------------------------------------------- 投資人須知


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """GS 中文投資人須知（docs/templates/gs-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis", width=590)
    w = d.w
    li = "利"  # 真實文件是相容字 U+F9DD；內建 CJK 字型畫不出來，合成文件寫標準字（相容字轉換由本機真實樣本測試涵蓋）
    w.line(374, f"中文投資人須知刊印日期: {s.date(s.trade_date)}", gap=12)
    w.line(249, "中文投資人須知(專業投資人)", gap=12)
    d.text(
        15,
        f"{s.name_zh}（TDCC 商品代號為{s.product_code}，ISIN:{ISIN}，下稱「本商品」) 英文名稱: {s.name_en}（商品種類:{PRODUCT_TYPE}）",
        gap=11,
    )
    d.text(15, f"本中文投資人須知係投資人權{li}重要事項，務請投資人詳細閱讀。", gap=11)
    w.line(15, "本商品之投資風險警語：", gap=11)
    d.section = "warn"
    warnings = _warnings(s)[:9]
    for zh, text in zip(ZH_NUM, warnings, strict=True):
        d.text(15, f"{zh} {text.replace('本中文產品說明書', '本中文投資人須知')}", 29, gap=11)
    d.section = "org"
    w.line(15, "一. 相關機構", gap=11)
    for t in (
        f"1. 發行機構:{ISSUER_ZH}（營業所在地:1 Fictional Court, London）。",
        "2. 各保證機構:(1)虛構集團有限公司（營業所在地:1 Fictional Street, New York）及(2)虛構證券有限公司（營業所在地:香港虛構道1號）。",
        "3. 總代理人:虛構總代理人臺北分公司（營業所在地:臺北市虛構路1號）。",
        f"4. 受託機構:{DIST['name']}（營業所在地:{ADDRESS}）。",
    ):
        d.text(15, t, 29, gap=11)
    d.section = "summary"
    w.line(15, "二. 境外結構型商品事項", gap=11)
    w.line(15, "1. 商品簡介:", gap=11)
    for t in (
        "(1) 受託對象:專業投資人",
        "(2) 本商品與國外相當之交易條件:專業投資人不適用",
        "(3) 本商品風險程度:RR4",
        "(4) 發行機構及各保證機構之長期債務信用評等:截至本中文投資人須知刊印日期，虛構評等。",
        "(5) 本商品之發行評等:本商品未經評等。",
        f"(6) 計價幣別:{s.currency_zh}（{s.iso_text}）",
        f"(7) 每單位面額:{s.money(s.denom)}",
        "(8) 商品到期日時計價幣別本金保本率:0%。",
        "(9) 本商品投資本金達成100%保本率之各項條件:不適用。",
        "(10) 連結標的資產:",
    ):
        d.text(15, t, 29, gap=11)
    d.section = "table"
    _price_table(d, s, tables_iis=True)
    d.section = "summary"
    for t in (
        f"(11) 商品年期:{s.tenor}個月。(詳細條件請參閱中文產品說明書第一章第13項)",
        f"(12) 發行日:{s.date(s.issue_date)}",
        f"(13) 交易日:{s.date(s.trade_date)}",
        f"(14) 評價日:{s.date(s.final_date)}",
        f"(15) 到期日:評價日後的第2個營業日，現預定為{s.date(s.maturity_date)}（下稱「預定到期日」）。",
        "(16) 觀察期間起始日、觀察期間結束日與付息日:請見中文產品說明書第一章第13項。",
        "(17) 開始受理投資人提前贖回日:發行日後次一臺北營業日，惟須經發行機構同意提前贖回。",
        "(18) 後續受理提前贖回日:開始受理投資人提前贖回日起至預定到期日前3個臺北營業日間。",
    ):
        d.text(15, t, 29, gap=11)
    d.section = "coupon"
    w.line(15, "2. 收益分配事項:", gap=11)
    d.text(
        15,
        f"於任一付息日應付之配息金額：每單位面額 × 配息率 × 應計基準。其中「配息率」就每個觀察期間而言，指年{li}率{s.annual}%/12（約為{s.monthly}%）。",
        gap=11,
    )
    d.section = "redeem"
    w.line(15, "3. 買回價金之計算:", gap=11)
    worst = "於評價日表現最差之連結標的最終參考價格" if s.basket else "最終參考價格"
    if s.ki == "D":
        maturity = (
            "(a)如已發生最終提前出場事件，或未發生下限觸發事件時:每單位面額 × 100%;"
            f"(b)如未發生最終提前出場事件，且已發生下限觸發事件，且{worst}等於或高於其執行價格時: 每單位面額 × 100%;"
            f"(c)如未發生最終提前出場事件，且已發生下限觸發事件，且{worst}低於其執行價格時:可交付資產之實物交割。"
            "「下限觸發事件」指如任一連結標的於其任一下限觸發觀察日之參考價格低於其對應之下限觸發價格，則視為已發生下限觸發事件。"
            "「下限觸發觀察期間」指自交易日開始（不含）至評價日結束（含）之期間。"
        )
    elif s.ki == "AM":
        maturity = (
            f"(a)如已發生最終提前出場事件，或{worst}等於或高於其下限觸發價格時：每單位面額 × 100%;"
            f"(b)如未發生最終提前出場事件，且{worst}低於其下限觸發價格時：可交付資產之實物交割。"
        )
    else:
        maturity = "(a)如最終參考價格等於或高於執行價格時:每單位面額 × 100%;(b)如最終參考價格低於執行價格時:可交付資產之實物交割。"
    d.text(
        15,
        "(1)到期買回：如未發生自動提前出場事件者，則本商品之各單位應依下開各段計算之到期買回金額後買回:" + maturity,
        gap=11,
    )
    scope = (
        "一籃子連結標的之任何共同預定交易日（且非為任何連結標的之中斷日）"
        if s.basket
        else "非為連結標的之中斷日之任何預定交易日"
    )
    autocall = (
        "(2)因發生自動提前出場事件買回：如於任一提前出場觀察日，各連結標的至少發生過一次記憶提前出場事件，則視為已發生自動提前出場事件。"
        if s.basket
        else "(2)因發生自動提前出場事件買回：如於任一提前出場觀察日之參考價格等於或高於提前出場價格，則視為已發生自動提前出場事件。"
    )
    autocall += f"其中「提前出場觀察日」指自第{s.first_callable}個觀察期間結束日開始（含）至評價日結束（不含）之期間內，{scope}。"
    if s.basket:
        autocall += "「記憶提前出場事件」：就連結標的而言，如該連結標的於任一提前出場觀察日之參考價格等於或高於其提前出場價格，則視為已發生記憶提前出場事件。"
    d.text(15, autocall, gap=11)
    w.line(15, "4. 發行或受託不成立之處理:", gap=11)
    d.text(15, "(1)發行或受託不成立之情形：(i)總申購金額低於300,000美元或等值其他外幣；(ii)重大市場事件。", gap=11)
    d.section = "rest"
    w.line(15, "三. 主要投資風險", gap=11)
    d.text(
        15,
        f"1. 基本風險資訊:(1) 最低收益風險：於最壞之情況，投資人有可能將損失所有未付{li}息及原始投資本金金額。",
        gap=11,
    )
    d.section = "fees"
    w.line(15, "四. 費用", gap=11)
    for cells in (
        [(15, "費用項目"), (128, "費率（百"), (185, "收取時點"), (298, "收取方式"), (511, "收取人")],
        [(128, "分比）")],
        [(15, "申購費用"), (128, "申購價金"), (185, "投資人向受託或銷售")],
        [(128, "的0%~5%"), (185, "機構申購時")],
        [(15, "贖回費用"), (128, "投資人提"), (185, "投資人向受託或銷售")],
        [(128, "前贖回價"), (185, "機構提前贖回時")],
        [(128, "金的0%~5%")],
        [(15, "管理費用（信託管理"), (128, "無"), (185, "不適用")],
        [(15, "分銷費用(如屬"), (100, "報酬 無"), (185, "不適用")],
        [(15, "發行機構或發"), (100, "費用 申購價金"), (185, "於本商品發行時一次")],
        [(15, "益應單獨列示)"), (128, "的0%~5%")],
        [(100, "折讓 無"), (185, "不適用")],
        [(15, "保費費用"), (128, "不適用"), (185, "不適用")],
        [(15, "其他費用"), (128, "無"), (185, "不適用")],
    ):
        d.row(cells, gap=10, size=8)
    d.section = "rest"
    for t in (
        "五. 相關機構之權利、義務及責任",
        "六. 協助投資人權益之保護方式",
        "七. 發行機構、總代理人及受託機構與投資人爭議之處理方式",
    ):
        w.line(15, t, gap=11)
        d.text(15, "1. 不核對。", gap=11)
    doc = w.doc
    while len(doc) < s.iis_pages:
        w.new_page()
        doc = w.doc
    while len(doc) > s.iis_pages:
        doc.delete_page(len(doc) - 1)
    for n, page in enumerate(doc, 1):
        page.insert_text((259, 815), f"第- {n} -頁，共{s.iis_pages}頁", fontname=FONT, fontsize=8)
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
