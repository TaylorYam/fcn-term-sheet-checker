"""版面工具：章、條、子項定位、跨行文字索引與欄位擷取 `capture`（各上手兩種文件的 parser 共用）。只依錨點與座標，不用頁碼定位。"""

from __future__ import annotations

import bisect
import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from decimal import InvalidOperation

from ..schema import Line, ParsedField
from ..text import full_brackets, squash

DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
TICKER_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9./-]*")  # 換行後只剩代號的一行（交易所碼在下一行）
TICKER_EXCHANGE = re.compile(r"[A-Z]{2}")

CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
}


@dataclass(frozen=True)
class LayoutSpec:
    """各上手範本的章、條、子項格式；由各上手 parser 提供。

    chapter_pattern 以 `{zh}`（章序中文數字）與 `{name}`（章名）填入後，須與整行文字完全相符。
    """

    chapter_pattern: str
    article_re: re.Pattern[str]
    article_max_x: float
    subitem_re: re.Pattern[str]
    subitem_max_x: float
    chapter_names: dict[int, tuple[str, str]] = field(default_factory=lambda: dict(CHAPTER_NAMES))


def parse_date(text: str) -> dt.date | None:
    """`YYYY 年M 月D 日` → date；不合法的日期回傳 None。不猜日月順序。"""
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return dt.date(*map(int, m.groups()))
    except ValueError:
        return None


def join_text(lines: Sequence[Line]) -> str:
    """多行合併：英數字之間補一個空白，其餘直接相接。"""
    out = ""
    for ln in lines:
        t = ln.text
        if out and re.search(r"[A-Za-z0-9.,)]$", out) and re.match(r"[A-Za-z0-9(]", t):
            out += " "
        out += t
    return out


def join_wrapped_tickers(lines: Sequence[Line]) -> list[Line]:
    """標的表彭博代碼欄窄，4 碼以上的代號會換成兩行（`AVGO` / `UW`）；接回一行 `AVGO UW` 才認得出標的列。"""
    out: list[Line] = []
    for ln in lines:
        prev = out[-1] if out else None
        if prev and TICKER_EXCHANGE.fullmatch(ln.text.strip()) and TICKER_SYMBOL.fullmatch(prev.text.strip()):
            out[-1] = replace(
                prev,
                x0=min(prev.x0, ln.x0),
                y0=min(prev.y0, ln.y0),
                x1=max(prev.x1, ln.x1),
                y1=max(prev.y1, ln.y1),
                text=f"{prev.text.strip()} {ln.text.strip()}",
            )
        else:
            out.append(ln)
    return out


@dataclass(frozen=True)
class Span:
    """lines[start:end] 範圍；title 為標題文字（條、子項）。"""

    start: int
    end: int
    title: str = ""


class TextIndex:
    """把一段文字行去空白後串接，並可由字元位置找回原始行（作為證據）。

    `unify_brackets`：半形括號換成全形（一對一換字，位置不變），句型用全形括號寫一次就好（MS）。
    `separate_digits`：上一個非空行結尾與下一行開頭都是數字時在中間插入 `|`，避免相鄰的數字相黏（表格一格一行、算式換行）。
    """

    def __init__(self, lines: Sequence[Line], *, unify_brackets: bool = False, separate_digits: bool = False):
        self.lines = list(lines)
        parts: list[str] = []
        self._starts: list[int] = []
        pos, last = 0, ""
        for ln in self.lines:
            s = squash(ln.text)
            if separate_digits and s and last.isdigit() and s[0].isdigit():
                parts.append("|")
                pos += 1
            self._starts.append(pos)
            parts.append(s)
            pos += len(s)
            last = s[-1] if s else last  # 空行不打斷相鄰數字的判斷
        text = "".join(parts)
        self.text = full_brackets(text) if unify_brackets else text

    def lines_for(self, start: int, end: int) -> list[Line]:
        if not self.lines:
            return []
        i = max(0, bisect.bisect_right(self._starts, start) - 1)
        j = max(i, bisect.bisect_right(self._starts, max(start, end - 1)) - 1)
        return self.lines[i : j + 1]

    def line_at(self, pos: int) -> int:
        """`text[pos]` 所在行的索引（`lines` 的位置）。"""
        return max(0, bisect.bisect_right(self._starts, pos) - 1)

    def line_start(self, i: int) -> int:
        """第 i 行在 `text` 的起點。"""
        return self._starts[i]

    def finditer(self, pattern: str | re.Pattern[str]):
        return re.finditer(pattern, self.text)


