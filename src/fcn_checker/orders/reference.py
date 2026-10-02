"""參考條件表 adapter：多列表格，每列一檔商品（docs/order-formats/reference-sheet.md）。

依格式設定把各列轉成標準欄位，並記下每個欄位的儲存格位置，供回填使用。未知欄名回報，不自行推測。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter

from ..config import ReferenceFormat
from ..ingestion import IngestionError
from ..schema import OrderValue

SOURCE = "參考條件表"


@dataclass
class OrderRecord:
    """核對規則使用的一筆條件：參考條件表的一列，轉成標準欄位。"""

    product_code: OrderValue
    fields: dict[str, OrderValue]
    unknown_columns: list[OrderValue] = field(default_factory=list)  # value = 欄名
    missing_columns: list[str] = field(default_factory=list)  # 設定有、檔案沒有的 Excel 欄名
    duplicate_columns: list[OrderValue] = field(default_factory=list)  # 重複出現的欄名（value = 欄名）
    source: str = SOURCE  # 條件來源名稱，用在核對訊息


def normalize_cell(v: Any) -> Any:
    """Excel 值標準化：數字轉 Decimal 並四捨五入到 9 位清除浮點尾數；日期轉 date；空字串轉 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(round(v, 9)))
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


@dataclass
class ReferenceRow:
    row: int  # Excel 列號
    product_code: OrderValue
    issuer: OrderValue
    fields: dict[str, OrderValue]  # 標準欄位 → 值與來源儲存格
    cells: dict[str, str]  # 標準欄位 → 儲存格位置（例：F4）


@dataclass
class ReferenceSheet:
    sheet: str
    rows: list[ReferenceRow]
    unknown_columns: list[OrderValue] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    duplicate_columns: list[OrderValue] = field(default_factory=list)

    def find(self, product_code: str) -> list[ReferenceRow]:
        return [r for r in self.rows if r.product_code.value == product_code]

    def record(self, row: ReferenceRow) -> OrderRecord:
        """把一列轉成核對規則使用的下單資料；欄位格式問題屬於整張表，每份說明書都會看到。"""
        return OrderRecord(
            row.product_code,
            row.fields,
            list(self.unknown_columns),
            list(self.missing_columns),
            list(self.duplicate_columns),
        )


def _code(v: object) -> str | None:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip() if v is not None else ""
    return s or None


def load_reference_sheet(path: Path, fmt: ReferenceFormat) -> ReferenceSheet:
    if not path.is_file():
        raise IngestionError("reference_not_found", f"找不到參考條件表檔案：{path.name}")
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        raise IngestionError("reference_unreadable", f"參考條件表無法開啟（可能已損毀或不是 Excel）：{e}") from e
    if fmt.sheet not in wb.sheetnames:
        raise IngestionError("reference_sheet_missing", f"參考條件表缺少工作表「{fmt.sheet}」")
    ws = wb[fmt.sheet]

    def ref(cell: str) -> str:
        return f"{fmt.sheet}!{cell}"

    std_cols: dict[str, int] = {}
    special: dict[str, int] = {}
    unknown: list[OrderValue] = []
    duplicate: list[OrderValue] = []
    seen: set[str] = set()
    headers: dict[int, str] = {}
    for col in range(1, ws.max_column + 1):
        header = normalize_cell(ws.cell(row=fmt.header_row, column=col).value)
        if header is None:
            continue  # 無表頭的欄一律忽略
        header = str(header)
        where = OrderValue(header, ref(f"{get_column_letter(col)}{fmt.header_row}"))
        if header in seen:
            duplicate.append(where)
            continue
        seen.add(header)
        headers[col] = header
        if header in (fmt.key_column, fmt.issuer_column):
            special[header] = col
        if header in fmt.columns:
            std_cols[fmt.columns[header]] = col
        elif header not in fmt.ignored and header not in special:
            unknown.append(where)
    missing = [h for h in (*fmt.columns, fmt.issuer_column) if h not in seen]
    if fmt.key_column not in special:
        raise IngestionError("reference_key_missing", f"參考條件表缺少配對用的欄位「{fmt.key_column}」")

    rows: list[ReferenceRow] = []
    for r in range(fmt.first_data_row, ws.max_row + 1):
        key_col = special[fmt.key_column]
        code = _code(ws.cell(row=r, column=key_col).value)
        if code is None:
            continue  # 空白列
        issuer_col = special.get(fmt.issuer_column)
        issuer_v = normalize_cell(ws.cell(row=r, column=issuer_col).value) if issuer_col else None
        issuer_ref = ref(f"{get_column_letter(issuer_col)}{r}") if issuer_col else ref(f"?{r}")
        fields: dict[str, OrderValue] = {}
        cells: dict[str, str] = {}
        for std, col in std_cols.items():
            cell = f"{get_column_letter(col)}{r}"
            raw = ws.cell(row=r, column=col).value
            value = _code(raw) if col == key_col else normalize_cell(raw)
            fields[std] = OrderValue(value, ref(cell), headers[col])
            cells[std] = cell
        rows.append(
            ReferenceRow(
                r,
                OrderValue(code, ref(f"{get_column_letter(key_col)}{r}"), fmt.key_column),
                OrderValue(issuer_v, issuer_ref, fmt.issuer_column),
                fields,
                cells,
            )
        )
    return ReferenceSheet(fmt.sheet, rows, unknown, missing, duplicate)
