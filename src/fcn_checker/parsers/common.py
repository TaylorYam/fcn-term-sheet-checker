"""各上手說明書 parser 共用的小工具：數字、推得欄位、文件上的一組值（含原文行）、月配息率出處。

MS 與 BNP（之後的上手同樣）的 `read` 與情境分析解析都用這裡的版本，不各自複製一份（Issue #196）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, NamedTuple, TypeVar

from ..schema import Evidence, FieldStatus, Line, ParsedField
from .layout import TextIndex

NUM = r"([\d,]+(?:\.\d+)?)"  # 數字（可有千分位與小數）
SCENARIO_HEAD = re.compile(r"^情境([一二三四五六七八九十]+)[:：](.*)$")  # 情境標題「情境X：標題」（去空白後）
# 說明書沒有自動提前出場價（Non-Call = 天期）：機制仍在、只是不會觸發，KO 視為期初價格的 100%（參考條件表填 100／進場價）
NO_AUTOCALL_KO_PCT = Decimal(100)

T = TypeVar("T")


def dec(s: str) -> Decimal:
    """去掉千分位逗號的 Decimal。"""
    return Decimal(s.replace(",", ""))


def number(s: str) -> int | Decimal:
    """金額：整數值回傳 int，否則 Decimal。"""
    d = dec(s)
    return int(d) if d == d.to_integral_value() else d


def ko_at_initial(prices: dict) -> dict:
    """一列標的價格補上 KO 價 = 期初價格（說明書沒有 KO 價時，見 `NO_AUTOCALL_KO_PCT`）。"""
    out = dict(prices)
    if "ko" not in out and "initial" in out:
        out["ko"] = out["initial"]
    return out


def derived(name: str, src: ParsedField, value=None) -> ParsedField:
    """由另一個欄位推得（證據沿用來源）：來源有問題時沿用來源的狀態與說明。"""
    if not src.ok:
        return ParsedField(name, src.status, None, list(src.evidence), list(src.candidates), src.note)
    return ParsedField(name, FieldStatus.PRESENT, value, list(src.evidence))


class Amount(NamedTuple):
    """金額與旁邊的幣別字（例：累積總配息金額）。"""

    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Hit(Generic[T]):
    """文件上的一個值（或一組具名的值，`typing.NamedTuple`）與原文行。"""

    value: T
    lines: tuple[Line, ...]


def hits(ti: TextIndex, pattern: str, convert: Callable[[re.Match[str]], T]) -> list[Hit[T]]:
    """`pattern` 在 `ti` 的每一處，`convert(match)` 為值。"""
    return [Hit(convert(m), tuple(ti.lines_for(m.start(), m.end()))) for m in ti.finditer(pattern)]


def one(found: list[Hit[T]]) -> Hit[T] | None:
    """剛好一處時回傳那一處；沒有或多處為 None（由規則轉人工覆核）。"""
    return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class Mention:
    """月配息率或年化報酬率的一個出處；值讀不到時為 None。"""

    where: str
    value: Decimal | None
    evidence: tuple[Evidence, ...]

    @classmethod
    def of_field(cls, where: str, pf: ParsedField) -> Mention:
        return cls(where, pf.value if pf.ok else None, tuple(pf.evidence))

    @classmethod
    def of_hit(cls, where: str, value: Decimal, hit: Hit) -> Mention:
        return cls(where, value, tuple(Evidence.of(ln) for ln in hit.lines))

    @classmethod
    def missing(cls, where: str, lines) -> Mention:
        return cls(where, None, tuple(Evidence.of(ln) for ln in lines))

    @classmethod
    def of_optional_hit(cls, where: str, hit: Hit | None, pick: Callable, fallback) -> Mention:
        """`hit` 找不到時為讀不到（證據為 `fallback` 行）；找到時值為 `pick(hit.value)`。"""
        return cls.missing(where, fallback) if hit is None else cls.of_hit(where, pick(hit.value), hit)
