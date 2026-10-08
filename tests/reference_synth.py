"""合成參考條件表：仿 FCN參考條件 Excel 的 `樣本清單`（不分上手）。

欄名對照與「發行機構」欄寫法都取自參考條件表格式設定（config/reference_sheet.toml）。
各上手特有的值（例：Non-Call(月)）由呼叫端或該上手的說明書合成器提供。所有數值皆為虛構。
"""

from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path
from typing import Any

import openpyxl

REFERENCE_FORMAT = Path(__file__).resolve().parents[1] / "config" / "reference_sheet.toml"
_FORMAT = tomllib.loads(REFERENCE_FORMAT.read_text(encoding="utf-8"))
# Excel 欄名 → 標準欄位（[columns] 內的 ignored 清單不是欄名）
REFERENCE_COLUMNS: dict[str, str] = {h: f for h, f in _FORMAT["columns"].items() if isinstance(f, str)}

REFERENCE_HEADERS = [
    "庫存狀態",
    "當日比價",
    "Product",
    "發行機構",
    "TDCC Code",
    "ISIN Code",
    "私銀註記",
    "單位面額",
    "承作幣別",
    "交易日",
    "發行日",
    *[f"比價日_{i}" for i in range(1, 13)],
    "最終比價日",
    "到期日",
    "期初定價",
    "KO(%)",
    "KO(Freq)",
    "KO(memo)",
    "K(%)",
    "KI(%)",
    "KI(Freq)",
    "UF",
    "Coupon p.a. (%)",
    "天期(月)",
    "Non-Call(月)",
    *[f"UL_{i}{suffix}" for i in range(1, 6) for suffix in ("", "_進場價", "_執行價", "_下限價", "_KO價", "_Memo")],
]
DATE_FORMAT = "mm-dd-yy"


def issuer_value(issuer: str) -> str:
    """上手代號 → 該上手在參考條件表「發行機構」欄的寫法。"""
    return _FORMAT["issuer_values"][issuer]


def make_row(issuer: str, fields: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """一列參考條件表：fields 以標準欄位為鍵（依格式設定對到 Excel 欄名），overrides 以 Excel 欄名覆寫。

    沒給的欄位（例：回填欄位 ISIN、發行日、比價日）留空；只用來辨識或存續管理的欄位填固定的虛構值。
    """
    row: dict[str, Any] = {
        "庫存狀態": None,
        "當日比價": None,
        "Product": "FCN",
        "發行機構": issuer_value(issuer),
        "私銀註記": "-",
        "UF": 1.5900000000000034,
        "期初定價": "收盤價",
        **{f"UL_{i}_Memo": "-" for i in range(1, 6)},
    }
    row.update({h: fields[f] for h, f in REFERENCE_COLUMNS.items() if f in fields})
    row.update(overrides)
    return row


def build_reference_sheet(
    path: Path, rows: list[dict[str, Any]], headers: list[str] | None = None, extra_sheets: tuple[str, ...] = ()
) -> Path:
    """仿 FCN參考條件 的 `樣本清單`：第 3 列表頭、第 4 列起資料，最右側一個無表頭欄；另有一張其他工作表。"""
    headers = headers or REFERENCE_HEADERS
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "樣本清單"
    for k, h in enumerate(headers, start=1):
        ws.cell(row=3, column=k, value=h)
    for r, row in enumerate(rows, start=4):
        for k, h in enumerate(headers, start=1):
            c = ws.cell(row=r, column=k, value=row.get(h))
            if isinstance(c.value, dt.datetime):  # 空白格維持「通用格式」，回填時要沿用表上日期格式
                c.number_format = DATE_FORMAT
        ws.cell(row=r, column=len(headers) + 9, value=1 if row.get("庫存狀態") else None)
    other = wb.create_sheet("詢價表格")
    other["B3"] = "其他工作表（回填時不得改動）"
    for name in extra_sheets:
        wb.create_sheet(name)
    wb.save(path)
    return path