def capture(
    name: str, ti: TextIndex, pattern: str, convert: Callable = lambda x: x, *, whole_match: bool = False
) -> ParsedField:
    """全文（已去空白）中 `pattern` 第 1 組的每一處；不同值 → 歧義，沒有 → 缺漏，轉不成值 → 不合法。

    證據預設只引值（第 1 組）所在的行；`whole_match` 連欄位標籤所在的行一起引（說明書 parser 的寫法）。
    """
    hits = []
    for m in ti.finditer(pattern):
        lns = ti.lines_for(m.start(), m.end()) if whole_match else ti.lines_for(m.start(1), m.end(1))
        try:
            value = convert(m[1])
        except (ValueError, TypeError, InvalidOperation):
            return ParsedField.invalid(name, lns, f"「{m[1]}」無法辨識")
        if value is None:
            return ParsedField.invalid(name, lns, f"「{m[1]}」無法辨識")
        hits.append((value, lns))
    return ParsedField.from_hits(name, hits, missing_note="找不到欄位標籤或已知寫法")


class Document:
    """說明書的版面結構：章 → 條 → 子項；格式由 LayoutSpec 決定。"""

    def __init__(self, lines: Sequence[Line], spec: LayoutSpec):
        self.lines = list(lines)
        self.spec = spec
        self.chapters = self._find_chapters()

    # ---- 章 ----
    def _find_chapters(self) -> dict[int, Span]:
        starts: dict[int, list[int]] = {}
        for i, ln in enumerate(self.lines):
            for n, (zh, name) in self.spec.chapter_names.items():
                if re.fullmatch(self.spec.chapter_pattern.format(zh=zh, name=name), ln.text):
                    starts.setdefault(n, []).append(i)
        found: dict[int, Span] = {}
        ordered = sorted((idx[0], n) for n, idx in starts.items() if len(idx) == 1)
        for k, (i, n) in enumerate(ordered):
            end = ordered[k + 1][0] if k + 1 < len(ordered) else len(self.lines)
            found[n] = Span(i, end, self.lines[i].text)
        return found

    def chapter_lines(self, n: int) -> list[Line]:
        sp = self.chapters.get(n)
        return self.lines[sp.start : sp.end] if sp else []

    def before_chapter1(self) -> list[Line]:
        sp = self.chapters.get(1)
        return self.lines[: sp.start] if sp else list(self.lines)

    # ---- 條 ----
    def articles(self, chapter: int) -> dict[int, Span]:
        sp = self.chapters.get(chapter)
        if not sp:
            return {}
        marks: list[tuple[int, int]] = []
        expect = 1
        for i in range(sp.start + 1, sp.end):
            ln = self.lines[i]
            m = self.spec.article_re.match(ln.text)
            if m and ln.x0 < self.spec.article_max_x and int(m.group(1)) == expect:
                marks.append((expect, i))
                expect += 1
        return self._article_spans(marks, sp.end)

    def _article_spans(self, marks: list[tuple[int, int]], end: int) -> dict[int, Span]:
        """(條號, 標號所在行) → 條號 → Span（到下一條或 `end` 為止）。"""
        out: dict[int, Span] = {}
        for k, (n, i) in enumerate(marks):
            stop = marks[k + 1][1] if k + 1 < len(marks) else end
            out[n] = Span(i, stop, self._title_after(i, stop))
        return out

    def _title_after(self, i: int, end: int) -> str:
        """同一列、位於標號右側的第一行文字。"""
        mark = self.lines[i]
        for j in range(i + 1, min(end, i + 4)):
            ln = self.lines[j]
            if ln.page == mark.page and abs(ln.y0 - mark.y0) < 3 and ln.x0 > mark.x0:
                return ln.text
        return ""

    def span_lines(self, sp: Span | None) -> list[Line]:
        return self.lines[sp.start : sp.end] if sp else []

    # ---- 子項 (n) ----
    def subitems(self, parent: Span | None) -> dict[int, Span]:
        if parent is None:
            return {}
        marks: list[tuple[int, int, str]] = []
        expect = 1
        for i in range(parent.start + 1, parent.end):
            ln = self.lines[i]
            m = self.spec.subitem_re.match(ln.text)
            if m and ln.x0 < self.spec.subitem_max_x and int(m.group(1)) == expect:
                title = m.group(2).strip()
                marks.append((expect, i, title))
                expect += 1
        out: dict[int, Span] = {}
        for k, (n, i, title) in enumerate(marks):
            end = marks[k + 1][1] if k + 1 < len(marks) else parent.end
            out[n] = Span(i, end, title or self._title_after(i, end))
        return out
