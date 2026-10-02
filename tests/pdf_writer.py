"""合成說明書用的 PDF 寫入工具（不分上手）。

各上手的說明書合成器（tests/synth.py、tests/hsbc_synth.py）都只透過這裡的公開工具排版；
頁尾等版面細節由各合成器自行加上。
"""

from __future__ import annotations

import datetime as dt
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


class PdfWriter:
    """A4 由上而下逐行排版；超過頁底自動換頁。"""

    TOP, BOTTOM = 80.0, 770.0

    def __init__(self) -> None:
        self.doc = fitz.open()
        self.page: fitz.Page
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=595, height=842)
        self.y = self.TOP

    def need(self, h: float) -> None:
        if self.y + h > self.BOTTOM:
            self.new_page()

    def put(self, x: float, y: float, text: str, size: float = 10) -> None:
        """西文字元（Latin-1）用 Helvetica、其餘用 CJK 字型，逐段相接排版，避免全形寬度造成溢出或重疊。"""
        for western, run in _runs(text):
            if western:
                self.page.insert_text((x, y + size), run, fontname="helv", fontsize=size)
                x += fitz.get_text_length(run, fontname="helv", fontsize=size)
            else:
                self.page.insert_text((x, y + size), run, fontname=FONT, fontsize=size)
                x += size * len(run)

    def line(self, x: float, text: str, size: float = 10, gap: float = 13) -> None:
        self.need(gap)
        self.put(x, self.y, text, size)
        self.y += gap

    def row(self, cells: list[tuple[float, str]], gap: float = 20, size: float = 10) -> None:
        self.need(gap)
        for x, t in cells:
            self.put(x, self.y, t, size)
        self.y += gap

    def para(self, x: float, text: str, width: int = 40, size: float = 10, gap: float = 13) -> None:
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
