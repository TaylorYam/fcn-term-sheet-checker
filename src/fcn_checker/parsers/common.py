"""各上手說明書 parser 共用的小工具：數字、推得欄位、文件上的一組值（含原文行）、月配息率出處、沒有 KO 價時的 KO 100%，
以及保留空白的原文合併文字 `RawText`、表格依座標分欄 `by_column`、同一句另一組的 `capture_group`（Nomura、GS）。

MS 與 BNP（之後的上手同樣）的 `read` 與情境分析解析都用這裡的版本，不各自複製一份（Issue #196）。
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Generic, NamedTuple, TypeVar

from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..text import squash
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


# ---------------------------------------------------------------- 原文合併文字與表格分欄（Nomura、GS）


class RawText:
    """多行原文合併（同 `layout.join_text`：英數字跨行補一個空白），並可由字元位置找回原始行（作為證據）。

    名稱、地址、電話等要保留空白的值在這裡讀；數字、日期與句型在去空白的 `TextIndex` 上讀。
    """

    def __init__(self, lines: Sequence[Line]):
        self.lines = list(lines)
        out, self._starts = "", []
        for ln in self.lines:
            t = ln.text.strip()
            if out and t and re.search(r"[A-Za-z0-9.,)]$", out) and re.match(r"[A-Za-z0-9(]", t):
                out += " "
            self._starts.append(len(out))
            out += t
        self.text = out

    def lines_for(self, start: int, end: int) -> list[Line]:
        if not self.lines:
            return []
        i = max(0, bisect.bisect_right(self._starts, start) - 1)
        j = max(i, bisect.bisect_right(self._starts, max(start, end - 1)) - 1)
        return self.lines[i : j + 1]

    def capture(self, name: str, pattern: str, convert: Callable = lambda x: x.strip(), group: int = 1) -> ParsedField:
        """`pattern` 第 `group` 組的每一處（證據為整個比對所在的行）；不同值 → 歧義，沒有 → 缺漏，轉不成值 → 不合法。"""
        found = []
        for m in re.finditer(pattern, self.text, re.S):
            lns = self.lines_for(m.start(), m.end())
            try:
                value = convert(m[group])
            except (ValueError, TypeError, InvalidOperation):
                value = None
            if value is None:
                return ParsedField.invalid(name, lns, f"「{m[group]}」無法辨識")
            found.append((value, lns))
        return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")


def by_column(lines: list[Line], gap: float = 20) -> list[Line]:
    """表格的行依 x 座標分欄（相鄰兩行 x 相差超過 `gap` 為不同欄），由左到右、每欄由上到下。

    一格拆成多行時，擷取順序可能逐欄也可能逐列；分欄後的順序固定（`執行價 (期初價格之X% )`、`ABC` / `UW Equity` 會接在一起）。"""
    columns: list[list[Line]] = []
    for ln in sorted(lines, key=lambda x: x.x0):
        if columns and ln.x0 - columns[-1][-1].x0 <= gap:
            columns[-1].append(ln)
        else:
            columns.append([ln])
    return [ln for col in columns for ln in sorted(col, key=lambda x: (x.page, x.y0))]


def split_at_columns(lines: list[Line], tol: float = 0.6) -> list[Line]:
    """一行橫跨好幾欄（PDF 把相鄰幾格擷取成同一行）時切回各欄：欄的左緣取這些行中至少兩行共有的 x0，
    行內某個字的左緣落在其他欄的左緣上就從那裡切開。沒有字座標（`Line.char_boxes`）的行不動。"""
    starts = sorted(ln.x0 for ln in lines)
    edges = [
        x
        for i, x in enumerate(starts)
        if (i and x - starts[i - 1] <= tol) or (i + 1 < len(starts) and starts[i + 1] - x <= tol)
    ]
    out: list[Line] = []
    for ln in lines:
        boxes = ln.char_boxes
        cuts = [
            i
            for i, (a, _) in enumerate(boxes)
            if i and not ln.text[i].isspace() and any(abs(a - e) <= tol for e in edges)
        ]
        if not cuts:
            out.append(ln)
            continue
        for a, b in zip([0, *cuts], [*cuts, len(boxes)], strict=True):
            keep = [i for i in range(a, b) if not ln.text[i].isspace()]
            if keep:
                i, j = keep[0], keep[-1] + 1
                out.append(replace(ln, x0=boxes[i][0], x1=boxes[j - 1][1], text=ln.text[i:j], chars=boxes[i:j]))
    return out


def column_text(lines: list[Line]) -> str:
    """`by_column` 順序接起來、去空白（表頭）。"""
    return "".join(squash(ln.text) for ln in by_column(lines))


def capture_group(lns: list[Line], pattern: str, group: int, name: str, convert: Callable = lambda x: x) -> ParsedField:
    """同一句的另一組（例：最低加價申購金額接在最低申購金額後面）。"""
    ti = TextIndex(lns)
    found = []
    for m in ti.finditer(pattern):
        try:
            found.append((convert(m[group]), ti.lines_for(m.start(), m.end())))
        except (ValueError, TypeError, InvalidOperation):
            return ParsedField.invalid(name, ti.lines_for(m.start(), m.end()), f"「{m[group]}」無法辨識")
    return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")
