"""說明書標準欄位：上手 adapter 與共用規則之間的 seam（CONTEXT.md「標準欄位」）。

各上手 parser 以 `ts.f(name)` 交出下列欄位（`ParsedField`，含狀態與證據）；共用的參考條件表欄位規則
（rules/reference.py）只讀這些欄位，不碰上手專屬的擷取結果。adapter 沒交出的欄位視為缺漏，
相關規則轉人工覆核並寫出欄位名稱。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from .schema import Evidence


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
    "autocall_schedule": "AutocallSchedule：提前出場排程（目前由上手註冊項目的 autocall_schedule 交出）",
}
