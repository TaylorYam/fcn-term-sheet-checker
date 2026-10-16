"""情境分析共用規則（rules/scenario.py）：不經 PDF，直接餵標準欄位（Issue #294）。

三塊（重印價格表、情境假設值、較差情境寫出的價格）各至少一個通過、一個不一致、一個欄位讀不到轉人工覆核；
較差情境另測期初價格 × % 四捨五入與無條件捨去兩種寫法都通過（Issue #259）。
"""

from __future__ import annotations

from decimal import Decimal

from fcn_checker.parsers.price_table import PriceTable, TableRow
from fcn_checker.rules import scenario
from fcn_checker.schema import ParsedField
from fcn_checker.standard_fields import PriceRow, ScenarioParam, ScenarioPrice, TableAt, absent
from harness import MISMATCH, PASS, REVIEW
from rule_fixtures import issuer_context, term_sheet

D = Decimal
MAIN = "第 9 項價格表"
REPRINT = "第 16 項情境價格表"
ROWS = (
    {"initial": D("100.4500"), "strike": D("70.3150"), "ko": D("100.4500"), "ki": D("60.2700")},
    {"initial": D("250.0000"), "strike": D("175.0000"), "ko": D("250.0000"), "ki": D("150.0000")},
)
PCTS = {"strike": D("70.00"), "ko": D("100.00"), "ki": D("60.00")}


def price_table(rows=ROWS, pcts=PCTS, tickers=("AAPL UW", "MSFT UW"), columns=("initial", "strike", "ko", "ki")):
    body = tuple(TableRow(dict(r), t, "", None, ()) for r, t in zip(rows, tickers, strict=True))
    return PriceTable(tuple(columns), dict(pcts), body, {}, ())


def missing(name: str) -> ParsedField:
    return ParsedField.missing(name, "讀不到")


def run(**fields):
    """三塊共用規則；沒給的情境欄位交 absent（範本沒有），給 None 的欄位不交（上手沒交出）。"""
    base = {
        "main_price_table": TableAt(MAIN, price_table()),
        "scenario_reprint_table": absent("scenario_reprint_table", "情境價格表"),
        "scenario_params": absent("scenario_params", "情境假設值"),
        "scenario_prices": absent("scenario_prices", "較差情境價格"),
        "denomination": 10000,
        "tenor_months": 12,
        "coupon_pa_pct": D("9.00"),
        "monthly_coupon_pct": D("0.7500"),
        "strike_pct": D("70.00"),
        "underlying_prices": tuple(PriceRow(None, dict(r), ()) for r in ROWS),
    }
    base.update(fields)
    declared = base.pop("declared", {})
    ts = term_sheet(**{k: v for k, v in base.items() if v is not None})
    return scenario.run(issuer_context(ts, **declared))


def by_rule(results, rid):
    return [r for r in results if r.rule_id == rid]


# ---------------------------------------------------------------- 重印價格表


def reprint(**kw):
    return by_rule(run(scenario_reprint_table=TableAt(REPRINT, price_table(**kw))), "scenario.reprint")


def test_reprint_equal_to_the_main_table_passes_and_lists_every_cell():
    [r] = reprint()
    assert r.status == PASS
    assert r.item.name == REPRINT
    assert r.expected == r.actual and len(r.actual) == 8


def test_reprint_cell_difference_names_row_column_and_both_values_in_chinese():
    rows = (dict(ROWS[0], strike=D("70.3151")), ROWS[1])
    [r] = reprint(rows=rows)
    assert r.status == MISMATCH
    assert "第 1 列執行價 70.3151" in r.message and "70.3150" in r.message
    assert "strike" not in r.message


def test_reprint_header_pct_and_ticker_must_match_when_both_sides_have_them():
    [r] = reprint(pcts=dict(PCTS, ki=D("65.00")))
    assert r.status == MISMATCH and "表頭下限價 %" in r.message
    [r] = reprint(tickers=("AAPL UW", "MSFT UQ"))
    assert r.status == MISMATCH and "第 2 列彭博代碼" in r.message
    [r] = reprint(tickers=(None, None))  # 情境表沒有代碼：不比
    assert r.status == PASS
    [r] = reprint(pcts={})  # 情境表表頭沒寫百分比：不比
    assert r.status == PASS


def test_reprint_with_other_columns_or_row_count_is_review():
    [r] = reprint(rows=ROWS[:1], tickers=("AAPL UW",))
    assert r.status == REVIEW and "列數" in r.message
    [r] = reprint(
        rows=tuple({k: v for k, v in x.items() if k != "ki"} for x in ROWS), columns=("initial", "strike", "ko")
    )
    assert r.status == REVIEW and "價格欄" in r.message


def test_unreadable_reprint_or_main_table_is_review():
    [r] = by_rule(run(scenario_reprint_table=missing("scenario_reprint_table")), "scenario.reprint")
    assert r.status == REVIEW and r.item.name == "情境價格表"
    out = run(scenario_reprint_table=TableAt(REPRINT, price_table()), main_price_table=missing("main_price_table"))
    [r] = by_rule(out, "scenario.reprint")
    assert r.status == REVIEW and r.item.name == REPRINT


