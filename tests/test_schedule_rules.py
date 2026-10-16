"""日期表共用檢查（rules/schedule.py）與排程共用推導（parsers/schedule.py）：不經 PDF，直接餵標準欄位（Issue #289）。

每項檢查至少一個通過、一個不一致、一個欄位讀不到轉人工覆核。日期在 2030 年（假日表涵蓋範圍由 harness 延長、沒有休市日）：
2030-01-04 是週五，2030-02-01 是週五。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from fcn_checker.parsers.schedule import autocall_schedule
from fcn_checker.rules import schedule
from fcn_checker.schema import FieldStatus, ParsedField
from fcn_checker.standard_fields import AutocallSchedule, DateRow, DateTable, PeriodStart
from harness import MISMATCH, PASS, REVIEW
from rule_fixtures import issuer_context, term_sheet

D = dt.date
ISSUE = D(2030, 1, 4)
ROWS = (
    DateRow(1, D(2030, 1, 7), D(2030, 2, 4), D(2030, 2, 4), D(2030, 2, 7)),
    DateRow(2, D(2030, 2, 5), D(2030, 3, 4), D(2030, 3, 4), D(2030, 3, 7)),
    DateRow(3, D(2030, 3, 5), D(2030, 4, 4), D(2030, 4, 4), D(2030, 4, 9)),
)
NEXT_DAY = PeriodStart("next_trading_day", "after_issue")
WHERE = "第 13 項日期表"


def table(rows=ROWS, start: PeriodStart | None = NEXT_DAY) -> DateTable:
    return DateTable(WHERE, tuple(rows), start)


def fields(**over):
    base = {
        "schedule_table": table(),
        "tenor_months": 3,
        "final_valuation_date": D(2030, 4, 4),
        "maturity_date": D(2030, 4, 9),
        "issue_date": ISSUE,
        "underlyings": ["AAPL UW"],
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def run(**over):
    results = schedule.run(issuer_context(term_sheet(**fields(**over))))
    return {r.rule_id: r for r in results}


def missing(name: str) -> ParsedField:
    return ParsedField.missing(name, "讀不到")


def test_all_six_pass_and_items_carry_the_table_location():
    out = run()
    assert set(out) == set(schedule.NAMES)
    assert {r.status for r in out.values()} == {PASS}
    assert out["schedule.last_payment"].item.name == f"{WHERE}末期配息日"
    assert out["schedule.period_start"].item.name == f"{WHERE}期始日"


def test_unreadable_table_turns_the_five_checks_to_review():
    out = run(schedule_table=ParsedField.invalid("schedule_table", [], "表格沒有任何列"))
    assert set(out) == set(list(schedule.NAMES)[:5])
    assert {r.status for r in out.values()} == {REVIEW}
    assert out["schedule.period_count"].item.name == "日期表期數"


def test_no_start_column_means_no_period_start_check():
    assert "schedule.period_start" not in run(schedule_table=table(start=None))


# ---------------------------------------------------------------- 期數


@pytest.mark.parametrize(
    "tenor,rows,status",
    [
        (3, ROWS, PASS),
        (4, ROWS, MISMATCH),
        (missing("tenor_months"), ROWS, REVIEW),
        (3, (ROWS[0], replace(ROWS[1], period=3), replace(ROWS[2], period=2)), MISMATCH),
    ],
)
def test_period_count(tenor, rows, status):
    r = run(tenor_months=tenor, schedule_table=table(rows))["schedule.period_count"]
    assert r.status == status
    if status == MISMATCH and tenor == 4:
        assert (r.expected, r.actual) == (4, 3)


# ---------------------------------------------------------------- 配息日、觀察日順序


def test_payment_order():
    assert run()["schedule.payment_order"].status == PASS
    bad = (ROWS[0], replace(ROWS[1], payment=D(2030, 2, 6)), ROWS[2])
    r = run(schedule_table=table(bad))["schedule.payment_order"]
    assert r.status == MISMATCH and r.actual == "第 2 期"
    assert "第 2 期配息日 2030-02-06 沒有晚於第 1 期 2030-02-07" in r.message
    blank = (ROWS[0], replace(ROWS[1], payment=None), ROWS[2])
    assert "第 2 期沒有配息日" in run(schedule_table=table(blank))["schedule.payment_order"].message


def test_observation_order():
    assert run()["schedule.observation_order"].status == PASS
    late = (ROWS[0], replace(ROWS[1], observation=D(2030, 3, 7)), ROWS[2])
    r = run(schedule_table=table(late))["schedule.observation_order"]
    assert r.status == MISMATCH and "第 2 期觀察日 2030-03-07 沒有早於配息日 2030-03-07" in r.message
    back = (ROWS[0], replace(ROWS[1], observation=D(2030, 2, 1)), ROWS[2])
    assert "沒有晚於第 1 期" in run(schedule_table=table(back))["schedule.observation_order"].message
    # 不可提前出場的期別沒有觀察日：只比有日期的期別
    blank = (replace(ROWS[0], observation=None), ROWS[1], ROWS[2])
    assert run(schedule_table=table(blank))["schedule.observation_order"].status == PASS


# ---------------------------------------------------------------- 末期觀察日、末期配息日


@pytest.mark.parametrize(
    "rid,field,value,status",
    [
        ("schedule.last_observation", "final_valuation_date", D(2030, 4, 4), PASS),
        ("schedule.last_observation", "final_valuation_date", D(2030, 4, 5), MISMATCH),
        ("schedule.last_observation", "final_valuation_date", missing("final_valuation_date"), REVIEW),
        ("schedule.last_payment", "maturity_date", D(2030, 4, 9), PASS),
        ("schedule.last_payment", "maturity_date", D(2030, 4, 10), MISMATCH),
        ("schedule.last_payment", "maturity_date", missing("maturity_date"), REVIEW),
    ],
)
def test_last_dates(rid, field, value, status):
    r = run(**{field: value})[rid]
    assert r.status == status
    if status == MISMATCH:
        assert r.expected == value and r.actual != value


# ---------------------------------------------------------------- 期始日


def starts(*days, start: PeriodStart = NEXT_DAY):
    rows = tuple(replace(r, start=d) for r, d in zip(ROWS, days, strict=True))
    return run(schedule_table=table(rows, start))["schedule.period_start"]


def test_next_trading_day_skips_weekends():
    assert starts(D(2030, 1, 7), D(2030, 2, 5), D(2030, 3, 5)).status == PASS
    friday = (replace(ROWS[0], end=D(2030, 2, 1), observation=D(2030, 2, 1)), *ROWS[1:])
    rows = (friday[0], replace(ROWS[1], start=D(2030, 2, 2)), ROWS[2])
    r = run(schedule_table=table(rows))["schedule.period_start"]
    assert r.status == MISMATCH and "第 2 期期始日應為 2030-02-04" in r.message


def test_next_trading_day_unknown_exchange_later_start_is_review():
    rows = (ROWS[0], replace(ROWS[1], start=D(2030, 2, 6)), ROWS[2])
    r = run(schedule_table=table(rows), underlyings=["7203 XX"])["schedule.period_start"]
    assert r.status == REVIEW and r.reason_code == "trading_calendar_uncertain"


def test_previous_end_rule_and_first_period_equals_issue_date():
    decl = PeriodStart("previous_end", "issue_date")
    assert starts(ISSUE, D(2030, 2, 4), D(2030, 3, 4), start=decl).status == PASS
    r = starts(D(2030, 1, 7), D(2030, 2, 4), D(2030, 3, 5), start=decl)
    assert r.status == MISMATCH
    assert "第 1 期期始日應為 2030-01-04" in r.message and "第 3 期期始日應為 2030-03-04" in r.message


def test_after_previous_end_rule():
    decl = PeriodStart("after_previous_end", "issue_date")
    assert starts(ISSUE, D(2030, 2, 6), D(2030, 3, 5), start=decl).status == PASS, "跳過幾天都可以"
    r = starts(ISSUE, D(2030, 2, 4), D(2030, 3, 5), start=decl)
    assert r.status == MISMATCH and "第 2 期期始日應晚於 2030-02-04" in r.message


def test_first_period_needs_the_issue_date():
    r = run(issue_date=missing("issue_date"))["schedule.period_start"]
    assert r.status == REVIEW


def test_blank_starts_before_the_first_callable_period():
    decl = PeriodStart("next_trading_day", None, blank=1)
    assert starts(None, D(2030, 2, 5), D(2030, 3, 5), start=decl).status == PASS
    r = starts(D(2030, 1, 7), D(2030, 2, 5), None, start=decl)
    assert r.status == MISMATCH
    assert "第 1 期期始日應空白" in r.message and "第 3 期沒有期始日" in r.message
    unknown = PeriodStart("next_trading_day", None, blank=None)
    assert starts(None, D(2030, 2, 5), D(2030, 3, 5), start=unknown).status == REVIEW


def test_no_previous_end_skips_the_comparison_and_start_after_end_is_mismatch():
    rows = (
        replace(ROWS[0], start=None, end=None),
        replace(ROWS[1], start=D(2030, 1, 20)),
        replace(ROWS[2], start=D(2030, 3, 4)),
    )
    decl = PeriodStart("previous_end", None, blank=1)
    assert run(schedule_table=table(rows, decl))["schedule.period_start"].status == PASS
    late = (ROWS[0], replace(ROWS[1], start=D(2030, 3, 5)), ROWS[2])
    r = run(schedule_table=table(late))["schedule.period_start"]
    assert r.status == MISMATCH and "期始日 2030-03-05 晚於期末日 2030-03-04" in r.message


# ---------------------------------------------------------------- 共用推導


def present(name, value):
    return ParsedField.present(name, value, [])


TABLE = present("schedule_table", table())


def test_daily_schedule_keeps_only_the_first_callable_and_last_period():
    out = autocall_schedule(present("ko_observation", "D"), present("first_callable_period", 2), TABLE)
    assert out["autocall_schedule"].value == AutocallSchedule("D", 2, 3, {2: D(2030, 3, 4), 3: D(2030, 4, 4)})
    assert out["ko_observation"].value == "D" and out["first_callable_period"].value == 2


def test_periodic_schedule_has_every_period_from_k_and_final_overrides_the_last():
    final = present("final_valuation_date", D(2030, 4, 5))
    obs, first = present("ko_observation", "P"), present("first_callable_period", 1)
    sched = autocall_schedule(obs, first, TABLE, "observation", final)["autocall_schedule"].value
    assert sched.dates == {1: D(2030, 2, 4), 2: D(2030, 3, 4), 3: D(2030, 4, 5)}


def test_derivation_failures_pass_the_status_through_all_three_fields():
    bad = ParsedField.ambiguous("first_callable_period", [2, 3], [], "出現多個")
    out = autocall_schedule(present("ko_observation", "D"), bad, TABLE, "end")
    assert {pf.status for pf in out.values()} == {FieldStatus.AMBIGUOUS}
    assert out["autocall_schedule"].candidates == [2, 3]

    out = autocall_schedule(present("ko_observation", "D"), present("first_callable_period", 4), TABLE, "end")
    assert {pf.status for pf in out.values()} == {FieldStatus.INVALID}
    assert "不在第 13 項日期表的第 1～3 期之間" in out["autocall_schedule"].note

    blank = present("schedule_table", table((ROWS[0], replace(ROWS[1], end=None), ROWS[2])))
    out = autocall_schedule(present("ko_observation", "D"), present("first_callable_period", 2), blank, "end")
    assert out["autocall_schedule"].status == FieldStatus.INVALID and "第 2 期" in out["autocall_schedule"].note
