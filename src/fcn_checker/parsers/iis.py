"""投資人須知（IIS）各上手共用的擷取工具；讀出結果的型別 `IisSheet` 在 investor_sheet.py（ADR 0007）。

欄位擷取 `capture` 與說明書 parser 共用，定義在 layout.py（這裡只是轉出）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..investor_sheet import IisSheet
from ..schema import Line, ParsedField
from ..standard_fields import Occurrence
from .layout import TextIndex, capture, join_text

__all__ = [
    "DATE",
    "FEES",
    "RATE",
    "IisSheet",
    "capture",
    "in_order",
    "integer",
    "number",
    "occurrences",
    "PAGE_SLASH",
    "page_totals",
    "raw",
    "section",
]

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


def in_order(text: str, anchors: Sequence[str]) -> bool:
    """`anchors` 依序都出現在 `text`（範本辨識的段落標題）。"""
    pos = -1
    for a in anchors:
        pos = text.find(a, pos + 1)
        if pos < 0:
            return False
    return True


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


def warning_distributor_names(
    fields: dict[str, ParsedField],
    ti: TextIndex,
    anchors: tuple[str, str],
    specs: Sequence[tuple[str, str, str]],
) -> None:
    """警語裡寫出受託機構名稱的每一處（欄位、位置說明、pattern）加進 `distributor_names`（Issue #306，每處都算名稱出處）。

    只在警語段（`anchors` 之間）內找；找不到警語段時每處都是缺漏，由審查標準規則轉人工覆核。
    """
    warnings = TextIndex(section(ti, anchors) or [])
    fields["distributor_names"] = standard_fields.occurrences(
        "distributor_names",
        [
            *fields["distributor_names"].value,
            *(
                Occurrence(field, "受託機構名稱（警語）", where, capture(field, warnings, pattern))
                for field, where, pattern in specs
            ),
        ],
    )


def section(ti: TextIndex, anchors: tuple[str, str]) -> list[Line] | None:
    """兩個錨點之間（含錨點所在行）的原文行；找不到或開頭錨點重複時為 None（BNP、SG）。"""
    start, end = anchors
    if ti.text.count(start) != 1:
        return None
    a = ti.text.index(start)
    b = ti.text.find(end, a)
    return None if b < 0 else ti.lines_for(a, b + len(end))


PAGE_OF = r"-?\s*第\s*(\d+)\s*頁，共\s*(\d+)\s*頁"  # 「第 N 頁，共 M 頁」
PAGE_SLASH = r"(\d+)\s*/\s*(\d+)"  # 「N/M」（Nomura）


def page_totals(lines: Sequence[Line], pattern: str = PAGE_OF, shown: str = "第 N 頁，共 M 頁") -> ParsedField:
    """每頁頁碼（整行等於 `pattern`，第 2 組為總頁數；預設「第 N 頁，共 M 頁」）的 M（每頁一個）。"""
    hits = [(int(m[2]), ln) for ln in lines if (m := re.fullmatch(pattern, ln.text.strip()))]
    if not hits:
        return ParsedField.missing("page_totals", f"找不到頁碼「{shown}」")
    return ParsedField.present("page_totals", tuple(m for m, _ in hits), [ln for _, ln in hits])
