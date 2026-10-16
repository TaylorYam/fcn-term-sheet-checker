"""本機 Nomura 真實樣本（Issue #239）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/nomura-check-rules.md §5）：8 份說明書與 8 份投資人須知都辨識為 Nomura 範本；只有一份的
參考條件表到期日比說明書早 1 天（說明書跳過美國假日，表填錯，2026-10-15 確認），說明書與投資人須知都判不一致；其餘 7 組
通過並回填。2025 年文件 2 份的審查通過日期 2025-06-24 依交易日通過（Issue #239 加入審查標準）。參考條件表用
data/orders/核對表格式..xlsx（含 8 列 Nomura）。
"""

import datetime as dt
from collections import Counter

import pytest

from fcn_checker.issuers import NOMURA
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "156*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("156*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_nomura(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 8 and len(investor_sheets) == 8
    assert all(i.report.template == NOMURA.template_id for i in term_sheets)
    assert all(i.report.template == NOMURA.iis.template_id for i in investor_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, investor_sheets = items
    for docs in (term_sheets, investor_sheets):
        bad = Counter((r.rule_id, r.reason_code) for i in docs for r in i.report.results if r.status in PROBLEMS)
        assert bad == Counter({("field.maturity_date", "value_mismatch"): 1})  # 表上到期日少算一個美國假日（表填錯）


def test_products_whose_two_documents_pass_are_filled(items):
    term_sheets, _ = items
    assert sum(i.fills_sheet for i in term_sheets) == 7


def test_template_variants_match_the_sample_table(items):
    """KO 觀察方式、記憶式、KI 型態與範本規格 §4.9 樣本總表一致（單一標的判非記憶式）。"""
    term_sheets, _ = items

    def actual(item, rule_id):
        return next(r.actual for r in item.report.results if r.rule_id == rule_id)

    found = Counter(
        (actual(i, "field.ko_observation"), actual(i, "field.ko_memory"), actual(i, "field.ki_type"))
        for i in term_sheets
    )
    daily, periodic = "期間每日觀察", "期末定日觀察"
    assert found == Counter(
        {
            (periodic, "非記憶式", "每期觀察"): 1,  # 單一標的
            (periodic, "非記憶式", "無 KI"): 2,  # 含 Non-Call = 天期
            (daily, "非記憶式", "到期觀察"): 1,  # 單一標的
            (periodic, "記憶式", "無 KI"): 1,
            (daily, "記憶式", "無 KI"): 2,
            (daily, "記憶式", "每日觀察"): 1,
        }
    )


def test_the_2025_documents_use_the_2025_06_24_review_date(items):
    term_sheets, _ = items
    dates = Counter(r.expected for i in term_sheets for r in i.report.results if r.rule_id == "standard.approval_date")
    assert dates[dt.date(2025, 6, 24)] == 2
    assert all(
        r.status == S.PASS for i in term_sheets for r in i.report.results if r.rule_id == "standard.approval_date"
    )


def test_annual_rate_and_scenario_returns_follow_the_period_rate(items):
    term_sheets, investor_sheets = items
    for rid, n in (("derive.coupon_pa", 8), ("derive.annualized_return", 16), ("doc.coupon_mentions", 16)):
        found = [r for i in term_sheets for r in i.report.results if r.rule_id == rid]
        assert len(found) == n and all(r.status == S.PASS for r in found), rid
    found = [
        r for i in investor_sheets for r in i.report.results if r.rule_id in ("iis.coupon_pa", "iis.coupon_per_period")
    ]
    assert len(found) == 16 and all(r.status == S.PASS for r in found)
