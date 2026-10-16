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
    first_date: ParsedField | None = None,
) -> dict[str, ParsedField]:
    """KO 觀察方式、第一個可提前出場期與提前出場排程（`SCHEDULE_KEYS` → 欄位）。

    `table` 為標準欄位 `schedule_table`（`DateTable`）；`column` 為比價日所在的 `DateRow` 欄，沒給時 D 取期末日（`end`）、
    P 取觀察日（`observation`）；
    `final` 有給時末期比價日取最終評價日，否則取表的末列；`first_date` 有給時第 k 期比價日取它（SG：第 k 期終止日
    「不適用」，取第 8 項觀察期間起始日）。依賴的欄位第一個有問題的決定三個欄位的狀態；
    k 不在第 1～n 期之間、需要的比價日空白時只有排程不合法（Non-Call 與比價日轉人工覆核），KO 觀察方式與 k 照說明書交出
    （上手規則仍可核對 k 的範圍）。
    """
    deps = [observation, first, table] + [p for p in (final, first_date) if p is not None]
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return failed(bad)
    t: DateTable = table.value
    k, n = first.value, len(t.rows)
    ev = list(first.evidence) + list(table.evidence)
    terms = {
        "ko_observation": _renamed("ko_observation", observation),
        "first_callable_period": _renamed("first_callable_period", first),
    }
    if not 1 <= k <= n:
        note = f"第一個可提前出場期（第 {k} 期）不在{t.where}的第 1～{n} 期之間"
        return {
            **terms,
            "autocall_schedule": ParsedField("autocall_schedule", FieldStatus.INVALID, evidence=ev, note=note),
        }
    daily = observation.value == "D"
    column = column or ("end" if daily else "observation")
    periods = (k, n) if daily else range(k, n + 1)
    dates: dict[int, dt.date | None] = {p: getattr(t.rows[p - 1], column) for p in periods}
    if first_date is not None:
        dates[k] = first_date.value
    if final is not None:
        dates[n] = final.value
    blank = [p for p, d in dates.items() if d is None]
    if blank:
        note = f"{t.where}以下期別沒有比價日：" + "、".join(f"第 {p} 期" for p in blank)
        return {
            **terms,
            "autocall_schedule": ParsedField("autocall_schedule", FieldStatus.INVALID, evidence=ev, note=note),
        }
    sched = AutocallSchedule(observation.value, k, n, {p: d for p, d in dates.items() if d is not None})
    return {**terms, "autocall_schedule": ParsedField("autocall_schedule", FieldStatus.PRESENT, sched, ev)}


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


def invalid(evidence: Sequence[Evidence], note: str) -> dict[str, ParsedField]:
    """上手讀出的條款彼此不一致（例：KO 寫法和日期表種類不同）：三個欄位都不合法，說明寫在 `note`。"""
    return {key: ParsedField(key, FieldStatus.INVALID, evidence=list(evidence), note=note) for key in SCHEDULE_KEYS}


def mapped_rows(rows: Sequence[Mapping[str, Any]], ko_rows: Sequence[Mapping[str, Any]]) -> tuple[DateRow, ...]:
    """P 型配息日表加上另一張自動提前出場觀察日表：觀察日取觀察日表裡出場日（`autocall`）= 該期配息日的那一列，
    對不到的期別沒有觀察日（CA、Natixis、BBVA；觀察日表的列號不是期別）。"""
    observed = {r["autocall"]: r["observation"] for r in ko_rows}
    return tuple(DateRow(r["period"], observation=observed.get(r["payment"]), payment=r["payment"]) for r in rows)


def blank_starts(rows: Sequence[DateRow]) -> int:
    """開頭期始日空白的期數（不可提前出場的期別寫「不適用」／N.A）；整張表都沒有期始日時為期數。"""
    return next((i for i, r in enumerate(rows) if r.start is not None), len(rows))
