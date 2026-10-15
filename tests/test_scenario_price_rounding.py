"""情境文字的執行價／下限價容許兩種進位（Issue #259，`rules.kit.scenario_price_ok`；HSBC、BNP、MS、SG 共用）。"""

from decimal import Decimal

import pytest

from fcn_checker.rules.kit import scenario_price_ok

D = Decimal


@pytest.mark.parametrize(
    ("actual", "ok"),
    [
        (D("289.9159"), True),  # = 標的表（四捨五入）
        (D("289.9158"), True),  # 期初 414.1655 × 70% = 289.91585 無條件捨去
        (D("289.9157"), False),
        (D("289.9160"), False),
    ],
)
def test_rounded_or_truncated_initial_times_pct(actual, ok):
    assert scenario_price_ok(actual, D("289.9159"), D("414.1655"), D("70")) is ok


def test_only_the_table_value_when_initial_or_pct_is_unknown():
    assert scenario_price_ok(D("289.9159"), D("289.9159"), None, None)
    assert not scenario_price_ok(D("289.9158"), D("289.9159"), None, D("70"))
    assert not scenario_price_ok(D("289.9158"), D("289.9159"), D("414.1655"), None)


def test_digits_follow_the_document_value():
    """位數依文件寫的值：寫 2 位時比 2 位的四捨五入／捨去。"""
    assert scenario_price_ok(D("289.91"), D("289.9159"), D("414.1655"), D("70"))
    assert scenario_price_ok(D("289.92"), D("289.9159"), D("414.1655"), D("70"))
