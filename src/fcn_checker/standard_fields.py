"""說明書標準欄位：上手 adapter 與共用規則之間的 seam（CONTEXT.md「標準欄位」）。

各上手 parser 以 `ts.f(name)` 交出下列欄位（`ParsedField`，含狀態與證據）；各上手共用的規則
（參考條件表欄位、審查標準、Non-Call／ISIN／發行日／比價日與回填）只讀這些欄位與全文索引，不碰上手專屬的擷取結果。
adapter 沒交出的欄位視為缺漏（`not_provided`），相關規則轉人工覆核並寫出欄位名稱。
範本本身沒有的欄位（例：MS 說明書沒有年利率）由 adapter 明確交出 `absent`（不適用），共用規則不核對、不報缺漏。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

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
    name: str | None = None  # 價格列上的標的名稱；只在讀不到標的代號時用來指出是哪一檔


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
    "underlyings": "list[str]：標的彭博代號，依說明書順序",
    "underlying_prices": "tuple[PriceRow, ...]：各標的價格列（代號、進場／執行／KO／下限價、證據、標的名稱）",
    "strike_pct": "Decimal：執行價為最初價格的 N%，保留說明書顯示位數",
    "ko_pct": "Decimal：KO 價為最初價格的 N%，保留說明書顯示位數；說明書沒有 KO 價（MS Non-Call = 天期）時不適用",
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
    "issue_price_others": "tuple[Occurrence, ...]：發行價格的其他出處（MS 第四章申購價金）；範本沒有時交出 absent",
    "issuer_name_cover": "str：封面「發行機構」中英文法人全名",
    "issuer_name_ch2": "str：第二章「發行機構」事業名稱",
    "issuer_name_ch1": "str：第一章只寫中文的發行機構名稱（MS 第一章第 3 項）；範本沒有這處時交出 absent",
    "distributor_name_cover": "str：封面受託或銷售機構名稱",
    "distributor_phone_cover": "str：封面受託或銷售機構電話",
    "distributor_address_cover": "str：封面受託或銷售機構地址",
    "distributor_name_ch2": "str：第二章受託或銷售機構事業名稱",
    "distributor_address_ch2": "str：第二章受託或銷售機構營業所在地",
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
