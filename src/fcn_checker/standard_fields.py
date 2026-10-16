"""說明書標準欄位：上手 adapter 與共用規則之間的 seam（CONTEXT.md「標準欄位」）。

各上手 parser 以 `ts.f(name)` 交出下列欄位（`ParsedField`，含狀態與證據）；各上手共用的規則
（參考條件表欄位、審查標準、Non-Call／ISIN／發行日／比價日與回填）只讀這些欄位與全文索引，不碰上手專屬的擷取結果。
adapter 沒交出的欄位視為缺漏（`not_provided`），相關規則轉人工覆核並寫出欄位名稱。
範本本身沒有的欄位（例：MS 說明書沒有年利率）由 adapter 明確交出 `absent`（不適用），共用規則不核對、不報缺漏。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from .parsers.layout import TextIndex
from .schema import Evidence, FieldStatus, ParsedField


class TermSheet(Protocol):
    """上手 adapter 讀出的說明書：`f(name)` 交出標準欄位（及上手專屬欄位），`full_text` 為全文索引。

    `f` 不丟例外：沒有交出的欄位回傳 `not_provided(name)`（實作可直接用 `lookup`）。
    各上手的實作可另外帶該上手規則需要的專屬資料（例：BARC 價格表原文列、HSBC 情境文字索引）。
    """

    full_text: TextIndex

    def f(self, name: str) -> ParsedField: ...


def not_provided(name: str) -> ParsedField:
    """上手 adapter 沒有交出的欄位：缺漏，說明寫出欄位名稱。"""
    kind = "標準欄位" if is_standard(name) else "欄位"
    return ParsedField.missing(name, f"上手未提供{kind}「{name}」")


def absent(name: str, what: str) -> ParsedField:
    """範本沒有的欄位（例：MS 說明書沒有年利率）：不適用，共用規則不核對也不報缺漏；說明寫在結果上。"""
    return ParsedField(name, FieldStatus.NOT_APPLICABLE, note=f"範本沒有{what}")


def absent_all(names: Sequence[str], what: str) -> dict[str, ParsedField]:
    """多個範本沒有的欄位一起交出 absent（例：`DISTRIBUTOR_OTHERS`）。"""
    return {name: absent(name, what) for name in names}


def lookup(fields: Mapping[str, ParsedField], name: str) -> ParsedField:
    """`TermSheet.f` 的共用實作：有交出就回傳該欄位，沒有就是 `not_provided`。"""
    pf = fields.get(name)
    return not_provided(name) if pf is None else pf


@dataclass(frozen=True)
class PriceRow:
    """各標的價格列，順序同 `underlyings`。"""

    ticker: str | None  # 價格列上的彭博代號；表上沒有代號時為 None，以 `underlyings` 同順序的代號為準
    prices: dict[str, Decimal]  # initial／strike／ko／ki；說明書無 KI 時沒有 ki
    evidence: tuple[Evidence, ...]
    # 標的名稱（BARC 價格列、MS 第 11 項標的表）：投資人須知的標的名稱和它比；讀不到代號時也用來指出是哪一檔
    name: str | None = None
    # 價格旁的幣別字（標的報價幣別，例：美元）；名稱樣板的 {quanto_*} 依它和承作幣別是否不同（DBS）。沒讀時為 None
    currency: str | None = None


@dataclass(frozen=True)
class AutocallSchedule:
    """說明書的提前出場排程（由 parsers/schedule.py 的共用推導從日期表推得）。"""

    observation: str  # D 期間每日觀察／P 每期定日觀察
    first_callable: int  # 第一個可以提前出場的期別（Non-Call(月)），最小為 1
    periods: int  # 期數
    dates: dict[int, dt.date]  # 期別 → 比價日；D 只有第 k 期與末期，P 為第 k 期起的每一期


@dataclass(frozen=True)
class DateRow:
    """日期表的一列（一期）；表上沒有的欄、或該期寫「不適用／N/A／-」的格為 None。"""

    period: int  # 期別（第 1～n 期）
    start: dt.date | None = None  # 期始日
    end: dt.date | None = None  # 期末日（觀察期間終止日）
    observation: dt.date | None = None  # 觀察日（評價日／提前出場評價日；期間每日觀察的表通常 = 期末日）
    payment: dt.date | None = None  # 配息日
    evidence: tuple[Evidence, ...] = ()


StartRule = Literal["previous_end", "next_trading_day"]


@dataclass(frozen=True)
class PeriodStart:
    """期始日的定義，由上手依範本宣告（Issue #289 決定 5）。"""

    rule: StartRule  # previous_end：= 前一期期末日（範本寫「開始日（不含當日）」）；next_trading_day：前一期期末後的下一個一籃子預定交易日
    first: (
        Literal["issue_date", "after_issue"] | None
    )  # 第 1 期有期始日時和發行日怎麼比：= 發行日／依 `rule` 由發行日推；None 不比
    blank: int | None = 0  # 開頭期始日空白的期數（例：第 1～k 期不可提前出場）；None 為讀不到 k、無法確認（轉人工覆核）


