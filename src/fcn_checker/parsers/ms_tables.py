"""MS 表格解析：日期表（第一章第 14 項(6)）、價格表（第 16 項與第 18 項重印）、標的表（第 11 項）、費用表（第四章第 2 項）。

擷取文字為「一格一行」的順序（由左到右、由上到下）；以表頭文字與結束錨點定位，不用頁碼（docs/templates/ms-zh-product-description.md §5）。
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..schema import Line, ParsedField
from ..text import full_brackets, squash
from .layout import DATE_RE, TextIndex, join_text, parse_date

# 日期表：標題 → 表型；表頭（去空白、括號統一全形）
DATE_TITLES = {
    "配息週期起始日、配息週期終止日與配息日：依下表所示": "D",
    "定價日、配息日與自動提前出場日：依下表所示": "P",
}
DATE_HEADER_CELLS = {
    "D": ("配息觀察期間（j）", "配息週期起始日（含）", "配息週期終止日（含）", "配息日"),
    "P": ("（j）", "定價日（j）", "配息日", "自動提前出場日"),
}
DATE_KEYS = {"D": ("start", "end", "payment"), "P": ("pricing", "payment", "autocall")}
DATE_TABLE_END = "如配息日非營業日"
NONE = "無"  # P 型自動提前出場日「無」

PRICE_COLUMNS = {"期初價格": "initial", "執行價": "strike", "下限價格": "ki", "自動提前出場價": "ko"}
PRICE_ORDER = ("initial", "strike", "ki", "ko")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9./-]*\s+[A-Z]{2}$")
PRICE = re.compile(r"^[\d,]+\.\d{4}$")
ROW_LABEL = re.compile(r"^k=(\d+)$")


def _norm(text: str) -> str:
    return full_brackets(squash(text))


def date_table(lines: list[Line]) -> ParsedField:
    """§14(6)：標題決定表型（D 配息週期起始／終止日、P 定價日），表頭須與表型一致；每列 4 格，結束於「如配息日非營業日」。

    值：{"type": "D"|"P", "rows": [各列 dict], "row_lines": [各列原文行]}；D 列為 period／start／end／payment，
    P 列為 period／pricing／payment／autocall（「無」為 None）。
    """
    name = "date_table"
    if not lines:
        return ParsedField.missing(name, "找不到第 14 項(6)")
    title = squash(lines[0].text)
    kinds = [t for k, t in DATE_TITLES.items() if k in title]
    if len(kinds) != 1:
        return ParsedField.invalid(name, lines[:1], "日期表標題不是已知的兩種寫法")
    kind = kinds[0]
    end = next((i for i, ln in enumerate(lines) if squash(ln.text).startswith(DATE_TABLE_END)), None)
    if end is None:
        return ParsedField.invalid(name, lines[:1], "找不到日期表結束錨點「如配息日非營業日」")
    first = next((i for i in range(1, end) if re.fullmatch(r"\d{1,2}", squash(lines[i].text))), None)
    if first is None:
        return ParsedField.invalid(name, lines[:end], "日期表沒有資料列")
    header = "".join(_norm(ln.text) for ln in lines[1:first])
    if header != "".join(DATE_HEADER_CELLS[kind]):
        return ParsedField.invalid(name, lines[:first], "日期表表頭與標題的表型不一致或寫法未知")
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    for ln in lines[first:end]:
        t = squash(ln.text)
        if _norm(ln.text) in DATE_HEADER_CELLS[kind]:  # 跨頁重複的表頭
            continue
        if re.fullmatch(r"\d{1,2}", t):
            rows.append({"period": int(t), "cells": []})
            row_lines.append([ln])
            continue
        if DATE_RE.fullmatch(t):
            value = parse_date(t)
            if value is None:
                return ParsedField.invalid(name, [ln], "日期表日期不合法")
        elif t == NONE and kind == "P":
            value = None
        else:
            return ParsedField.invalid(name, [ln], "日期表出現無法辨識的儲存格")
        rows[-1]["cells"].append(value)
        row_lines[-1].append(ln)
    out = []
    for row, lns in zip(rows, row_lines, strict=True):
        cells = row["cells"]
        if len(cells) != 3 or None in cells[:2] or (kind == "D" and cells[2] is None):
            return ParsedField.invalid(name, lns, f"日期表第 {row['period']} 列欄位數或內容不符範本")
        out.append({"period": row["period"], **dict(zip(DATE_KEYS[kind], cells, strict=True))})
    if [r["period"] for r in out] != list(range(1, len(out) + 1)):
        return ParsedField.invalid(name, lines[first:end], "日期表期數不是從 1 起連續")
    return ParsedField.present(name, {"type": kind, "rows": out, "row_lines": row_lines}, lines[first:end])


def price_table(name: str, lines: list[Line], title: str | None) -> ParsedField:
    """價格表：表頭依序為期初價格、執行價、（下限價格）、（自動提前出場價），百分比取「（期初價格的X%）」；
    `title` 為表上方的標題（例：期初價格、執行價及自動提前出場價），列出的欄須與表頭一致（第 18 項重印表沒有標題，
    為 None）。每列 k=n、彭博代碼、各價格。

    值：{"columns": [...], "headers": {欄: 百分比}, "rows": [{"ticker", "prices"}], "row_lines": [...]}。
    """
    first = next((i for i, ln in enumerate(lines) if ROW_LABEL.fullmatch(squash(ln.text))), None)
    if first is None:
        return ParsedField.invalid(name, lines, "價格表沒有資料列（k=1）")
    header = _norm("".join(ln.text for ln in lines[:first]))
    found = list(re.finditer(r"(期初價格|執行價|下限價格|自動提前出場價)(?:（期初價格的([\d.]+)%）)?", header))
    columns = [PRICE_COLUMNS[m[1]] for m in found]
    expected = [c for c in PRICE_ORDER if c in columns]
    if columns != expected or columns[:2] != ["initial", "strike"]:
        return ParsedField.invalid(name, lines[:first], "價格表表頭欄位或順序不符範本")
    if found[0][2] is not None or any(m[2] is None for m in found[1:]):
        return ParsedField.invalid(name, lines[:first], "價格表表頭百分比缺漏或格式未知")
    titled = [PRICE_COLUMNS[k] for k in PRICE_COLUMNS if title is not None and k in squash(title)]
    if title is not None and titled != columns:
        return ParsedField.invalid(name, lines[:first], "價格表標題列出的欄與表頭不一致")
    headers = {PRICE_COLUMNS[m[1]]: Decimal(m[2]) for m in found[1:]}
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    for ln in lines[first:]:
        t = squash(ln.text)
        label = ROW_LABEL.fullmatch(t)
        if label:
            if rows and len(rows[-1]["cells"]) != len(columns) + 1:
                return ParsedField.invalid(name, row_lines[-1], "價格表列不完整")
            if int(label[1]) != len(rows) + 1:
                return ParsedField.invalid(name, [ln], "價格表列標籤 k 不連續")
            rows.append({"cells": []})
            row_lines.append([ln])
            continue
        if not rows:
            return ParsedField.invalid(name, [ln], "價格表出現無法辨識的儲存格")
        cells = rows[-1]["cells"]
        if not cells and TICKER.fullmatch(ln.text.strip()):
            cells.append(re.sub(r"\s+", " ", ln.text.strip()))
        elif cells and PRICE.fullmatch(t) and len(cells) <= len(columns):
            cells.append(Decimal(t.replace(",", "")))
        else:
            return ParsedField.invalid(name, [ln], "價格表彭博代碼或價格無法辨識")
        row_lines[-1].append(ln)
    if not rows or len(rows[-1]["cells"]) != len(columns) + 1:
        return ParsedField.invalid(name, lines[first:], "價格表列不完整")
    value = {
        "columns": columns,
        "headers": headers,
        "rows": [{"ticker": r["cells"][0], "prices": dict(zip(columns, r["cells"][1:], strict=True))} for r in rows],
        "row_lines": row_lines,
    }
    return ParsedField.present(name, value, lines[first:])


def price_region(lines: list[Line], end_prefixes: tuple[str, ...]) -> tuple[str, list[Line]] | None:
    """價格表的標題（`期初價格…：依下表所示`）與表格所在的行（到下一個以 `end_prefixes` 開頭的行之前）。"""
    ti = TextIndex(lines)
    hits = list(ti.finditer(r"期初價格[^：:]*[：:]依下表所示"))
    if len(hits) != 1:
        return None
    title_lines = ti.lines_for(hits[0].start(), hits[0].end())
    start = lines.index(title_lines[-1]) + 1
    end = next((i for i in range(start, len(lines)) if _norm(lines[i].text).startswith(end_prefixes)), None)
    if end is None:
        return None
    return hits[0][0], lines[start:end]


def underlying_table(lines: list[Line]) -> ParsedField:
    """第 11 項標的表的彭博代碼欄（依序）：表頭「彭博代碼」正下方、到「相對權重」之前的代碼。"""
    name = "underlyings_art11"
    headers = [ln for ln in lines if squash(ln.text).startswith("彭博代碼")]
    end = next((ln for ln in lines if squash(ln.text).startswith("相對權重")), None)
    if len(headers) != 1 or end is None:
        return ParsedField.missing(name, "找不到第 11 項標的表的彭博代碼欄或結束錨點")
    hdr = headers[0]
    a, b = lines.index(hdr) + 1, lines.index(end)
    cells = [ln for ln in lines[a:b] if hdr.x0 - 30 <= ln.xc <= hdr.x1 + 30]
    bad = [ln for ln in cells if not TICKER.fullmatch(ln.text.strip())]
    if bad or not cells:
        return ParsedField.invalid(name, bad or lines[a:b], "第 11 項彭博代碼欄無法辨識")
    tickers = [re.sub(r"\s+", " ", ln.text.strip()) for ln in cells]
    return ParsedField.present(name, tickers, cells)


def underlying_names(lines: list[Line]) -> ParsedField:
    """第 11 項標的表的名稱欄（依序）：彭博代碼欄左側、表頭以下到「相對權重」之前的文字（名稱可換行，也可能跨頁接在
    下一頁頂端）。多標的時依擷取順序歸到前一個列號（`1`、`2`…）；單一標的沒有列號欄，全部屬於那一列。
    只用來和投資人須知的標的名稱比對（docs/rules/iis-check-rules.md B12）。"""
    name = "underlying_names_art11"
    headers = [ln for ln in lines if squash(ln.text).startswith("彭博代碼")]
    end = next((ln for ln in lines if squash(ln.text).startswith("相對權重")), None)
    if len(headers) != 1 or end is None:
        return ParsedField.missing(name, "找不到第 11 項標的表的彭博代碼欄或結束錨點")
    hdr = headers[0]
    region = lines[lines.index(hdr) + 1 : lines.index(end)]
    count = sum(1 for ln in region if hdr.x0 - 30 <= ln.xc <= hdr.x1 + 30)
    left = [ln for ln in region if ln.x1 <= hdr.x0]  # 彭博代碼欄左側：列號與名稱
    rows: list[list[Line]] = [[] for _ in range(count)]
    row = 0 if count == 1 else -1
    for ln in left:
        t = ln.text.strip()
        if count > 1 and re.fullmatch(r"\d{1,2}", t):
            if int(t) != row + 2:
                return ParsedField.invalid(name, [ln], "第 11 項標的表的列號不連續")
            row += 1
        elif row < 0 or row >= count:
            return ParsedField.invalid(name, [ln], "第 11 項標的表的名稱對不到列號")
        else:
            rows[row].append(ln)
    if not count or any(not r for r in rows):
        return ParsedField.invalid(name, region, "第 11 項標的表有一列沒有名稱")
    names = [re.sub(r"\s+", " ", join_text(r)).strip() for r in rows]
    return ParsedField.present(name, names, [ln for r in rows for ln in r])


FEE_ROWS = ("申購費用", "提前贖回費用", "管理費用", "分銷費用", "保費費用", "解約費用", "其他費用")


def fee(label: str, name: str, lines: list[Line]) -> ParsedField:
    """第四章費用表：該費用項目那一列「費率」欄的區間（例 0%~5%）。"""
    anchors = [ln for ln in lines if ln.x0 < 100 and squash(ln.text).startswith(label)]
    if len(anchors) != 1:
        return ParsedField.missing(name, "費用列錨點缺漏／重複")
    lab = anchors[0]
    rate_hdr = [ln for ln in lines if ln.page == lab.page and squash(ln.text).startswith("費率")]
    if len(rate_hdr) != 1:
        return ParsedField.missing(name, "找不到費用表「費率」欄")
    left = rate_hdr[0].x0 - 15
    right = next((ln.x0 for ln in lines if ln.page == lab.page and squash(ln.text) == "收取時點"), left + 80)
    nexts = [
        ln.y0
        for ln in lines
        if ln.page == lab.page and ln.x0 < 100 and ln.y0 > lab.y0 + 1 and squash(ln.text).startswith(FEE_ROWS)
    ]
    bottom = min(nexts) if nexts else lab.y0 + 100
    region = [
        ln for ln in lines if ln.page == lab.page and left <= ln.x0 < right - 5 and lab.y0 - 1 <= ln.y0 < bottom - 1
    ]
    ti = TextIndex(region)
    hits = [(m[1], ti.lines_for(m.start(), m.end())) for m in ti.finditer(r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)")]
    return ParsedField.from_hits(name, hits, missing_note="費率欄找不到費率區間")
