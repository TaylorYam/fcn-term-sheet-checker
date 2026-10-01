"""文字擷取：逐頁輸出文字行、頁碼與 bbox；排除頁碼雜訊。不判斷核對結果。"""

from __future__ import annotations

import re

import fitz  # PyMuPDF

from .ingestion import IngestionError
from .schema import Line

_PAGE_OF = re.compile(r"^Page \d+ of \d+$")
_PAGE_NUM = re.compile(r"^\d{1,3}$")
_FOOTER_Y = 780.0  # 頁尾頁碼 y≈784–798


def _is_page_noise(text: str, x0: float, x1: float, y0: float, page: fitz.Page) -> bool:
    """頁尾頁碼：p1 右下「Page N of M」、其他頁底部置中的阿拉伯數字。表格內靠近頁底的數字不排除。"""
    if y0 < page.rect.height - (842 - _FOOTER_Y):
        return False
    if _PAGE_OF.match(text):
        return True
    return bool(_PAGE_NUM.match(text)) and abs((x0 + x1) / 2 - page.rect.width / 2) < 20


def extract_lines(doc: fitz.Document) -> list[Line]:
    out: list[Line] = []
    try:
        for pno, page in enumerate(doc, start=1):
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for ln in block["lines"]:
                    text = "".join(s["text"] for s in ln["spans"]).strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = ln["bbox"]
                    if _is_page_noise(text, x0, x1, y0, page):
                        continue
                    out.append(Line(pno, x0, y0, x1, y1, text))
    except Exception as e:
        raise IngestionError("pdf_unreadable", f"說明書文字擷取失敗：{e}") from e
    return out
