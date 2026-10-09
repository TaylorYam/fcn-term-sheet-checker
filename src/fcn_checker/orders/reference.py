"""參考條件表 adapter：多列表格，每列一檔商品（docs/order-formats/reference-sheet.md）。

依格式設定把各列轉成標準欄位（`OrderRecord`），同時記下每個欄位的 Excel 欄名與儲存格位置，供核對結果與回填使用。
標準欄位名怎麼組（`underlying_{i}`、`underlying_{i}_{價格}`、`autocall_date_{n}`）只在這裡定義，規則用型別化的讀法。
未知欄名回報，不自行推測。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
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
UNDERLYING_SLOTS = 5  # UL_1～UL_5
PRICE_FIELDS = {"initial": "initial_price", "strike": "strike_price", "ki": "ki_price", "ko": "ko_price"}


def underlying_key(i: int) -> str:
    """第 i 檔標的代號的標準欄位（UL_i）。"""
    return f"underlying_{i}"


def price_key(i: int, col: str) -> str:
    """第 i 檔標的某價格的標準欄位；`col` 為 initial／strike／ki／ko（UL_i_進場價 等）。"""
    return f"underlying_{i}_{PRICE_FIELDS[col]}"


def autocall_date_key(n: int) -> str:
    """第 n 期比價日的標準欄位（比價日_n）。"""
    return f"autocall_date_{n}"


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


def headers_of(fmt: ReferenceFormat) -> dict[str, str]:
    """標準欄位 → 格式設定的 Excel 欄名（同一標準欄位對到多個欄名時取先宣告的）。"""
    out: dict[str, str] = {}
    for header, std in fmt.columns.items():
        out.setdefault(std, header)
    return out


@dataclass
class OrderRecord:
    """參考條件表的一列（核對規則的下單資料）：標準欄位 → 值、Excel 欄名與儲存格，一處完成。

    `fields` 只有這張表有的欄；`headers` 是格式設定宣告的全部欄名（表上缺的欄也查得到欄名，回填規則據此報缺欄）。
    表頭欄名的問題（未知、缺少、重複）屬於整張表，每一列都帶同一份。
    """

    row: int  # Excel 列號
    product_code: OrderValue
    issuer: OrderValue
    fields: dict[str, OrderValue]  # 標準欄位 → 值與來源儲存格（`OrderValue.column` 為 Excel 欄名）
    cells: dict[str, str]  # 標準欄位 → 儲存格位置（例：F4）
    headers: Mapping[str, str] = field(default_factory=dict)  # 標準欄位 → 格式設定的 Excel 欄名
    unknown_columns: list[OrderValue] = field(default_factory=list)  # value = 欄名
    missing_columns: list[str] = field(default_factory=list)  # 設定有、檔案沒有的 Excel 欄名
    duplicate_columns: list[OrderValue] = field(default_factory=list)  # 重複出現的欄名（value = 欄名）
    source: str = SOURCE  # 條件來源名稱，用在核對訊息

    def get(self, key: str) -> OrderValue | None:
        """標準欄位的值與來源；表上沒有這欄時為 None（值空白時是 value 為 None 的 OrderValue）。"""
        return self.fields.get(key)

    def has(self, key: str) -> bool:
        """表上有這一欄。"""
        return key in self.cells

    def cell(self, key: str) -> str | None:
        """儲存格位置（例：F4）；表上沒有這欄時為 None。"""
        return self.cells.get(key)

    def header(self, key: str) -> str | None:
        """格式設定給這個標準欄位的 Excel 欄名；格式設定沒有宣告時為 None。"""
        return self.headers.get(key)

    def underlying(self, i: int) -> OrderValue | None:
        """第 i 檔標的代號（UL_i）。"""
        return self.get(underlying_key(i))

    def price(self, i: int, col: str) -> OrderValue | None:
        """第 i 檔標的某價格；`col` 為 initial／strike／ki／ko。"""
        return self.get(price_key(i, col))

    def autocall_date(self, n: int) -> OrderValue | None:
        """第 n 期比價日（比價日_n）。"""
        return self.get(autocall_date_key(n))

    @property
    def underlying_slots(self) -> int:
        """格式設定有幾檔標的的價格欄（UL_1～UL_n）。"""
        suffix = f"_{PRICE_FIELDS['initial']}"
        return sum(1 for key in self.headers if key.startswith("underlying_") and key.endswith(suffix))

    @classmethod
    def offline(
        cls, fmt: ReferenceFormat, values: Mapping[str, Any], *, row: int = 4, issuer: Any = None
    ) -> OrderRecord:
        """不經 Excel 直接以標準欄位值建一列（測試與離線用）：有格式設定的每一欄，沒給值的欄是空白格；
        儲存格依 `[columns]` 的順序從 A 欄起編號。"""
        fields: dict[str, OrderValue] = {}
        cells: dict[str, str] = {}
        code = OrderValue(None, f"{fmt.sheet}!?{row}", fmt.key_column)
        for col, (header, std) in enumerate(fmt.columns.items(), start=1):
            cell = f"{get_column_letter(col)}{row}"
            value = _code(values.get(std)) if header == fmt.key_column else values.get(std)
            fields[std] = OrderValue(value, f"{fmt.sheet}!{cell}", header)
            cells[std] = cell
            if header == fmt.key_column:
                code = OrderValue(value, f"{fmt.sheet}!{cell}", fmt.key_column)
        return cls(
            row,
            code,
            OrderValue(issuer, f"{fmt.sheet}!?{row}", fmt.issuer_column),
            fields,
            cells,
            headers_of(fmt),
        )


@dataclass
class ReferenceSheet:
    sheet: str
    rows: list[OrderRecord]
    unknown_columns: list[OrderValue] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    duplicate_columns: list[OrderValue] = field(default_factory=list)

    def find(self, product_code: str) -> list[OrderRecord]:
        return [r for r in self.rows if r.product_code.value == product_code]


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

    declared = headers_of(fmt)
    rows: list[OrderRecord] = []
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
            OrderRecord(
                r,
                OrderValue(code, ref(f"{get_column_letter(key_col)}{r}"), fmt.key_column),
                OrderValue(issuer_v, issuer_ref, fmt.issuer_column),
                fields,
                cells,
                declared,
                list(unknown),
                list(missing),
                list(duplicate),
            )
        )
    return ReferenceSheet(fmt.sheet, rows, unknown, missing, duplicate)
