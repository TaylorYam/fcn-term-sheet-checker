"""文件接收：hash、加密與損毀檢查。不解析金融欄位、不嘗試繞過密碼。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import fitz  # PyMuPDF


class IngestionError(Exception):
    """輸入檔無法讀取（不存在、損毀、加密）。reason_code 供報告使用。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_pdf(path: Path) -> fitz.Document:
    if not path.is_file():
        raise IngestionError("pdf_not_found", f"找不到說明書檔案：{path.name}")
    try:
        doc = fitz.open(path, filetype="pdf")
    except Exception as e:  # PyMuPDF 對損毀檔會丟出多種例外
        raise IngestionError("pdf_unreadable", f"說明書 PDF 無法開啟（可能已損毀）：{e}") from e
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise IngestionError("pdf_encrypted", "說明書 PDF 已加密，請提供未加密版本")
    if doc.page_count == 0:
        doc.close()
        raise IngestionError("pdf_unreadable", "說明書 PDF 沒有任何頁面")
    return doc
