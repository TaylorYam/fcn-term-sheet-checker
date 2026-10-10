"""合成說明書與投資人須知用的 PDF 寫入工具（不分上手），以及三家共用的改字 `Edit`。

各上手的合成器（tests/synth.py、tests/ms_synth.py、tests/hsbc_synth.py）都只透過這裡的公開工具排版；
頁尾等版面細節由各合成器自行加上。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import fitz

FONT = "china-t"


def zh_date(d: dt.date) -> str:
    return f"{d.year} 年{d.month} 月{d.day} 日"


def _is_western(ch: str) -> bool:
    try:
        ch.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def _runs(text: str) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    for ch in text:
        w = _is_western(ch)
        if out and out[-1][0] == w:
            out[-1] = (w, out[-1][1] + ch)
        else:
            out.append((w, ch))
    return out


@dataclass(frozen=True)
class Edit:
    """合成文件的一處改字（製造錯誤用）：在段落代號落在 `where` 之內的文字單位裡，把 `old` 換成 `new`。

    段落代號是以「.」分層的路徑，第一層是文件：說明書 `ts`、投資人須知 `iis`（例：`ts.art17`、`iis.warn`）。
    `where` 以整層比對前綴：`ts.art1` 含 `ts.art1.x`，不含 `ts.art17`；空白表示兩份文件全文。
    各上手的段落代號見各合成器的模組說明。一行文字整個被換成空字串時，那一行不畫（不佔行距）。
    """

    old: str
    new: str
    where: str = ""

    def covers(self, path: str) -> bool:
        return not self.where or path == self.where or path.startswith(self.where + ".")


def apply_edits(edits: Sequence[Edit], path: str, text: str) -> str:
    """依序套用段落 `path` 之內的改字。"""
    for e in edits:
        if e.covers(path):
            text = text.replace(e.old, e.new)
    return text


class PdfWriter:
    """A4 由上而下逐行排版；超過頁底自動換頁。

    寫入的每個文字單位（`put`／`line`／`row` 一格／`para` 整段／`numbered` 標題）先套用 `edits` 中涵蓋
    目前段落（`kind`.`section`）的改字；合成器自行分行的文字先以 `edit` 整段改過，再在 `verbatim()` 內寫入。
    """

    TOP, BOTTOM = 80.0, 770.0

    def __init__(self, edits: Sequence[Edit] = (), kind: str = "ts") -> None:
        self.doc = fitz.open()
        self.page: fitz.Page
        self.y = 0.0
        self.edits, self.kind = tuple(edits), kind  # kind：文件層段落代號（ts／iis）
        self.section = ""  # 目前段落代號（不含文件層）
        self._verbatim = False
        self.new_page()

    def path(self, section: str | None = None) -> str:
        sec = self.section if section is None else section
        return f"{self.kind}.{sec}" if sec else self.kind

    def edit(self, text: str, section: str | None = None) -> str:
        """套用涵蓋段落 `section`（省略為目前段落）的改字；`verbatim()` 內不改。"""
        return text if self._verbatim else apply_edits(self.edits, self.path(section), text)

    @contextmanager
    def verbatim(self) -> Iterator[None]:
        """區塊內寫入的文字不再改字（已先以 `edit` 整段改過、再由合成器自行分行的文字）。"""
        before, self._verbatim = self._verbatim, True
        try:
            yield
        finally:
            self._verbatim = before

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=595, height=842)
        self.y = self.TOP

    def need(self, h: float) -> None:
        if self.y + h > self.BOTTOM:
            self.new_page()

    def put(self, x: float, y: float, text: str, size: float = 10, section: str | None = None) -> None:
        """西文字元（Latin-1）用 Helvetica、其餘用 CJK 字型，逐段相接排版，避免全形寬度造成溢出或重疊。

        `section` 指定這個文字單位的段落代號（例：表格某一格），省略為目前段落。
        """
        for western, run in _runs(self.edit(text, section)):
            if western:
                self.page.insert_text((x, y + size), run, fontname="helv", fontsize=size)
                x += fitz.get_text_length(run, fontname="helv", fontsize=size)
            else:
                self.page.insert_text((x, y + size), run, fontname=FONT, fontsize=size)
                x += size * len(run)

    def line(self, x: float, text: str, size: float = 10, gap: float = 13) -> None:
        text = self.edit(text)
        if not text:
            return
        self.need(gap)
        with self.verbatim():
            self.put(x, self.y, text, size)
        self.y += gap

    def row(
        self, cells: Sequence[tuple[float, str] | tuple[float, str, str]], gap: float = 20, size: float = 10
    ) -> None:
        """一列表格；格子可帶第三個元素作為該格的段落代號。"""
        self.need(gap)
        for x, t, *section in cells:
            self.put(x, self.y, t, size, section[0] if section else None)
        self.y += gap

    def para(self, x: float, text: str, width: int = 40, size: float = 10, gap: float = 13) -> None:
        text = self.edit(text)
        with self.verbatim():
            for k in range(0, len(text), width):
                self.line(x, text[k : k + width], size, gap)

    def numbered(self, n: str, x_num: float, x_body: float, title: str, gap: float = 20) -> None:
        self.need(gap)
        self.put(x_num, self.y, n)
        self.put(x_body, self.y, title)
        self.y += gap

    def space(self, h: float = 8) -> None:
        self.y += h

    def save(self, path: Path) -> Path:
        self.doc.save(path)
        self.doc.close()
        return path
