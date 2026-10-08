"""投資人須知（IIS）讀出結果與各上手共用的擷取工具（docs/rules/iis-check-rules.md，ADR 0007）。

投資人須知只摘錄部分條件，各上手範本有的項目不同：讀出結果以 `provides` 宣告這個範本有哪些欄位，
範本沒有的欄位不核對（不推定通過，也不報缺漏）；範本有但讀不到的欄位照常是缺漏，規則轉人工覆核。

欄位名稱：和說明書意義相同的沿用標準欄位（standard_fields.STANDARD_FIELDS，例：`currency_zh`、
`underlyings`、`issue_date`、`fee_<費用項目>`），共用規則可以直接讀；投資人須知才有的欄位見 `IIS_FIELDS`。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .. import standard_fields
from ..schema import Line, ParsedField
from ..standard_fields import Occurrence
from .layout import TextIndex, join_text

# 投資人須知才有的欄位：名稱 → 值的形狀
IIS_FIELDS: dict[str, str] = {
    "product_codes": "tuple[Occurrence, ...]：封面各處商品代號，須等於檔名前 12 碼",
    "page_totals": "tuple[int, ...]：每頁頁首「第 N 頁，共 M 頁」的 M",
    "underlying_names": "list[str]：標的中文名稱，依說明書順序",
    "min_subscription": "int：最低申購金額",
    "monthly_coupon_pct": "Decimal：每月配息率 %，保留顯示位數",
    "issuer_names": "tuple[Occurrence, ...]：各處發行機構名稱",
    "distributor_names": "tuple[Occurrence, ...]：各處受託或銷售機構名稱",
    "distributor_addresses": "tuple[Occurrence, ...]：各處受託或銷售機構地址",
    "distributor_phones": "tuple[Occurrence, ...]：各處受託或銷售機構電話",
}

DATE = r"(\d{4}年\d{1,2}月\d{1,2}日)"
RATE = r"(\d+(?:\.\d+)?%~\d+(?:\.\d+)?%)"
FEES = ("申購費用", "提前贖回費用", "分銷費用")  # 範本費用表的項目（審查標準 [fees] 的鍵）


@dataclass
class IisSheet:
    """一份投資人須知的讀出結果：`f(name)` 交出欄位，`full_text` 為全文索引（固定警語、風險等級、禁用語用）。"""

    fields: dict[str, ParsedField]
    full_text: TextIndex
    provided: frozenset[str]  # 這個範本有的欄位；不在其中的不核對
    issuer_name_zh_only: bool = False  # 範本的發行機構名稱只寫中文（HSBC）

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)

    def provides(self, name: str) -> bool:
        return name in self.provided


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
