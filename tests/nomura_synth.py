"""Nomura 合成說明書（docs/templates/nomura-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/pdf_writer.py 排版：封面「◎ 標籤：值」（標籤 x 45、跨行的值 x 159）；章名「一、商品基本資料」x 45；第一章條號「N.」
x 45（1.～9. 獨立一行、10. 以後與標題同一行）、內文 x 70；表格一格一個文字單位（同一列同一高度）；每頁頁首「N/M」。
`spaced=True` 為 2025 年文件寫法（數字與單位之間多一個空白）。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`、`ts.warn`（封面警語）、第一章各條 `ts.art{n}`
（標的表 `ts.art7.table`、日期表 `ts.art11.table`、下限觸發決定日表 `ts.art11.ki`、情境設定參數與百分比表
`ts.art14.table`）、其他各章 `ts.ch二`～`ts.ch五`。
投資人須知（docs/templates/nomura-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
每頁頁碼「N/4」；段落代號 `iis.cover`、`iis.warn`（警語(一)～(十二)）、`iis.org`（第一 相關機構）、`iis.summary`（第二 商品簡介）、
`iis.coupon`（第三 收益分配事項）、`iis.redeem`（第四、第五）、`iis.fees`（第七）、`iis.rest`（其他）。
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
from pdf_writer import Edit, PdfWriter, require_used
from reference_synth import UL, ProductSpec, build_reference_sheet, price, reference_row, weekday_on_or_after

NAME = STANDARD["product_name"]["nomura"]
WARNING = STANDARD["risk"]["fixed_warning_by_issuer"]["nomura"]
ISSUER = STANDARD["issuer_name"]["nomura"]
GUARANTOR = STANDARD["guarantor_name"]["nomura"]
AGENT = STANDARD["agent_name"]["nomura"]
PRODUCT_TYPE = STANDARD["product_type"]["nomura"]
MINIMUM_ISSUE = STANDARD["minimum_issue_size"]["nomura"]
DIST = STANDARD["distributor"]
PHONE = "+886 2 5556 1313"  # 審查標準 phone_equivalents 的 Nomura 寫法
ADDRESS = "台北市民生東路三段158號6樓"  # 審查標準 address_equivalents 的 Nomura 寫法
ISIN = "XS1999900001"
APPROVAL = dt.date(2026, 6, 11)
Q4 = Decimal("0.0001")
TICKERS = {
    "US": ("ZNA UN", "ZNB UW", "ZNC UW", "ZND UN", "ZNE UW"),
    "JT": ("7001 JT", "7002 JT", "7003 JT", "7004 JT", "7005 JT"),
}
EXCHANGE = {"UN": "紐約證券交易所", "UW": "那斯達克交易所", "JT": "東京證券交易所"}
REDEEM_START = (
    "投資人得於發行日之次一營業日起(受限於「後續受理投資人贖回日期」)，向受託機構申請贖回，且發行機構於接受受託機構之請求後，"
    "始得受理投資人贖回之申請與相關事宜。"
)
REDEEM_LATER = (
    "投資人得於本商品發行後存續期間內之每一臺北、紐約、倫敦、香港、新加坡之共同營業日及預定交易日之日至到期日之前第五個營業日止"
    "向受託機構申請贖回，且發行機構於接受受託機構之請求後，始得受理贖回之申請與相關事宜。"
)
ZH_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二")


def underlyings(count: int, market: str) -> tuple[UL, ...]:
    """Nomura 合成文件的標的：美股期初價 100n + 0.45（2 位），日股 1000n + 500（整數）。"""
    out = []
    for i in range(1, count + 1):
        ticker = TICKERS[market][i - 1]
        initial = Decimal(1000 * i + 500) if market == "JT" else Decimal(100 * i) + Decimal("0.45")
        out.append(UL(f"ALPHA {i} HOLDINGS INC", EXCHANGE[ticker.split()[1]], ticker, initial))
    return tuple(out)


@dataclass
class Spec(ProductSpec):
    """Nomura 合成說明書的規格：商品規格加上 Nomura 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count`、`market` 產生、最終比價日與到期日由日期表推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    單一標的一律非記憶式（範本只有單一標的非記憶式句型）。
    """

    issuer: str = "NOMURA"
    product_code: str = "156199990001"
    ko_obs: str = "P"
    ki: str = "none"  # none／AM／D／P
    tenor: int = 6
    first_callable: int = 1
    annual: Decimal = Decimal("10.00")
    strike: Decimal = Decimal("70.00")
    ko: Decimal = Decimal("100.00")
    ki_pct: Decimal = Decimal("60.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    market: str = "US"  # 標的市場：US 美股、JT 日股（價格沒有小數）
    vwap: bool = False  # 期初定價 VWAP（說明書不寫，表上「期初定價」VWAP、期初價格 4 位小數）
    spaced: bool = False  # 2025 年文件：數字與單位之間多一個空白
    ko_column: bool = False  # 日期表多一欄「自動提前出場價」（NR2）
    merged_prices: bool = False  # 標的表執行價與自動提前出場價擷取成同一行（以空白分開）
    approval: dt.date = APPROVAL
    iis_pages: int = 4

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count, self.market)
        if self.count == 1:
            self.memory = False
        self.final_date, self.maturity_date = self.ends[-1], self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期觀察期間終止日（P 型為自動提前出場決定日）：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def starts(self) -> list[dt.date]:
        """D 型各期觀察期間開始日：第 1 期為交易日後一個平日，其後為前期終止日後一個平日。"""
        prev = [self.trade_date, *self.ends[:-1]]
        return [weekday_on_or_after(d + dt.timedelta(days=1)) for d in prev]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=3)) for d in self.ends]

    @property
    def period_rate(self) -> Decimal:
        """每期（每月）配息率 = 年利率 ÷ 12，四捨五入到 4 位。"""
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    @property
    def basket(self) -> bool:
        return self.count > 1

    @property
    def name_zh(self) -> str:
        under = NAME["basket_zh"] if self.basket else NAME["single_zh"]
        memo = NAME["memory_zh"] if self.memory else ""
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh, memory_zh=memo, underlying_zh=under)

    @property
    def name_en(self) -> str:
        under = NAME["basket_en"] if self.basket else NAME["single_en"]
        memo = NAME["memory_en"] if self.memory else ""
        return NAME["en"].format(tenor=self.tenor, ccy=self.ccy, memory_en=memo, underlying_en=under)

    def sp(self, text: str) -> str:
        """2025 年文件寫法：數字後接中文時多一個空白（`4 個月`、`2025 年9 月22 日`、`10,000 澳幣`）。"""
        return re.sub(r"(\d)(?=[一-鿿])", r"\1 ", text) if self.spaced else text

    def date(self, d: dt.date) -> str:
        return self.sp(f"{d.year}年{d.month}月{d.day}日")

    def money(self, x: int | Decimal) -> str:
        return self.sp(f"{Decimal(x):,.0f}{self.currency_zh}")

    def initial_text(self, x: Decimal) -> str:
        """標的表期初價格：VWAP 4 位小數；日股整數；美股照報價。"""
        if self.vwap:
            return f"{x:.4f}"
        return f"{x:.0f}" if self.market == "JT" else f"{x:.2f}"


def _tokens(text: str) -> list[str]:
    """換行單位：連續的英數字（含符號）不拆開，中文一字一個。"""
    return re.findall(r"[\x21-\x7e]+|\s|.", text)


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（英數字不拆開）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts", width: float = 540):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"
        self.width = width

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
        self.w.line(45, f"{zh}、{name}", gap=20)

    def article(self, n: int, title: str) -> None:
        """第一章條號：1.～9. 獨立一行（標題在下一行），10. 以後與標題同一行；其他章一律獨立一行。"""
        if self.section == "cover" or self.section.startswith("art") or self.section == "ch一":
            self.section = f"art{n}"
        if n >= 10 and self.section.startswith("art"):
            self.text(45, f"{n}. {title}", 70)
            return
        self.w.line(45, f"{n}.", gap=14)
        self.text(70, title)


def _warnings() -> list[str]:
    """封面警語(一)～(十二) 與第三章 6.(1)～(12)：(一) 為固定警語；(五) 受託機構、(六) 保證機構、(七) 總代理人名稱。"""
    return [
        WARNING,
        "發行機構與總代理人已指派受託機構依據境外結構型商品管理規則，於中華民國境內將本商品募集銷售予專業投資人。",
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障。",
        f"本商品雖經{DIST['name']}審查，並不代表證實申請事項或保證該商品之價值。",
        f"本商品到期日或持有期間如有保證配息收益或保證保本率，係由{GUARANTOR['cover']}保證。",
        f"為免疑義，其餘內容應由{AGENT['cover']}及其負責人依法負責。",
        "本商品係依英國法為準據法發行，於中華民國境內受託買賣。",
        "本產品說明書內容並未經過任何主管機關審查。",
        "本產品說明書係依中華民國境外結構型商品管理規則，透過受託機構提供予投資人審閱。",
        "投資人之審閱期間不得低於三日。",
        "請注意，於交易日確認之計息基準取決於市場狀況，詳見第一章商品基本資料中第14項。",
    ]


# ---------------------------------------------------------------- 說明書


def _cover(d: _Doc, s: Spec) -> None:
    w = d.w
    w.line(174, "(適用於專業投資人及OSU客戶)", gap=20)
    w.line(235, "中文產品說明書", gap=24)
    w.need(13)
    with w.verbatim():
        w.put(45, w.y, "◎ 商品代號 /")
    w.y += 13
    w.line(61, w.edit("商品中文名稱："), gap=0)
    d.text(159, f"{s.name_zh}(ISIN No. {ISIN} / TDCC商品代號為{s.product_code})")
    w.line(45, "◎ 商品英文名稱：", gap=0)
    d.text(159, f"{s.name_en} (ISIN No. {ISIN} / TDCC No. {s.product_code} )")
    d.text(45, f"◎ 商品種類：{PRODUCT_TYPE}")
    d.text(45, "◎ 發行機構註冊地：新加坡")
    d.text(45, f"◎ 計價幣別：{s.currency_zh}({s.ccy})")
    for label, name, phone, address in (
        ("發行機構之名稱及地址", ISSUER, "+65 0000 0000", "1 Fictional Boulevard, Singapore 000000"),
        ("總代理人之名稱、電話及地址", AGENT["cover"], "+886 2 0000 0000", "台北市虛構路1號"),
        ("保證機構之名稱及地址", GUARANTOR["cover"], "+81 3 0000 0000", "1-1-1 Fictional, Tokyo, Japan"),
    ):
        d.text(45, f"◎ {label}：{name}")
        d.text(60, f"電話：{phone} 地址：{address}")
    d.text(45, f"◎ 受託機構之名稱、電話及地址：{DIST['name']}")
    d.text(60, f"電話：{PHONE}")
    d.text(60, s.sp(f"地址：{ADDRESS}"))
    d.text(45, f"◎ {DIST['name']}(受託機構)審查通過之日期：{s.date(s.approval)}")
    w.space(20)
    w.line(45, "◎本商品之投資風險警語：", gap=16)
    d.section = "warn"
    for zh, text in zip(ZH_NUM, _warnings(), strict=True):
        w.line(45, f"({zh})", gap=0)
        d.text(81, text)
    d.section = "cover"
    d.text(45, "潛在投資人投資本商品前，應詳閱本產品說明書之內容。")
    d.text(353, f"產品說明書刊印日期：{s.date(s.trade_date)}")


def _article7(d: _Doc, s: Spec) -> None:
    unit = s.currency_zh
    worst = "表現最差之連結標的" if s.basket else "連結標的"
    indiv = "個別" if s.basket else ""
    each = "各連結標的" if s.basket else "連結標的"
    d.row([(71, "最低投資金額"), (167, s.money(s.denom))])
    d.text(71, f"每單位商品面額 {s.money(s.denom)}")
    d.row([(71, "發行價格"), (167, "每單位商品面額的100％")])
    d.text(70, "主要給付項目為每配息日之配息金額及自動提前出場贖回金額或到期贖回金額：")
    d.text(
        70,
        f"於每配息日，發行機構就每單位商品面額將依下列計算公式計算，以{unit}為計價單位給付（四捨五入至小數點第二位）：",
    )
    d.text(70, "每配息日之配息金額 ＝ 每單位商品面額×固定配息率")
    d.text(70, f"固定配息率：就每配息日而言，{s.period_rate}%")
    d.text(
        70, "*投資人請注意，配息後本商品市場價值將有機會相對降低。（例如本商品配息前淨值為95.35%，假設配息率為0.34%）。"
    )
    if s.ko_obs == "D":
        d.text(
            70,
            f"自動提前出場決定日：自{s.date(s.ends[s.first_callable - 1])}起（含）至最終評價日止（含）之各預定交易日。"
            "如任何自動提前出場決定日為任何連結標的的中斷日，則該日應予忽略。",
        )
    else:
        d.text(
            70,
            s.sp("自動提前出場決定日：各自動提前出場決定日即下述第11項(6)「自動提前出場決定日」之表格所示。")
            + "如該日非為預定交易日，則次一預定交易日應為自動提前出場決定日。",
        )
    d.text(70, "自動提前出場日：在自動提前出場事件視為發生之自動提前出場決定日後的第三個紐約營業日。")
    d.text(70, "自動提前出場贖回金額 ＝ 每單位商品面額×100%。")
    if s.memory:
        d.text(
            70,
            "自動提前出場事件：於一自動提前出場決定日當日或之前任一自動提前出場決定日，所有連結標的均發生記憶事件時，"
            "自動提前出場事件即於該自動提前出場決定日發生或視為發生。",
        )
        d.text(
            70,
            "記憶事件：於任何自動提前出場決定日，任何連結標的之收盤價高於或等於其個別自動提前出場價時，該連結標的即發生記憶事件。"
            "於連結標的發生記憶事件時，該連結標的就未來記憶事件而言，應不列入考慮。",
        )
    elif s.basket:
        d.text(
            70,
            "自動提前出場事件：於任何自動提前出場決定日，所有連結標的之收盤價均高於或等於其個別自動提前出場價時，"
            "自動提前出場事件即於該自動提前出場決定日發生或視為發生。",
        )
    else:
        d.text(
            70,
            "自動提前出場事件：於任何自動提前出場決定日，連結標的之收盤價高於或等於自動提前出場價時，"
            "自動提前出場事件即於該自動提前出場決定日發生或視為發生。",
        )
    if s.ki != "none":
        anyone, its = ("任一", "其") if s.basket else ("", "")
        d.text(
            70,
            f"下限觸發事件：若{anyone}連結標的於下限觸發決定日之收盤價小於{its}下限觸發價格，則視為發生下限觸發事件。",
        )
        days = {
            "AM": "最終評價日",
            "D": "自交易日起（不含）至最終評價日止（含）之各預定交易日。如任何下限觸發決定日為任何連結標的之中斷日，該日應予忽略。",
            "P": s.sp("各下限觸發決定日即下述第11項(6)「下限觸發決定日」之表格所示。"),
        }[s.ki]
        d.text(70, f"下限觸發決定日：{days}")
    d.text(70, f"於到期日，發行機構將依下列計算公式以{unit}為計價單位給付：")
    d.text(70, "到期贖回金額：若不曾發生任何自動提前出場事件，到期日將依下列方式贖回：")
    d.text(
        70,
        f"(1) 如於最終評價日，{worst}之最終價格等於或高於{indiv}執行價時，每單位商品面額於到期日之到期贖回金額應等於：",
    )
    d.text(241, "每單位商品面額x 100.00%")
    if s.ki == "none":
        d.text(70, f"(2) 如於最終評價日，{worst}之最終價格低於{indiv}執行價時，每單位商品面額之到期贖回金額應等於：")
    else:
        d.text(
            70,
            f"(2) 如於最終評價日，{worst}之最終價格低於{indiv}執行價但下限觸發事件未有發生時，每單位商品面額於到期日之到期贖回金額應等於：",
        )
        d.text(241, "每單位商品面額x 100.00%")
        d.text(
            70,
            f"(3) 如於最終評價日，{worst}之最終價格低於執行價時，且有發生下限觸發事件，每單位商品面額之到期贖回金額應等於：",
        )
    d.text(247, "股票交割股數+剩餘金額")
    d.text(70, f"股票交割股數：(每單位商品面額x最終匯率)/ {worst}之執行價")
    d.text(70, f"期初價格    ：{each}於下表所示之期初價格。")
    d.text(70, f"執行價        ：{each}於下表所示之執行價。")
    d.text(70, f"自動提前出場價：{each}於下表所示之自動提前出場價。")
    if s.ki != "none":
        d.text(70, f"下限觸發價：{each}於下表所示之下限觸發價")
    d.section = "art7.table"
    _price_table(d, s)
    d.section = "art7"


def _price_table(d: _Doc, s: Spec) -> None:
    """第 7 項標的表（7 號字）：表頭一格拆成多行（K／KO／KI % 寫在表頭）；每檔一列，名稱與彭博代碼拆兩行（單數檔
    「代號 交易所碼」／「Equity」，雙數檔「代號」／「交易所碼 Equity 交易所」）。表頭「期初價格 執行價」擠在同一行，
    執行價欄的續行對齊「執」字（真實樣本）。"""
    ki = s.ki != "none"
    d.w.need(60 + 26 * s.count)  # 表格不跨頁
    k_x = 290 + 7 * 4 + fitz.get_text_length(" ", fontname="helv", fontsize=7)  # 「執行價」接在「期初價格 」之後

    def row(cells: list[tuple[float, str]], gap: float) -> None:
        d.row(cells, gap=gap, size=7)

    row(
        [
            (60, "連結標的"),
            (140, "彭博代碼"),
            (185, "交易所"),
            (225, "關連交易"),
            (262, "計價"),
            (290, "期初價格 執行價"),
            (395, "自動提前出"),
            *([(445, "下限觸發價")] if ki else []),
        ],
        9,
    )
    row(
        [(239, "所"), (262, "幣別"), (k_x, "(期初價格"), (397, "場價 (期初"), *([(445, "(期初價格之")] if ki else [])],
        9,
    )
    row([(k_x, f"之{s.strike}% )"), (402, "價格之"), *([(452, f"{s.ki_pct}% )")] if ki else [])], 9)
    row([(398, f"{s.ko}% )")], 12)
    unit = "日圓" if s.market == "JT" else "美元"
    for i, u in enumerate(s.underlyings, 1):
        code, exch = u.ticker.split()
        name_a, name_b = u.name.split(" ", 2)[:2], u.name.split(" ", 2)[2]
        prices = [price(u.initial, s.strike), price(u.initial, s.ko)]
        cells = [(45, str(i)), (60, " ".join(name_a))]
        cells += [(140, f"{code} {exch}")] if i % 2 else [(145, code)]
        cells += [(185, EXCHANGE[exch][:4]), (225, "所有交易"), (262, unit), (295, s.initial_text(u.initial))]
        if s.merged_prices:
            cells.append((345, f"{prices[0]:,.4f} {prices[1]:,.4f}"))
        else:
            cells += [(345, f"{prices[0]:,.4f}"), (395, f"{prices[1]:,.4f}")]
        if ki:
            cells.append((445, f"{price(u.initial, s.ki_pct):,.4f}"))
        row(cells, 9)
        rest = [(53, name_b), (145, "Equity") if i % 2 else (136, f"{exch} Equity {EXCHANGE[exch][:4]}")]
        row([*rest, (190 if i % 2 else 200, EXCHANGE[exch][4:]), (239, "所")], 14)


def _article8_9(d: _Doc, s: Spec, n: int) -> None:
    if n == 8:
        if s.basket:
            for i, u in enumerate(s.underlyings, 1):
                d.text(70, f"{'連結標的資產：' if i == 1 else ''}連結標的{i}：{u.name}")
        else:
            d.text(70, f"連結標的資產：連結標的：{s.underlyings[0].name}")
        d.text(70, "相對權重：不適用")
        d.text(70, "與投資績效之關聯情形：請見第一章第7項「主要給付項目及其計算方式」。")
        return
    for i, u in enumerate(s.underlyings, 1):
        d.text(70, f"({i}) 連結標的{i if s.basket else ''}：{u.name}")
        d.text(70, "虛構公司主要從事虛構業務。")
        d.text(70, f"彭博代碼：{u.ticker} Equity/交易所：{u.exchange}/相關交易所：所有交易所")


def _article11(d: _Doc, s: Spec) -> None:
    d.text(70, s.sp(f"(1) 商品年期：{s.tenor}個月（可能因自動提前出場事件或計畫提前贖回事件之發生而變更）"), 94)
    d.text(70, f"(2) 交易日：{s.date(s.trade_date)}", 94)
    d.text(70, f"(3) 發行日：{s.date(s.issue_date)}", 94)
    d.text(70, f"(4) 預定到期日：{s.date(s.maturity_date)} ，即最終評價日後之第三個紐約營業日", 94)
    d.text(70, "(5) 到期日：預定到期日，可能因自動提前出場事件或計畫提前贖回事件之發生而變更", 94)
    title = "配息日" if s.ko_obs == "D" else "配息日/自動提前出場日"
    d.text(70, f"(6) 配息日：為下表標題為「{title}」欄位所載之個別日期，依據營業日順延慣例調整（各為「配息日」）。", 94)
    d.section = "art11.table"
    k, n = s.first_callable, s.tenor
    if s.ko_obs == "D":
        d.row([(55, "t"), (78, "觀察期間開始日(含)"), (204, "觀察期間終止日(含)"), (359, "配息日")])
        for j in range(1, n + 1):
            end = s.date(s.ends[j - 1]) + ("(最終評價日)" if j == n else "")
            pay = s.date(s.payments[j - 1]) + ("(到期日)" if j == n else "")
            d.row([(54, str(j)), (89, s.date(s.starts[j - 1])), (185, end), (329, pay)])
    else:
        head = [(106, "t"), (139, "自動提前出場決定日")]
        head += (
            [(276, "自動提前出場價"), (386, "配息日/自動提前出場日")]
            if s.ko_column
            else [(259, "配息日/自動提前出場日")]
        )
        d.row(head)
        for j in range(1, n + 1):
            obs = "不適用" if j < k else s.date(s.ends[j - 1])
            pay = s.date(s.payments[j - 1]) + ("(到期日)" if j == n else "")
            cells = [(105, str(j)), (150, obs)]
            if s.ko_column:
                cells += [(266, "連結標的期初價格的"), (388, pay)]
                d.row(cells, gap=13)
                d.row([(293, f"{s.ko}%")], gap=15)
            else:
                cells.append((259, pay))
                d.row(cells)
        if s.ki == "P":
            d.section = "art11.ki"
            d.row([(182, "t"), (300, "下限觸發決定日")])
            for j in range(1, n + 1):
                d.row([(181, str(j)), (298, s.date(s.ends[j - 1]))])
    d.section = "art11"
    d.text(94, "「營業日順延慣例」係指若相關日並非營業日，則該日應順延至次一營業日。")
    d.text(70, "(7) 自動提前出場日：如上，即於自動提前出場事件視為發生之自動提前出場決定日後的第三個紐約營業日。", 94)
    d.text(70, f"(8) 最終評價日：即{s.date(s.final_date)}。如該日非為預定交易日，則次一預定交易日應為最終評價日。", 94)
    d.text(70, "(9) 中斷截止日：原預定應進行觀察或評價之日後八（8）個預定交易日之日。", 94)


def _article14(d: _Doc, s: Spec) -> None:
    d.text(70, "(1) 投資收益計算方法：請見上述第7項「主要給付項目及其計算方式」。", 94)
    d.text(70, "(4) 以例示之情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：", 94)
    d.text(94, "設定參數:")
    d.section = "art14.table"
    for label, value in (
        ("年期", s.sp(f"{s.tenor}個月")),
        ("每單位商品面額", s.money(s.denom)),
        ("發行價格", "100%"),
        ("投資單位數", s.sp("1單位")),
        ("投資金額", f"{s.denom:,} x 100% x 1 = {s.money(s.denom)}"),
        ("固定配息率", f"就每配息日而言，{s.period_rate}%"),
    ):
        d.row([(96, label), (209, value)])
    head = [(214, "期初價格"), (309, "執行價"), (381, "自動提前出場價")]
    if s.ki != "none":
        head.append((476, "下限觸發價格"))
    d.row(head)
    for u in s.underlyings:
        cells = [(96, u.name), (221, "100%"), (310, f"{s.strike}%"), (398, f"{s.ko}%")]
        if s.ki != "none":
            cells.append((491, f"{s.ki_pct}%"))
        d.row(cells)
    d.section = "art14"
    total = (Decimal(s.denom) * s.period_rate / 100 * s.tenor).quantize(Decimal("0.01"))
    d.text(70, "情境一（自動提前出場）")
    d.text(70, "假設於本商品發行後，所有連結標的均於第一自動提前出場決定日發生自動提前出場事件，則本商品實際配息如下：")
    d.text(70, f"本情境中年化報酬率為{s.annual}%")
    d.text(70, "情境二（一般情境）")
    d.text(70, "假設於本商品發行後，不曾發生任何自動提前出場事件，則本商品實際配息如下：")
    d.text(70, "到期贖回金額：每單位商品面額x 100.00%")
    d.text(70, f"本情境中年化報酬率為[({total:,}+{s.denom:,})/{s.denom:,}-1]*(12/{s.tenor})={s.annual}%")
    d.text(70, "情境三（較差情境）")
    d.text(
        70,
        f"假設於最終評價日，連結標的的最終價格為或相當於35{s.currency_zh}，低於執行價70{s.currency_zh}，則投資人除利息"
        f"{total:,}{s.currency_zh}外，發行機構將支付30.00{s.currency_zh}，較原始投資金額損失約41.34%。",
    )
    d.text(70, "情境四（發行機構或保證機構無法履約）")
    d.text(70, "在最壞情況下，投資人將損失其投資之所有本金及利息（總報酬率則為-100%）。")


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 Nomura 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()  # 說明書與投資人須知共用：改字換到哪一份都算
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    w = d.w
    _cover(d, s)

    d.chapter("一", "商品基本資料")
    art1_en = f"{ISSUER} " + s.name_en.replace(f" issued by {ISSUER}", "")
    titles = {
        1: f"商品名稱：{s.name_zh} ({art1_en}) (ISIN No. {ISIN})",
        2: "商品風險程度：RR4",
        3: "發行機構或保證機構名稱及其長期債務信用評等：",
        4: "商品之發行評等：不適用",
        5: f"計價幣別：{s.currency_zh}({s.ccy})",
        6: "計價貨幣本金保本率：不適用。本商品不保本。",
        7: "主要給付項目及其計算方式：",
        8: "連結標的資產及其相對權重、與投資績效之關聯情形：",
        9: "連結標的之相關說明或評等資料：",
        10: "連結標的調整之條件及方法：不核對。",
        11: "商品年期、發行日、到期日及其他依個別商品性質而定之日期：",
        12: "配息資料及其計算公式：請見前述第7項「主要給付項目及其計算方式」。",
        13: "到期贖回計算公式，包含最低保證收益率及參與率：不適用。",
        14: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
        15: "境外結構型商品之固定配息率及其風險說明。",
        16: "發行機構得提前贖回或投資人得提前贖回之條件及應注意事項：",
    }
    for n in range(1, 22):
        d.article(n, titles.get(n, f"第{n}項說明（不核對）"))
        if n == 3:
            d.text(70, f"保證機構名稱：{GUARANTOR['ch1']}")
            d.text(70, "保證機構之長期債務信用評等為：虛構評等")
        elif n == 7:
            _article7(d, s)
        elif n in (8, 9):
            _article8_9(d, s, n)
        elif n == 11:
            _article11(d, s)
        elif n == 14:
            _article14(d, s)
        elif n == 15:
            d.text(70, f"(1)    固定配息率：就每配息日而言，{s.period_rate}%。")
            d.text(70, "(2)    風險說明：請參閱第三章「商品風險揭露」之說明。")
        elif n == 16:
            d.text(
                70,
                f"(1) 投資人提前贖回：投資人得請求發行機構在到期日前提前贖回本商品，最低金額為{s.money(s.denom)}，"
                f"並以{s.money(s.denom)}為增加贖回之單位，但發行機構無義務依投資人要求於到期日前提前贖回本商品。",
                94,
            )
            d.text(70, "(6) 違約事件：借款債務至少10,000,000美元（或其他貨幣之相當金額）因違約而需提前償還。", 94)

    d.chapter("二", "相關機構事業概況")
    d.w.line(45, "1. 發行機構", gap=14)
    for t in (
        f"(1) 事業名稱：{ISSUER}",
        "(2) 設立日期：2012年5月17日",
        "(3) 營業所在地：1 Fictional Boulevard, Singapore",
        "(4) 負責人姓名：虛構甲",
    ):
        d.text(55, t, 81)
    d.w.line(45, "2. 保證機構：", gap=14)
    for k, t in enumerate(
        (f"事業名稱：{GUARANTOR['ch2']}", "設立日期：1925年12月25日", "營業所在地：1-1-1 Fictional, Tokyo"), 1
    ):
        d.row([(55, f"({k})"), (81, t)])
    d.w.line(45, "3. 總代理人、計算代理機構、主付款代理機構及付款代理機構、受託機構、保管機構及其他相關機構", gap=14)
    for k, (title, name, place, head) in enumerate(
        (
            ("總代理人：", AGENT["ch2"], "台北市虛構路1號", "虛構乙"),
            ("計算代理機構", "Nomura Singapore Limited", "1 Fictional Boulevard, Singapore", "虛構丙"),
            (
                "主付款代理機構及付款代理機構：",
                "Fictional Bank, N.A., London Branch",
                "1 Fictional Square, London",
                "虛構丁",
            ),
            ("受託機構：", DIST["name"], s.sp(ADDRESS), DIST["chairman"]),
        ),
        1,
    ):
        d.text(55, f"({k}) {title}")
        for t in (
            f"(a) 事業名稱：{name}",
            "(b) 設立日期：2000年11月20日",
            f"(c) 營業所在地：{place}",
            f"(d) 負責人姓名：{head}",
        ):
            d.text(81, t)
    d.text(55, "(5) 保管機構：無")
    d.w.line(45, "4. 交易架構說明：不核對。", gap=14)
    d.w.line(45, "5. 利害關係人揭露：不核對。", gap=14)

    d.chapter("三", "商品風險揭露")
    for n, title in ((1, "基本風險資訊："), (2, "個別商品風險資訊："), (6, "本商品之投資風險警語")):
        d.article(n, title)
        if n == 2:  # 匯率風險的幣別說明文字（Issue #316）
            d.text(
                71,
                f"本商品係以{s.currency_zh}計價，當投資人以新台幣或其他貨幣轉換成{s.currency_zh}進行投資時，"
                "本金加利息轉換回新台幣或其他幣別可能產生匯兌風險。",
                95,
            )
        if n == 6:
            for k, t in enumerate(_warnings(), 1):
                d.text(71, f"({k}) {t}", 95)

    d.chapter("四", "一般交易事項")
    d.article(1, "商品開始受理申購、開始受理贖回日期及後續受理贖回日期，每營業日受理申購、贖回申請截止時間：")
    for t in (
        f"(1) 商品開始受理申購日期：{s.date(s.trade_date)}",
        f"(2) 接受申購截止日：{s.date(s.trade_date)}",
        f"(3) 開始受理投資人贖回日期：{REDEEM_START}",
        f"(4) 後續受理投資人贖回日期：{REDEEM_LATER}",
        "(5) 每營業日受理申購、投資人贖回申請截止時間：投資人可於受託機構營業日台北下午時間三點整以前提出申請。",
    ):
        d.text(70, t, 94)
    d.article(2, "投資人應負擔的各項費用及金額或計算基準之表列：")
    d.text(70, "本商品係複委託項下之投資標的，投資人同意下列相關費用：")
    for row in (
        [(94, "費用項目"), (169, "費率(百分比)"), (262, "收取時點"), (382, "收取方式"), (496, "收取人")],
        [(71, "申購費用"), (167, "申購價金的"), (239, "投資人向受託或"), (479, "受託或銷售")],
        [(167, "0%~5%"), (239, "銷售機構申購時"), (479, "機構")],
        [(71, "提前贖回費用"), (167, "投資人提前"), (239, "投資人向受託或"), (479, "受託或銷售")],
        [(167, "贖回價金的"), (239, "銷售機構提前贖")],
        [(167, "0%~5%"), (239, "回時")],
        [(71, "管理費用（信託"), (167, "無"), (239, "不適用")],
        [(71, "管理費或管銷費")],
        [(71, "用）")],
        [(71, "分銷費用（如屬"), (167, "申購價金的"), (239, "於本商品發行時")],
        [(71, "發行機構或發行"), (167, "0%~5%"), (239, "一次給付")],
        [(71, "人給予受託或銷")],
        [(71, "售機構之報酬、費")],
        [(71, "用、折讓等各項利")],
        [(71, "益應單獨列示）")],
        [(71, "保費費用"), (167, "不適用")],
        [(71, "解約費用"), (167, "不適用")],
        [(71, "其他費用"), (167, "無")],
    ):
        d.row(row, gap=14)
    d.article(3, "商品交易架構：不核對。")
    d.article(4, "最低申購金額及最低後續申購金額：")
    d.text(70, s.sp(f"最低申購金額：{s.money(s.denom)}（相當1單位商品面額）最低加價申購金額：{s.money(s.denom)}"))
    d.article(5, "申購價金之計算：每單位商品面額× 申購單位數 × 100% + 申購費用")
    d.article(6, "申購手續及資金給付方式：不核對。")
    d.article(7, "發行不成立之情形：")
    d.text(
        70,
        f"發行本商品之前提條件包括：(a) 發行機構於交易截止日或之前收到之本商品有效申購金額總計至少達{MINIMUM_ISSUE}；且不存在不利之市場情況。",
    )
    d.article(8, "最低贖回金額或單位數：")
    d.text(
        70,
        s.sp(
            f"本商品本金之最低贖回金額：{s.money(s.denom)}（相當1單位商品面額），並以{s.money(s.denom)}為增加贖回之單位。"
        )
        + "本商品必須以單位商品面額之完整倍數贖回。",
    )
    for n in range(9, 17):
        d.article(n, f"第{n}項說明（不核對）")
        if n == 15:
            d.text(214, f"受託機構聯絡方式：{PHONE}")

    d.chapter("五", "特別記載事項")
    d.article(1, "不核對。")
    _page_marks(w.doc, 288)
    path = w.save(path)
    require_used(edits, used)
    return path


def _page_marks(doc: fitz.Document, x: float) -> None:
    """每頁頁首「N/M」（真實文件在頁面頂端或底部單獨一行，擷取時排除）。"""
    for n, page in enumerate(doc, 1):
        page.insert_text((x, 830), f"{n}/{len(doc)}", fontname="helv", fontsize=9)


# ---------------------------------------------------------------- 投資人須知


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """Nomura 中文投資人須知（docs/templates/nomura-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis", width=575)
    w = d.w
    worst = "表現最差之連結標的" if s.basket else "連結標的"
    indiv = "個別" if s.basket else ""
    w.line(255, "中文投資人須知", gap=12)
    d.text(17, s.name_zh.replace(f"{ISSUER}發行", f"{ISSUER} 發行"))
    d.text(22, s.name_en)
    d.text(14, f"商品種類：{PRODUCT_TYPE} (ISIN 號碼: {ISIN} / TDCC商品代號為{s.product_code} )")
    w.line(14, "本商品之投資風險警語：", gap=12)
    d.section = "warn"
    warnings = _warnings()
    warnings[9] = "本投資人須知係依中華民國境外結構型商品管理規則，透過受託機構提供予投資人審閱。"
    for zh, text in zip(ZH_NUM, warnings, strict=True):
        d.text(14, f"({zh}){text}", gap=11)
    d.section = "org"
    w.line(14, "第一  相關機構", gap=12)
    for t in (
        f"1. 發行機構: {ISSUER}，營業所在地：1 Fictional Boulevard, Singapore",
        f"2. 保證機構：{GUARANTOR['cover']}，營業所在地：1-1-1 Fictional, Tokyo",
        f"3. 總代理人：{AGENT['cover']}，營業所在地：台北市虛構路1號",
        f"4. 受託機構：{DIST['name']}，營業所在地：{s.sp(ADDRESS)}",
    ):
        d.text(14, t, gap=11)
    d.section = "summary"
    w.line(14, "第二 商品簡介", gap=12)
    if s.basket:
        names = "；".join(f"連結標的{i}: {u.name}" for i, u in enumerate(s.underlyings, 1)) + "。"
    else:
        names = f"連結標的: {s.underlyings[0].name}。"
    for n, t in enumerate(
        (
            "本商品受託對象：專業投資人及OSU客戶。",
            "本商品與國外相當之交易條件：專業投資人及OSU客戶不適用。",
            "本商品風險程度：RR4",
            "發行機構或保證機構之長期債務信用評等：截至本中文投資人須知刊印日期，發行機構未獲評等。",
            "本商品之發行評等：不適用。",
            f"計價幣別：{s.currency_zh}({s.ccy})",
            "計價貨幣本金保本率：不適用。本商品不保本。",
            "投資本金達成保本率之各項條件：不適用。",
            f"連結標的資產：{names}",
            s.sp(f"本商品年期：{s.tenor}個月（可能因自動提前出場事件或計畫提前贖回事件之發生而變更）。"),
            f"交易日：{s.date(s.trade_date)}",
            f"發行日：{s.date(s.issue_date)}",
            f"預定到期日：{s.date(s.maturity_date)} ，即最終評價日後之三個紐約營業日",
            "到期日：預定到期日，可能因自動提前出場事件或計畫提前贖回事件之發生而變更",
            f"開始受理投資人贖回日期：{REDEEM_START}",
            f"後續受理投資人贖回日期：{REDEEM_LATER}",
        ),
        1,
    ):
        w.need(11)
        with w.verbatim():
            w.put(15, w.y, f"{n}.")
        d.text(43, t, gap=11)
    d.section = "coupon"
    w.line(14, "第三 收益分配事項：", gap=12)
    w.line(15, "1.", gap=0)
    parts = [
        f"分配項目、分配時間：於每配息日，發行機構就每單位商品面額以{s.currency_zh}為計價單位給付：每配息日之配息金額 ＝ "
        f"每單位商品面額×固定配息率。固定配息率：就每配息日而言，{s.period_rate}%。*投資人請注意，配息後本商品市場價值將有機會相對降低。"
    ]
    if s.memory:
        parts.append(
            "「自動提前出場事件」：於一自動提前出場決定日當日或之前任一自動提前出場決定日，所有連結標的均發生記憶事件時，"
            "自動提前出場事件即於該自動提前出場決定日發生或視為發生。「記憶事件」：於任何自動提前出場決定日，任何連結標的之收盤價"
            "高於或等於其個別自動提前出場價時，該連結標的即發生記憶事件。"
        )
    elif s.basket:
        parts.append(
            "「自動提前出場事件」：於任何自動提前出場決定日，所有連結標的之收盤價均高於或等於其個別自動提前出場價時，自動提前出場事件"
            "即於該自動提前出場決定日發生或視為發生。"
        )
    else:
        parts.append(
            "「自動提前出場事件」：於任何自動提前出場決定日，連結標的之收盤價高於或等於自動提前出場價時，自動提前出場事件即於該自動"
            "提前出場決定日發生或視為發生。"
        )
    parts.append("「配息日」：詳見中文產品說明書第一章第11項(6)配息日之表格標題為「配息日」欄位所載之個別日期。")
    if s.ko_obs == "D":
        parts.append(
            f"「自動提前出場決定日」：自{s.date(s.ends[s.first_callable - 1])}起（含）至最終評價日止（含）之各預定交易日。"
            "如任何自動提前出場決定日為任何連結標的的中斷日，該日應予忽略。"
        )
    else:
        parts.append(
            s.sp(
                "「自動提前出場決定日」：各自動提前出場決定日詳見中文產品說明書第11項(6)「自動提前出場決定日」之表格所示。"
            )
        )
    if s.ki != "none":
        anyone, its = ("任一", "其") if s.basket else ("", "")
        days = {
            "AM": "最終評價日。",
            "D": "自交易日起（不含）至最終評價日止（含）之各預定交易日。",
            "P": s.sp("各下限觸發決定日即中文產品說明書第一章第11項(6)「下限觸發決定日」之表格所示。"),
        }[s.ki]
        parts.append(
            f"「下限觸發事件」：若{anyone}連結標的於下限觸發決定日之收盤價小於{its}下限觸發價格，則視為發生下限觸發事件。"
            f"「下限觸發決定日」：{days}"
        )
    d.text(43, "".join(parts), gap=11)
    w.line(15, "2.", gap=0)
    d.text(43, "「營業日」係指商業銀行及外匯市場辦理付款交割且開放一般營業之日：就付款日而言：紐約。", gap=11)
    d.section = "redeem"
    w.line(14, "第四 贖回價金之計算：", gap=12)
    d.text(
        14,
        f"1. 到期贖回計算公式：(1) 如於最終評價日，{worst}之最終價格等於或高於執行價時，每單位商品面額x100.00%。",
        gap=11,
    )
    if s.ki == "none":
        d.text(
            14,
            f"(2) 如於最終評價日，{worst}之最終價格低於{indiv}執行價時，每單位商品面額之到期贖回金額應等於：",
            gap=11,
        )
    else:
        d.text(
            14,
            f"(2) 如於最終評價日，{worst}之最終價格低於{indiv}執行價但下限觸發事件未有發生時，每單位商品面額x100.00%。",
            gap=11,
        )
    w.line(14, "第五 受託不成立之處理：", gap=12)
    d.text(
        14,
        f"1. 受託不成立之情形：需發行機構於交易截止日或之前收到之本商品有效申購金額總計至少達{MINIMUM_ISSUE}；且不存在不利之市場情況。",
        gap=11,
    )
    d.section = "rest"
    w.line(14, "第六 商品風險揭露（以下僅為中文產品說明書「商品風險揭露」之節錄）", gap=12)
    d.text(14, "1. 基本風險資訊：(1) 最低收益風險：最差狀況下，投資人可能會損失所有的本金和利息。", gap=11)
    d.section = "fees"
    w.line(14, "第七 商品相關費用", gap=12)
    d.text(14, "本商品係複委託項下之投資標的，投資人同意下列相關費用：", gap=11)
    for row in (
        [(47, "費用項目"), (137, "費率(百分比)"), (245, "收取時點")],
        [(15, "申購費用"), (128, "申購價金的"), (213, "投資人向受託或銷售")],
        [(128, "0%~5%"), (213, "機構申購時")],
        [(15, "提前贖回費用"), (128, "投資人提前贖"), (213, "投資人向受託或銷售")],
        [(128, "回價金的"), (213, "機構提前贖回時")],
        [(128, "0%~5%")],
        [(15, "分銷費用（如屬發行"), (128, "申購價金的"), (213, "於本商品發行時一次")],
        [(15, "機構或發行人給予受"), (128, "0%~5%"), (213, "給付")],
        [(15, "託或銷售機構之報")],
        [(15, "酬、費用、折讓等各")],
        [(15, "項利益應單獨列示）")],
        [(15, "其他費用"), (128, "無")],
    ):
        d.row(row, gap=11)
    d.section = "rest"
    w.line(14, "第八 相關機構之權利、義務及責任", gap=12)
    d.text(14, "1. 投資人之申購及贖回，須經發行機構確認後始生效力。", gap=11)
    w.line(14, "第九 協助投資人權益之保護方式", gap=12)
    d.text(14, "1. 投資人與發行機構、總代理人或受託機構發生爭議、訴訟之處理方式：", gap=11)
    d.text(215, f"受託機構聯絡方式：{PHONE}", gap=11)
    w.line(14, "第十 發行機構、總代理人或受託機構與投資人爭議之處理方式", gap=12)
    d.text(14, "1. 總代理人擔任發行機構於中華民國境內之訴訟代理人。", gap=11)
    doc = w.doc
    while len(doc) < s.iis_pages:
        w.new_page()
        doc = w.doc
    while len(doc) > s.iis_pages:
        doc.delete_page(len(doc) - 1)
    for n, page in enumerate(doc, 1):
        page.insert_text((291, 830), f"{n}/{s.iis_pages}", fontname="helv", fontsize=9)
    return w.save(path)


# ---------------------------------------------------------------- 參考條件表與核對


def row(spec: Spec, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列；VWAP 時表上「期初定價」為 VWAP、進場價空白（NR6）。`overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(spec)
    if spec.vwap:
        out["期初定價"] = "VWAP"
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
