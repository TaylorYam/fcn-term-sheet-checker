"""BARC 說明書第一章 §13 的配息表與自動提前出場表（範本規格 §4.2、§4.3、§5.2、§5.3）。

表格是「每格一行文字」攤平輸出：先找表頭（同頁、y 相近的一組表頭文字），
列首為 x<80 的期別 t（依 1、2、3… 連號），儲存格依表頭 x 中心指派；表格可能跨頁。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import AutocallSchedule
from ..text import squash
from .layout import Document, Span, parse_date

# 表頭文字 → 欄位鍵
HEADER_KEYS = {
    "配息評價日t": "valuation",
    "評價日t": "valuation",
    "配息支付日t": "payment",
    "期始日(含)t": "start",
    "期末日(含)t": "end",
    "自動提前出場評價日": "ko_valuation",
    "自動提前出場觸發百分比": "trigger",
    "指定提前現金贖回日": "early_redemption",
}
_ROW_MAX_X = 80.0
_STOP_MAX_X = 100.0
_HEADER_DY = 15.0
_CELL_DX = 60.0
_ROW_DY = 4.0
_PCT_RE = re.compile(r"^([\d.]+)%$")

NA = "N/A"


@dataclass
class Cell:
    text: str
    value: dt.date | Decimal | str | None  # 日期、百分比、"N/A"；無法辨識為 None
    noncallable: bool = False  # 註記「(非自動提前出場評價日)」


@dataclass
class ScheduleRow:
    t: int
    cells: dict[str, Cell]
    lines: list[Line]

    def get(self, key: str):
        c = self.cells.get(key)
        return c.value if c else None


@dataclass
class Table:
    kind: str  # coupon（配息評價日／評價日表）、combined（觀察期合併表）、ko_period、ko_fixed
    columns: list[str]
    rows: list[ScheduleRow]
    header: list[Line]
    issues: list[str] = field(default_factory=list)


def _cell(text: str) -> Cell:
    if text == NA:
        return Cell(text, NA)
    if "非自動提" in text:
        return Cell(text, parse_date(text), noncallable=True)
    m = _PCT_RE.match(text)
    if m:
        return Cell(text, Decimal(m.group(1)))
    return Cell(text, parse_date(text))


def _header_groups(lines: list[Line]) -> list[tuple[int, list[tuple[int, Line]]]]:
    """回傳 [(表頭最後一行的索引, [(索引, 表頭行)…])]；同頁且 y 相差 ≤ 15 pt 視為同一組表頭。"""
    groups: list[list[tuple[int, Line]]] = []
    for i, ln in enumerate(lines):
        if ln.text not in HEADER_KEYS:
            continue
        g = groups[-1] if groups else None
        if g and g[-1][1].page == ln.page and abs(g[-1][1].y0 - ln.y0) <= _HEADER_DY and i - g[-1][0] <= 6:
            g.append((i, ln))
        else:
            groups.append([(i, ln)])
    return [(g[-1][0], g) for g in groups if len(g) >= 2]


def _classify(keys: set[str]) -> str | None:
    if {"ko_valuation", "trigger", "early_redemption"} <= keys:
        return "ko_fixed"
    if {"start", "end", "trigger"} <= keys:
        return "ko_period"
    if {"start", "end", "payment"} <= keys:
        return "combined"
    if {"valuation", "payment"} <= keys:
        return "coupon"
    return None


def _rows(lines: list[Line], after: int, header: list[Line]) -> tuple[list[ScheduleRow], list[str]]:
    cols = [(HEADER_KEYS[h.text], h) for h in header]
    header_ids = {id(h) for h in header}
    rows: list[ScheduleRow] = []
    issues: list[str] = []
    expect = 1
    i = after + 1
    while i < len(lines):
        ln = lines[i]
        if ln.x0 < _ROW_MAX_X and ln.text == str(expect):
            row_lines = [ln]
            cells: dict[str, Cell] = {}
            j = i + 1
            # 同列儲存格：直到下一個列首或停止行；換行的註記（y 不同）略過
            while j < len(lines) and lines[j].x0 >= _STOP_MAX_X:
                j += 1
            for c in lines[i + 1 : j]:
                if c.page != ln.page or abs(c.y0 - ln.y0) > _ROW_DY:
                    continue
                key, h = min(cols, key=lambda kv: abs(kv[1].xc - c.xc))
                if abs(h.xc - c.xc) > _CELL_DX or key in cells:
                    issues.append(f"第 {expect} 期儲存格「{c.text}」無法對應到唯一欄位")
                else:
                    cells[key] = _cell(c.text)
                row_lines.append(c)
            missing = [k for k, _ in cols if k not in cells]
            bad = [k for k, c in cells.items() if c.value is None]
            if missing or bad:
                issues.append(f"第 {expect} 期缺少或無法辨識的欄位：{'、'.join(missing + bad)}")
            rows.append(ScheduleRow(expect, cells, row_lines))
            expect += 1
            i = j
            continue
        if ln.x0 < _STOP_MAX_X and id(ln) not in header_ids:
            break  # 下一個子項、說明文字或下一張表的表頭
        i += 1
    return rows, issues


def find_tables(doc: Document, art13: Span | None) -> list[Table]:
    """§13 第 (6) 項起至第 (8) 項前的所有表格。"""
    subs = doc.subitems(art13)
    if 6 not in subs or 8 not in subs:
        return []
    lines = doc.lines[subs[6].start : subs[8].start]
    tables: list[Table] = []
    for last, group in _header_groups(lines):
        header = [ln for _, ln in group]
        keys = [HEADER_KEYS[h.text] for h in header]
        kind = _classify(set(keys))
        if kind is None or len(set(keys)) != len(keys):
            continue
        rows, issues = _rows(lines, last, header)
        tables.append(Table(kind, keys, rows, header, issues))
    return tables


def _table_field(name: str, tables: list[Table], kinds: tuple[str, ...]) -> ParsedField:
    hits = [t for t in tables if t.kind in kinds]
    if not hits:
        return ParsedField.missing(name, "第 13 條找不到對應表格")
    if len(hits) > 1:
        return ParsedField.ambiguous(name, [], [h for t in hits for h in t.header], "第 13 條出現多張同類表格")
    t = hits[0]
    ev = t.header + [ln for r in t.rows for ln in r.lines]
    if not t.rows:
        return ParsedField.invalid(name, t.header, "表格沒有任何列")
    if t.issues:
        return ParsedField.invalid(name, ev, "；".join(t.issues))
    return ParsedField.present(name, t, ev)


def coupon_table(tables: list[Table]) -> ParsedField:
    """配息評價日／支付日表；觀察期合併表以期末日為評價日。"""
    f = _table_field("coupon_table", tables, ("coupon", "combined"))
    if f.ok and f.value.kind == "combined":  # 複製一份，不改動兼作提前出場表的同一張表
        t = f.value
        rows = [ScheduleRow(r.t, {**r.cells, "valuation": r.cells["end"]}, r.lines) for r in t.rows]
        f.value = Table(t.kind, t.columns, rows, t.header, list(t.issues))
    return f


def ko_table(tables: list[Table], ko_observation: ParsedField) -> ParsedField:
    """自動提前出場表。Daily 非記憶式沒有獨立的提前出場表，觀察期合併表兼作提前出場表。"""
    f = _table_field("ko_table", tables, ("ko_fixed", "ko_period"))
    if f.status.value == "missing":
        combined = [t for t in tables if t.kind == "combined"]
        if len(combined) == 1:
            return _table_field("ko_table", combined, ("combined",))
        if ko_observation.ok and ko_observation.value == "P":
            # 只有 Period End 非記憶式的「評價日t」表兼作提前出場評價日；記憶式必須另有提前出場表
            coupon = [t for t in tables if t.kind == "coupon" and any(h.text == "評價日t" for h in t.header)]
            if len(coupon) == 1:
                return _table_field("ko_table", coupon, ("coupon",))  # 評價日表兼作提前出場評價日
    return f


def guaranteed_periods(ko: ParsedField) -> ParsedField:
    """由提前出場表推得保證配息期（核對規則 §3.3）。

    期間型（含觀察期合併表）：第 1 期期始日有日期 → 0；否則 = 第一個期末日有日期的期別 G
    （第 1～G−1 期期始日、期末日皆為 N/A）。
    定日型：評價日表 = 開頭註記「(非自動提前出場評價日)」的期數；自動提前出場評價日表 = 0。
    可提前出場的期別之後又出現不可提前出場的期別 → invalid。
    """
    name = "guaranteed_periods"
    if not ko.ok:
        return ParsedField(name, ko.status, None, list(ko.evidence), note=ko.note)
    t: Table = ko.value
    if t.kind in ("ko_period", "combined"):
        callable_ = [isinstance(r.get("end"), dt.date) for r in t.rows]
        first = t.rows[0]
        if isinstance(first.get("start"), dt.date):
            g = 0
        else:
            g = callable_.index(True) + 1 if True in callable_ else None
            before = t.rows[: (g or len(t.rows)) - 1]
            if g is None or any(r.get("start") != NA or r.get("end") != NA for r in before):
                return ParsedField.invalid(name, t.header, "提前出場表的 N/A 期別不屬於已知型態")
        start = max(g - 1, 0)
        if not all(callable_[start:]):
            return ParsedField.invalid(name, t.header, "可提前出場的期別之後又出現不可提前出場的期別")
        anchor = t.rows[start]
    else:
        key = "ko_valuation" if t.kind == "ko_fixed" else "valuation"
        flags = [bool(r.cells[key].noncallable) or r.get(key) == NA for r in t.rows]
        g = flags.index(False) if False in flags else len(flags)
        if any(flags[g:]) or g == len(flags):
            return ParsedField.invalid(name, t.header, "不可提前出場的期別不是連續出現在開頭")
        anchor = t.rows[g]
    return ParsedField.present(name, g, t.header + anchor.lines)


def _observation_block(doc: Document, art13: Span | None) -> list[Line] | ParsedField:
    """§13(7)「自動提前出場觀察期：」定義句（Daily Memory）的文字行；找不到時回傳 missing 欄位。"""
    subs = doc.subitems(art13)
    if 7 not in subs:
        return ParsedField.missing("observation_period", "找不到第 13 條第 (7) 項")
    lines = doc.span_lines(subs[7])
    starts = [i for i, ln in enumerate(lines) if ln.text.startswith("自動提前出場觀察期：")]
    if len(starts) != 1:
        return ParsedField.missing("observation_period", "第 13 條第 (7) 項沒有「自動提前出場觀察期：」定義句")
    i = starts[0]
    block = [lines[i]]
    for ln in lines[i + 1 : i + 6]:
        if abs(ln.x0 - lines[i].x0) > 3:
            break
        block.append(ln)
    return block


def _renamed(pf: ParsedField, name: str) -> ParsedField:
    return ParsedField(name, pf.status, pf.value, list(pf.evidence), pf.candidates, pf.note)


def guaranteed_periods_text(doc: Document, art13: Span | None) -> ParsedField:
    """§13(7)「自動提前出場觀察期：」定義句（Daily Memory）。第 N 個 → N；t 等於 1 至 n → 0。"""
    name = "guaranteed_periods_text"
    block = _observation_block(doc, art13)
    if isinstance(block, ParsedField):
        return _renamed(block, name)
    text = squash("".join(ln.text for ln in block))
    m = re.search(r"就(?:首個|第\S{1,3}個)（即t等於(\d+)的情況）自動提前出場觀察期而言，指期末日(\d+)", text)
    if m and m.group(1) == m.group(2):
        return ParsedField.present(name, int(m.group(1)), block)
    if re.search(r"就t等於1至\d+的情況而言，則指自相關期始日起（含）至相關期末日止（含）", text):
        return ParsedField.present(name, 0, block)
    return ParsedField.invalid(name, block, "「自動提前出場觀察期」定義句不屬於已知寫法")


def observation_t_ranges(doc: Document, art13: Span | None) -> ParsedField:
    """§13(7) 定義句中各段 t 的起訖：「t 等於 g」→ (g, g)；「t 等於 a 至 b」→ (a, b)，依出現順序。"""
    name = "observation_t_ranges"
    block = _observation_block(doc, art13)
    if isinstance(block, ParsedField):
        return _renamed(block, name)
    text = squash("".join(ln.text for ln in block))
    ranges = [(int(a), int(b or a)) for a, b in re.findall(r"t等於(\d+)(?:至(\d+))?", text)]
    if not ranges:
        return ParsedField.invalid(name, block, "定義句找不到「t 等於…」")
    return ParsedField.present(name, ranges, block)


def autocall_schedule(f: Callable[[str], ParsedField]) -> ParsedField:
    """由 §13 提前出場表推得提前出場排程（第一個可提前出場期、各期比價日）。

    D：Non-Call = 保證配息期 G（第 G 期期始日 N/A、期末日起可提前出場），比價日 = 各期期末日。
    P：Non-Call = G + 1，比價日 = 各期自動提前出場評價日（或評價日表的評價日）。
    D 型第 1 期期始日就有日期（G = 0，從第一天開始比價）尚無已確認的填法，轉人工覆核。
    """
    name = "autocall_schedule"
    obs, ko, g, text = (f(k) for k in ("ko_observation", "ko_table", "guaranteed_periods", "guaranteed_periods_text"))
    for pf in (obs, ko, g):
        if not pf.ok:
            return ParsedField(name, pf.status, None, list(pf.evidence), note=pf.note)
    if text.ok and text.value != g.value:
        return ParsedField(
            name,
            FieldStatus.INVALID,
            None,
            g.evidence + text.evidence,
            note="提前出場表與 §13(7) 定義句推得的保證配息期不同",
        )
    table: Table = ko.value
    if obs.value == "D":
        if g.value == 0:
            return ParsedField(
                name,
                FieldStatus.INVALID,
                None,
                list(g.evidence),
                note="第 1 期期始日就有日期（從第一天開始比價），比價日填法尚未確認",
            )
        first, key = g.value, "end"
    else:
        first = g.value + 1
        key = "ko_valuation" if table.kind == "ko_fixed" else "valuation"
    dates = {r.t: r.get(key) for r in table.rows if r.t >= first}
    bad = [t for t, d in dates.items() if not isinstance(d, dt.date)]
    if bad:
        return ParsedField(
            name,
            FieldStatus.INVALID,
            None,
            [Evidence.of(h) for h in table.header],
            note="以下期別的比價日無法辨識：" + "、".join(f"第 {t} 期" for t in bad),
        )
    sched = AutocallSchedule(obs.value, first, len(table.rows), dates)
    return ParsedField(name, FieldStatus.PRESENT, sched, list(g.evidence))
