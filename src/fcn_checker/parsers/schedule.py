"""日期表的共用推導：各上手讀出日期表後，由這裡推得提前出場排程（Issue #289）。

上手只交出三件上手知識：第一個可提前出場期 k（`first`，各上手自己讀）、比價日在日期表的哪一欄（`column`）、
末期比價日取表的末列還是最終評價日（`final`）。KO 觀察方式由上手讀好交進來。

- D 期間每日觀察：比價日只放第 k 期與末期（回填與投資人須知只讀這兩期，Issue #289 決定 7）。
- P 每期定日觀察：比價日放第 k 期起的每一期。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import AutocallSchedule, DateRow, DateTable

SCHEDULE_KEYS = ("ko_observation", "first_callable_period", "autocall_schedule")


def _renamed(key: str, pf: ParsedField) -> ParsedField:
    return ParsedField(key, pf.status, pf.value, list(pf.evidence), pf.candidates, pf.note)


def failed(pf: ParsedField) -> dict[str, ParsedField]:
    """依賴的欄位有問題：三個欄位都帶它的狀態、證據與說明（讀不到、歧義、不合法或不適用）。"""
    return {key: ParsedField(key, pf.status, None, list(pf.evidence), pf.candidates, pf.note) for key in SCHEDULE_KEYS}


def autocall_schedule(
    observation: ParsedField,
    first: ParsedField,
    table: ParsedField,
    column: str | None = None,
    final: ParsedField | None = None,
) -> dict[str, ParsedField]:
    """KO 觀察方式、第一個可提前出場期與提前出場排程（`SCHEDULE_KEYS` → 欄位）。

    `table` 為標準欄位 `schedule_table`（`DateTable`）；`column` 為比價日所在的 `DateRow` 欄，沒給時 D 取期末日（`end`）、
    P 取觀察日（`observation`）；
    `final` 有給時末期比價日取最終評價日，否則取表的末列。依賴的欄位第一個有問題的決定三個欄位的狀態；
    k 不在第 1～n 期之間、需要的比價日空白時三個欄位都是不合法。
    """
    deps = [observation, first, table] + ([final] if final is not None else [])
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return failed(bad)
    t: DateTable = table.value
    k, n = first.value, len(t.rows)
    ev = list(first.evidence) + list(table.evidence)
    if not 1 <= k <= n:
        note = f"第一個可提前出場期（第 {k} 期）不在{t.where}的第 1～{n} 期之間"
        return {key: ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note) for key in SCHEDULE_KEYS}
    daily = observation.value == "D"
    column = column or ("end" if daily else "observation")
    periods = (k, n) if daily else range(k, n + 1)
    dates: dict[int, dt.date | None] = {p: getattr(t.rows[p - 1], column) for p in periods}
    if final is not None:
        dates[n] = final.value
    blank = [p for p, d in dates.items() if d is None]
    if blank:
        note = f"{t.where}以下期別沒有比價日：" + "、".join(f"第 {p} 期" for p in blank)
        return {key: ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note) for key in SCHEDULE_KEYS}
    sched = AutocallSchedule(observation.value, k, n, {p: d for p, d in dates.items() if d is not None})
    return {
        "ko_observation": _renamed("ko_observation", observation),
        "first_callable_period": _renamed("first_callable_period", first),
        "autocall_schedule": ParsedField("autocall_schedule", FieldStatus.PRESENT, sched, ev),
    }


def rows_of(
    rows: Sequence[Mapping[str, Any]], observation: str | None, row_lines: Sequence[Sequence[Line]] = ()
) -> tuple[DateRow, ...]:
    """上手日期表的 dict 列（`period` 與 `start`／`end`／`payment`，沒有的鍵為空）→ `DateRow`；觀察日取 `observation` 這一欄
    （例：`end`、`pricing`、`valuation`；None 為表上沒有觀察日）。`row_lines` 為各列原文行（證據），沒有時不帶。"""
    lines = list(row_lines) or [()] * len(rows)
    return tuple(
        DateRow(
            r["period"],
            start=r.get("start"),
            end=r.get("end"),
            observation=r.get(observation) if observation else None,
            payment=r.get("payment"),
            evidence=tuple(Evidence.of(ln) for ln in ls),
        )
        for r, ls in zip(rows, lines, strict=True)
    )


def table_field(source: ParsedField, build: Callable[[Any], DateTable | ParsedField]) -> ParsedField:
    """標準欄位 `schedule_table`：上手讀出的日期表（`source`）有問題時帶它的狀態與說明，否則以 `build(值)` 轉成 `DateTable`
    （`build` 也可直接回傳有問題的欄位，例：依賴的另一張表讀不到）。"""
    name = "schedule_table"
    if not source.ok:
        return ParsedField(name, source.status, None, list(source.evidence), source.candidates, source.note)
    built = build(source.value)
    if isinstance(built, ParsedField):
        return ParsedField(name, built.status, built.value, list(built.evidence), built.candidates, built.note)
    return ParsedField(name, FieldStatus.PRESENT, built, list(source.evidence))
