"""核對結果檔：一批說明書核對後唯一的 Excel 產出（Issue #72）。

- 「回填後」：原參考條件表 `樣本清單` 的版面（表頭與表頭以上各列、欄寬、列高、儲存格與日期格式），
  資料列只留下整份通過且已回填的列，順序照原表；可以直接匯入資料庫。原檔其他工作表不帶入。
- 「錯誤清單」：每份沒通過的說明書一列，依 PDF 輸入順序：TDCC Code、PDF 檔名、錯訊（多條以換行分隔）。

檔名 `<參考條件表檔名>_核對結果_<YYYYMMDD-HHMMSS>.xlsx`，寫到指定資料夾，不覆蓋既有檔案。
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Iterable, Sequence
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from openpyxl.styles import Alignment
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .config import ReferenceFormat
from .ingestion import IngestionError

FILLED_SHEET = "回填後"
ERROR_SHEET = "錯誤清單"
ERROR_HEADERS = ("TDCC Code", "PDF 檔名", "錯訊")
ERROR_WIDTHS = {"A": 16, "B": 40, "C": 100}


@dataclass(frozen=True)
class ErrorRow:
    """錯誤清單的一列。"""

    tdcc_code: str | None
    pdf_name: str
    message: str


def output_path(out_dir: Path, reference_sheet: Path, now: dt.datetime) -> Path:
    return out_dir / f"{reference_sheet.stem}_核對結果_{now:%Y%m%d-%H%M%S}.xlsx"


def build(wb: Workbook, rfmt: ReferenceFormat, keep: Iterable[int], errors: Sequence[ErrorRow]) -> bytes:
    """`wb` 是已回填的參考條件表（會被改寫）；`keep` 是要留在「回填後」的資料列號。回傳 xlsx 內容。"""
    for name in wb.sheetnames:
        if name != rfmt.sheet:
            del wb[name]
    ws = wb[rfmt.sheet]
    ws.title = FILLED_SHEET
    _keep_rows(ws, rfmt.first_data_row, sorted(set(keep)))
    _error_sheet(wb.create_sheet(ERROR_SHEET), errors)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    """遞增的列號 → 連續區段（起始列, 列數）。"""
    out: list[tuple[int, int]] = []
    for r in rows:
        if out and out[-1][0] + out[-1][1] == r:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((r, 1))
    return out


def _keep_rows(ws: Worksheet, first: int, keep: list[int]) -> None:
    """第 `first` 列起只留下 `keep` 這些列並往上靠攏；儲存格格式與列高跟著列走。"""
    dims = [copy(ws.row_dimensions[r]) for r in keep]
    kept = set(keep)
    drop = [r for r in range(first, ws.max_row + 1) if r not in kept]
    for start, n in reversed(_runs(drop)):  # 由下往上刪，前面的列號不受影響
        ws.delete_rows(start, n)
    for r in [r for r in ws.row_dimensions if r >= first]:
        del ws.row_dimensions[r]
    for r, dim in enumerate(dims, start=first):
        dim.index = r
        ws.row_dimensions[r] = dim


def _error_sheet(ws: Worksheet, errors: Sequence[ErrorRow]) -> None:
    ws.append(ERROR_HEADERS)
    for e in errors:
        ws.append((e.tdcc_code, e.pdf_name, e.message))
    for col, width in ERROR_WIDTHS.items():
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")


def write(data: bytes, out: Path) -> None:
    """寫出核對結果檔；檔案已存在時不覆蓋，丟出 IngestionError。"""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("xb") as f:
            f.write(data)
    except OSError as e:
        raise IngestionError("output_exists", f"無法寫入核對結果檔 {out.name}：{e}") from e
