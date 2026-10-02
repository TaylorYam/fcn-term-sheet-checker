"""參考條件表的共用規則：Non-Call(月)、回填欄位（ISIN、比價日）的核對與回填決策。

各上手只提供「說明書的 ISIN」與「提前出場排程」（AutocallSchedule）；比價日填法在這裡實作一次：

- D（期間每日觀察）：只填 `比價日_{Non-Call}`，其他格 `-`。
- P（每期定日觀察）：`比價日_{Non-Call}`～`比價日_{期數}` 每格填該期比價日，其他格 `-`。

表上空白的格子在整份核對通過後回填；已有值（含 `-`）的格子只比對，不覆蓋。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from ..config import ReferenceFormat
from ..orders.reference import ReferenceRow
from ..schema import CheckResult, OrderValue, ParsedField
from ..schema import CheckStatus as S
from .common import Context, doc_review, order_value, result, to_int

SLOTS = 12
DASH = "-"


@dataclass(frozen=True)
class AutocallSchedule:
    """說明書的提前出場排程（由各上手 parser 結果推得）。"""

    observation: str  # D 期間每日觀察／P 每期定日觀察
    first_callable: int  # 第一個可以提前出場的期別（Non-Call(月)），最小為 1
    periods: int  # 期數
    dates: dict[int, dt.date]  # 期別 → 比價日；至少包含第一個可提前出場期起的每一期


@dataclass(frozen=True)
class CellDecision:
    """回填欄位的一格：fill = 空白、核對通過後回填；match = 已有相同值；mismatch = 已有不同值，保留原值。"""

    column: str  # Excel 欄名
    cell: str  # 儲存格位置，例 F4
    sheet_value: Any
    expected: Any  # 日期、"-" 或 ISIN
    action: str


def _column(fmt: ReferenceFormat, std: str) -> str:
    return next(h for h, s in fmt.columns.items() if s == std)


def _decide(fmt: ReferenceFormat, row: ReferenceRow, std: str, expected: Any) -> CellDecision:
    ov = row.fields.get(std)
    value = ov.value if ov else None
    if value is None:
        action = "fill"
    elif value == expected:
        action = "match"
    else:
        action = "mismatch"
    return CellDecision(_column(fmt, std), row.cells.get(std, "?"), value, expected, action)


def expected_slots(sched: AutocallSchedule, tenor: int | None) -> list[Any] | str:
    """12 格比價日的期望值；無法對應格子時回傳原因。"""
    if tenor is not None and sched.periods != tenor:
        return f"說明書提前出場表有 {sched.periods} 期，與天期 {tenor} 個月不同，比價日無法對應到格子"
    if sched.periods > SLOTS:
        return f"說明書有 {sched.periods} 期，超過參考條件表的 {SLOTS} 格比價日"
    if not 1 <= sched.first_callable <= sched.periods:
        return f"第一個可提前出場的期別（{sched.first_callable}）不在第 1～{sched.periods} 期之間"
    last = sched.first_callable if sched.observation == "D" else sched.periods
    callable_ = range(sched.first_callable, last + 1)
    missing = [n for n in callable_ if n not in sched.dates]
    if missing:
        return "說明書抓不到以下期別的比價日：" + "、".join(f"第 {n} 期" for n in missing)
    return [sched.dates[n] if n in callable_ else DASH for n in range(1, SLOTS + 1)]


def first_callable_period(ctx: Context, sched: ParsedField) -> CheckResult:
    rid, key = "field.first_callable_period", "first_callable_period"
    v, ov, problem = order_value(ctx, key, rid, key, sched, to_int, "整數")
    if problem:
        return problem
    if not sched.ok:
        return doc_review(rid, key, sched, v, [ov])
    s: AutocallSchedule = sched.value
    ok = v == s.first_callable
    return result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=v,
        actual=s.first_callable,
        pf=sched,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message="Non-Call(月) = 第一個可以提前出場的期別（最小為 1）",
    )


def _fill_message(decisions: list[CellDecision]) -> str:
    n = sum(d.action == "fill" for d in decisions)
    return f"表上空白 {n} 格，整份核對通過後回填" if n else ""


def isin(
    ctx: Context, fmt: ReferenceFormat, row: ReferenceRow, pf: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    rid, key = "backfill.isin", "isin"
    ov: OrderValue | None = row.fields.get(key)
    if not pf.ok:
        return doc_review(rid, key, pf, ov.value if ov else None, [ov]), []
    d = _decide(fmt, row, key, pf.value)
    ok = d.action != "mismatch"
    return (
        result(
            rid,
            key,
            S.PASS if ok else S.MISMATCH,
            expected=d.sheet_value,
            actual=pf.value,
            pf=pf,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message=_fill_message([d]) or ("" if ok else "表上 ISIN 與說明書不同，保留原值"),
        ),
        [d],
    )


def compare_dates(
    ctx: Context, fmt: ReferenceFormat, row: ReferenceRow, sched: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    rid, key = "backfill.compare_dates", "compare_dates"
    ovs = [row.fields.get(f"autocall_date_{n}") for n in range(1, SLOTS + 1)]
    if not sched.ok:
        return doc_review(rid, key, sched, None, ovs), []
    tenor = ctx.ts.f("tenor_months")
    slots = expected_slots(sched.value, tenor.value if tenor.ok else None)
    if isinstance(slots, str):
        return (
            result(rid, key, S.REVIEW_REQUIRED, pf=sched, ov=ovs, reason="compare_dates_unmapped", message=slots),
            [],
        )
    decisions = [_decide(fmt, row, f"autocall_date_{n}", e) for n, e in enumerate(slots, start=1)]
    bad = [d for d in decisions if d.action == "mismatch"]
    rule = "D：只填第一個可提前出場期" if sched.value.observation == "D" else "P：第一個可提前出場期起每期都填"
    return (
        result(
            rid,
            key,
            S.MISMATCH if bad else S.PASS,
            expected={d.column: d.sheet_value for d in bad} or None,
            actual={d.column: d.expected for d in bad} or [d.expected for d in decisions],
            pf=sched,
            ov=ovs,
            reason="value_mismatch" if bad else "",
            message="；".join(x for x in (rule, _fill_message(decisions)) if x)
            + ("；表上值與說明書不同的格子保留原值" if bad else ""),
        ),
        decisions,
    )
