"""價格表讀取（說明書與投資人須知；情境分析重印的同一張表也是）：上手 adapter 只宣告表長什麼樣子（`TableSpec`），
y 座標、跨頁、換行、擠格都在這裡處理（Issue #287）。

一張價格表：表頭在上（每欄一格、可拆成多行，常帶「(期初價格的 X%)」），每檔標的一列；欄依表頭各格的 x 位置排序。
讀法：

1. 相鄰幾格擷取成同一行時，先依字座標切回各欄（`split_at_columns`）。
2. 找列錨點（`TableSpec.anchor`）：左欄列號（`number`）、彭博代碼（`ticker`，代號與交易所碼換成兩行時取代號那一行）
   或執行價格欄的價格（`price`）。
3. 第 1 列錨點以上是表頭；表頭的格是同一欄上下相接的行（可跨到下一頁；橫跨兩格以上的說明文字不算）。表頭從有
   關鍵字的格的上緣起，更上面的文字不算；格內垂直置中、比列錨點高的那幾行（例：代碼拆三行、中間一行和列號齊平）
   也不算表頭。
4. 其餘每行歸到同一頁 y 中心最近的列錨點；價格欄裡換行的值（`23,425.0000` / `日圓`）跟著第一行的列。所在頁沒有
   列錨點時，只有緊接最後一列的下一頁算最後一列溢過來的部分，隔更遠就讀不出（轉人工覆核，不悄悄丟掉或接上）。
   表頭同樣只接到緊接的下一頁。
5. 每行依 x 歸到表頭的欄；同一格的行由上到下接起來（數字或幣別字換行時接回），同一行內以空白分開的是不同的值。
   一列的價格依欄序排成一串，個數須等於價格欄數（相鄰幾格擠成一行、切不開時仍依序對到各欄）。

錯訊一律稱「價格表」、句型固定；表在文件的位置（`TableSpec.where`）與表頭百分比的寫法（`TableSpec.pct_label`）由上手交出。
代碼從哪裡來、百分比從表頭或定義句讀、沒有 KO 欄時 KO 價等於期初價格、列名、期初定價、`PriceRow.currency` 都留在各上手。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal, NamedTuple

from .. import standard_fields
from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from . import money
from .common import by_column, dec, derived
from .layout import join_text

NUMBER = r"\d[\d,]*(?:\.\d+)?"
CURRENCY = "(?:" + "|".join(sorted(money.CURRENCY_WORDS, key=len, reverse=True)) + ")"
# 一格的值：幣別字在前（`美元 123.4567`）或在後（`123.4567 美元`），也可以沒有
PREFIXED = re.compile(rf"({CURRENCY})\s*({NUMBER})")
SUFFIXED = re.compile(rf"({NUMBER})\s*({CURRENCY})?")
VALUE = re.compile(rf"(?:{CURRENCY})?{NUMBER}(?:{CURRENCY})?")  # 一格剛好一個值（去空白後）
COMPLETE = re.compile(rf"{CURRENCY}{NUMBER}|{NUMBER}{CURRENCY}")  # 帶幣別字的完整一格
PRICE_START = re.compile(r"^\d[\d,]*\.\d+(?!\d|\s*%)")  # 以價格開頭的行（表頭的「60.00% )」不算）
ROW_NUMBER = re.compile(r"^(\d{1,2})(?:\s|$)")  # 左欄列號（可能和名稱同一行：`2 KAWASAKI`）
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9./-]*")
EXCHANGE = re.compile(r"[A-Z]{2}(?:\s+(?:Equity|EQUITY))?")
TICKER_START = re.compile(r"^[A-Z0-9][A-Z0-9./-]*\s+[A-Z]{2}(?![A-Za-z0-9])")
TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9][A-Z0-9./-]*)\s+([A-Z]{2})(?![A-Za-z0-9])")
NUMBER_CHARS = "0123456789,."
NEAR = 20.0  # 和任何表頭格都不重疊的行，離表頭格幾 pt 內仍算那一欄
ADJACENT = 0.6  # 同一格上下兩行的間距上限（行高的倍數）
GAP = 1.0  # 擷取成同一行的相鄰兩格之間至少的間隙（pt）
ALIGN = 0.6  # 擷取成同一行的幾格：字的左緣與其他行的左緣相差幾 pt 內算同一欄的左緣
ANCHOR_WORDS = {"number": "列號", "ticker": "彭博代碼", "price": "執行價格欄的價格"}


@dataclass(frozen=True)
class Column:
    """價格欄：`key`（strike／ko／ki）與表頭關鍵字；`required` 的欄表頭沒有時讀不出。"""

    key: str
    word: str
    required: bool = True


@dataclass(frozen=True)
class TableSpec:
    """一張價格表長什麼樣子（各上手 adapter 宣告）。

    where：錯訊裡的表名，例「第一章第 15 項價格表」「情境分析價格表」。
    initial：期初價格欄的表頭關鍵字（其他欄的「(期初價格之X%)」也含這幾個字，只含它、不含價格欄關鍵字的那一格才是）。
    columns：期初價格以外的價格欄；欄序依表頭的 x 位置，不依這裡的順序。
    anchor：列錨點，`number` 左欄列號、`ticker` 彭博代碼、`price` 執行價格欄的價格（表內沒有列號與代碼時）。
    pct：表頭百分比的寫法（regex，第 1 組為數字）；None 表示表頭沒有百分比。pct_label 是錯訊裡的寫法。
    code：彭博代碼欄的表頭關鍵字；None 表示表內沒有代碼。
    """

    where: str
    initial: str
    columns: tuple[Column, ...]
    anchor: Literal["number", "ticker", "price"]
    pct: str | None = None
    pct_label: str = ""
    code: str | None = None

    def __post_init__(self) -> None:
        if self.anchor == "ticker" and self.code is None:
            raise ValueError("以彭博代碼為列錨點時要宣告代碼欄")


@dataclass(frozen=True)
class TableRow:
    """價格表的一列：各欄價格（含 initial）、彭博代碼（表內沒有代碼時為 None）、名稱、價格旁的幣別字、原文行。"""

    prices: dict[str, Decimal]
    ticker: str | None
    name: str
    currency: str | None
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class PriceTable:
    """讀出的價格表：`columns` 為價格欄（含 initial）依表頭 x 位置的順序，`pcts` 為表頭百分比（沒有寫時不含該欄），
    `heads` 為各欄（價格欄與代碼欄）表頭去空白的文字。"""

    columns: tuple[str, ...]
    pcts: dict[str, Decimal]
    rows: tuple[TableRow, ...]
    heads: dict[str, str]
    header_lines: tuple[Line, ...] = field(repr=False)

    @property
    def prices(self) -> list[dict[str, Decimal]]:
        return [dict(r.prices) for r in self.rows]

    @property
    def tickers(self) -> list[str | None]:
        return [r.ticker for r in self.rows]

    @property
    def names(self) -> list[str]:
        return [r.name for r in self.rows]

    @property
    def currencies(self) -> list[str | None]:
        return [r.currency for r in self.rows]


class _Col(NamedTuple):
    """表頭一欄：x 範圍與表頭的格。"""

    x0: float
    x1: float
    cell: list[Line]


class _Unreadable(Exception):
    def __init__(self, lines: Sequence[Line], note: str):
        super().__init__(note)
        self.lines, self.note = list(lines), note


def read(name: str, lines: Sequence[Line], spec: TableSpec) -> ParsedField:
    """`lines` 為這張表所在的範圍（表的位置由上手決定）；讀不出時錯訊寫出原因與那幾行。"""
    if not lines:
        return ParsedField.missing(name, f"找不到{spec.where}")
    lines = split_at_columns(list(lines))
    try:
        table = _read(lines, spec)
    except _Unreadable as e:
        return ParsedField.invalid(name, e.lines, e.note)
    return ParsedField.present(name, table, lines)


def price_rows(
    table: ParsedField,
    tickers: Sequence[str] | None = None,
    *,
    names: Sequence[str | None] | None = None,
    currency: bool = False,
) -> ParsedField:
    """價格表 → 標準欄位 `underlying_prices`（順序同表上的列）；表有問題時沿用它的狀態。

    tickers：代碼不在表內時由上手依列序交出（例：另一張標的表）；預設用表上的代碼。names：列名（預設表上的名稱）。
    currency：`PriceRow.currency` 是否帶價格旁的幣別字。列數須相同，由上手先檢查。"""
    name = "underlying_prices"
    if not table.ok:
        return derived(name, table)
    t: PriceTable = table.value
    codes = list(tickers) if tickers is not None else t.tickers
    labels = list(names) if names is not None else [r.name or None for r in t.rows]
    rows = tuple(
        standard_fields.PriceRow(
            code,
            dict(r.prices),
            tuple(Evidence.of(ln) for ln in r.lines),
            name=label,
            currency=r.currency if currency else None,
        )
        for r, code, label in zip(t.rows, codes, labels, strict=True)
    )
    return ParsedField(name, FieldStatus.PRESENT, rows, list(table.evidence))


def underlyings(table: ParsedField) -> ParsedField:
    """表上的彭博代碼 → 標準欄位 `underlyings`（依表上順序）；表有問題時沿用它的狀態。"""
    if not table.ok:
        return derived("underlyings", table)
    return ParsedField("underlyings", FieldStatus.PRESENT, list(table.value.tickers), list(table.evidence))


# ---------------------------------------------------------------- 讀法


def _read(lines: list[Line], spec: TableSpec) -> PriceTable:
    first = _first_anchor(lines, spec)
    header, body = _split(lines, first, spec)
    cells = _cells(header)
    cols, pcts, heads = _columns(cells, header, spec)
    anchors = _anchors(lines, body, cols, spec)
    if _page_key(min(_keyword_lines(cells, spec), key=_page_key)) < (anchors[0].page - 1, 0):
        raise _Unreadable(header, f"{spec.where}跨頁時讀不到該頁的列")
    others = [c for c in cells if all(c is not col.cell for col in cols.values())]
    rows_lines = _rows(body, anchors, cols, others, spec)
    order = sorted(cols, key=lambda k: cols[k].x0)
    rows = [_row(k, lns, anchors[k - 1], others, cols, order, spec) for k, lns in enumerate(rows_lines, start=1)]
    return PriceTable(
        columns=tuple(c for c in order if c != "code"),
        pcts=pcts,
        rows=tuple(rows),
        heads=heads,
        header_lines=tuple(header),
    )


def _page_key(ln: Line) -> tuple[int, float]:
    return ln.page, ln.y0


def _reading(ln: Line) -> tuple[int, float, float]:
    """由上到下、同一列由左到右。"""
    return ln.page, ln.y0, ln.x0


def _yc(ln: Line) -> float:
    return (ln.y0 + ln.y1) / 2


def _overlap(a: Line, b: Line) -> float:
    return _span_overlap(a, b.x0, b.x1)


def _span_overlap(ln: Line, x0: float, x1: float) -> float:
    """這一行與 x 範圍重疊的寬度（不重疊時為負，絕對值是相隔的距離）。"""
    return min(ln.x1, x1) - max(ln.x0, x0)


def _first_anchor(lines: list[Line], spec: TableSpec) -> Line:
    """第 1 列的列錨點（決定表頭的下緣）；`price` 先取最上面的價格行（還不知道執行價格欄在哪）。"""
    found = _candidates(lines, spec)
    if not found:
        raise _Unreadable(lines, f"{spec.where}找不到第 1 列（{ANCHOR_WORDS[spec.anchor]}）")
    return found[0]


def _candidates(lines: list[Line], spec: TableSpec) -> list[Line]:
    """可能是列錨點的行，依頁與 y 排序。列號、代碼取最左邊那一串（同欄的其他數字或代碼不算）。"""
    ordered = sorted(lines, key=_page_key)
    if spec.anchor == "price":
        return [ln for ln in ordered if PRICE_START.match(ln.text.strip())]
    if spec.anchor == "number":
        ones = [ln for ln in ordered if (m := ROW_NUMBER.match(ln.text.strip())) and m[1] == "1"]
        if not ones:
            return []
        left = min(ones, key=lambda x: x.x0)
        marks: list[Line] = []
        for ln in ordered:
            m = ROW_NUMBER.match(ln.text.strip())
            if m and int(m[1]) == len(marks) + 1 and abs(ln.x0 - left.x0) < 10:
                marks.append(ln)
        return marks
    codes = [
        ln
        for ln in ordered
        if TICKER_START.match(ln.text.strip())
        or (  # 代號與交易所碼換成兩行（`AVGO` / `UW`）：代號那一行是列錨點
            SYMBOL.fullmatch(ln.text.strip())
            and any(EXCHANGE.fullmatch(b.text.strip()) and _adjacent(ln, b) for b in lines)
        )
    ]
    if not codes:
        return []
    left = min(codes, key=lambda x: x.x0)
    return [ln for ln in codes if _overlap(ln, left) > 0]


def _split(lines: list[Line], first: Line, spec: TableSpec) -> tuple[list[Line], list[Line]]:
    """第 1 列錨點的上緣以上是表頭，以下是列。表頭從有關鍵字的那幾格的上緣起，更上面的文字（範圍裡表前的說明、
    上一頁頁底的註腳）不算。格內垂直置中、比列錨點高的行（沒有表頭關鍵字、從表頭最後一行之下才開始、緊接著下面
    的列）仍屬於列。"""
    top = (first.page, first.y0)
    above = [ln for ln in lines if (ln.page, _yc(ln)) < top]
    below = [ln for ln in lines if (ln.page, _yc(ln)) >= top]
    cells = _cells(above)
    keyed = [c for c in cells if _has_keyword(c, spec)]
    if not keyed:
        return above, below
    start = min((ln.page, ln.y0) for c in keyed for ln in c)
    bottom = max((ln.page, ln.y1) for c in keyed for ln in c)
    attached: list[Line] = []
    for c in cells:
        if c in keyed or (c[0].page, c[0].y0) <= bottom:
            continue
        if any(_adjacent(c[-1], b) for b in below):
            attached += c
    header = [ln for ln in above if ln not in attached and (ln.page, ln.y1) > start]
    return header, below + attached


def _adjacent(upper: Line, lower: Line) -> bool:
    """同一頁、x 重疊、上下相接（間距不到行高的 `ADJACENT` 倍，或上下重疊）。"""
    if upper is lower or upper.page != lower.page or _overlap(upper, lower) <= 0.5:
        return False
    height = max(upper.y1 - upper.y0, lower.y1 - lower.y0)
    return upper.y0 <= lower.y0 and lower.y0 - upper.y1 <= ADJACENT * height and lower.y1 > upper.y0


def _cells(lines: list[Line]) -> list[list[Line]]:
    """表頭的格：同一欄上下相接的行；可跨到下一頁（上一頁頁底的一行接下一頁頁首的一行）。每格的行依頁與 y 排序。"""
    edges = {}
    for ln in lines:
        lo, hi = edges.get(ln.page, (ln.y0, ln.y1))
        edges[ln.page] = (min(lo, ln.y0), max(hi, ln.y1))
    parent = list(range(len(lines)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(lines):
        for j in range(i + 1, len(lines)):
            if _same_cell(a, lines[j], edges) and not _spans(a, lines[j], lines) and not _spans(lines[j], a, lines):
                parent[find(i)] = find(j)
    groups: dict[int, list[Line]] = {}
    for i, ln in enumerate(lines):
        groups.setdefault(find(i), []).append(ln)
    cells = [sorted(g, key=_reading) for g in groups.values()]
    return sorted(cells, key=lambda c: min(x.x0 for x in c))


def _spans(wide: Line, other: Line, lines: list[Line]) -> bool:
    """`wide` 橫跨 `other` 那一列的兩格以上（例：表前的說明文字、註腳）：不是同一格。"""
    half = (other.y1 - other.y0) / 2
    row = [x for x in lines if x is not wide and x.page == other.page and abs(_yc(x) - _yc(other)) < half]
    return sum(_overlap(wide, x) > 0.5 for x in row) >= 2


def _same_cell(a: Line, b: Line, edges: dict[int, tuple[float, float]]) -> bool:
    upper, lower = (a, b) if (a.page, a.y0) <= (b.page, b.y0) else (b, a)
    if upper.page == lower.page:
        return _adjacent(upper, lower)
    if lower.page != upper.page + 1 or _overlap(upper, lower) <= 0.5:
        return False
    # 跨頁：上一頁最下面的那一列接下一頁最上面的那一列
    reach = ADJACENT * max(upper.y1 - upper.y0, lower.y1 - lower.y0)
    return upper.y1 >= edges[upper.page][1] - reach and lower.y0 <= edges[lower.page][0] + reach


def _text(cell: Sequence[Line]) -> str:
    return "".join(squash(ln.text) for ln in cell)


def _words(spec: TableSpec) -> list[str]:
    return [spec.initial, *(c.word for c in spec.columns), *([spec.code] if spec.code else [])]


def _has_keyword(cell: list[Line], spec: TableSpec) -> bool:
    text = _text(cell)
    return any(w in text for w in _words(spec))


def _keyword_lines(cells: list[list[Line]], spec: TableSpec) -> list[Line]:
    return [ln for c in cells if _has_keyword(c, spec) for ln in c]


def _columns(
    cells: list[list[Line]], header: list[Line], spec: TableSpec
) -> tuple[dict[str, _Col], dict[str, Decimal], dict[str, str]]:
    """表頭各欄（價格欄、期初價格欄、代碼欄）的格與表頭百分比。"""
    out: dict[str, list[Line]] = {}
    pcts: dict[str, Decimal] = {}
    for col in spec.columns:
        hit = [c for c in cells if col.word in _text(c)]
        if not hit and not col.required:
            continue
        out[col.key] = _one(hit, col.word, header, spec)
        if spec.pct is not None:
            m = re.search(spec.pct, _text(out[col.key]))
            if m is None:
                raise _Unreadable(out[col.key], f"{spec.where}表頭「{col.word}」讀不到「{spec.pct_label}」")
            pcts[col.key] = Decimal(m[1])
    words = [c.word for c in spec.columns]
    out["initial"] = _one(
        [c for c in cells if spec.initial in _text(c) and not any(w in _text(c) for w in words)],
        spec.initial,
        header,
        spec,
    )
    if spec.code is not None:
        out["code"] = _one([c for c in cells if spec.code in _text(c)], spec.code, header, spec)
    cols = {k: _Col(min(x.x0 for x in c), max(x.x1 for x in c), c) for k, c in out.items()}
    return cols, pcts, {k: _text(c) for k, c in out.items()}


def _one(hit: list[list[Line]], word: str, header: list[Line], spec: TableSpec) -> list[Line]:
    if len(hit) > 1:
        raise _Unreadable(header, f"{spec.where}表頭「{word}」出現多欄")
    if not hit:
        raise _Unreadable(header, f"{spec.where}表頭找不到「{word}」欄")
    return hit[0]


def _anchors(lines: list[Line], body: list[Line], cols: dict[str, _Col], spec: TableSpec) -> list[Line]:
    """各列的列錨點；`price` 為執行價格欄的價格行。"""
    if spec.anchor != "price":
        return [ln for ln in _candidates(lines, spec) if ln in body]
    strike = cols["strike"]
    found = [
        ln
        for ln in sorted(body, key=_page_key)
        if PRICE_START.match(ln.text.strip()) and _span_overlap(ln, strike.x0, strike.x1) > 0
    ]
    if not found:
        raise _Unreadable(body, f"{spec.where}找不到第 1 列（{ANCHOR_WORDS[spec.anchor]}）")
    return found


def _rows(
    body: list[Line], anchors: list[Line], cols: dict[str, _Col], others: list[list[Line]], spec: TableSpec
) -> list[list[Line]]:
    """每行歸到同一頁 y 中心最近的列；所在頁沒有列時，只有緊接最後一列的下一頁算最後一列。價格欄裡換行的值
    （`23,425.0000` / `日圓`、`美元` / `123.4567`、`19,740.000` / `0日圓`）跟著第一行所在的列：列距小時，換下來的
    那一行可能離下一列一樣近。"""
    last = anchors[-1].page
    row_of: dict[int, int] = {}
    for i, ln in enumerate(body):
        same = [k for k, a in enumerate(anchors) if a.page == ln.page]
        if same:
            row_of[i] = min(same, key=lambda j: abs(_yc(anchors[j]) - _yc(ln)))
        elif ln.page == last + 1:
            row_of[i] = len(anchors) - 1
        else:
            raise _Unreadable([ln], f"{spec.where}跨頁時讀不到該頁的列")
    prices = {k for k in cols if k != "code"}
    column = {i: _column_of(ln, cols, others) for i, ln in enumerate(body)}
    head: dict[int, int] = {}  # 價格欄的行 → 它那一格的第一行
    text: dict[int, str] = {}  # 格的第一行 → 目前接起來的文字
    for i in sorted((i for i in column if column[i] in prices), key=lambda i: _page_key(body[i])):
        ln = body[i]
        uppers = [j for j in head if column[j] == column[i] and _adjacent(body[j], ln)]
        for j in sorted(uppers, key=lambda j: body[j].y1, reverse=True):
            joined = text[head[j]] + squash(ln.text)
            if not COMPLETE.fullmatch(text[head[j]]) and VALUE.fullmatch(joined):
                head[i] = head[j]
                text[head[j]] = joined
                row_of[i] = row_of[head[j]]
                break
        else:
            head[i] = i
            text[i] = squash(ln.text)
    rows: list[list[Line]] = [[] for _ in anchors]
    for i, ln in enumerate(body):
        rows[row_of[i]].append(ln)
    return rows


def _column_of(ln: Line, cols: dict[str, _Col], others: list[list[Line]]) -> str | None:
    """這一行屬於哪一欄（價格欄、期初價格欄、代碼欄；其他表頭欄與表頭沒有的欄為 None）：x 重疊最多的表頭格；
    都不重疊時取 `NEAR` pt 內最近的表頭格（例：表頭沒有欄名的列號、情境表的「連結標的1」不算到期初價格欄）。"""
    spans: list[tuple[str | None, float, float]] = [(k, c.x0, c.x1) for k, c in cols.items()]
    spans += [(None, min(x.x0 for x in c), max(x.x1 for x in c)) for c in others]
    best = max(spans, key=lambda s: _span_overlap(ln, s[1], s[2]))
    if _span_overlap(ln, best[1], best[2]) > 0:
        return best[0]
    return best[0] if -_span_overlap(ln, best[1], best[2]) < NEAR else None


def _row(
    k: int,
    lns: list[Line],
    anchor: Line,
    others: list[list[Line]],
    cols: dict[str, _Col],
    order: list[str],
    spec: TableSpec,
) -> TableRow:
    by_col: dict[str | None, list[Line]] = {}
    for ln in (w for x in lns for w in _words_of(x)):
        by_col.setdefault(_column_of(ln, cols, others), []).append(ln)
    ordered = sorted(lns, key=_reading)
    keys = [c for c in order if c != "code"]
    found: list[tuple[Decimal, str | None]] = []
    for col in keys:
        found += _values(by_col.get(col, []))
    if len(found) != len(keys):
        raise _Unreadable(ordered, f"{spec.where}第 {k} 列有 {len(found)} 個價格，與 {len(keys)} 欄不符")
    currencies = {c for _, c in found}
    if len(currencies) > 1:
        raise _Unreadable(ordered, f"{spec.where}第 {k} 列各格的幣別字不同")
    ticker = None
    if spec.code is not None:
        text = join_text(sorted(by_col.get("code", []), key=_reading))
        hits = TICKER.findall(text)
        if len(hits) != 1:
            raise _Unreadable(ordered, f"{spec.where}第 {k} 列讀不到彭博代碼")
        ticker = f"{hits[0][0]} {hits[0][1]}"
    left = min(c.x0 for key, c in cols.items() if key in ("code", "initial"))
    label = [ln for ln in lns if ln.xc < left and ln is not anchor and not re.fullmatch(r"\d{1,2}", ln.text.strip())]
    name = re.sub(r"^\d{1,2}\s+", "", join_text(by_column(label))).strip()
    return TableRow(
        prices=dict(zip(keys, (v for v, _ in found), strict=True)),
        ticker=ticker,
        name=name,
        currency=currencies.pop(),
        lines=tuple(ordered),
    )


def _words_of(ln: Line) -> list[Line]:
    """一行依空白切成詞（有字座標時）：擠成一行、切欄時找不到對齊左緣的幾格（`340.74 美元 234.5995 美`），
    每個詞仍各自歸到所在的欄。"""
    spans = ln.aligned_char_spans
    if not spans:
        return [ln]
    out = []
    for m in re.finditer(r"\S+", ln.text):
        i, j = m.span()
        out.append(replace(ln, x0=spans[i][0], x1=spans[j - 1][1], text=m[0], char_spans=spans[i:j]))
    return out


def _values(cell: list[Line]) -> list[tuple[Decimal, str | None]]:
    """一格的值（價格與旁邊的幣別字）：上下兩行接起來（換行的數字或幣別字接回）；同一行內以空白分開的、左右並排
    的兩行（例：價格旁的上標註腳）是不同的值。"""
    text, prev = "", None
    for ln in sorted(cell, key=_reading):
        side_by_side = prev is not None and prev.page == ln.page and abs(_yc(prev) - _yc(ln)) < (ln.y1 - ln.y0) / 2
        text += (" " if side_by_side else "") + ln.text.strip()
        prev = ln
    if re.match(CURRENCY, text):
        return [(dec(m[2]), m[1]) for m in PREFIXED.finditer(text)]
    return [(dec(m[1]), m[2]) for m in SUFFIXED.finditer(text)]


def split_at_columns(lines: list[Line], tol: float = ALIGN) -> list[Line]:
    """一行橫跨好幾欄（PDF 把相鄰幾格擷取成同一行）時切回各欄。欄的左緣取這些行中至少兩行共有的 x0；空白之後的字
    也可以對齊只有一行的 x0（例：欄內只有一行表頭續行）。行內某個字的左緣落在欄的左緣上、且和前一個字之間有空白或
    間隙時從那裡切開；字和前一個字相接（數字中間）或是價格後面的幣別字時不切。沒有字座標（`Line.aligned_char_spans`）
    的行不動。"""
    starts = sorted(ln.x0 for ln in lines)
    shared = [
        x
        for i, x in enumerate(starts)
        if (i and x - starts[i - 1] <= tol) or (i + 1 < len(starts) and starts[i + 1] - x <= tol)
    ]

    def cut(ln: Line, spans: tuple[tuple[float, float], ...], i: int) -> bool:
        prev = ln.text[i - 1]
        if ln.text[i].isspace():
            return False
        if not prev.isspace() and spans[i][0] - spans[i - 1][1] < GAP:
            return False  # 字和前一個字相接：同一格的字（例：數字中間、表頭「期初價格」的「初」恰好對齊某一格的左緣）
        before = ln.text[:i].rstrip()
        if re.match(CURRENCY, ln.text[i:]) and before and before[-1] in NUMBER_CHARS:
            return False  # 價格後面的幣別字（`457.0000 美元`）屬於同一格
        edges = starts if prev.isspace() else shared
        return any(abs(spans[i][0] - e) <= tol for e in edges)

    out: list[Line] = []
    for ln in lines:
        spans = ln.aligned_char_spans
        cuts = [i for i in range(1, len(spans)) if cut(ln, spans, i)]
        if not cuts:
            out.append(ln)
            continue
        for a, b in zip([0, *cuts], [*cuts, len(spans)], strict=True):
            keep = [i for i in range(a, b) if not ln.text[i].isspace()]
            if keep:
                i, j = keep[0], keep[-1] + 1
                out.append(replace(ln, x0=spans[i][0], x1=spans[j - 1][1], text=ln.text[i:j], char_spans=spans[i:j]))
    return out
