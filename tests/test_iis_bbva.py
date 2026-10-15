"""BBVA 投資人須知黑箱回歸（Issue #269）：合成說明書＋投資人須知經批量入口；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bbva_synth import WARNING, Spec, check_iis
from fcn_checker.issuers import BBVA
from fcn_checker.schema import CheckStatus as S
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from test_check_bbva import VARIANTS, _dir


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass(tmp_path, spec):
    r = check_iis(tmp_path, spec).report
    assert r.template == BBVA.iis.template_id
    assert r.status == S.PASS, problems(r)
    assert rule(r, "iis.page_totals")[0].status == S.PASS
    assert len(rule(r, "iis.product_code")) == 2  # TDCC 商品代號、受託機構商品代號
    assert rule(r, "standard.fixed_warning")[0].actual == 2  # 警語 1.、商品簡介 (3)
    assert rule(r, "iis.redemption_start")[0].status == S.PASS
    assert not [x for x in r.results if x.rule_id == "doc.print_date"]  # 範本沒有刊印日期
    assert not [x for x in r.results if x.rule_id == "field.first_callable_period"]  # 只寫參照說明書
    assert bool([x for x in r.results if x.rule_id == "field.ki_pct"]) == (spec.ki != "none")
    assert bool([x for x in r.results if x.rule_id == "iis.no_ki_barrier"]) == (spec.ki == "none")
    assert bool([x for x in r.results if x.rule_id == "iis.ko_mentions"]) == spec.memory
    periodic = spec.ko_obs == "P"
    assert len([x for x in r.results if x.rule_id == "iis.coupon_per_period"]) == (
        2 if periodic else 0
    )  # 每期配息率、計息天數慣例
    assert bool([x for x in r.results if x.rule_id == "field.coupon_pa_pct"]) == (not periodic)
    assert {x.field for x in rule(r, "standard.distributor")} == {
        "distributor_name_org",
        "distributor_address_org",
        "distributor_phone_org",
    }


@pytest.mark.parametrize(
    ("spec", "edit", "broken"),
    [
        pytest.param(
            Spec(),
            Edit(
                "開始受理透過次級市場買賣之贖回日期：2030年1月15日",
                "開始受理透過次級市場買賣之贖回日期：2030年1月16日",
                "iis.summary",
            ),
            ("iis.redemption_start", "redemption_start"),
            id="開始受理贖回日期",
        ),
        pytest.param(
            Spec(ko_obs="P"),
            Edit("配息率（Rate of Interest）：1.5000%", "配息率（Rate of Interest）：1.6000%", "iis.coupon"),
            ("iis.coupon_per_period", "coupon_per_period_pct"),
            id="每期配息率",
        ),
        pytest.param(
            Spec(ko_obs="P"),
            Edit("「1/1", "「30/360", "iis.coupon"),
            ("iis.coupon_per_period", "day_count"),
            id="計息天數慣例",
        ),
        pytest.param(
            Spec(),
            Edit("最佳表現值大於或等於100.00%（出場值）", "最佳表現值大於或等於105.00%（出場值）", "iis.coupon"),
            ("iis.ko_mentions", "ko_at_maturity_pct"),
            id="到期條件出場值",
        ),
        pytest.param(
            Spec(ki="none"),
            Edit("最差表現值大於或等於75.00%：100%", "最差表現值大於或等於70.00%：100%", "iis.coupon"),
            ("iis.no_ki_barrier", "no_ki_barrier_pct"),
            id="無 KI 到期門檻",
        ),
        pytest.param(
            Spec(),
            Edit("ZBB UN Equity", "ZBX UN Equity", "iis.summary"),
            ("field.underlyings", "underlyings"),
            id="標的",
        ),
        pytest.param(
            Spec(),
            Edit("受託機構商品代號：326199990001", "受託機構商品代號：326199990009", "iis.cover"),
            ("iis.product_code", "product_code_distributor"),
            id="受託機構商品代號",
        ),
        pytest.param(
            Spec(),
            Edit("(電話: +886 2 5556 1313", "(電話: 02-0000-0000", "iis.org"),
            ("standard.distributor", "distributor_phone_org"),
            id="受託機構電話",
        ),
        pytest.param(
            Spec(),
            Edit("B.V. (營業所在地", "B.V. Ltd. (營業所在地", "iis.org"),
            ("standard.issuer_name", "issuer_name_org"),
            id="發行機構名稱",
        ),
    ],
)
def test_iis_rules_catch_errors(tmp_path, spec, edit, broken):
    r = check_iis(tmp_path, spec, edits=[edit]).report
    assert broken in {(rid, f) for rid, f, _ in problems(r)}, problems(r)


def test_iis_coupon_is_compared_with_the_sheet(tmp_path):
    """KO 每日型的年利率和參考條件表比；KO 每期型的每期配息率 × 12 也要等於表上年利率（共用 iis.coupon_pa）。"""
    r = check_iis(tmp_path, Spec(), **{"Coupon p.a. (%)": 17.0}).report
    assert rule(r, "field.coupon_pa_pct")[0].status == S.MISMATCH
    r = check_iis(_dir(tmp_path, "p"), Spec(ko_obs="P"), **{"Coupon p.a. (%)": 17.0}).report
    assert rule(r, "iis.coupon_pa")[0].status == S.MISMATCH


def test_forbidden_wording_follows_the_trade_date_exemption(tmp_path):
    """發行不成立之處理「受託投資本商品之總金額」不加允許片語：交易日在特例期之後判不一致（Issue #247）。"""
    edit = Edit(
        "發行機構可全權決定取消本商品之發行。", "若受託投資本商品之總金額未達最低申購總金額，得取消發行。", "iis.coupon"
    )
    r = check_iis(tmp_path, Spec(), edits=[edit]).report
    assert rule(r, "standard.forbidden_wording")[0].status == S.MISMATCH


def test_fixed_warning_needs_two_occurrences(tmp_path):
    r = check_iis(tmp_path, Spec(), edits=[Edit(WARNING, "本商品風險程度等級為【RR4】。", "iis.summary")]).report
    assert rule(r, "standard.fixed_warning")[0].status == S.MISMATCH


def test_five_pages_is_a_mismatch(tmp_path):
    r = check_iis(tmp_path, Spec(iis_pages=5)).report
    assert rule(r, "iis.pages")[0].status == S.MISMATCH


def test_missing_section_title_fails_recognition(tmp_path):
    r = check_iis(tmp_path, Spec(), edits=[Edit("四、 費用", "四、 其他", "iis.fees")]).report
    assert rule(r, "template.detect")[0].status == S.REVIEW_REQUIRED


def test_ki_percentage_is_compared_with_the_sheet(tmp_path):
    r = check_iis(tmp_path, Spec(ki_pct=Decimal("60.00")), **{"KI(%)": 55.0}).report
    assert rule(r, "field.ki_pct")[0].status == S.MISMATCH
