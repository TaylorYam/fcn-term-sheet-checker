"""HSBC 表格解析：以章／子項提供的區域及表頭辨識，不用頁碼。"""

from __future__ import annotations

import re
from decimal import Decimal

from ..schema import Line, ParsedField
from ..text import squash
from .layout import TextIndex, parse_date

PRICE_LABELS = {"期初股價": "initial", "執行價": "strike", "自動提前到期價格": "ko", "觸及不保本價格": "ki"}
NUMBER = re.compile(r"[\d,]+\.\d{4}")
HEADER_PCT = r"\(即期初股價的([\d.]+)%\)"
SYMBOL_HEAD = re.compile(r"[A-Z0-9./-]+")  # 彭博代號欄窄，代號可能在字中間換行
SYMBOL_TAIL = re.compile(r"[A-Z0-9./-]+\s+[A-Z]{2}")


def price_table(name: str, lines: list[Line]) -> ParsedField:
    """價格表：表頭欄名依序為期初股價、執行價、自動提前到期價格、（觸及不保本價格），期初股價以外各帶「(即期初股價的X%)」。

    百分比通常緊接在各自欄名之後；表頭跨頁時欄名全在上一頁、百分比全在下一頁（OOS，#252）。百分比裡也有「期初股價」，
    所以先遮掉百分比再找欄名，百分比依出現順序對到期初股價以外的欄。
    """
    ti = TextIndex(lines)
    pcts = list(re.finditer(HEADER_PCT, ti.text))
    masked = re.sub(HEADER_PCT, lambda m: "□" * len(m[0]), ti.text)
    headers = list(re.finditer(r"期初股價|執行價|自動提前到期價格|觸及不保本價格", masked))
    if not headers:
        return ParsedField.missing(name, "找不到價格表頭")
    columns = [PRICE_LABELS[m[0]] for m in headers]
    if columns not in (["initial", "strike", "ko"], ["initial", "strike", "ko", "ki"]):
        return ParsedField.invalid(name, lines, "價格表頭順序或數量不符範本")
    if len(pcts) != len(columns) - 1 or pcts[0].start() < headers[1].start():
        return ParsedField.invalid(name, lines, "價格表頭百分比缺漏或格式未知")
    header_pcts = {col: Decimal(m[1]) for col, m in zip(columns[1:], pcts, strict=True)}
    end = max(headers[-1].end(), pcts[-1].end())
    last = ti.lines_for(end - 1, end)[-1]
    start = lines.index(last) + 1
    tokens = lines[start:]
    rows: list[dict] = []
    row_lines: list[list[Line]] = []
    pending: list[Line] = []
    prices: list[Decimal] = []
    for ln in tokens:
        pending.append(ln)
        if NUMBER.fullmatch(squash(ln.text)):
            prices.append(Decimal(squash(ln.text).replace(",", "")))
        if len(prices) == len(columns):
            pre = [x.text.strip() for x in pending if not NUMBER.fullmatch(squash(x.text))]
            if len(pre) > 1 and SYMBOL_HEAD.fullmatch(pre[0]) and SYMBOL_TAIL.fullmatch(pre[1]):
                pre = [pre[0] + pre[1], *pre[2:]]  # 代號在字中間換行（`QCO` / `M UQ`）：接回不加空白
            joined = " ".join(pre)
            ticker = re.match(r"^([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})(.*)$", joined)
            if not ticker:
                return ParsedField.invalid(name, pending, "彭博代號或交易所尾碼無法辨識")
            rest = squash(ticker[3])
            details = re.fullmatch(r"(.*?)(USD|JPY|CNH|HKD|EUR|AUD|GBP)([A-Z0-9]+)", rest)
            if not details:
                return ParsedField.invalid(name, pending, "標的幣別／交易所欄位不完整")
            rows.append(
                {
                    "ticker": ticker[1] + " " + ticker[2],
                    "label": details[1],
                    "currency": details[2],
                    "exchange": details[3],
                    "prices": dict(zip(columns, prices, strict=True)),
                }
            )
            row_lines.append(pending)
            pending = []
            prices = []
    if pending or not rows:
        return ParsedField.invalid(name, lines, "價格表列不完整或無資料")
    return ParsedField.present(name, {"rows": rows, "headers": header_pcts, "row_lines": row_lines}, lines)


def coupon_table(lines: list[Line], daily: bool) -> ParsedField:
    name = "coupon_table"
    if not lines:
        return ParsedField.missing(name)
    texts = [squash(x.text) for x in lines]
    marker = "Nt" if daily else "付息日(如未在該計息期間自動提前到期)"
    ti = TextIndex(lines)
    hits = [
        m
        for m in re.finditer(re.escape(marker), ti.text)
        if not daily or ti.lines_for(m.start(), m.end())[0].text == "Nt"
    ]
    ends = list(re.finditer("註1[:：]", ti.text))
    if len(hits) != 1 or len(ends) != 1:
        return ParsedField.invalid(name, lines, "配息表頭或結束錨點缺漏／重複")
    first = ti.lines_for(hits[0].start(), hits[0].end())[-1]
    last = ti.lines_for(ends[0].start(), ends[0].end())[0]
    a, b = lines.index(first) + 1, lines.index(last)
    cells = texts[a:b]
    width = 5 if daily else 2
    if not cells or len(cells) % width:
        return ParsedField.invalid(name, lines[a:b], "配息表欄位數不完整")
    rows = []
    for offset in range(0, len(cells), width):
        row = cells[offset : offset + width]
        if not re.fullmatch(r"\d{1,2}", row[0]):
            return ParsedField.invalid(name, lines[a:b], "配息期數格式未知")
        dates = [None if x == "-" else parse_date(x) for x in row[1:4] if daily] if daily else [parse_date(row[1])]
        if daily:
            if (
                any(d is None and t != "-" for d, t in zip(dates, row[1:4], strict=True))
                or dates[1] is None
                or dates[2] is None
                or not re.fullmatch(r"-|\d+", row[4])
            ):
                return ParsedField.invalid(name, lines[a:b], "配息表日期或Nt格式未知")
            rows.append(
                {
                    "period": int(row[0]),
                    "start": dates[0],
                    "end": dates[1],
                    "payment": dates[2],
                    "nt": None if row[4] == "-" else int(row[4]),
                }
            )
        else:
            if dates[0] is None:
                return ParsedField.invalid(name, lines[a:b], "配息日不合法")
            rows.append({"period": int(row[0]), "payment": dates[0]})
    return ParsedField.present(name, rows, lines[a:b])


def ko_table(lines: list[Line]) -> ParsedField:
    name = "ko_table"
    texts = [squash(x.text) for x in lines]
    try:
        a = texts.index("自動提前到期金額付款日") + 1
        b = next(i for i in range(a, len(texts)) if "投資人應注意" in texts[i])
    except (ValueError, StopIteration):
        return ParsedField.missing(name, "找不到定期KO表頭或結束錨點")
    cells = texts[a:b]
    if not cells or len(cells) % 3:
        return ParsedField.invalid(name, lines[a:b], "定期KO表列不完整")
    rows = []
    for i in range(0, len(cells), 3):
        serial, decision, payment = cells[i : i + 3]
        d, p = parse_date(decision), parse_date(payment)
        if not re.fullmatch(r"\d+", serial) or d is None or p is None:
            return ParsedField.invalid(name, lines[a:b], "定期KO表日期或流水號格式未知")
        rows.append({"decision": d, "payment": p})
    return ParsedField.present(name, rows, lines[a:b])
