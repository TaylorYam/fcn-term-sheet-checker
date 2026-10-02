"""Issue #41：依 #38 範圍新增的規則（文件內重複出現處、情境試算、審查標準固定值）。

測試切點同 test_check_barc.py：批量核對入口與合成資料；不直接測擷取函式。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from harness import MISMATCH, NA, PASS, REVIEW, problems, results
from synth import UL, Spec, check

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
    ("pdf_kw", "rule_id"),
    [
        # 正式月配息率（第 9、14、15(2)、17 條）全部正確，只有重複出現處其中一處錯
        ({"repeat_overrides": {"§9(3)": "1.0100"}}, "doc.coupon_repeats"),
        ({"repeat_overrides": {"§16(i)": "1.0100"}}, "doc.coupon_repeats"),
        ({"repeat_overrides": {"§16(ii)": "1.0100"}}, "doc.coupon_repeats"),
        ({"repeat_overrides": {"§16(iii)": "0.1000"}}, "doc.coupon_repeats"),
        # 定義句正確，只有價格表或情境表欄頭的執行比例錯
        ({"strike_headers": {"§15": "71.00"}}, "doc.price_header_pct"),
        ({"strike_headers": {"§16": "71.00"}}, "doc.price_header_pct"),
        ({"strike_headers": {"§16(ii)": "71.00"}}, "doc.price_header_pct"),
        # 情境試算的總報酬與年化率
        ({"general_total": "6.10"}, "doc.scenario_returns"),
        ({"general_annualized": "12.50"}, "doc.scenario_returns"),
        ({"favourable_total": "1.1000"}, "doc.scenario_returns"),
        # 其他重複出現處
        ({"scenario_notional": 5000}, "doc.scenario_notional"),
        ({"t_range_end": 5}, "doc.observation_t_range"),
        ({"distributor_code": "029199990002"}, "doc.distributor_product_code"),
        ({"art5_currency": "日幣"}, "doc.currency_consistency"),
    ],
)
def test_repeated_value_wrong_while_formal_terms_are_right(tmp_path, pdf_kw, rule_id):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(**pdf_kw))
    assert problems(report) == {(rule_id, MISMATCH)}
    bad = [r for r in results(report, rule_id) if r.status == MISMATCH]
    assert bad and all(r.document_evidence for r in bad)


def test_repeated_coupon_mismatch_points_to_the_wrong_place(tmp_path):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(repeat_overrides={"§16(ii)": "1.0100"}))
    r = {x.field: x for x in results(report, "doc.coupon_repeats")}
    assert r["第9條(3)"].status == PASS
    assert r["第16條"].status == MISMATCH and r["第16條"].actual == ["1.0100"]
    assert any("1.0100" in e.text for e in r["第16條"].document_evidence)
    assert {x.status for x in results(report, "doc.coupon_consistency")} == {PASS}


def test_strike_header_mismatch_while_definition_matches_reference_k(tmp_path):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(strike_headers={"§15": "71.00"}))
    assert results(report, "field.strike_pct")[0].status == PASS
    assert {r.status for r in results(report, "derive.prices")} == {PASS}
    r = results(report, "doc.price_header_pct", "strike_pct")[0]
    assert r.status == MISMATCH and r.expected == Decimal("70.00") and r.actual == ["第15條 71.00%"]


@pytest.mark.parametrize(
    "pdf_kw",
    [
        {
            "title_name": "英商巴克萊銀行7個月美元計價連結股權記憶式自動提前出場結構型商品（不保本）（無擔保及無保證機構）"
        },
        {"art1_name": "英商巴克萊銀行6個月美元計價連結股權自動提前出場結構型商品（不保本）（無擔保及無保證機構）"},
    ],
    ids=["title", "article1"],
)
def test_product_name_repeated_elsewhere_must_match_cover(tmp_path, pdf_kw):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(**pdf_kw))
    assert problems(report) == {("doc.name_consistency", MISMATCH)}


def test_half_width_name_suffix_is_stripped_for_title(tmp_path):
    spec = Spec()
    name = spec.expected_name_zh().replace("（下稱「本商品」）", "(下稱「本商品」)")
    title = spec.expected_name_zh().replace("（下稱「本商品」）", "")
    report = check(tmp_path, spec, pdf_spec=spec.with_(name_zh=name, title_name=title))
    assert problems(report) == set()
    assert {r.status for r in results(report, "doc.name_consistency")} == {PASS}


def test_daily_related_coupon_in_unknown_wording_requires_review(tmp_path):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(repeat_overrides={"§9(3)": "約1.0000"}))
    assert problems(report) == {("doc.coupon_repeats", REVIEW)}
    r = results(report, "doc.coupon_repeats", "第9條(3)")[0]
    assert r.reason_code == "document_missing"


def test_scenario_return_unreadable_requires_review(tmp_path):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(favourable_total="約1"))
    r = results(report, "doc.scenario_returns", "scenario_favourable_total")[0]
    assert r.status == REVIEW and r.reason_code == "document_missing"


# ---------------------------------------------------------------- 審查標準固定值


@pytest.mark.parametrize(
    ("pdf_kw", "rule_id", "field"),
    [
        ({"issuer_cover": "英商巴克萊銀行有限公司（Barclays Bank PLC）"}, "standard.issuer_name", "issuer_name_cover"),
        ({"issuer_ch2": "英商巴克萊銀行股份有限公司"}, "standard.issuer_name", "issuer_name_ch2"),
        (
            {"distributor_cover": ("玉山綜合證券股份有限公司", "02-5556-1314", "台北市松山區民生東路三段158號6樓")},
            "standard.distributor",
            "distributor_phone_cover",
        ),
        (
            {"distributor_cover": ("玉山證券股份有限公司", "02-5556-1313", "台北市松山區民生東路三段158號6樓")},
            "standard.distributor",
            "distributor_name_cover",
        ),
        (
            {"distributor_address_ch2": "台北市松山區民生東路三段158號7樓"},
            "standard.distributor",
            "distributor_address_ch2",
        ),
        ({"fees": {"分銷費用": "0%~3%"}}, "standard.fees", "分銷費用"),
    ],
)
def test_fixed_value_differs_from_review_standard(tmp_path, pdf_kw, rule_id, field):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(**pdf_kw))
    assert problems(report) == {(rule_id, MISMATCH)}
    assert [r.field for r in results(report, rule_id) if r.status == MISMATCH] == [field]


def test_fixed_values_ignore_line_breaks(tmp_path):
    # 地址在 PDF 中常因換行出現空白（例：「158 號6 樓」）
    spec = Spec(distributor_address_ch2="台北市松山區民生東路三段158 號6 樓")
    report = check(tmp_path, spec)
    assert {r.status for r in results(report, "standard.distributor")} == {PASS}


def test_non_standard_issue_price_requires_review(tmp_path):
    spec = Spec()
    report = check(tmp_path, spec, pdf_spec=spec.with_(issue_price="99.5"))
    assert problems(report) == {("standard.issue_price", REVIEW)}
    assert report.status == REVIEW
