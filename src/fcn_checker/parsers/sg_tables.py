"""SG 表格解析：封面兩欄表、標的表（第一章 §8 末與 §15 重印）、日期表（§12）、費用表（第四章 §2）。

擷取文字為「一格一行」；新版面長數字會拆成兩行（`12,345.0` / `000日圓`）。價格表以 parsers/price_table.py 讀
（docs/templates/sg-zh-product-description.md §5）。
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..schema import Line, ParsedField
from ..text import squash
from . import money
from . import price_table as price_table_module
from .layout import DATE_RE, TextIndex, capture, parse_date

CCY = money.CURRENCY
# 第 8 項價格表（表頭沒有百分比）與第 15 項重印價格表（表頭帶「(期初價格的X%)」）
PRICE_TABLE = price_table_module.TableSpec(
    where="第一章第 8 項價格表",
    initial="期初價格",
    columns=(
        price_table_module.Column("ko", "自動提前出場價", required=False),
        price_table_module.Column("ki", "下限價", required=False),
        price_table_module.Column("strike", "執行價"),
    ),
    anchor="price",
)
REPRINT_TABLE = replace(
    PRICE_TABLE,
    where="第一章第 15 項重印價格表",
    pct=r"[(（]期初價格的(\d+(?:\.\d+)?)%[)）]",
    pct_label="(期初價格的X%)",
)
VALUE_X = 200.0  # 封面兩欄表：標籤在左欄（x < 200）、值在右欄
COVER_LABELS = (
    "商品中文名稱",
    "商品英文名稱",
    "商品種類",
    "發行機構註冊地",
    "計價幣別",
    "發行機構之名稱、電話及地址",
    "總代理人之名稱、電話及地址",
    "保證機構之名稱及地址",
    "受託或銷售機構之名稱、電話及地址",
    "受託或銷售機構審查通過之日期及文號",
)
WARNING_HEAD = "本商品之投資風險警語"
DATE_HEAD = "配息週期(i)"
NOT_APPLICABLE = "不適用"
FINAL_LABEL, MATURITY_LABEL = "（最終評價日）", "（到期日）"
FEE_PATTERNS = {  # 第四章 §2 費用表（去空白）：費用項目 → 該列「費率(百分比)」欄的寫法
    "申購費用": r"申購費用申購價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
    "提前贖回費用": r"提前贖回費用投資人提前贖回價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
    "分銷費用": r"分銷費用[(（][^)）]*[)）]申購價金的(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)",
}


# ---------------------------------------------------------------- 封面兩欄表


def cover_items(lines: list[Line]) -> dict[str, list[Line]]:
    """封面「商品中文名稱」到「受託或銷售機構審查通過之日期及文號」的兩欄表：標籤（去空白）→ 右欄的值行。

    擷取順序是左欄標籤、再右欄的值；到「本商品之投資風險警語」為止。
    """
    items: dict[str, list[Line]] = {}
    current: str | None = None
    for ln in lines:
        s = squash(ln.text)
        if s.startswith(WARNING_HEAD):
            break
        if ln.x0 < VALUE_X and s in COVER_LABELS:
            current = s
            items.setdefault(s, [])
        elif current is not None and ln.x0 >= VALUE_X:
            items[current].append(ln)
    return items


# ---------------------------------------------------------------- 標的表


def price_table(name: str, lines: list[Line], spec: price_table_module.TableSpec, count: int | None) -> ParsedField:
    """價格表（`parsers/price_table.py` 讀，以執行價格欄的價格切列：單一標的的表沒有 k 欄）：表頭決定欄位（期初價格、
    自動提前出場價（有自動提前出場時）、下限價（有 KI 時）、執行價），每檔一列。`count` 為標的數（§9(1) 的代碼數），
    列數不同時讀不出。"""
    if not lines:
        return ParsedField.missing(name, f"找不到{spec.where}")
    table = price_table_module.read(name, lines, spec)
    if table.ok and count is not None and len(table.value.rows) != count:
        rows = len(table.value.rows)
        return ParsedField.invalid(
            name, list(table.evidence), f"{spec.where}有 {rows} 列，與第 9 項(1) 的 {count} 檔不符"
        )
    return table


# ---------------------------------------------------------------- 日期表


def date_table(lines: list[Line]) -> ParsedField:
    """§12 日期表：表頭「配息週期(i)」之後的表頭格決定表型（D：配息週期起始日／終止日／配息日；P：評價日／配息日），
    資料列從「i=1」起到第一個不是日期、「不適用」或末列標籤的行為止。

    值：{"type": "D"|"P", "rows": [...], "row_lines": [...], "final_label_rows": [...], "maturity_label_rows": [...]}
    （標「（最終評價日）」「（到期日）」的期別；範本只標在末列）；
    D 列為 period／start／end／payment（第 1～Non-Call 期起始與終止日為 None），P 列為 period／pricing／payment。
    """
    name = "date_table"
    heads = [i for i, ln in enumerate(lines) if squash(ln.text) == DATE_HEAD]
    if len(heads) != 1:
        return ParsedField.missing(name, "找不到日期表表頭「配息週期(i)」" if not heads else "日期表表頭出現多次")
    first = next((i for i in range(heads[0] + 1, len(lines)) if squash(lines[i].text) == "i=1"), None)
    if first is None:
        return ParsedField.invalid(name, lines[heads[0] :], "日期表沒有資料列")
    header = "".join(squash(ln.text) for ln in lines[heads[0] + 1 : first])
    if header.startswith("配息週期起始日"):
        kind, keys = "D", ("start", "end", "payment")
    elif header.startswith("評價日(i)"):
        kind, keys = "P", ("pricing", "payment")
    else:
        return ParsedField.invalid(name, lines[heads[0] : first], "日期表表頭不是已知的兩種寫法")
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    labels: dict[str, list[int]] = {FINAL_LABEL: [], MATURITY_LABEL: []}
    end = first
    for i in range(first, len(lines)):
        t = squash(lines[i].text)
        if re.fullmatch(r"i=\d{1,2}", t):
            rows.append({"period": int(t[2:]), "cells": []})
            row_lines.append([lines[i]])
        elif t in (FINAL_LABEL, MATURITY_LABEL):
            if not rows:
                return ParsedField.invalid(name, [lines[i]], "日期表在第一列之前出現末列標籤")
            labels[t].append(rows[-1]["period"])
            row_lines[-1].append(lines[i])
        elif t == NOT_APPLICABLE or DATE_RE.fullmatch(t):
            if not rows:
                return ParsedField.invalid(name, [lines[i]], "日期表出現無法辨識的儲存格")
            value = None if t == NOT_APPLICABLE else parse_date(t)
            if t != NOT_APPLICABLE and value is None:
                return ParsedField.invalid(name, [lines[i]], "日期表日期不合法")
            rows[-1]["cells"].append(value)
            row_lines[-1].append(lines[i])
        else:
            break
        end = i + 1
    out = []
    for row, lns in zip(rows, row_lines, strict=True):
        cells = row["cells"]
        if len(cells) != len(keys) or cells[-1] is None:
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列欄位數或內容不符範本")
        if kind == "D" and (cells[0] is None) != (cells[1] is None):
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列起始日與終止日只有一個是「不適用」")
        out.append({"period": row["period"], **dict(zip(keys, cells, strict=True))})
    if not out or [r["period"] for r in out] != list(range(1, len(out) + 1)):
        return ParsedField.invalid(name, lines[first:end], "日期表期數不是從 1 起連續")
    value = {
        "type": kind,
        "rows": out,
        "row_lines": row_lines,
        "final_label_rows": labels[FINAL_LABEL],
        "maturity_label_rows": labels[MATURITY_LABEL],
    }
    return ParsedField.present(name, value, lines[first:end])


# ---------------------------------------------------------------- 費用表


def fee(label: str, name: str, lines: list[Line]) -> ParsedField:
    """第四章 §2 費用表：該費用項目那一列「費率 (百分比)」欄的區間（例 0%~5%）；寫法見 FEE_PATTERNS。"""
    pattern = FEE_PATTERNS.get(label)
    if pattern is None:
        return ParsedField.missing(name, f"範本沒有費用項目「{label}」的寫法")
    return capture(name, TextIndex(lines), pattern, whole_match=True)
