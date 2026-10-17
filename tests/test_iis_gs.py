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
        # Issue #317：重複寫出的幣別字、天期、到期買回 %
        (Edit("以美元計價之畸零現金金額", "以日圓計價之畸零現金金額", "iis.redeem"), "field.currency"),
        (Edit("本商品係以美元計價", "本商品係以日圓計價", "iis.rest"), "field.currency"),
        (Edit("期初淨值自50,000美元", "期初淨值自50,000日圓", "iis.fees"), "field.currency"),
        (Edit("包含於6個月後始返還本金", "包含於7個月後始返還本金", "iis.rest"), "iis.tenor_mentions"),
        (Edit("通路服務費率於6個月間", "通路服務費率於7個月間", "iis.fees"), "iis.tenor_mentions"),
        (Edit("每單位面額 × 100%;", "每單位面額 × 99%;", "iis.redeem"), "iis.redemption_pct"),
        (Edit("受到計價幣別（美元）", "受到計價幣別（日圓）", "iis.rest"), "field.currency"),
        (Edit("假設申購5單位(50,000美元)", "假設申購5單位(50,000日圓)", "iis.fees"), "field.currency"),
        (Edit("降至48,500美元", "降至48,500日圓", "iis.fees"), "field.currency"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_items_are_checked(tmp_path, edit, broken):
    r = check_iis(tmp_path, edits=[edit]).report
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("包含於6個月後始返還本金", "包含返還本金", "iis.rest"), "iis.tenor_mentions"),
        (Edit("每單位面額 × 100%;", "每單位面額;", "iis.redeem"), "iis.redemption_pct"),
        (Edit("本商品係以美元計價", "本商品係以外幣計價", "iis.rest"), "field.currency"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:12],
)
def test_unreadable_repeats_need_review(tmp_path, edit, broken):
    """重複寫出處讀不到（Issue #317）：轉人工覆核。"""
    r = check_iis(tmp_path, edits=[edit]).report
    assert S.REVIEW_REQUIRED in {x.status for x in rule(r, broken)}, problems(r)


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


def test_entrusted_investment_wording_is_exempt_up_to_2026_10_30(tmp_path):
    """警語「受託投資」：交易日 2026-10-30（含）以前特例通過，之後判不一致（共用特例，Issue #338）。"""
    edit = Edit("於中華民國境內受託買賣", "於中華民國境內受託投資", "iis.warn")
    old = Spec(trade_date=dt.date(2026, 10, 30), issue_date=dt.date(2026, 11, 6))
    assert rule(check_iis(tmp_path / "old", old, edits=[edit]).report, "standard.forbidden_wording")[0].status == S.PASS
    new = check_iis(
        tmp_path / "new", Spec(trade_date=dt.date(2026, 10, 31), issue_date=dt.date(2026, 11, 6)), edits=[edit]
    ).report
    assert rule(new, "standard.forbidden_wording")[0].status == S.MISMATCH


@pytest.fixture(autouse=True)
def _dirs(tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()


@pytest.mark.parametrize(
    ("edit", "field"),
    [
        (
            Edit("四 本商品雖經玉山證券（受託機構）審查", "四 本商品雖經玉山証券（受託機構）審查", "iis.warn"),
            "distributor_name_w4a",
        ),
        (Edit("且玉山證券（受託機構）不負", "且玉山証券（受託機構）不負", "iis.warn"), "distributor_name_w4b"),
        (Edit("責。玉山證券（受託機構）依法", "責。玉山証券（受託機構）依法", "iis.warn"), "distributor_name_w4c"),
        (
            Edit("而非由玉山證券（受託機構）所保證", "而非由玉山証券（受託機構）所保證", "iis.warn"),
            "distributor_name_w5",
        ),
        (Edit("受託機構（玉山證券）收取", "受託機構（玉山証券）收取", "iis.warn"), "distributor_name_w6a"),
        (Edit("及由玉山證券（受託機構）另行", "及由玉山証券（受託機構）另行", "iis.warn"), "distributor_name_w6b"),
        (Edit("係由玉山證券（受託機構）負責外", "係由玉山証券（受託機構）負責外", "iis.warn"), "distributor_name_w6c"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:14],
)
def test_warning_distributor_name_is_checked(tmp_path, edit, field):
    """警語裡的受託機構名稱每處都算名稱出處（Issue #306）：只有這一處錯，相關機構名稱正確。"""
    r = check_iis(tmp_path, edits=[edit]).report
    assert ("standard.distributor", field) in {(rid, f) for rid, f, _ in problems(r)}, problems(r)


def test_warning_distributor_names_are_read(tmp_path):
    r = check_iis(tmp_path).report
    assert {x.field for x in rule(r, "standard.distributor")} >= {
        "distributor_name_org",
        "distributor_name_w4a",
        "distributor_name_w4b",
        "distributor_name_w4c",
        "distributor_name_w5",
        "distributor_name_w6a",
        "distributor_name_w6b",
        "distributor_name_w6c",
    }
