"""核對結果檔：一批說明書核對後唯一的 Excel 產出（Issue #72）。

- 「回填後」：原參考條件表 `樣本清單` 刪掉不要的列，欄位再照參考條件表欄位順序重排（Issue #173）：各欄從第 1 列起的值、
  儲存格與日期格式、欄寬跟著欄走；表上缺少的欄在該欄應在的位置補一個只有表頭的空白欄；沒有表頭（或不在參考條件表欄位順序、
  重複）的欄接在最右邊，保持原相對順序。資料列只留下整份通過且已回填的列，順序照原表；可以直接匯入資料庫。
  原檔其他工作表不帶入。
- 「錯誤清單」：每份沒通過的說明書一列，依 PDF 輸入順序：TDCC Code、PDF 檔名、錯訊（多條以換行分隔）。

檔名 `<參考條件表檔名>_核對結果_<YYYYMMDD-HHMMSS>.xlsx`，寫到指定資料夾，不覆蓋既有檔案。

「回填後」是刪掉不要的列做出來的。合併儲存格、格式化條件、資料驗證的範圍與公式不會跟著位移，
`樣本清單` 有這些設定時不產生核對結果檔並寫出原因，避免默默產出錯位的表；要重排欄位時，合併儲存格（含表頭以上）、
篩選或排序條件也一樣。篩選範圍跟著資料列數縮小，補欄時跟著變寬。
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Iterable, Sequence
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from openpyxl.styles import Alignment
from openpyxl.styles.cell_style import StyleArray
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.dimensions import ColumnDimension
from openpyxl.worksheet.worksheet import Worksheet

from .config import ReferenceFormat
from .identification import Identification, name_code
from .ingestion import IngestionError, write_new
from .orders.reference import normalize_cell

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
    def of(cls, identification: Identification, messages: Sequence[str]) -> ErrorRow:
        """一份 PDF 的錯誤清單列（TDCC Code 見 `tdcc_code`）。錯訊多條以換行分隔。"""
        return cls(tdcc_code(identification), identification.pdf.name, "\n".join(messages))


def tdcc_code(identification: Identification) -> str | None:
    """錯誤清單（與回報包）的 TDCC Code：辨識結果的商品代號（說明書封面；投資人須知為檔名）；
    取不到時用檔名前 12 碼（12 位數字才算），否則為 None。"""
    return identification.product_code or name_code(identification.pdf)


def output_path(out_dir: Path, reference_sheet: Path, now: dt.datetime) -> Path:
    return out_dir / f"{reference_sheet.stem}_核對結果_{now:%Y%m%d-%H%M%S}.xlsx"


def check_layout(wb: Workbook, rfmt: ReferenceFormat) -> None:
    """版面無法安全刪列時丟出 IngestionError。要在回填前檢查：資料列的合併儲存格連回填都寫不進去。"""
    ws = wb[rfmt.sheet]
    unsupported = _unsupported_layout(ws, rfmt.first_data_row)
    if not _is_identity(_arrangement(ws, rfmt)):  # 欄位要照參考條件表欄位順序重排：這些設定不會跟著欄移動
        if ws.merged_cells.ranges:
            unsupported.append("合併儲存格（欄位要照參考條件表欄位順序重排）")
        if ws.auto_filter.filterColumn or ws.auto_filter.sortState:
            unsupported.append("篩選或排序條件（欄位要照參考條件表欄位順序重排）")
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
    _arrange_columns(ws, rfmt)
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


def _arrangement(ws: Worksheet, rfmt: ReferenceFormat) -> list[int | str]:
    """「回填後」由左到右每一欄的來源：原表欄號，或表上缺少、要補空白欄的欄名。

    參考條件表欄位順序的欄依序在前；沒有表頭、不在參考條件表欄位順序或重複出現的欄接在後面，保持原相對順序。
    只看到有儲存格的最右欄：Excel 常把欄格式一路設到 XFD，只有欄寬沒有儲存格的欄不搬。"""
    standard = set(rfmt.column_order)
    named: dict[str, int] = {}
    rest: list[int] = []
    for col in range(1, ws.max_column + 1):
        header = normalize_cell(ws.cell(row=rfmt.header_row, column=col).value)
        if header is not None and str(header) in standard and str(header) not in named:
            named[str(header)] = col
        else:
            rest.append(col)
    return [named.get(h, h) for h in rfmt.column_order] + rest


def _is_identity(order: list[int | str]) -> bool:
    return order == list(range(1, len(order) + 1))


def _dim_columns(ws: Worksheet, last: int) -> dict[int, ColumnDimension]:
    """欄號 → 欄設定（欄寬、隱藏等）；一筆設定涵蓋多欄時展開，只到第 `last` 欄。"""
    out = {}
    for dim in ws.column_dimensions.values():
        lo = dim.min or column_index_from_string(dim.index)
        for c in range(lo, min(dim.max or lo, last) + 1):
            out[c] = dim
    return out


def _arrange_columns(ws: Worksheet, rfmt: ReferenceFormat) -> None:
    """欄位照參考條件表欄位順序重排；已照參考條件表欄位順序時不動。合併儲存格已由 `check_layout` 擋下。"""
    order = _arrangement(ws, rfmt)
    if _is_identity(order):
        return
    dims = _dim_columns(ws, ws.max_column)
    rows = range(1, ws.max_row + 1)
    # 直接搬 _value／data_type／_style：經 `.value` 會重新判斷型別（例：「=」開頭的字串變公式），格式也只能整組複製
    snap = {
        src: [(c._value, c.data_type, copy(c._style), c.hyperlink, c.comment) for c in (ws.cell(r, src) for r in rows)]
        for src in order
        if isinstance(src, int)
    }
    for col, src in enumerate(order, start=1):
        for r in rows:
            cell = ws.cell(r, col)
            if isinstance(src, int):
                cell._value, cell.data_type, cell._style, hyperlink, comment = snap[src][r - 1]
            else:  # 表上缺少的欄：只有表頭
                cell._value, cell.data_type, cell._style, hyperlink, comment = None, "n", StyleArray(), None, None
            cell.hyperlink, cell.comment = hyperlink, comment
    present = [col for col, src in enumerate(order, start=1) if isinstance(src, int)]
    for col, src in enumerate(order, start=1):
        if isinstance(src, str):  # 表頭格式比照最近的原有欄的表頭格
            cell = ws.cell(rfmt.header_row, col)
            cell.value = src
            if present:
                near = min(present, key=lambda c: abs(c - col))
                cell._style = copy(ws.cell(rfmt.header_row, near)._style)
    ws.column_dimensions.clear()
    for col, src in enumerate(order, start=1):
        if isinstance(src, int) and src in dims:
            dim = copy(dims[src])
            dim.index = get_column_letter(col)
            dim.min = dim.max = col
            ws.column_dimensions[dim.index] = dim
    if ws.auto_filter.ref:
        min_col, min_row, max_col, max_row = range_boundaries(ws.auto_filter.ref)
        added = sum(isinstance(src, str) for src in order)
        ws.auto_filter.ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col + added)}{max_row}"


def _error_sheet(ws: Worksheet, errors: Sequence[ErrorRow]) -> None:
    ws.append([header for header, _ in ERROR_COLUMNS])
    for e in errors:
        ws.append((e.tdcc_code, e.pdf_name, e.message))
    for k, (_, width) in enumerate(ERROR_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(k)].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
