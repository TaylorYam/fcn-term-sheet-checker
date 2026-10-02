"""文件接收：hash、加密與損毀檢查。不解析金融欄位、不嘗試繞過密碼。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from .schema import CheckResult, CheckStatus


class IngestionError(Exception):
    """輸入檔無法讀取（不存在、損毀、加密）。reason_code 供報告使用。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def error_result(rule_id: str, field: str, e: IngestionError) -> CheckResult:
    """輸入檔問題轉成 ERROR 結果。"""
    return CheckResult(
        rule_id=rule_id, field=field, status=CheckStatus.ERROR, reason_code=e.reason_code, message=str(e)
    )


def file_meta(path: Path | None) -> dict[str, Any]:
    """核對紀錄 metadata 用的檔名、完整路徑與 sha256；檔案不存在時 sha256 為 None。"""
    meta: dict[str, Any] = {
        "file": path.name if path else None,
        "path": str(path.resolve()) if path else None,
        "sha256": None,
    }
    if path is not None and path.is_file():
        meta["sha256"] = sha256_of(path)
    return meta


def write_new(path: Path, data: bytes, what: str) -> None:
    """輸出檔一律新建（exclusive create）：已存在時不覆蓋（output_exists），資料夾或檔案無法寫入時
    output_unwritable；寫到一半的檔案會移除。`what` 是錯訊裡的檔案名稱，例如「核對結果檔」。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)  # 路徑上有同名檔案時 Windows 也丟 FileExistsError
    except OSError as e:
        raise IngestionError("output_unwritable", f"無法建立{what}的資料夾 {path.parent}：{e}") from e
    try:
        f = path.open("xb")
    except FileExistsError as e:
        raise IngestionError("output_exists", f"{what} {path.name} 已經存在，不覆蓋") from e
    except OSError as e:
        raise IngestionError("output_unwritable", f"無法寫入{what} {path}：{e}") from e
    try:
        with f:
            f.write(data)
    except OSError as e:
        path.unlink(missing_ok=True)
        raise IngestionError("output_unwritable", f"無法寫入{what} {path}：{e}") from e


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
