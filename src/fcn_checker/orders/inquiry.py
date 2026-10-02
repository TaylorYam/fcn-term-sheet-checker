"""上手詢價表 adapter：依格式設定把 Excel 轉成標準欄位。未知欄名回報，不自行推測。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter

from ..config import OrderFormat
from ..ingestion import IngestionError
from ..schema import CheckResult, CheckStatus, OrderValue


@dataclass
class OrderRecord:
    product_code: OrderValue
    fields: dict[str, OrderValue]
    unknown_columns: list[OrderValue] = field(default_factory=list)  # value = 欄名
    missing_columns: list[str] = field(default_factory=list)  # 設定有、檔案沒有的 Excel 欄名
    duplicate_columns: list[OrderValue] = field(default_factory=list)  # 重複出現的欄名（value = 欄名）
    selection_errors: list[CheckResult] = field(default_factory=list)


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


def load_inquiry(path: Path, fmt: OrderFormat, product_code: str | None = None) -> OrderRecord:
    if not path.is_file():
        raise IngestionError("order_not_found", f"找不到詢價表檔案：{path.name}")
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    except Exception as e:
        raise IngestionError("order_unreadable", f"詢價表無法開啟（可能已損毀或不是 Excel）：{e}") from e
    if fmt.sheet not in wb.sheetnames:
        raise IngestionError("order_sheet_missing", f"詢價表缺少工作表「{fmt.sheet}」")
    ws = wb[fmt.sheet]
    if fmt.kind == "table":
        try:
            return _load_table(ws, fmt, product_code)
        finally:
            wb.close()

    def ref(cell: str) -> str:
        return f"{fmt.sheet}!{cell}"

    raw_code = ws[fmt.product_code_cell].value
    if isinstance(raw_code, float) and raw_code.is_integer():
        raw_code = int(raw_code)
    code = str(raw_code).strip() if raw_code is not None else None
    product_code = OrderValue(code or None, ref(fmt.product_code_cell))

    fields: dict[str, OrderValue] = {}
    unknown: list[OrderValue] = []
    duplicate: list[OrderValue] = []
    seen: set[str] = set()
    for col in range(1, ws.max_column + 1):
        header = normalize_cell(ws.cell(row=fmt.header_row, column=col).value)
        if header is None:
            continue
        header = str(header)
        letter = get_column_letter(col)
        if header in seen:
            duplicate.append(OrderValue(header, ref(f"{letter}{fmt.header_row}")))
        elif header in fmt.columns:
            std = fmt.columns[header]
            value = normalize_cell(ws.cell(row=fmt.data_row, column=col).value)
            fields[std] = OrderValue(value, ref(f"{letter}{fmt.data_row}"))
            seen.add(header)
        elif header in fmt.ignored:
            seen.add(header)
        else:
            unknown.append(OrderValue(header, ref(f"{letter}{fmt.header_row}")))
            seen.add(header)
    missing = [h for h in fmt.columns if h not in seen]
    wb.close()
    return OrderRecord(product_code, fields, unknown, missing, duplicate)


def _code(v: Any) -> str | None:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip() if v is not None else None


def _load_table(ws, fmt: OrderFormat, product_code: str | None) -> OrderRecord:
    headers = [normalize_cell(c.value) for c in ws[fmt.header_row]]

    def issue(reason, message, source="", expected=None, actual=None):
        r = CheckResult(
            "order.table_pairing",
            "product_code",
            CheckStatus.REVIEW_REQUIRED,
            expected=expected,
            actual=actual,
            reason_code=reason,
            message=message,
            order_source=[source] if source else [],
        )
        return OrderRecord(OrderValue(None, source), {}, selection_errors=[r])

    keys = [i + 1 for i, h in enumerate(headers) if h == fmt.key_column]
    issuers = [i + 1 for i, h in enumerate(headers) if h == fmt.issuer_column]
    if len(keys) != 1 or len(issuers) != 1:
        return issue("order_pairing_column", "商品代號或上手欄位缺漏／重複")
    if product_code is None:
        return issue("document_product_code", "說明書商品代號缺漏或有歧義，無法找整理表列")
    matching = [
        row for row in range(fmt.data_row, ws.max_row + 1) if _code(ws.cell(row, keys[0]).value) == product_code
    ]
    if len(matching) != 1:
        return issue(
            "order_row_missing" if not matching else "order_row_ambiguous",
            "整理表找不到商品代號或同代號有多列",
            expected=product_code,
            actual=len(matching),
        )
    row = matching[0]
    issuer_cell = ws.cell(row, issuers[0])
    if normalize_cell(issuer_cell.value) != fmt.issuer_value:
        return issue(
            "issuer_mismatch",
            "整理表配對列的發行機構不符",
            f"{fmt.sheet}!{issuer_cell.coordinate}",
            fmt.issuer_value,
            normalize_cell(issuer_cell.value),
        )
    fields, unknown, duplicate, seen = {}, [], [], set()
    for col, header in enumerate(headers, 1):
        if header is None:
            continue
        label = str(header)
        source = f"{fmt.sheet}!{get_column_letter(col)}{row}"
        href = f"{fmt.sheet}!{get_column_letter(col)}{fmt.header_row}"
        if label in seen:
            duplicate.append(OrderValue(label, href))
        elif label in fmt.columns:
            raw = ws.cell(row, col).value
            value = _code(raw) if fmt.columns[label] == "product_code" else normalize_cell(raw)
            if value == fmt.empty_value and fmt.columns[label] != "ki_type":
                value = None
            fields[fmt.columns[label]] = OrderValue(value, source)
        elif label not in fmt.ignored:
            unknown.append(OrderValue(label, href))
        seen.add(label)
    missing = [h for h in fmt.columns if h not in seen]
    code_cell = ws.cell(row, keys[0])
    return OrderRecord(
        OrderValue(_code(code_cell.value), f"{fmt.sheet}!{code_cell.coordinate}"), fields, unknown, missing, duplicate
    )
