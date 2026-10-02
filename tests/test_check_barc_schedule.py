"""配息表、提前出場表、Non-Call、比價日、日期規則 B／C、§16 重印表、最低申購／贖回金額。

透過批量核對入口驗證（見 test_check_barc.check），資料皆為合成。
"""

from __future__ import annotations

import datetime as dt

import pytest

from fcn_checker.schema import CheckStatus
from harness import MISMATCH, PASS, REVIEW, problems, results
from synth import Spec, check

SCHEDULE_RULES = (
    "field.first_callable_period",
    "backfill.compare_dates",
    "schedule.coupon_dates",
    "schedule.final_period",
    "schedule.autocall_dates",
    "doc.autocall_trigger_per_period",
    "doc.scenario_price_table",
    "doc.min_subscription_redemption",
)


TWELVE = {"tenor": 12, "final_date": dt.date(2031, 1, 7), "maturity_date": dt.date(2031, 1, 10)}
VARIANTS = [
    pytest.param(Spec(ko_obs="D", memory=True, guaranteed=1), 1, id="daily-memory-G1"),
    pytest.param(Spec(ko_obs="D", memory=True, guaranteed=2), 2, id="daily-memory-G2"),
    pytest.param(Spec(ko_obs="D", memory=False, guaranteed=1, ki="AM"), 1, id="daily-combined-G1"),
    pytest.param(Spec(ko_obs="P", memory=True), 1, id="periodend-memory-G0"),
    pytest.param(Spec(ko_obs="P", memory=False), 1, id="periodend-G0"),
    pytest.param(Spec(ko_obs="P", memory=False, guaranteed=11, **TWELVE), 12, id="periodend-12m-G11"),
    pytest.param(Spec(break_coupon_after=4, **TWELVE), 1, id="daily-12m-cross-page"),
]


@pytest.mark.parametrize(("spec", "non_call"), VARIANTS)
def test_schedule_variants_pass(tmp_path, spec, non_call):
    report = check(tmp_path, spec)
    assert problems(report) == set()
    for rule_id in SCHEDULE_RULES:
        assert {r.status for r in results(report, rule_id)} <= {PASS, CheckStatus.NOT_APPLICABLE}
    r = results(report, "field.first_callable_period")[0]
    assert r.status == PASS and r.actual == non_call and r.document_evidence


def test_daily_observation_from_first_day_requires_review(tmp_path):
    # 第 1 期期始日就有日期（S08、S10 型）：說明書內部規則照常通過，Non-Call 與比價日轉人工覆核
    report = check(tmp_path, Spec(ko_obs="D", memory=True, guaranteed=0), overrides={"Non-Call(月)": 1})
    assert problems(report) == {("field.first_callable_period", REVIEW), ("backfill.compare_dates", REVIEW)}


def test_not_covered_only_lists_deferred_rules(tmp_path):
    report = check(tmp_path)
    assert {n["rule_id"] for n in report.not_covered} == {
        "field.monthly_ki",
        "doc.underlying_names",
        "doc.initial_prices",
        "doc.scenario_other_returns",
    }


def test_non_call_zero_is_mismatch(tmp_path):
    report = check(tmp_path, overrides={"Non-Call(月)": 0})
    assert problems(report) == {("field.first_callable_period", MISMATCH)}


def test_guaranteed_periods_text_disagrees_with_table(tmp_path):
    report = check(tmp_path, Spec(guaranteed=1, guaranteed_text=2))
    r = results(report, "field.first_callable_period")[0]
    assert r.status == REVIEW and r.reason_code == "document_invalid"
    assert "定義句" in r.message


def test_coupon_payment_before_valuation(tmp_path):
    report = check(tmp_path, Spec(coupon_overrides={(3, "payment"): "2030 年3 月1 日"}))
    assert problems(report) == {("schedule.coupon_dates", MISMATCH)}
    bad = [r for r in results(report, "schedule.coupon_dates") if r.status == MISMATCH][0]
    assert "第 3 期" in bad.actual


def test_last_valuation_must_equal_final_valuation_date(tmp_path):
    spec = Spec(ko_obs="P", memory=False, coupon_overrides={(6, "valuation"): "2030 年7 月5 日"})
    report = check(tmp_path, spec)
    assert problems(report) == {("schedule.final_period", MISMATCH)}


def test_fixed_autocall_redemption_date_must_match_payment(tmp_path):
    spec = Spec(ko_obs="P", memory=True, ko_overrides={(2, "early_redemption"): "2030 年3 月20 日"})
    report = check(tmp_path, spec)
    assert problems(report) == {("schedule.autocall_dates", MISMATCH)}


def test_period_end_date_must_match_coupon_valuation(tmp_path):
    report = check(tmp_path, Spec(ko_overrides={(4, "end"): "2030 年5 月2 日"}))
    assert ("schedule.autocall_dates", MISMATCH) in problems(report)
    c3 = results(report, "schedule.autocall_dates", "end")[0]
    assert c3.status == MISMATCH and "第 4 期" in c3.actual


def test_period_start_must_follow_previous_end(tmp_path):
    report = check(tmp_path, Spec(ko_overrides={(3, "start"): "2030 年3 月20 日"}))
    assert problems(report) == {("schedule.autocall_dates", MISMATCH)}
    assert results(report, "schedule.autocall_dates", "start")[0].status == MISMATCH


def test_trigger_per_period(tmp_path):
    report = check(tmp_path, Spec(ko_overrides={(4, "trigger"): "105.00%"}))
    assert problems(report) == {("doc.autocall_trigger_per_period", MISMATCH)}


def test_scenario_price_table_reprint(tmp_path):
    report = check(tmp_path, Spec(scenario_overrides={(1, "strike"): "86.4200"}))
    assert problems(report) == {("doc.scenario_price_table", MISMATCH)}


def test_min_subscription_must_equal_denomination(tmp_path):
    report = check(tmp_path, Spec(min_subscription=20000))
    assert problems(report) == {("doc.min_subscription_redemption", MISMATCH)}
    assert results(report, "doc.min_subscription_redemption", "min_redemption")[0].status == PASS


def test_unreadable_table_cell_requires_review(tmp_path):
    report = check(tmp_path, Spec(ko_overrides={(2, "end"): "另行公告"}))
    assert results(report, "field.first_callable_period")[0].status == REVIEW
    assert report.status == REVIEW


def test_callable_period_without_trigger_percentage(tmp_path):
    report = check(tmp_path, Spec(ko_overrides={(3, "trigger"): "N/A"}))
    assert problems(report) == {("doc.autocall_trigger_per_period", MISMATCH)}


def test_period_end_memory_without_autocall_table_is_not_silently_passed(tmp_path):
    # 把提前出場表表頭改成不認得的寫法：不得退回用配息表當提前出場表
    spec = Spec(ko_obs="P", memory=True)
    report = check(tmp_path, spec, pdf_spec=spec.with_(ko_header_override="提前出場評價日一覽"))
    assert results(report, "field.first_callable_period")[0].status == REVIEW
