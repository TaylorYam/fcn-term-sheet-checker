"""本機 BBVA 真實樣本（Issue #269）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/bbva-check-rules.md §5）：5 份說明書與 5 份投資人須知都辨識為 BBVA 範本（說明書章名
「揭露」是 CJK 相容字，轉換後才辨識得到；合成文件畫不出相容字，由這裡涵蓋）；差異只有一組
參考條件表到期日差一年（表上錯，說明書與投資人須知都判到期日不一致）；其餘 4 組通過並回填（含標的順序和表上不同的那組、
VWAP 那組；「受託投資本商品之總金額」依 Issue #247 交易日特例通過）。參考條件表用 data/orders/核對表格式..xlsx（含 5 列 BBVA）。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import BBVA
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "326*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("326*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_bbva(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 5 and len(investor_sheets) == 5
    assert all(i.report.template == BBVA.template_id for i in term_sheets)
    assert all(i.report.template == BBVA.iis.template_id for i in investor_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, investor_sheets = items

    def bad(docs):
        return Counter((r.rule_id, r.reason_code) for i in docs for r in i.report.results if r.status in PROBLEMS)

    assert bad(term_sheets) == Counter({("field.maturity_date", "value_mismatch"): 1})
    assert bad(investor_sheets) == Counter({("field.maturity_date", "value_mismatch"): 1})


def test_products_whose_two_documents_pass_are_filled(items):
    term_sheets, _ = items
    assert sum(i.fills_sheet for i in term_sheets) == 4


def test_forbidden_wording_passes_under_the_trade_date_exemption(items):
    """第四章／投資人須知發行不成立「受託投資本商品之總金額」：交易日都在 2026-10-15 以前，依 Issue #247 特例通過。"""
    term_sheets, investor_sheets = items
    found = [
        r for i in term_sheets + investor_sheets for r in i.report.results if r.rule_id == "standard.forbidden_wording"
    ]
    assert len(found) == 10 and all(r.status == S.PASS and r.actual for r in found)


def test_template_variants_match_the_sample_table(items):
    """KO 觀察方式、記憶式、KI 型態與範本規格 §4.11 樣本總表一致。"""
    term_sheets, _ = items

    def actual(item, rule_id):
        return next((r.actual for r in item.report.results if r.rule_id == rule_id), None)

    found = Counter(
        (actual(i, "field.ko_observation"), actual(i, "field.ko_memory"), actual(i, "field.ki_type"))
        for i in term_sheets
    )
    assert found == Counter(
        {
            ("期間每日觀察", "記憶式", "無 KI"): 1,
            ("期末定日觀察", "記憶式", "到期觀察"): 1,
            ("期間每日觀察", "非記憶式", "無 KI"): 1,
            ("期間每日觀察", "記憶式", "到期觀察"): 2,
        }
    )
