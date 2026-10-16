"""本機 JPM 真實樣本（Issue #233）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/jpm-check-rules.md §5）：5 份說明書與 5 份投資人須知都辨識為 JPM 範本，全部通過、
全部回填。表上標的排序和說明書不同的多標的商品依代號配對後通過（說明書依英文公司名稱字母順序排列；期初定價 VWAP 的
那份價格不比對）。參考條件表用含 JPM 列的公司原始待建檔表 data/orders/核對表格式..xlsx。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import JPM
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "076*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("076*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def _reordered(item, rid: str) -> bool:
    """價格結果的項目（表上那一格）不是依文件順序的 UL_n：表上排序和文件不同、依代號配對。"""
    first = [r for r in item.report.results if r.rule_id == rid and r.field.endswith(("進場價", "進場 價"))]
    return any(r.item.name != f"UL_{n} 進場價" for n, r in enumerate(first, start=1))


def test_every_sample_is_recognised_as_jpm(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 5 and len(investor_sheets) == 5
    assert all(i.report.template == JPM.template_id for i in term_sheets)
    assert all(i.report.template == JPM.iis.template_id for i in investor_sheets)


def test_samples_pass_as_the_exploration_said(items):
    term_sheets, investor_sheets = items
    bad = Counter(
        (r.rule_id, r.reason_code)
        for i in term_sheets + investor_sheets
        for r in i.report.results
        if r.status in PROBLEMS
    )
    assert bad == Counter()
    assert all(i.report.status == S.PASS for i in term_sheets + investor_sheets)


def test_underlyings_in_a_different_order_pass_by_ticker(items):
    """多標的的 4 份表上排序都和說明書不同：說明書的 VWAP 那份價格不和表上比，其餘 3 份價格依代號對到表上那一格；
    投資人須知 4 份都依代號標出表上那一格（VWAP 那份改和說明書比）。"""
    term_sheets, investor_sheets = items
    assert sum(_reordered(i, "field.underlying_prices") for i in term_sheets) == 3
    assert sum(_reordered(i, "iis.underlying_prices") for i in investor_sheets) == 4
    for i in term_sheets + investor_sheets:
        assert [r.status for r in i.report.results if r.rule_id == "field.underlyings"] == [S.PASS]


def test_ko_and_ki_types_match_the_exploration(items):
    """樣本總表：KO 每日 3 份、每期 2 份；記憶式 3 份；KI 無 3 份、每日 1 份（單一標的）、到期 1 份。"""
    term_sheets, investor_sheets = items
    for group in (term_sheets, investor_sheets):
        found = Counter(
            (r.rule_id, r.actual)
            for i in group
            for r in i.report.results
            if r.rule_id in ("field.ko_observation", "field.ko_memory", "field.ki_type")
        )
        assert found == Counter(
            {
                ("field.ko_observation", "期間每日觀察"): 3,
                ("field.ko_observation", "期末定日觀察"): 2,
                ("field.ko_memory", "記憶式"): 3,
                ("field.ko_memory", "非記憶式"): 2,
                ("field.ki_type", "無 KI"): 3,
                ("field.ki_type", "每日觀察"): 1,
                ("field.ki_type", "到期觀察"): 1,
            }
        )


def test_every_product_is_filled(items):
    term_sheets, _ = items
    assert sum(i.fills_sheet for i in term_sheets) == 5
