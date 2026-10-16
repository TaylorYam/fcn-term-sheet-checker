"""MS 表格解析：日期表（第一章第 14 項(6)）、價格表（第 16 項與第 18 項重印；以 parsers/price_table.py 讀）、標的表（第 11 項）、費用表（第四章第 2 項）。

擷取文字為「一格一行」的順序（由左到右、由上到下）；以表頭文字與結束錨點定位，不用頁碼（docs/templates/ms-zh-product-description.md §5）。
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..schema import Line, ParsedField
from ..text import full_brackets, squash
from . import price_table as price_table_module
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

PRICE_COLUMNS = {"期初價格": "initial", "執行價": "strike", "下限價格": "ki", "自動提前出場價": "ko"}  # 標題列出的欄
PRICE_TABLE = price_table_module.TableSpec(
    where="第一章第 16 項價格表",
    initial="期初價格",
    columns=(
        price_table_module.Column("strike", "執行價"),
        price_table_module.Column("ki", "下限價格", required=False),
        price_table_module.Column("ko", "自動提前出場價", required=False),
    ),
    anchor="ticker",
    pct=r"[（(]期初價格的([\d.]+)%[）)]",
    pct_label="（期初價格的X%）",
    code="彭博代碼",
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="第一章第 18 項重印價格表")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9./-]*\s+[A-Z]{2}$")


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


def price_table(
    name: str, lines: list[Line], spec: price_table_module.TableSpec, title: str | None = None
) -> ParsedField:
    """價格表（`parsers/price_table.py` 讀）：每列 k=n、彭博代碼、期初價格、執行價、（下限價格）、（自動提前出場價），
    表頭百分比取「（期初價格的X%）」。`title` 為表上方的標題（例：期初價格、執行價及自動提前出場價），列出的欄須與
    表頭一致（第 18 項重印表沒有標題，為 None）。"""
    table = price_table_module.read(name, lines, spec)
    if not table.ok or title is None:
        return table
    titled = [PRICE_COLUMNS[k] for k in PRICE_COLUMNS if k in squash(title)]
    if sorted(titled) != sorted(table.value.columns):
        return ParsedField.invalid(name, list(table.value.header_lines), f"{spec.where}標題列出的欄與表頭不一致")
    return table


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


def _art11_table(lines: list[Line]) -> tuple[Line, list[Line]] | None:
    """第 11 項標的表的「彭博代碼」表頭與表頭以下到「相對權重」之前的行；找不到或表頭重複時為 None。"""
    headers = [ln for ln in lines if squash(ln.text).startswith("彭博代碼")]
    end = next((ln for ln in lines if squash(ln.text).startswith("相對權重")), None)
    if len(headers) != 1 or end is None:
        return None
    return headers[0], lines[lines.index(headers[0]) + 1 : lines.index(end)]


def _in_ticker_column(ln: Line, hdr: Line) -> bool:
    return hdr.x0 - 30 <= ln.xc <= hdr.x1 + 30


def underlying_table(lines: list[Line]) -> ParsedField:
    """第 11 項標的表的彭博代碼欄（依序）：表頭「彭博代碼」正下方、到「相對權重」之前的代碼。"""
    name = "underlyings_art11"
    table = _art11_table(lines)
    if table is None:
        return ParsedField.missing(name, "找不到第 11 項標的表的彭博代碼欄或結束錨點")
    hdr, region = table
    cells = [ln for ln in region if _in_ticker_column(ln, hdr)]
    bad = [ln for ln in cells if not TICKER.fullmatch(ln.text.strip())]
    if bad or not cells:
        return ParsedField.invalid(name, bad or region, "第 11 項彭博代碼欄無法辨識")
    tickers = [re.sub(r"\s+", " ", ln.text.strip()) for ln in cells]
    return ParsedField.present(name, tickers, cells)


def underlying_names(lines: list[Line]) -> ParsedField:
    """第 11 項標的表的名稱欄（依序）：彭博代碼欄左側、表頭以下到「相對權重」之前的文字（名稱可換行，也可能跨頁接在
    下一頁頂端）。多標的時依擷取順序歸到前一個列號（`1`、`2`…）；單一標的沒有列號欄，全部屬於那一列。
    只用來和投資人須知的標的名稱比對（docs/rules/iis-check-rules.md B12）。"""
    name = "underlying_names_art11"
    table = _art11_table(lines)
    if table is None:
        return ParsedField.missing(name, "找不到第 11 項標的表的彭博代碼欄或結束錨點")
    hdr, region = table
    count = sum(1 for ln in region if _in_ticker_column(ln, hdr))
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
    """第四章費用表：該費用項目那一列「費率」欄的區間（例 0%~5%）。

    費率欄從表頭「費用項目」的右緣起、到「收取時點」之前：表頭「費率」可能置中拆成兩行（`費率` / `(百分比)`），
    比費率格右移 20 pt 以上，不能拿它的 x 當左界（Issue #310）；費用項目名稱換行仍從左欄起頭，不會落進這個範圍。"""
    anchors = [ln for ln in lines if ln.x0 < 100 and squash(ln.text).startswith(label)]
    if len(anchors) != 1:
        return ParsedField.missing(name, "費用列錨點缺漏／重複")
    lab = anchors[0]
    rate_hdr = [ln for ln in lines if ln.page == lab.page and squash(ln.text).startswith("費率")]
    item_hdr = [ln for ln in lines if ln.page == lab.page and squash(ln.text) == "費用項目"]
    if len(rate_hdr) != 1 or len(item_hdr) != 1:
        return ParsedField.missing(name, "找不到費用表「費用項目」或「費率」欄")
    left = item_hdr[0].x1
    right = next((ln.x0 for ln in lines if ln.page == lab.page and squash(ln.text) == "收取時點"), rate_hdr[0].x0 + 65)
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
