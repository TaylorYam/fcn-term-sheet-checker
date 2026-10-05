"""文件接收：hash、加密與損毀檢查。不解析金融欄位、不嘗試繞過密碼。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from .schema import CheckResult, CheckStatus, Item


class IngestionError(Exception):
    """輸入檔無法讀取（不存在、損毀、加密）或輸出檔無法寫入。reason_code 記在結果與核對紀錄。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def error_result(rule_id: str, field: str, e: IngestionError) -> CheckResult:
    """輸入檔問題轉成 ERROR 結果；`field` 是哪個檔案的中文名稱（例：說明書），也是結果的項目名稱。"""
    return CheckResult(
        rule_id=rule_id,
        field=field,
        status=CheckStatus.ERROR,
        reason_code=e.reason_code,
        message=str(e),
        item=Item.note(field),
    )


def _sha256_or_none(path: Path) -> str | None:
    try:
        return sha256_of(path)
    except OSError:
        return None


@dataclass(frozen=True)
class SourceSnapshot:
    """一次核對的來源快照：參考條件表、各份說明書與設定檔的路徑和 sha256，各在讀取之前取一次
    （設定檔在載入核對設定前，參考條件表與說明書在預覽讀取前）。

    核對紀錄的 hash 取自這裡；之後要確認「來源還是不是同一份」時明確呼叫 `still_valid`（重新計算 hash）。
    讀不到的檔案 sha256 為 None。
    """

    files: tuple[tuple[Path, str | None], ...]

    @classmethod
    def take(cls, paths: Sequence[Path], *, taken: SourceSnapshot | None = None) -> SourceSnapshot:
        """取 paths 的 hash；taken 是更早已取過 hash 的檔案（核對設定載入前取的設定檔），原樣併入。"""
        files = tuple((Path(p), _sha256_or_none(Path(p))) for p in paths)
        return cls(files + (taken.files if taken is not None else ()))

    def sha256(self, path: Path) -> str | None:
        path = Path(path)
        for p, h in self.files:
            if p == path:
                return h
        raise KeyError(f"{path} 不在來源快照中")

    def meta(self, path: Path) -> dict[str, Any]:
        """核對紀錄 metadata 用的檔名、完整路徑與 sha256。"""
        path = Path(path)
        return {"file": path.name, "path": str(path.resolve()), "sha256": self.sha256(path)}

    def still_valid(self) -> bool:
        """每個檔案都與取快照時相同：內容一樣；取快照時就讀不到的，現在也還讀不到（那份說明書已記成執行錯誤）。"""
        return all(_sha256_or_none(p) == h for p, h in self.files)


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
