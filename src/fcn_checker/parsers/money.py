"""金額旁的幣別字：三家 parser 把「面額、最低金額、情境試算金額」旁邊寫的幣別交給共用規則（Issue #170）。

parser 只認得這裡列的幣別寫法（中文幣別或 3 碼 ISO 代碼），不知道哪一個才對：中文幣別對 ISO 代碼的對照在審查標準
（`[currency]`），比對在 rules/reference.py `currency_others`。新幣別要兩邊都加：這裡讓 parser 讀得到，審查標準讓規則對得上。
寫法分兩種：BARC 金額在前（`10,000 美元`）、MS 與 HSBC 幣別在前（`美元10,000`）。

商品幣別不必等於標的幣別（FCN 高度客製化，例：連結日股、美元計價），所以實物交割算式裡的股價（含「股」的行）
一律不當成商品幣別的出處。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Money, Occurrence
from .layout import TextIndex

CURRENCY_WORDS = (
    "美元",
    "日幣",
    "日圓",
    "人民幣",
    "境外人民幣",
    "港幣",
    "澳幣",
    "歐元",
    "英鎊",
    "南非幣",
    "新台幣",
    "加幣",
    "紐幣",
    "瑞郎",
    "星幣",
    "新加坡幣",
)
NUMBER = r"\d[\d,]*(?:\.\d+)?"  # 至少一個數字（單獨一個逗號不是金額）
# 幣別字：長的中文寫法先比（人民幣不會被當成「民幣」），或前面不接英文字母的 3 碼 ISO 代碼（交易所名稱的尾巴不算）。
# 全文索引去掉了換行，幣別字後面可能直接接下一行的字，所以不看後面。
CURRENCY = "(?:" + "|".join(sorted(CURRENCY_WORDS, key=len, reverse=True)) + "|(?<![A-Za-z])[A-Z]{3})"
AMOUNT_THEN_CURRENCY = rf"({NUMBER})\s*({CURRENCY})"  # BARC：10,000 美元
CURRENCY_THEN_AMOUNT = rf"({CURRENCY})\s*({NUMBER})"  # MS、HSBC：美元10,000


def shares_line(text: str) -> bool:
    """含「股」的行是實物交割算式（股數、股價用標的幣別），裡面的金額不當成商品幣別的出處。"""
    return "股" in text


def amounts(
    ti: TextIndex,
    pattern: str,
    *,
    currency_group: int,
    skip: Callable[[str], bool] = shares_line,
    start: int = 0,
    end: int | None = None,
) -> tuple[Money, ...]:
    """`ti.text[start:end]` 內每一處金額旁的幣別字；所在行有 `skip` 成立的（預設：含「股」）不算。"""
    end = len(ti.text) if end is None else end
    out = []
    for m in ti.finditer(pattern):
        if m.start() < start or m.start() >= end:
            continue
        lines = ti.lines_for(m.start(), m.end())
        if any(skip(ln.text) for ln in lines):
            continue
        out.append(Money(m[0], m[currency_group], tuple(Evidence.of(ln) for ln in lines)))
    return tuple(out)


def scenario_occurrence(
    field: str, name: str, where: str, found: Sequence[Money], fallback: Sequence[Line]
) -> Occurrence:
    """一個情境的金額幣別出處：值是該情境裡每一處金額旁的幣別；一處金額都沒有時為缺漏（證據引情境開頭）。"""
    if not found:
        pf = ParsedField(field, FieldStatus.MISSING, evidence=[Evidence.of(x) for x in fallback], note="找不到金額")
    else:
        pf = ParsedField(field, FieldStatus.PRESENT, tuple(found), [e for money in found for e in money.evidence])
    return Occurrence(field, name, where, pf)
