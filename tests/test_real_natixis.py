"""本機 Natixis 真實樣本（Issue #261）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/natixis-check-rules.md §5）：8 份說明書與 8 份投資人須知都辨識為 Natixis 範本（新舊兩版）；
差異只有一組舊版文件「受託機構商品代號為」空白（兩份都轉人工覆核）、一份投資人須知 5 頁、一份投資人須知 KO 定義沒寫 %
（都判不一致）、單一標的那組參考條件表沒有列；其餘 4 組通過並回填（含 2024 年那份，審查日期補了 2024-03-20）。
參考條件表用 data/核對表格式..xlsx（含 7 列 Natixis）。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import NATIXIS
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "328*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("328*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_natixis(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 8 and len(investor_sheets) == 8
    assert all(i.report.template == NATIXIS.template_id for i in term_sheets)
    assert all(i.report.template == NATIXIS.iis.template_id for i in investor_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, investor_sheets = items

    def bad(docs):
        return Counter((r.rule_id, r.reason_code) for i in docs for r in i.report.results if r.status in PROBLEMS)

    assert bad(term_sheets) == Counter(
        {("doc.distributor_product_code", "blank_field"): 1, ("batch.pairing", "reference_row_missing"): 1}
    )
    assert bad(investor_sheets) == Counter(
        {
            ("doc.distributor_product_code", "blank_field"): 1,
            ("iis.pages", "value_mismatch"): 1,
            ("batch.pairing", "reference_row_missing"): 1,
        }
    )


def test_products_whose_two_documents_pass_are_filled(items):
    term_sheets, _ = items
    assert sum(i.fills_sheet for i in term_sheets) == 5  # NX7 KO 定義是參照說明書、不核對 KO %（Issue #272）


def test_template_variants_match_the_sample_table(items):
    """KO 觀察方式、記憶式、KI 型態與範本規格 §4.11 樣本總表一致（單一標的那份沒有參考條件表列）。"""
    term_sheets, _ = items

    def actual(item, rule_id):
        return next((r.actual for r in item.report.results if r.rule_id == rule_id), None)

    found = Counter(
        (actual(i, "field.ko_observation"), actual(i, "field.ko_memory"), actual(i, "field.ki_type"))
        for i in term_sheets
        if actual(i, "field.ko_observation")
    )
    assert found == Counter(
        {
            ("期間每日觀察", "記憶式", "每期觀察"): 1,
            ("期間每日觀察", "非記憶式", "到期觀察"): 1,
            ("期末定日觀察", "非記憶式", "無 KI"): 1,
            ("期末定日觀察", "記憶式", "到期觀察"): 1,
            ("期間每日觀察", "記憶式", "無 KI"): 1,
            ("期間每日觀察", "記憶式", "到期觀察"): 1,
            ("期間每日觀察", "記憶式", "每日觀察"): 1,
        }
    )


def test_every_internal_rule_passes_where_a_term_sheet_has_its_row(items):
    """有參考條件表列的 7 份：說明書內部規則（doc.／derive.／schedule.）只有空白的受託機構商品代號不通過。"""
    term_sheets, _ = items
    own = [
        r
        for i in term_sheets
        for r in i.report.results
        if r.rule_id.startswith(("doc.", "derive.", "schedule.")) and r.status in PROBLEMS
    ]
    assert [r.rule_id for r in own] == ["doc.distributor_product_code"]
