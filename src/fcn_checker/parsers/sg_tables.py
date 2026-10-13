"""SG 表格解析：封面兩欄表、標的表（第一章 §8 末與 §15 重印）、日期表（§12）、費用表（第四章 §2）。

擷取文字為「一格一行」；新版面長數字會拆成兩行（`12,345.0` / `000日圓`），去空白後串接的全文把它接回。
價格表以表頭文字決定欄位、在去空白全文上讀值（docs/templates/sg-zh-product-description.md §5）。
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..schema import Line, ParsedField
from ..text import squash
from . import money
from .layout import DATE_RE, TextIndex, capture, parse_date

CCY = money.CURRENCY
PRICE = r"([\d,]+\.\d{4})"
PRICE_CELL = PRICE + "(" + CCY + ")"
HEADERS = (("initial", "期初價格"), ("ko", "自動提前出場價"), ("ki", "下限價"), ("strike", "執行價"))
PCT_HEADER = r"[(（]期初價格的(\d+(?:\.\d+)?)%[)）]"  # §15 重印表表頭的百分比
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


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


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


def price_table(name: str, lines: list[Line], count: int | None, *, reprint: bool) -> ParsedField:
    """標的表：表頭決定欄位（期初價格、自動提前出場價（有自動提前出場時）、下限價（有 KI 時）、執行價），每檔一列。

    `count` 為標的數（§8 表由 §9(1) 的代碼數決定；重印表由 §8 表的列數決定）。值：{"columns": [...],
    "rows": [{"prices": {欄: Decimal}}], "row_lines": [...], "currencies": {幣別字}, "pcts": {欄: Decimal}（重印表表頭的百分比）}。
    """
    if not lines:
        return ParsedField.missing(name, "找不到標的表")
    ti = TextIndex(lines)
    chars = list(ti.text)
    for i, ln in enumerate(ti.lines):  # 列首「k=1」獨立一行，去空白串接後會黏到下一格的價格：遮掉（位置不變）
        s = squash(ln.text)
        if re.fullmatch(r"k=\d{1,2}", s):
            a = ti.line_start(i)
            chars[a : a + len(s)] = "#" * len(s)
    text = "".join(chars)
    first = re.search(PRICE_CELL, text)
    if first is None:
        return ParsedField.invalid(name, lines, "標的表沒有「價格 幣別」格")
    head = text[: first.start()]
    columns = [col for col, label in HEADERS if label in head]
    if "initial" not in columns or "strike" not in columns:
        return ParsedField.invalid(name, lines, "標的表表頭缺少期初價格或執行價")
    cells = list(re.finditer(PRICE_CELL, text))
    stray = re.sub(PRICE_CELL, "", text[first.start() :])
    if re.search(r"\d[\d,]*\.\d{4}", stray):
        return ParsedField.invalid(name, lines, "標的表有無法辨識的價格格")
    ncol = len(columns)
    if count is None:
        count = len(cells) // ncol if cells and len(cells) % ncol == 0 else None
    if count is None or len(cells) != count * ncol:
        return ParsedField.invalid(name, lines, f"標的表有 {len(cells)} 個價格格，與 {count or '?'} 檔 × {ncol} 欄不符")
    rows, row_lines = [], []
    for i in range(count):
        prices: dict[str, Decimal] = {}
        lns: list[Line] = []
        for j, col in enumerate(columns):
            m = cells[i * ncol + j]
            prices[col] = _dec(m[1])
            for ln in ti.lines_for(m.start(), m.end()):
                if ln not in lns:
                    lns.append(ln)
        rows.append({"prices": prices})
        row_lines.append(lns)
    pcts: dict[str, Decimal] = {}
    if reprint:
        found = [Decimal(x) for x in re.findall(PCT_HEADER, head)]
        pct_cols = [c for c in columns if c != "initial"]
        if len(found) != len(pct_cols):
            return ParsedField.invalid(name, lines, "重印表表頭的百分比數與欄位數不符")
        pcts = dict(zip(pct_cols, found, strict=True))
    value = {
        "columns": columns,
        "rows": rows,
        "row_lines": row_lines,
        "currencies": {m[2] for m in cells},
        "pcts": pcts,
    }
    return ParsedField.present(name, value, ti.lines_for(first.start(), cells[-1].end()))


# ---------------------------------------------------------------- 日期表


def date_table(lines: list[Line]) -> ParsedField:
    """§12 日期表：表頭「配息週期(i)」之後的表頭格決定表型（D：配息週期起始日／終止日／配息日；P：評價日／配息日），
    資料列從「i=1」起到第一個不是日期、「不適用」或末列標籤的行為止。

    值：{"type": "D"|"P", "rows": [...], "row_lines": [...], "final_labelled": bool, "maturity_labelled": bool}；
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
    labels: set[str] = set()
    end = first
    for i in range(first, len(lines)):
        t = squash(lines[i].text)
        if re.fullmatch(r"i=\d{1,2}", t):
            rows.append({"period": int(t[2:]), "cells": []})
            row_lines.append([lines[i]])
        elif t in (FINAL_LABEL, MATURITY_LABEL):
            labels.add(t)
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
        "final_labelled": FINAL_LABEL in labels,
        "maturity_labelled": MATURITY_LABEL in labels,
    }
    return ParsedField.present(name, value, lines[first:end])


# ---------------------------------------------------------------- 費用表


def fee(label: str, name: str, lines: list[Line]) -> ParsedField:
    """第四章 §2 費用表：該費用項目那一列「費率 (百分比)」欄的區間（例 0%~5%）；寫法見 FEE_PATTERNS。"""
    pattern = FEE_PATTERNS.get(label)
    if pattern is None:
        return ParsedField.missing(name, f"範本沒有費用項目「{label}」的寫法")
    return capture(name, TextIndex(lines), pattern, whole_match=True)
