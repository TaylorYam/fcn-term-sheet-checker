"""Nomura 中文投資人須知（Issue #239）：docs/templates/nomura-zh-iis.md、docs/rules/iis-check-rules.md §3 Nomura 欄。

合成 PDF 經公開批量入口核對（說明書＋投資人須知＋參考條件表一列）；商品、標的與價格皆虛構。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import bnp_synth
import citi_synth
import ms_synth
import sg_synth
import synth
from fcn_checker.issuers import NOMURA
from harness import MISMATCH, NA, PASS, REVIEW, check_all
from nomura_synth import WARNING, Spec, build_iis_pdf, check_item
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row


def pair(tmp_path, spec: Spec | None = None, **kw):
    """（說明書, 投資人須知）兩份的批量核對項目。"""
    ts = check_item(tmp_path, spec or Spec(), **kw)
    return ts, ts.partner


D_SPEC = Spec(ko_obs="D")
KO_START = D_SPEC.date(D_SPEC.ends[0])


def bad(item) -> set[tuple[str, str, str]]:
    return {(r.rule_id, r.field, r.status) for r in item.report.results if r.status not in (PASS, NA)}


VARIANTS = {
    "P 記憶式、無 KI": Spec(),
    "D 記憶式、每日 KI": Spec(ko_obs="D", ki="D", first_callable=2, ko=Decimal("105.00")),
    "D 單一標的、到期 KI": Spec(count=1, ko_obs="D", ki="AM", tenor=4, ko=Decimal("95.00")),
    "P 單一標的、每期 KI、2025 年文件": Spec(count=1, ki="P", tenor=4, currency_zh="澳幣", market="JT", spaced=True),
    "P 非記憶式、日圓": Spec(memory=False, currency_zh="日圓", market="JT", annual=Decimal("6.00")),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_consistent_nomura_term_sheet_and_iis_pass_and_the_term_sheet_is_filled(tmp_path, spec):
    ts, iis = pair(tmp_path, spec)
    assert iis.report.template == NOMURA.iis.template_id
    assert bad(iis) == set() and iis.report.status == PASS
    assert ts.report.status == PASS and ts.fills_sheet
    checked = {(r.rule_id, r.field) for r in iis.report.results if r.status == PASS}
    for rule, field in [
        ("iis.pages", "pages"),
        ("iis.page_totals", "page_totals"),
        ("iis.product_code", "product_code_tdcc"),
        ("iis.isin", "isin"),
        ("iis.name_zh", "name_zh"),
        ("iis.name_en", "name_en"),
        ("iis.issue_date", "issue_date"),
        ("iis.coupon_pa", "coupon_per_period_pct"),
        ("iis.coupon_per_period", "coupon_per_period_pct"),
        ("iis.underlying_names", "underlying_names_en"),
        ("iis.redemption_start", "redemption_start_text"),
        ("iis.redemption_start", "redemption_later_text"),
        ("field.currency", "currency"),
        ("field.tenor_months", "tenor_months"),
        ("field.trade_date", "trade_date"),
        ("field.maturity_date", "maturity_date"),
        ("field.ko_observation", "ko_observation"),
        ("field.ko_memory", "ko_memory"),
        ("field.ki_type", "ki_type"),
        ("standard.fixed_warning", "fixed_warning"),
        ("standard.risk_level", "risk_level_summary"),
        ("standard.product_type", "product_type"),
        ("standard.minimum_issue_size", "minimum_issue_size"),
        ("standard.issuer_name", "issuer_name_org"),
        ("standard.guarantor_name", "guarantor_name_org"),
        ("standard.agent_name", "agent_name_org"),
        ("standard.distributor", "distributor_name_org"),
        ("standard.distributor", "distributor_address_org"),
        ("standard.distributor", "distributor_phone_rights"),
        ("standard.fees", "分銷費用"),
    ]:
        assert (rule, field) in checked, (rule, field)
    if spec.ko_obs == "D":
        assert ("iis.ko_observation_dates", "ko_observation_start") in checked
    # 範本沒有的欄位不核對、不報缺漏：刊印日期、價格、百分比、最終評價日、面額、受理申購日
    rules = {r.rule_id for r in iis.report.results}
    assert not rules & {"doc.print_date", "iis.underlying_prices", "field.strike_pct", "field.final_valuation_date"}
    assert not rules & {"doc.subscription_start_date", "field.denomination", "iis.min_subscription"}


def test_fixed_warning_appears_once(tmp_path):
    _, iis = pair(tmp_path)
    assert [r.actual for r in iis.report.results if r.rule_id == "standard.fixed_warning"] == [1]


@pytest.mark.parametrize(
    ("spec", "edit", "expected"),
    [
        (
            Spec(),
            Edit("就每配息日而言，0.8333%。*", "就每配息日而言，0.8500%。*", "iis.coupon"),
            {
                ("iis.coupon_pa", "coupon_per_period_pct", MISMATCH),
                ("iis.coupon_per_period", "coupon_per_period_pct", MISMATCH),
            },
        ),
        (
            D_SPEC,
            Edit(f"「自動提前出場決定日」：自{KO_START}", "「自動提前出場決定日」：自2030年3月11日", "iis.coupon"),
            {("iis.ko_observation_dates", "ko_observation_start", MISMATCH)},
        ),
        (
            Spec(),
            Edit("連結標的2: ALPHA 2 HOLDINGS INC", "連結標的2: ALPHA 9 HOLDINGS INC", "iis.summary"),
            {("iis.underlying_names", "underlying_names_en", MISMATCH)},
        ),
        (
            Spec(),
            Edit("到期日之前第五個營業日", "到期日之前第三個營業日", "iis.summary"),
            {("iis.redemption_start", "redemption_later_text", MISMATCH)},
        ),
        (
            Spec(),
            Edit("詳見中文產品說明書第11項(6)「自動提前出場決定日」", "詳見說明書", "iis.coupon"),
            {("field.ko_observation", "ko_observation", REVIEW)},
        ),
        (
            Spec(ki="AM"),
            Edit("「下限觸發決定日」：最終評價日。", "「下限觸發決定日」：每月最後一個交易日。", "iis.coupon"),
            {("field.ki_type", "ki_type", REVIEW)},
        ),
        (
            Spec(),
            Edit(WARNING[:20], WARNING[:19] + "X", "iis.warn"),
            {("standard.fixed_warning", "fixed_warning", MISMATCH)},
        ),
        (
            Spec(),
            Edit("2. 保證機構：Nomura Holdings, Inc.", "2. 保證機構：Nomura Holdings Inc.", "iis.org"),
            {("standard.guarantor_name", "guarantor_name_org", MISMATCH)},
        ),
        (
            Spec(),
            Edit("有效申購金額總計至少達三十萬美元", "有效申購金額總計至少達五十萬美元", "iis.redeem"),
            {("standard.minimum_issue_size", "minimum_issue_size", MISMATCH)},
        ),
    ],
    ids=[
        "每期配息率",
        "D 型 KO 起日",
        "標的英文名稱",
        "後續受理贖回日期",
        "KO 觀察方式寫法",
        "KI 寫法",
        "固定警語",
        "保證機構",
        "發行不成立門檻",
    ],
)
def test_iis_rules_find_the_wrong_value(tmp_path, spec, edit, expected):
    ts, iis = pair(tmp_path, spec, edits=[edit])
    assert ts.report.status == PASS, bad(ts)  # 只改投資人須知
    assert bad(iis) == expected


def test_page_count_is_checked(tmp_path):
    ts, iis = pair(tmp_path, Spec(iis_pages=5))
    assert ("iis.pages", "pages", MISMATCH) in bad(iis)
    assert not any(r.rule_id == "iis.page_totals" and r.status != PASS for r in iis.report.results)


@pytest.mark.parametrize(
    "build",
    [
        lambda p: synth.build_iis_pdf(p, synth.Spec()),
        lambda p: ms_synth.build_iis_pdf(p, ms_synth.Spec()),
        lambda p: bnp_synth.build_iis_pdf(p, bnp_synth.Spec()),
        lambda p: sg_synth.build_iis_pdf(p, sg_synth.Spec()),
        lambda p: citi_synth.build_iis_pdf(p, citi_synth.Spec()),
    ],
    ids=["BARC", "MS", "BNP", "SG", "Citi"],
)
def test_other_issuers_iis_are_not_nomura(tmp_path, build):
    spec = Spec()
    pdf = build(tmp_path / f"{spec.product_code}_IIS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    for item in check_all(sheet, [pdf]).items:
        assert item.report.template != NOMURA.iis.template_id


def test_nomura_iis_is_not_a_term_sheet(tmp_path):
    """投資人須知誤命名成 `_TS.pdf`：說明書範本不會把它認成 Nomura 說明書。"""
    spec = Spec()
    pdf = build_iis_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    item = next(i for i in check_all(sheet, [pdf]).items if i.term_sheet == pdf)
    assert item.report.template != NOMURA.template_id
