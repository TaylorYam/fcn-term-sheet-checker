"""核對結果檔：一批說明書核對後唯一的 Excel 產出（Issue #72）。

- 「回填後」：原參考條件表 `樣本清單` 的版面（表頭與表頭以上各列、欄寬、列高、儲存格與日期格式），
  資料列只留下整份通過且已回填的列，順序照原表；可以直接匯入資料庫。原檔其他工作表不帶入。
- 「錯誤清單」：每份沒通過的說明書一列，依 PDF 輸入順序：TDCC Code、PDF 檔名、錯訊（多條以換行分隔）。

檔名 `<參考條件表檔名>_核對結果_<YYYYMMDD-HHMMSS>.xlsx`，寫到指定資料夾，不覆蓋既有檔案。

「回填後」是刪掉不要的列做出來的。合併儲存格、格式化條件、資料驗證的範圍與公式不會跟著位移，
`樣本清單` 有這些設定時不產生核對結果檔並寫出原因，避免默默產出錯位的表。篩選範圍跟著資料列數縮小。
"""

from __future__ import annotations

import datetime as dt
import io
import re
from collections.abc import Iterable, Sequence
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .config import ReferenceFormat
from .ingestion import IngestionError, write_new

FILLED_SHEET = "回填後"
ERROR_SHEET = "錯誤清單"
ERROR_COLUMNS = (("TDCC Code", 16), ("PDF 檔名", 40), ("錯訊", 100))  # 表頭與欄寬


@dataclass(frozen=True)
class ErrorRow:
    """錯誤清單的一列。"""

    tdcc_code: str | None
    pdf_name: str
    message: str

    @classmethod
    def of(cls, term_sheet: Path, product_code: str | None, messages: Sequence[str]) -> ErrorRow:
        """一份說明書的錯誤清單列。TDCC Code 取說明書封面商品代號；取不到時用檔名前 12 碼（12 位數字才算），否則留白。
        錯訊多條以換行分隔。"""
        head = term_sheet.name[:12]
        code = product_code or (head if re.fullmatch(r"[0-9]{12}", head) else None)
        return cls(code, term_sheet.name, "\n".join(messages))


def output_path(out_dir: Path, reference_sheet: Path, now: dt.datetime) -> Path:
    return out_dir / f"{reference_sheet.stem}_核對結果_{now:%Y%m%d-%H%M%S}.xlsx"


def check_layout(wb: Workbook, rfmt: ReferenceFormat) -> None:
    """版面無法安全刪列時丟出 IngestionError。要在回填前檢查：資料列的合併儲存格連回填都寫不進去。"""
    unsupported = _unsupported_layout(wb[rfmt.sheet], rfmt.first_data_row)
    if unsupported:
        raise IngestionError(
            "result_layout_unsupported",
            f"參考條件表「{rfmt.sheet}」有{'、'.join(unsupported)}，刪列後無法保證版面正確，未產生核對結果檔",
        )


def save(wb: Workbook, rfmt: ReferenceFormat, keep: Iterable[int], errors: Sequence[ErrorRow], out: Path) -> Path:
    """寫出核對結果檔並回傳路徑；檔案已存在（不覆蓋）或無法寫入時丟出 IngestionError。

    `wb` 是已通過 `check_layout`、已回填的參考條件表（會被改寫）；`keep` 是要留在「回填後」的資料列號。
    """
    write_new(out, _build(wb, rfmt, keep, errors), "核對結果檔")
    return out


def _build(wb: Workbook, rfmt: ReferenceFormat, keep: Iterable[int], errors: Sequence[ErrorRow]) -> bytes:
    for name in wb.sheetnames:
        if name != rfmt.sheet:
            del wb[name]
    ws = wb[rfmt.sheet]
    ws.title = FILLED_SHEET
    ws.sheet_view.tabSelected = True
    wb.active = ws
    _keep_rows(ws, rfmt.first_data_row, sorted(set(keep)))
    _error_sheet(wb.create_sheet(ERROR_SHEET), errors)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _unsupported_layout(ws: Worksheet, first: int) -> list[str]:
    """刪列時不會跟著位移的設定。"""
    found = []
    if any(r.max_row >= first for r in ws.merged_cells.ranges):
        found.append("資料列的合併儲存格")
    if ws.conditional_formatting:
        found.append("格式化條件")
    if ws.data_validations.dataValidation:
        found.append("資料驗證")
    if any(c.data_type == "f" for row in ws.iter_rows() for c in row):
        found.append("公式")
    return found


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
    if ws.auto_filter.ref:
        min_col, min_row, max_col, max_row = range_boundaries(ws.auto_filter.ref)
        if max_row >= first:
            last = max(first + len(keep) - 1, min_row)
            ws.auto_filter.ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{last}"


def _error_sheet(ws: Worksheet, errors: Sequence[ErrorRow]) -> None:
    ws.append([header for header, _ in ERROR_COLUMNS])
    for e in errors:
        ws.append((e.tdcc_code, e.pdf_name, e.message))
    for k, (_, width) in enumerate(ERROR_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(k)].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