def test_template_without_scenario_blocks_gives_no_results():
    assert run() == []


# ---------------------------------------------------------------- 情境假設值


def params(*items, **fields):
    return by_rule(run(scenario_params=tuple(items), **fields), "scenario.params")


def present(value) -> ParsedField:
    return ParsedField.present("x", value, [])


def test_params_equal_to_the_document_values_pass_with_names_from_the_shared_table():
    out = params(
        ScenarioParam("scenario_tenor", "第 16 項基本假設", present(12)),
        ScenarioParam("scenario_denomination", "第 16 項基本假設", present(10000)),
        ScenarioParam("scenario_monthly_coupon", "第 16 項情境一配息金額的", present(D("0.75"))),
        ScenarioParam("scenario_coupon_pa", "第 15 項情境 (i) 每次配息金額的", present(D("9.00"))),
        ScenarioParam("scenario_strike_pct", "第 17 項情境 4 ", present(D("70.00"))),
    )
    assert [r.status for r in out] == [PASS] * 5
    assert [r.item.name for r in out][:2] == ["第 16 項基本假設天期", "第 16 項基本假設每單位面額"]
    assert len({r.field for r in out}) == 5


def test_param_different_from_the_document_value_is_mismatch():
    [r] = params(ScenarioParam("scenario_tenor", "第 16 項基本假設", present(6)))
    assert r.status == MISMATCH and (r.expected, r.actual) == (12, 6)


def test_param_with_a_multiple_equals_the_official_value_times_it():
    item = ScenarioParam("scenario_investment", "情境假設", present(50000), times=present(5))
    [r] = params(item)
    assert r.status == PASS and r.expected == 50000
    [r] = params(ScenarioParam("scenario_investment", "情境假設", present(40000), times=present(5)))
    assert r.status == MISMATCH


def test_unreadable_param_official_value_or_list_is_review():
    [r] = params(ScenarioParam("scenario_tenor", "第 16 項基本假設", missing("scenario_tenor")))
    assert r.status == REVIEW
    [r] = params(ScenarioParam("scenario_tenor", "第 16 項基本假設", present(12)), tenor_months=missing("tenor_months"))
    assert r.status == REVIEW
    [r] = by_rule(run(scenario_params=missing("scenario_params")), "scenario.params")
    assert r.status == REVIEW


def test_template_without_the_official_value_compares_with_the_reference_sheet():
    item = ScenarioParam("scenario_annualized_return", "第 18 項情境一", present(D("9.00")))
    no_rate = absent("coupon_pa_pct", "年利率")
    [r] = params(item, coupon_pa_pct=no_rate, declared={"coupon_pa_pct": "9.004"})
    assert r.status == PASS and r.order_source  # 依說明書位數四捨五入後比，預期值出處是參考條件表
    [r] = params(item, coupon_pa_pct=no_rate, declared={"coupon_pa_pct": "9.5"})
    assert r.status == MISMATCH
    monthly = ScenarioParam("scenario_monthly_coupon", "第 18 項情境一", present(D("0.7500")))
    [r] = params(monthly, monthly_coupon_pct=absent("monthly_coupon_pct", "月配息率"), declared={"coupon_pa_pct": "9"})
    assert r.status == PASS


# ---------------------------------------------------------------- 較差情境寫出的價格


def prices(*items, **fields):
    return by_rule(run(scenario_prices=tuple(items), **fields), "scenario.strike")


WHERE = "第 17 項情境 4 "


def test_strike_equal_to_the_table_passes():
    [r] = prices(ScenarioPrice(WHERE, 1, "strike", present(D("70.3150"))))
    assert r.status == PASS and r.item.name == f"{WHERE}第 1 檔執行價"


def test_strike_rounded_or_truncated_from_initial_times_pct_both_pass():
    # 100.4500 × 70% = 70.315：兩位數寫法四捨五入 70.32、無條件捨去 70.31 都接受（Issue #259）
    for shown in ("70.32", "70.31"):
        [r] = prices(ScenarioPrice(WHERE, 1, "strike", present(D(shown))))
        assert r.status == PASS, shown


def test_strike_or_initial_different_from_the_table_is_mismatch():
    [r] = prices(ScenarioPrice(WHERE, 2, "strike", present(D("176.0000"))))
    assert r.status == MISMATCH and "第 2 檔執行價" in r.message and "strike" not in r.message
    [r] = prices(ScenarioPrice(WHERE, 1, "initial", present(D("100.4000"))))
    assert r.status == MISMATCH and "期初價格" in r.message


def test_unknown_underlying_or_unreadable_price_is_review():
    [r] = prices(ScenarioPrice(WHERE, None, "strike", present(D("70.3150"))))
    assert r.status == REVIEW
    [r] = prices(ScenarioPrice(WHERE, 3, "strike", present(D("70.3150"))))
    assert r.status == REVIEW
    [r] = prices(ScenarioPrice(WHERE, 1, "strike", missing("scenario_strike")))
    assert r.status == REVIEW
    [r] = prices(
        ScenarioPrice(WHERE, 1, "strike", present(D("70.3150"))), underlying_prices=missing("underlying_prices")
    )
    assert r.status == REVIEW
