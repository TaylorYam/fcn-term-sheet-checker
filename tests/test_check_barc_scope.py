"""Issue #41：依 #38 範圍新增的規則（文件內重複出現處、情境試算、審查標準固定值）。

測試切點同 test_check_barc.py：批量核對入口與合成資料；不直接測擷取函式。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from harness import MISMATCH, NA, PASS, REVIEW, problems, results
from pdf_writer import Edit
from reference_synth import UL
from synth import Spec, check

NEW_RULES = (
    "doc.name_consistency",
    "doc.distributor_product_code",
    "doc.currency_consistency",
    "doc.scenario_notional",
    "doc.price_header_pct",
    "doc.coupon_repeats",
    "doc.scenario_returns",
    "doc.observation_t_range",
    "standard.issuer_name",
    "standard.distributor",
    "standard.fees",
    "standard.issue_price",
)


def test_new_rules_pass_when_document_is_consistent(tmp_path):
    report = check(tmp_path)
    assert problems(report) == set()
    for rule_id in NEW_RULES:
        assert {r.status for r in results(report, rule_id)} == {PASS}, rule_id
    # 第 9 條(3) 與第 16 條都有逐處比對，且附證據
    repeats = {r.field: r for r in results(report, "doc.coupon_repeats")}
    assert set(repeats) == {"第9條(3)", "第16條"}
    assert all(r.document_evidence for r in repeats.values())


@pytest.mark.parametrize(
    "spec",
    [
        Spec(ko_obs="D", memory=False, ki="AM"),
        Spec(ko_obs="P", memory=True, ki="D"),
        Spec(
            ko_obs="P",
            memory=False,
            underlyings=(UL("單一標的公司", "紐約證券交易所", "SOLO UN", Decimal("45.6700")),),
        ),
    ],
    ids=["daily-nonmemory", "periodend-memory", "periodend-single"],
)
def test_t_range_only_applies_to_daily_memory(tmp_path, spec):
    report = check(tmp_path, spec)
    assert problems(report) == set()
    assert results(report, "doc.observation_t_range")[0].status == NA


def test_t_range_without_guaranteed_period(tmp_path):
    report = check(tmp_path, Spec(guaranteed=0))
    r = results(report, "doc.observation_t_range")[0]
    assert r.status == PASS and r.actual == "t=1～6"


# ---------------------------------------------------------------- 正式條款正確、重複出現處錯誤


@pytest.mark.parametrize(
    ("edits", "rule_id"),
    [
        # 正式月配息率（第 9、14、15(2)、17 條）全部正確，只有重複出現處其中一處錯
        ([Edit("相關配息率為1.0000%", "相關配息率為1.0100%", "ts.art9")], "doc.coupon_repeats"),
        ([Edit("(100% + 1.0000%)", "(100% + 1.0100%)", "ts.art16.i")], "doc.coupon_repeats"),
        ([Edit("1.0000%", "1.0100%", "ts.art16.ii")], "doc.coupon_repeats"),
        ([Edit("1.0000%", "0.1000%", "ts.art16.iii")], "doc.coupon_repeats"),
        # 定義句正確，只有價格表或情境表欄頭的執行比例錯
        ([Edit("格的70.00%", "格的71.00%", "ts.art15.price")], "doc.price_header_pct"),
        ([Edit("格的70.00%", "格的71.00%", "ts.art16.price")], "doc.price_header_pct"),
        ([Edit("最初價格的70.00%", "最初價格的71.00%", "ts.art16.ii")], "doc.price_header_pct"),
        # 情境試算的總報酬與年化率
        ([Edit("= 6.0000%", "= 6.10%", "ts.art16.ii")], "doc.scenario_returns"),
        ([Edit("報酬率：12.00%", "報酬率：12.50%", "ts.art16.ii")], "doc.scenario_returns"),
        ([Edit("- 1 = 1.0000%", "- 1 = 1.1000%", "ts.art16.i")], "doc.scenario_returns"),
        # 其他重複出現處
        ([Edit("10,000 美元", "5,000 美元", "ts.art16")], "doc.scenario_notional"),
        ([Edit("至6 的情況", "至5 的情況", "ts.art13")], "doc.observation_t_range"),
        (
            [
                Edit("029199990001", "029199990002", "ts.cover.distributor_code"),
                Edit("受託或銷售機構商品代號:029199990001", "受託或銷售機構商品代號:029199990002", "iis.p1"),
            ],
            "doc.distributor_product_code",
        ),
        ([Edit("計價幣別：美元", "計價幣別：日幣", "ts.art5")], "doc.currency_consistency"),
    ],
)
def test_repeated_value_wrong_while_formal_terms_are_right(tmp_path, edits, rule_id):
    report = check(tmp_path, edits=edits)
    assert problems(report) == {(rule_id, MISMATCH)}
    bad = [r for r in results(report, rule_id) if r.status == MISMATCH]
    assert bad and all(r.document_evidence for r in bad)


def test_repeated_coupon_mismatch_points_to_the_wrong_place(tmp_path):
    report = check(tmp_path, edits=[Edit("1.0000%", "1.0100%", "ts.art16.ii")])
    r = {x.field: x for x in results(report, "doc.coupon_repeats")}
    assert r["第9條(3)"].status == PASS
    assert r["第16條"].status == MISMATCH and r["第16條"].actual == ["1.0100"]
    assert any("1.0100" in e.text for e in r["第16條"].document_evidence)
    assert {x.status for x in results(report, "doc.coupon_consistency")} == {PASS}


def test_strike_header_mismatch_while_definition_matches_reference_k(tmp_path):
    report = check(tmp_path, edits=[Edit("格的70.00%", "格的71.00%", "ts.art15.price")])
    assert results(report, "field.strike_pct")[0].status == PASS
    assert {r.status for r in results(report, "derive.prices")} == {PASS}
    r = results(report, "doc.price_header_pct", "strike_pct")[0]
    assert r.status == MISMATCH and r.expected == Decimal("70.00") and r.actual == ["第15條 71.00%"]


@pytest.mark.parametrize(
    "edits",
    [
        [Edit("6個月", "7個月", "ts.title")],
        [Edit("記憶式", "", "ts.art1"), Edit("（下稱「本商品」）", "", "ts.art1")],
    ],
    ids=["title", "article1"],
)
def test_product_name_repeated_elsewhere_must_match_cover(tmp_path, edits):
    report = check(tmp_path, edits=edits)
    assert problems(report) == {("doc.name_consistency", MISMATCH)}


def test_half_width_name_suffix_is_stripped_for_title(tmp_path):
    report = check(tmp_path, edits=[Edit("（下稱「本商品」）", "(下稱「本商品」)", "ts")])  # 標題本來就不含後綴
    assert problems(report) == set()
    assert {r.status for r in results(report, "doc.name_consistency")} == {PASS}


def test_daily_related_coupon_in_unknown_wording_requires_review(tmp_path):
    report = check(tmp_path, edits=[Edit("相關配息率為1.0000%", "相關配息率為約1.0000%", "ts.art9")])
    assert problems(report) == {("doc.coupon_repeats", REVIEW)}
    r = results(report, "doc.coupon_repeats", "第9條(3)")[0]
    assert r.reason_code == "document_missing"


def test_scenario_return_unreadable_requires_review(tmp_path):
    report = check(tmp_path, edits=[Edit("- 1 = 1.0000%", "- 1 = 約1%", "ts.art16.i")])
    r = results(report, "doc.scenario_returns", "scenario_favourable_total")[0]
    assert r.status == REVIEW and r.reason_code == "document_missing"


# ---------------------------------------------------------------- 審查標準固定值


@pytest.mark.parametrize(
    ("edits", "rule_id", "field"),
    [
        (
            [Edit("巴克萊銀行股份有限公司", "巴克萊銀行有限公司", "ts.cover.issuer")],
            "standard.issuer_name",
            "issuer_name_cover",
        ),
        ([Edit("（Barclays Bank PLC）", "", "ts.ch二.1")], "standard.issuer_name", "issuer_name_ch2"),
        (
            [Edit("02-5556-1313", "02-5556-1314", "ts.cover.distributor")],
            "standard.distributor",
            "distributor_phone_cover",
        ),
        (
            [Edit("玉山綜合證券股份有限公司", "玉山證券股份有限公司", "ts.cover.distributor")],
            "standard.distributor",
            "distributor_name_cover",
        ),
        ([Edit("158號6樓", "158號7樓", "ts.ch二.5")], "standard.distributor", "distributor_address_ch2"),
        (
            [Edit("0%~5%", "0%~3%", "ts.ch四.fees.分銷費用"), Edit("0%~5%", "0%~3%", "iis.p3.fees.分銷費用")],
            "standard.fees",
            "分銷費用",
        ),
    ],
)
def test_fixed_value_differs_from_review_standard(tmp_path, edits, rule_id, field):
    report = check(tmp_path, edits=edits)
    assert problems(report) == {(rule_id, MISMATCH)}
    assert [r.field for r in results(report, rule_id) if r.status == MISMATCH] == [field]


def test_fixed_values_ignore_line_breaks(tmp_path):
    # 地址在 PDF 中常因換行出現空白（例：「158 號6 樓」）
    report = check(tmp_path, edits=[Edit("158號6樓", "158 號6 樓", "ts.ch二.5")])
    assert {r.status for r in results(report, "standard.distributor")} == {PASS}


def test_non_standard_issue_price_requires_review(tmp_path):
    report = check(tmp_path, edits=[Edit("面額之100%", "面額之99.5%")])  # 說明書與投資人須知
    assert problems(report) == {("standard.issue_price", REVIEW)}
    assert report.status == REVIEW
