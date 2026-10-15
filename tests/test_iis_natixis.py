"""Natixis 投資人須知黑箱回歸（Issue #261）：合成說明書＋投資人須知經批量入口；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fcn_checker.issuers import NATIXIS
from fcn_checker.schema import CheckStatus as S
from harness import field_problems as problems
from harness import results as rule
from natixis_synth import ADDRESS, WARNING, Spec, check_iis
from pdf_writer import Edit
from test_check_natixis import VARIANTS


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass(tmp_path, spec):
    r = check_iis(tmp_path, spec).report
    assert r.template == NATIXIS.iis.template_id
    assert r.status == S.PASS, problems(r)
    assert rule(r, "iis.page_totals")[0].status == S.PASS
    assert rule(r, "iis.monthly_coupon")[0].status == S.PASS
    assert rule(r, "iis.ko_definition")[0].status == S.PASS
    assert rule(r, "field.ko_pct")[0].status == S.PASS
    assert rule(r, "standard.fixed_warning")[0].actual == 2  # 警語 (1)、商品簡介
    assert rule(r, "iis.name_en")[0].status == S.PASS  # 「Non Principal」視同說明書封面的「Non-Principal」
    assert [x.status for x in rule(r, "doc.print_date")] == [S.PASS]
    assert bool([x for x in r.results if x.rule_id == "field.first_callable_period"]) == (spec.ko_obs == "D")
    assert bool([x for x in r.results if x.rule_id == "field.ki_pct"]) == (spec.ki != "none")
    assert not [x for x in r.results if x.rule_id in ("iis.underlyings", "iis.underlying_prices")]  # 沒有價格表


NO_PCT = "自動提前到期贖回價格為中文產品說明書第一章第14項所列之期初進場價格。"


def test_ko_definition_without_a_percentage_reads_as_100_percent(tmp_path):
    """KO 定義寫成「…所列之期初進場價格」（沒有 %）＝ KO 100%（2026-10-15 確認，Issue #270）：KO 100% 的商品通過。"""
    spec = Spec(ko=Decimal("100.00"))
    edit = Edit("自動提前到期贖回價格為期初進場價格之100.00%。", NO_PCT, "iis.coupon")
    r = check_iis(tmp_path, spec, edits=[edit]).report
    assert r.status == S.PASS, problems(r)
    assert rule(r, "iis.ko_definition")[0].status == S.PASS
    assert rule(r, "field.ko_pct")[0].actual == Decimal(100)


def test_ko_definition_without_a_percentage_is_a_mismatch_when_ko_is_not_100(tmp_path):
    """同上寫法、但 KO 是 105%（NX7 的情況）：KO % 判不一致（100 ≠ 105）。"""
    edit = Edit("自動提前到期贖回價格為期初進場價格之105.00%。", NO_PCT, "iis.coupon")
    r = check_iis(tmp_path, edits=[edit]).report
    assert rule(r, "field.ko_pct")[0].status == S.MISMATCH
    assert rule(r, "iis.ko_definition")[0].status == S.PASS


def test_currency_bullet_may_be_a_black_circle_with_a_space(tmp_path):
    """商品簡介「● 計價幣別：美元(USD)」（列項符號是 ●，後面有空格；Issue #270）也讀得到。"""
    r = check_iis(tmp_path, edits=[Edit("• 計價幣別：", "● 計價幣別：", "iis")]).report
    assert r.status == S.PASS, problems(r)


def test_five_pages_is_a_mismatch(tmp_path):
    r = check_iis(tmp_path, Spec(iis_pages=5)).report
    assert rule(r, "iis.pages")[0].status == S.MISMATCH


def test_old_version_blank_distributor_code_needs_review(tmp_path):
    r = check_iis(tmp_path, Spec(old=True)).report
    assert {(rid, f) for rid, f, _ in problems(r)} == {("doc.distributor_product_code", "distributor_code")}


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("期初進場價格之105.00%", "期初進場價格之110.00%", "iis.coupon"), "field.ko_pct"),
        (Edit("期初進場價格之75.00%", "期初進場價格之70.00%", "iis.coupon"), "field.strike_pct"),
        (Edit("期初進場價格之60.00%", "期初進場價格之65.00%", "iis.coupon"), "field.ki_pct"),
        (Edit("月配息率：1.5000%", "月配息率：1.5100%", "iis.coupon"), "iis.monthly_coupon"),
        (Edit("(即年利率18.00%)", "(即年利率18.50%)", "iis.coupon"), "field.coupon_pa_pct"),
        (Edit("收盤價皆曾經高於", "收盤價皆高於", "iis.coupon"), "field.ko_memory"),
        (Edit("從第二個配息觀察期間", "從第三個配息觀察期間", "iis.summary"), "field.first_callable_period"),
        (
            Edit("觸及事件判定日：最終評價日", "觸及事件判定日：觸及事件判定期間之各預定交易日", "iis.summary"),
            "field.ki_type",
        ),
        (Edit("1. 發行機構：法國外貿銀行 (", "1. 發行機構：法國外貿銀行台北分行 (", "iis.org"), "standard.issuer_name"),
        (Edit(f"(地址：{ADDRESS})", "(地址：台北市大安區虛構路2號)", "iis.org"), "standard.distributor"),
        (Edit("0%~5%", "0%~3%", "iis.fees"), "standard.fees"),
        (Edit(WARNING[:20], WARNING[:19] + "X", "iis.warn"), "standard.fixed_warning"),
        (Edit("刊印日期：2030年01月07日", "刊印日期：2030年01月20日", "iis.cover"), "doc.print_date"),
        (Edit("計價幣別：美元(USD)", "計價幣別：日圓(JPY)", "iis.summary"), "field.currency"),
        (Edit("TDCC商品代號為328199990001", "TDCC商品代號為328199990002", "iis.cover"), "iis.product_code"),
        (
            Edit(
                "最低申購金額：1單位商品面額，即10,000美元", "最低申購金額：1單位商品面額，即50,000美元", "iis.summary"
            ),
            "iis.min_subscription",
        ),
        (Edit("發行價格：100%", "發行價格：99%", "iis.summary"), "standard.issue_price"),
        (Edit("Unsecured and Non-Guaranteed)", "Unsecured and Guaranteed)", "iis.cover"), "iis.name_en"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_items_are_checked(tmp_path, edit, broken):
    r = check_iis(tmp_path, edits=[edit]).report
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)