@dataclass(frozen=True)
class DateTable:
    """說明書的日期表（CONTEXT.md「日期表」）：每期一列，加上表的位置與期始日的定義。"""

    where: str  # 表在說明書的位置，用在項目名稱（例：第 13 條日期表）
    rows: tuple[DateRow, ...]
    start: PeriodStart | None = None  # None：表上沒有期始日欄，不查期始日


@dataclass(frozen=True)
class Money:
    """情境試算裡一處金額旁的幣別：金額原文（顯示用）、幣別字（中文或 ISO 代碼）、所在行。"""

    text: str  # 金額與幣別字的原文（例：10,100.00 美元）
    currency: str  # 金額旁的幣別字
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Occurrence:
    """審查標準規則涵蓋的一處出處；每處各自產生一筆結果。"""

    field: str  # 結果的欄位名稱（例：print_date_final）
    name: str  # 結果的項目名稱，由上手隨出處交出；各上手同一件事用同一個名稱（例：刊印日期（最終版）、最低申購金額）
    where: str  # 說明書上的位置，用在結果訊息（例：第四章商品開始受理申購日期）
    value: ParsedField


def occurrences(name: str, items: list[Occurrence]) -> ParsedField:
    """把上手的各出處包成一個標準欄位（清單本身一定存在，各出處自帶狀態與證據）。"""
    return ParsedField(name, FieldStatus.PRESENT, tuple(items))


