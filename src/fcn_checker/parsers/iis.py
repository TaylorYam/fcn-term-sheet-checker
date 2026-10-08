"""投資人須知（IIS）各上手共用的擷取工具；讀出結果的型別 `IisSheet` 在 investor_sheet.py（ADR 0007）。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation

from .. import standard_fields
from ..investor_sheet import IisSheet
from ..schema import Line, ParsedField
from ..standard_fields import Occurrence
from .layout import TextIndex, join_text

__all__ = ["DATE", "FEES", "RATE", "IisSheet", "capture", "integer", "number", "occurrences", "page_totals", "raw"]

DATE = r"(\d{4}年\d{1,2}月\d{1,2}日)"
RATE = r"(\d+(?:\.\d+)?%~\d+(?:\.\d+)?%)"
FEES = ("申購費用", "提前贖回費用", "分銷費用")  # 範本費用表的項目（審查標準 [fees] 的鍵）


def number(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def integer(text: str) -> int:
    d = number(text)
    if d != d.to_integral_value():
        raise ValueError(text)
    return int(d)


def capture(name: str, ti: TextIndex, pattern: str, convert: Callable = lambda x: x) -> ParsedField:
    """全文（已去空白）中 `pattern` 第 1 組的每一處；不同值 → 歧義，沒有 → 缺漏。"""
    hits = []
    for m in ti.finditer(pattern):
        lns = ti.lines_for(m.start(1), m.end(1))
        try:
            value = convert(m[1])
        except (ValueError, TypeError, InvalidOperation):
            return ParsedField.invalid(name, lns, f"「{m[1]}」無法辨識")
        if value is None:
            return ParsedField.invalid(name, lns, f"「{m[1]}」無法辨識")
        hits.append((value, lns))
    return ParsedField.from_hits(name, hits, missing_note="找不到欄位標籤或已知寫法")


def raw(ti: TextIndex, pattern: str) -> tuple[str, list[Line]] | None:
    """`pattern` 第一處涵蓋的原始行合併文字（保留英數字之間的空白，例：彭博代號）。"""
    m = re.search(pattern, ti.text)
    if not m:
        return None
    lns = ti.lines_for(m.start(), m.end())
    return join_text(lns), lns


def occurrences(name: str, ti: TextIndex, specs: Sequence[tuple[str, str, str, str]], convert=lambda x: x):
    """出處清單型欄位：每個出處（欄位、項目名稱、位置說明、pattern）各讀一個值，每處各自核對。"""
    items = [
        Occurrence(field, label, where, capture(field, ti, pattern, convert)) for field, label, where, pattern in specs
    ]
    return standard_fields.occurrences(name, items)


def page_totals(lines: Sequence[Line]) -> ParsedField:
    """頁首「第 N 頁，共 M 頁」的 M（每頁一個）。"""
    hits = [(int(m[2]), ln) for ln in lines if (m := re.fullmatch(r"-?\s*第\s*(\d+)\s*頁，共\s*(\d+)\s*頁", ln.text))]
    if not hits:
        return ParsedField.missing("page_totals", "找不到頁首「第 N 頁，共 M 頁」")
    return ParsedField.present("page_totals", tuple(m for m, _ in hits), [ln for _, ln in hits])
