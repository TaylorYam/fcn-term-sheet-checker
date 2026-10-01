"""版面工具：章、條、子項定位與跨行文字索引。只依錨點與座標，不用頁碼定位。"""

from __future__ import annotations

import bisect
import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..schema import Line

DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

CHAPTER_NAMES = {
    1: ("一", "商品基本資料"),
    2: ("二", "相關機構事業概況"),
    3: ("三", "商品風險揭露"),
    4: ("四", "一般交易事項"),
    5: ("五", "特別記載事項"),
}

_ARTICLE_RE = re.compile(r"^(\d{1,2})\.$")
_SUBITEM_RE = re.compile(r"^\((\d{1,2})\)\s*(.*)$")
_ARTICLE_MAX_X = 62.0
_SUBITEM_MAX_X = 100.0


def parse_date(text: str) -> dt.date | None:
    """`YYYY 年M 月D 日` → date；不合法的日期回傳 None。不猜日月順序。"""
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return dt.date(*map(int, m.groups()))
    except ValueError:
        return None


def squash(text: str) -> str:
    """移除所有空白（含換行）。"""
    return re.sub(r"\s+", "", text)


def join_text(lines: Sequence[Line]) -> str:
    """多行合併：英數字之間補一個空白，其餘直接相接。"""
    out = ""
    for ln in lines:
        t = ln.text
        if out and re.search(r"[A-Za-z0-9.,)]$", out) and re.match(r"[A-Za-z0-9(]", t):
            out += " "
        out += t
    return out


@dataclass(frozen=True)
class Span:
    """lines[start:end] 範圍；title 為標題文字（條、子項）。"""

    start: int
    end: int
    title: str = ""


class TextIndex:
    """把一段文字行去空白後串接，並可由字元位置找回原始行（作為證據）。"""

    def __init__(self, lines: Sequence[Line]):
        self.lines = list(lines)
        parts: list[str] = []
        self._starts: list[int] = []
        pos = 0
        for ln in self.lines:
            s = squash(ln.text)
            self._starts.append(pos)
            parts.append(s)
            pos += len(s)
        self.text = "".join(parts)

    def lines_for(self, start: int, end: int) -> list[Line]:
        if not self.lines:
            return []
        i = max(0, bisect.bisect_right(self._starts, start) - 1)
        j = max(i, bisect.bisect_right(self._starts, max(start, end - 1)) - 1)
        return self.lines[i : j + 1]

    def finditer(self, pattern: str | re.Pattern[str]):
        return re.finditer(pattern, self.text)


class Document:
    """BARC 說明書的版面結構：章 → 條 → 子項。"""

    def __init__(self, lines: Sequence[Line]):
        self.lines = list(lines)
        self.chapters = self._find_chapters()

    # ---- 章 ----
    def _find_chapters(self) -> dict[int, Span]:
        starts: dict[int, list[int]] = {}
        for i, ln in enumerate(self.lines):
            for n, (zh, name) in CHAPTER_NAMES.items():
                if re.fullmatch(rf"第{zh}章\s*{name}", ln.text):
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
            m = _ARTICLE_RE.match(ln.text)
            if m and ln.x0 < _ARTICLE_MAX_X and int(m.group(1)) == expect:
                marks.append((expect, i))
                expect += 1
        out: dict[int, Span] = {}
        for k, (n, i) in enumerate(marks):
            end = marks[k + 1][1] if k + 1 < len(marks) else sp.end
            out[n] = Span(i, end, self._title_after(i, end))
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
            m = _SUBITEM_RE.match(ln.text)
            if m and ln.x0 < _SUBITEM_MAX_X and int(m.group(1)) == expect:
                title = m.group(2).strip()
                marks.append((expect, i, title))
                expect += 1
        out: dict[int, Span] = {}
        for k, (n, i, title) in enumerate(marks):
            end = marks[k + 1][1] if k + 1 < len(marks) else parent.end
            out[n] = Span(i, end, title or self._title_after(i, end))
        return out
