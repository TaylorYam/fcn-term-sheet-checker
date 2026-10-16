"""本機 GS 真實樣本（Issue #248）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/gs-check-rules.md §5）：5 份說明書與 5 份投資人須知都辨識為 GS 範本；只有一組的一檔日股
價格是參考條件表的 2 倍（作業人員確認表上錯），說明書與投資人須知都依共用規則轉人工覆核（疑似分割）；其餘 4 組通過並回填。
投資人須知的「受託投資」交易日都在 2026-10-15 以前，依共用特例通過（Issue #247）；投資人須知的 CJK 相容字「利」轉換後讀得到
配息率。參考條件表用 data/orders/核對表格式..xlsx（含 5 列 GS）。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import GS
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "148*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("148*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_gs(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 5 and len(investor_sheets) == 5
    assert all(i.report.template == GS.template_id for i in term_sheets)
    assert all(i.report.template == GS.iis.template_id for i in investor_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    """只有一組的一檔標的價格是表上 2 倍（4 個價格欄，表上錯）：兩份都轉人工覆核並寫出比例。"""
    term_sheets, investor_sheets = items
    for docs, rid in ((term_sheets, "field.underlying_prices"), (investor_sheets, "iis.underlying_prices")):
        bad = Counter((r.rule_id, r.reason_code) for i in docs for r in i.report.results if r.status in PROBLEMS)
        assert bad == Counter({(rid, "split_suspected"): 4})


def test_products_whose_two_documents_pass_are_filled(items):
    term_sheets, _ = items
    assert sum(i.fills_sheet for i in term_sheets) == 4


def test_template_variants_match_the_sample_table(items):
    """KO 觀察方式、記憶式、KI 型態、期初定價與範本規格 §4.11 樣本總表一致（單一標的判非記憶式）。"""
    term_sheets, _ = items

    def actual(item, rule_id):
        return next(r.actual for r in item.report.results if r.rule_id == rule_id)

    found = Counter(
        (actual(i, "field.ko_memory"), actual(i, "field.ki_type"), actual(i, "field.initial_pricing"))
        for i in term_sheets
    )
    assert found == Counter(
        {
            ("記憶式", "每日觀察", "開盤價"): 1,
            ("記憶式", "每日觀察", "收盤價"): 1,
            ("記憶式", "到期觀察", "VWAP"): 1,
            ("記憶式", "到期觀察", "收盤價"): 1,
            ("非記憶式", "無 KI", "開盤價"): 1,
        }
    )


def test_entrusted_investment_wording_is_exempt(items):
    _, investor_sheets = items
    found = [r for i in investor_sheets for r in i.report.results if r.rule_id == "standard.forbidden_wording"]
    assert found and all(r.status == S.PASS for r in found)


def test_monthly_coupon_is_read_after_compatibility_characters(items):
    term_sheets, investor_sheets = items
    for docs, rid in ((term_sheets, "derive.monthly_coupon"), (investor_sheets, "iis.monthly_coupon")):
        found = [r for i in docs for r in i.report.results if r.rule_id == rid]
        assert len(found) == 5 and all(r.status == S.PASS for r in found), rid