# 名稱 → 值的形狀。不適用（NOT_APPLICABLE，須由說明書明確判定或範本沒有 `absent`）時共用規則不核對：
# 與參考條件表比對的欄位（含各標的 KO 價）結果為不適用並附說明，出處清單與 `issuer_name_ch1` 不產生結果；
# 其他欄位交出不適用時相關規則仍轉人工覆核。
STANDARD_FIELDS: dict[str, str] = {
    "product_code": "str：商品代號（12 位數字）",
    "isin": "str：ISIN",
    "currency_zh": "str：中文幣別（例：美元），由審查標準對照 ISO 代碼",
    "currency_others": "tuple[Occurrence, ...]：承作幣別的其他出處，須等於參考條件表承作幣別：面額、最低金額、情境假設等"
    "單一金額旁的幣別字（值為 str：中文幣別或 ISO 代碼，由規則依審查標準對照）、"
    "情境試算每個情境一處（值為 tuple[Money, ...]：該情境各金額旁的幣別）；商品幣別不必等於標的幣別，承作幣別只影響"
    "面額、配息、本金，股價（執行價等）與實物交割算式不列入；範本沒有時交出 absent",
    "underlyings": "list[str]：標的彭博代號，依說明書順序",
    "underlying_prices": "tuple[PriceRow, ...]：各標的價格列（代號、進場／執行／KO／下限價、證據、標的名稱、價格旁的幣別字）",
    "strike_pct": "Decimal：執行價為最初價格的 N%，保留說明書顯示位數",
    "ko_pct": "Decimal：KO 價為最初價格的 N%，保留說明書顯示位數；說明書沒有 KO 價（Non-Call = 天期）時為 100",
    "ki_pct": "Decimal：下限價為最初價格的 N%，保留說明書顯示位數；無 KI 時不讀",
    "coupon_pa_pct": "Decimal：年利率 %，保留說明書顯示位數",
    "tenor_months": "int：天期（月）",
    "trade_date": "date：交易日",
    "issue_date": "date：發行日",
    "final_valuation_date": "date：最終比價日",
    "maturity_date": "date：到期日",
    "denomination": "int 或 Decimal：每單位面額",
    "ko_observation": "str：D 期間每日觀察／P 每期定日觀察",
    "ko_memory": "bool：是否記憶式",
    "ki_type": "str：none 無 KI／AM 到期觀察／D 每日觀察／P 每期觀察（每個配息週期終止日）／M 每月觀察（BARC Monthly KI，"
    "尚無樣本）；none 可用 NOT_APPLICABLE 狀態交出",
    "autocall_schedule": "AutocallSchedule：提前出場排程（第一個可提前出場期、期數、各期比價日），由 parsers/schedule.py 的"
    "共用推導從 `schedule_table` 推得",
    "schedule_table": "DateTable：日期表（每期一列：期別、期始日、期末日、觀察日、配息日；表的位置；期始日的定義），"
    "由 rules/schedule.py 做結構檢查",
    "initial_pricing": "str：期初定價（參考條件表格式 [values.initial_pricing] 的對照值 open／close／vwap）；說明書寫明時才交出"
    "（Citi 標的表表頭「期初評價日之收盤價格／VWAP」），範本沒有交 absent，共用規則只檢查表上是允許值",
    "min_amounts": "tuple[Occurrence, ...]：須等於參考條件表單位面額 × 倍數的各最低金額出處（最低交易／申購／加購／贖回金額）；"
    "倍數依審查標準 min_amount_multiples.<上手>.<出處欄位>，沒設定為 1",
    "subscription_dates": "tuple[Occurrence, ...]：須等於交易日的受理申購日出處（開始、結束）",
    "print_dates": "tuple[Occurrence, ...]：須在交易日當天至允許天數內的刊印日期出處",
    # 審查標準規則（docs/rules/review-standard.md）
    "name_zh": "str：商品中文名稱",
    "name_en": "str：商品英文名稱",
    "approval_date": "date：受託或銷售機構審查通過之日期",
    "chairman": "str：受託或銷售機構負責人姓名，保留原字碼（不做異體字轉換）",
    "issue_price_pct": "Decimal：發行價格為商品面額之 N%",
    "issue_price_others": "tuple[Occurrence, ...]：發行價格的其他出處（MS 第四章申購價金）；範本沒有時交出 absent",
    "issuer_name_cover": "str：封面「發行機構」中英文法人全名；範本沒有封面發行機構欄時交出 absent（BBVA）",
    "issuer_name_ch2": "str：第二章「發行機構」事業名稱",
    "issuer_name_ch1": "str：第一章只寫中文的發行機構名稱（MS 第一章第 3 項）；範本沒有這處時交出 absent",
    "distributor_name_cover": "str：封面受託或銷售機構名稱",
    "distributor_phone_cover": "str：封面受託或銷售機構電話",
    "distributor_address_cover": "str：封面受託或銷售機構地址",
    "distributor_name_ch2": "str：第二章受託或銷售機構事業名稱",
    "distributor_address_ch2": "str：第二章受託或銷售機構營業所在地",
    # 審查標準有該上手分節才核對的固定值（BNP）；沒有分節的上手不會被讀，可不交出（issuer-onboarding §6.2a）
    "product_type": "str：封面「商品種類」（審查標準 product_type.<上手> 有固定值時核對；MS 依標的數另由上手規則核對）",
    "guarantor_name_cover": "str：封面「保證機構」名稱（審查標準 guarantor_name.<上手> 有 cover 時核對）",
    "guarantor_name_ch1": "str：第一章保證機構名稱（審查標準 guarantor_name.<上手> 有 ch1 時核對）",
    "guarantor_name_ch2": "str：第二章「保證機構」事業名稱（審查標準 guarantor_name.<上手> 有 ch2 時核對）",
    "agent_name_cover": "str：封面「總代理人」名稱（審查標準 agent_name.<上手> 有 cover 時核對；SG）",
    "agent_name_ch2": "str：第二章「總代理人」事業名稱（審查標準 agent_name.<上手> 有 ch2 時核對；SG）",
    "minimum_issue_size": "str：第四章發行不成立門檻的金額寫法（審查標準 minimum_issue_size.<上手> 有固定值時核對）",
    "distributor_phone_ch4": "str：第四章受託機構聯絡電話（BNP 第四章第 16 項）；範本沒有這處時交出 absent",
    "distributor_names_others": "tuple[Occurrence, ...]：受託機構名稱的其他出處（值為 str，例：BBVA 第 1 頁 13. 後的明細 (i)；"
    "Issue #305；Natixis 封面與第三章警語、第四章 10.、14.，Issue #318）；範本沒有時交出 absent",
    "distributor_addresses_others": "tuple[Occurrence, ...]：受託機構地址的其他出處（BBVA 第 1 頁明細 (iii)）；範本沒有時交出 absent",
    "chairman_others": "tuple[Occurrence, ...]：受託機構負責人的其他出處（BBVA 第 1 頁明細 (iv)），保留原字碼；範本沒有時交出 absent",
}
# 受託機構名稱、地址、負責人的其他出處（Issue #305）：BBVA 範本有、Natixis 有名稱（Issue #318），其他上手一起交出 absent（`absent_all`）
DISTRIBUTOR_OTHERS = ("distributor_names_others", "distributor_addresses_others", "chairman_others")

