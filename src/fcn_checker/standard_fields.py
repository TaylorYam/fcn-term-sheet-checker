"""說明書標準欄位：上手 adapter 與共用規則之間的 seam（CONTEXT.md「標準欄位」）。

各上手 parser 以 `ts.f(name)` 交出下列欄位（`ParsedField`，含狀態與證據）；各上手共用的規則
（參考條件表欄位、審查標準、Non-Call／ISIN／發行日／比價日與回填）只讀這些欄位與全文索引，不碰上手專屬的擷取結果。
adapter 沒交出的欄位視為缺漏（`not_provided`），相關規則轉人工覆核並寫出欄位名稱。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .parsers.layout import TextIndex
from .schema import Evidence, FieldStatus, ParsedField


class TermSheet(Protocol):
    """上手 adapter 讀出的說明書：`f(name)` 交出標準欄位（及上手專屬欄位），`full_text` 為全文索引。

    `f` 不丟例外：沒有交出的欄位回傳 `not_provided(name)`。
    各上手的實作可另外帶該上手規則需要的專屬資料（例：BARC 價格表原文列、HSBC 情境文字索引）。
    """

    full_text: TextIndex

    def f(self, name: str) -> ParsedField: ...


def not_provided(name: str) -> ParsedField:
    """上手 adapter 沒有交出的欄位：缺漏，說明寫出欄位名稱。"""
    return ParsedField.missing(name, f"上手未提供標準欄位「{name}」")


@dataclass(frozen=True)
class PriceRow:
    """各標的價格列，順序同 `underlyings`。"""

    ticker: str | None  # 價格列上的彭博代號；表上沒有代號時為 None，以 `underlyings` 同順序的代號為準
    prices: dict[str, Decimal]  # initial／strike／ko／ki；說明書無 KI 時沒有 ki
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class AutocallSchedule:
    """說明書的提前出場排程（由各上手 parser 結果推得）。"""

    observation: str  # D 期間每日觀察／P 每期定日觀察
    first_callable: int  # 第一個可以提前出場的期別（Non-Call(月)），最小為 1
    periods: int  # 期數
    dates: dict[int, dt.date]  # 期別 → 比價日；至少包含第一個可提前出場期起的每一期


@dataclass(frozen=True)
class Occurrence:
    """審查標準規則涵蓋的一處出處；每處各自產生一筆結果。"""

    field: str  # 結果的欄位名稱（例：print_date_final）
    where: str  # 說明書上的位置，用在結果訊息（例：第四章商品開始受理申購日期）
    value: ParsedField


def occurrences(name: str, items: list[Occurrence]) -> ParsedField:
    """把上手的各出處包成一個標準欄位（清單本身一定存在，各出處自帶狀態與證據）。"""
    return ParsedField(name, FieldStatus.PRESENT, tuple(items))


FEE_PREFIX = "fee_"  # 費用表各項目：fee_<費用項目>，費用項目依審查標準 [fees]

# 名稱 → 值的形狀
STANDARD_FIELDS: dict[str, str] = {
    "product_code": "str：商品代號（12 位數字）",
    "isin": "str：ISIN",
    "currency_zh": "str：中文幣別（例：美元），由審查標準對照 ISO 代碼",
    "underlyings": "list[str]：標的彭博代號，依說明書順序",
    "underlying_prices": "tuple[PriceRow, ...]：各標的價格列（代號、進場／執行／KO／下限價、證據）",
    "strike_pct": "Decimal：執行價為最初價格的 N%，保留說明書顯示位數",
    "ko_pct": "Decimal：KO 價為最初價格的 N%，保留說明書顯示位數",
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
    "ki_type": "str：none 無 KI／AM 到期觀察／D 每日觀察／M 每月觀察；none 可用 NOT_APPLICABLE 狀態交出",
    "autocall_schedule": "AutocallSchedule：提前出場排程（第一個可提前出場期、期數、各期比價日）",
    "min_amounts": "tuple[Occurrence, ...]：須等於參考條件表單位面額的各最低金額出處（最低交易／申購／加購／贖回金額）",
    "subscription_dates": "tuple[Occurrence, ...]：須等於交易日的受理申購日出處（開始、結束）",
    "print_dates": "tuple[Occurrence, ...]：須在交易日當天至允許天數內的刊印日期出處",
    # 審查標準規則（docs/rules/review-standard.md）
    "name_zh": "str：商品中文名稱",
    "name_en": "str：商品英文名稱",
    "approval_date": "date：受託或銷售機構審查通過之日期",
    "chairman": "str：受託或銷售機構負責人姓名，保留原字碼（不做異體字轉換）",
    "issue_price_pct": "Decimal：發行價格為商品面額之 N%",
    "issuer_name_cover": "str：封面「發行機構」中英文法人全名",
    "issuer_name_ch2": "str：第二章「發行機構」事業名稱",
    "distributor_name_cover": "str：封面受託或銷售機構名稱",
    "distributor_phone_cover": "str：封面受託或銷售機構電話",
    "distributor_address_cover": "str：封面受託或銷售機構地址",
    "distributor_name_ch2": "str：第二章受託或銷售機構事業名稱",
    "distributor_address_ch2": "str：第二章受託或銷售機構營業所在地",
    FEE_PREFIX + "<費用項目>": "str：第四章費用表該費用項目的費率區間（例：0%~5%）；費用項目名稱同審查標準 [fees] 的鍵",
}


def is_standard(name: str) -> bool:
    return name in STANDARD_FIELDS or (name.startswith(FEE_PREFIX) and len(name) > len(FEE_PREFIX))
