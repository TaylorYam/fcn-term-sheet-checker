"""交易所假日表與一籃子預定交易日（Issue #208）。

說明書的自動提前出場觀察期是期始日到期末日之間的「一籃子預定交易日」：所有標的交易所都開市的日子。
所以期始日 = 前一期期末日（第 1 期為發行日）後的下一個一籃子預定交易日，跳過週末，也跳過任一標的交易所的休市日。

標的的市場由彭博代號最後的交易所碼決定（`config/exchange_holidays.toml` 的 `bloomberg_codes`）。
有標的的交易所不在假日表、或要推算的日期不在假日表涵蓋範圍時，結果帶著「不確定」的原因：
規則在期始日剛好等於推算結果時照樣通過，不一致時轉人工覆核，不判不一致。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    """一個市場（例：美國）：彭博交易所碼、全天休市日與假日表涵蓋範圍。"""

    code: str
    name: str
    bloomberg_codes: frozenset[str]
    covered_from: dt.date
    covered_through: dt.date
    holidays: frozenset[dt.date]

    def covers(self, first: dt.date, last: dt.date) -> bool:
        return self.covered_from <= first and last <= self.covered_through


@dataclass(frozen=True)
class NextTradingDay:
    """推算結果：下一個一籃子預定交易日，以及無法確定的原因（空字串表示確定）。"""

    day: dt.date
    uncertain: str = ""


@dataclass(frozen=True)
class TradingCalendar:
    """交易所假日表（`config/exchange_holidays.toml`）。"""

    version: int
    markets: tuple[Market, ...]

    def market_of(self, ticker: str) -> Market | None:
        """彭博代號（例：GOOGL UW、ABC UN Equity）最後的交易所碼對到的市場；對不到為 None。"""
        parts = [p for p in ticker.upper().split() if p != "EQUITY"]
        code = parts[-1] if len(parts) >= 2 else None
        return next((m for m in self.markets if code in m.bloomberg_codes), None)

    def next_trading_day(self, after: dt.date, tickers: Sequence[str] | None) -> NextTradingDay:
        """`after` 之後的下一個一籃子預定交易日。讀不到標的、標的交易所不在假日表或超出涵蓋範圍時另附原因。"""
        markets: list[Market] = []
        unknown: list[str] = []
        for t in tickers or ():
            m = self.market_of(t)
            if m is None:
                unknown.append(t)
            elif m not in markets:
                markets.append(m)
        day = after + dt.timedelta(days=1)
        while day.weekday() >= 5 or any(day in m.holidays for m in markets):
            day += dt.timedelta(days=1)
        first = after + dt.timedelta(days=1)
        reasons = []
        if not tickers:
            reasons.append("讀不到標的，無法確定標的交易所的假日")
        if unknown:
            reasons.append(f"標的 {'、'.join(unknown)} 的交易所不在交易所假日表")
        reasons.extend(
            f"{first}～{day} 不在{m.name}假日表的涵蓋範圍（{m.covered_from}～{m.covered_through}）"
            for m in markets
            if not m.covers(first, day)
        )
        return NextTradingDay(day, "；".join(reasons))
