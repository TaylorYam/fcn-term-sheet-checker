"""GS 投資人須知黑箱回歸（Issue #248）：合成說明書＋投資人須知經批量入口；不直接測擷取或解析的內部函式。

合成投資人須知的「利」寫標準字（內建 CJK 字型畫不出相容字 U+F9DD）；相容字轉換由本機真實樣本測試（test_real_gs.py）涵蓋。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from fcn_checker.issuers import GS
from fcn_checker.schema import CheckStatus as S
from gs_synth import WARNING, Spec, check_iis
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from test_check_gs import VARIANTS, _reordered


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass(tmp_path, spec):
    r = check_iis(tmp_path, spec).report
    assert r.template == GS.iis.template_id
    assert r.status == S.PASS, problems(r)
    assert rule(r, "iis.page_totals")[0].status == S.PASS
    assert rule(r, "iis.monthly_coupon")[0].status == S.PASS
    assert rule(r, "field.first_callable_period")[0].status == S.PASS
    assert rule(r, "standard.fixed_warning")[0].actual == 1
    assert [x.status for x in rule(r, "doc.print_date")] == [S.PASS]
    has_ki = spec.ki != "none"
    assert bool([x for x in r.results if x.rule_id == "field.ki_pct"]) == has_ki


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("100.4500美元", "100.5500美元", "iis.table"), "iis.underlying_prices"),
        (Edit("（約為1.0000%）", "（約為1.1000%）", "iis.coupon"), "iis.monthly_coupon"),
        (Edit("年利率12.00%/12", "年利率12.50%/12", "iis.coupon"), "field.coupon_pa_pct"),
        (Edit("自第3個觀察期間結束日開始", "自第2個觀察期間結束日開始", "iis.redeem"), "field.first_callable_period"),
        (
            Edit("1. 發行機構:英商高盛國際公司（", "1. 發行機構:英商高盛國際公司台灣分公司（", "iis.org"),
            "standard.issuer_name",
        ),
        (Edit("（營業所在地:台北市松山區", "（營業所在地:台北市大安區", "iis.org"), "standard.distributor"),
        (Edit("的0%~5%", "的0%~3%", "iis.fees"), "standard.fees"),
        (Edit(WARNING[:20], WARNING[:19] + "X", "iis.warn"), "standard.fixed_warning"),
        (
            Edit("中文投資人須知刊印日期: 2030年1月7日", "中文投資人須知刊印日期: 2030年1月20日", "iis.cover"),
            "doc.print_date",
        ),
        (Edit("(6) 計價幣別:美元", "(6) 計價幣別:日圓", "iis.summary"), "field.currency"),
        (Edit("TDCC 商品代號為148199990001", "TDCC 商品代號為148199990002", "iis.cover"), "iis.product_code"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_items_are_checked(tmp_path, edit, broken):
    r = check_iis(tmp_path, edits=[edit]).report
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_monthly_coupon_is_rounded_half_up_to_four_places(tmp_path):
    """年利率 8% ÷ 12 = 0.66666…：須寫 0.6667（half-up 4 位），去尾的 0.6666 判不一致。"""
    edit = Edit("（約為0.6667%）", "（約為0.6666%）", "iis.coupon")
    r = check_iis(tmp_path, Spec(annual=Decimal("8.00")), edits=[edit]).report
    assert rule(r, "iis.monthly_coupon")[0].status == S.MISMATCH


def test_ki_type_is_read_from_the_redemption_terms(tmp_path):
    edit = Edit("「下限觸發觀察期間」指自交易日開始（不含）至評價日結束（含）之期間。", "", "iis.redeem")
    r = check_iis(tmp_path, Spec(ki="D"), edits=[edit]).report
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_a_different_exchange_code_still_pairs_prices_by_symbol(tmp_path):
    """表上順序不同且有一檔交易所碼寫錯（Issue #265）：投資人須知也只報代碼不符，價格依代號和表上同一檔比照常通過。"""
    spec = Spec()
    out = _reordered(spec)
    symbol, exchange = out["UL_1"].split()
    out["UL_1"] = f"{symbol} {'UN' if exchange != 'UN' else 'UQ'}"
    r = check_iis(tmp_path, spec, **out).report
    assert rule(r, "field.underlyings")[0].status == S.MISMATCH
    prices = rule(r, "iis.underlying_prices")
    assert prices and all(x.status == S.PASS for x in prices), [(x.field, x.status, x.message) for x in prices]


def test_page_count_must_be_four(tmp_path):
    r = check_iis(tmp_path, Spec(iis_pages=5)).report
    assert rule(r, "iis.pages")[0].status == S.MISMATCH


def test_entrusted_investment_wording_is_exempt_up_to_2026_10_15(tmp_path):
    """警語「受託投資」：交易日 2026-10-15（含）以前特例通過，之後判不一致（共用特例，Issue #247）。"""
    edit = Edit("於中華民國境內受託買賣", "於中華民國境內受託投資", "iis.warn")
    old = Spec(trade_date=dt.date(2026, 10, 15), issue_date=dt.date(2026, 10, 22))
    assert rule(check_iis(tmp_path / "old", old, edits=[edit]).report, "standard.forbidden_wording")[0].status == S.PASS
    new = check_iis(tmp_path / "new", Spec(), edits=[edit]).report
    assert rule(new, "standard.forbidden_wording")[0].status == S.MISMATCH


@pytest.fixture(autouse=True)
def _dirs(tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
