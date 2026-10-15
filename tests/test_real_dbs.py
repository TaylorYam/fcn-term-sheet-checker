"""本機 DBS 真實樣本（Issue #280）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/dbs-check-rules.md §5）：4 份說明書與 4 份投資人須知都辨識為 DBS 範本，全部通過並回填
（受託機構「(含國際證券業務分公司)」與「(02)5556-1313」是等價寫法、「受託投資」都是允許片語、投資人須知價格表表頭與費用表列
跨頁照讀）。參考條件表用 data/orders/核對表格式..xlsx（含 4 列 DBS）。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import DBS
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import TERM_SHEETS, pdfs, requires

ORDERS = TERM_SHEETS.parent / "orders" / "核對表格式..xlsx"

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "078*_TS.pdf", ORDERS)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(ORDERS, pdfs("078*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_dbs(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 4 and len(investor_sheets) == 4
    assert all(i.report.template == DBS.template_id for i in term_sheets)
    assert all(i.report.template == DBS.iis.template_id for i in investor_sheets)


def test_every_sample_passes_and_is_filled(items):
    term_sheets, investor_sheets = items
    bad = Counter(
        (r.rule_id, r.reason_code)
        for i in term_sheets + investor_sheets
        for r in i.report.results
        if r.status in PROBLEMS
    )
    assert not bad, bad
    assert all(i.fills_sheet for i in term_sheets)


def test_template_variants_match_the_sample_table(items):
    """KO 觀察方式、記憶式、KI 型態與範本規格 §4.11 樣本總表一致（只有 KO 每日記憶式）。"""
    term_sheets, _ = items

    def actual(item, rule_id):
        return next((r.actual for r in item.report.results if r.rule_id == rule_id), None)

    found = Counter(
        (actual(i, "field.ko_observation"), actual(i, "field.ko_memory"), actual(i, "field.ki_type"))
        for i in term_sheets
    )
    assert found == Counter({("期間每日觀察", "記憶式", "無 KI"): 2, ("期間每日觀察", "記憶式", "到期觀察"): 2})


def test_fixed_warning_and_allowed_phrases(items):
    """固定警語說明書 2 次、投資人須知 1 次；「受託投資」都在允許片語內。"""
    term_sheets, investor_sheets = items
    for docs, n in ((term_sheets, 2), (investor_sheets, 1)):
        for i in docs:
            assert next(r.actual for r in i.report.results if r.rule_id == "standard.fixed_warning") == n
            assert next(r.status for r in i.report.results if r.rule_id == "standard.forbidden_wording") == S.PASS