# 保證機構名稱的出處（審查標準 guarantor_name.<上手> 的鍵）→ 標準欄位、說明的開頭、項目名稱；設定檔與審查標準規則共用這一份
GUARANTOR_PLACES = {
    "cover": ("guarantor_name_cover", "封面「保證機構」名稱", "封面保證機構名稱"),
    "ch1": ("guarantor_name_ch1", "第一章保證機構名稱", "第一章保證機構名稱"),
    "ch2": ("guarantor_name_ch2", "第二章「保證機構」事業名稱", "第二章保證機構名稱"),
}
# 總代理人名稱的出處（審查標準 agent_name.<上手> 的鍵，SG）→ 標準欄位、說明的開頭、項目名稱
AGENT_PLACES = {
    "cover": ("agent_name_cover", "封面「總代理人」名稱", "封面總代理人名稱"),
    "ch2": ("agent_name_ch2", "第二章「總代理人」事業名稱", "第二章總代理人名稱"),
}

# 另有一組費用欄位：每個費用項目一個，名稱由 fee_field 產生
FEE_FIELD_SHAPE = "str：第四章費用表該費用項目的費率區間（例：0%~5%）；費用項目名稱同審查標準 [fees] 的鍵"
_FEE_PREFIX = "fee_"


def fee_field(label: str) -> str:
    """費用項目（例：申購費用）的標準欄位名稱。"""
    return _FEE_PREFIX + label


def is_standard(name: str) -> bool:
    """是否為標準欄位：STANDARD_FIELDS 的名稱，或某個費用項目的費用欄位。"""
    return name in STANDARD_FIELDS or (name.startswith(_FEE_PREFIX) and len(name) > len(_FEE_PREFIX))
