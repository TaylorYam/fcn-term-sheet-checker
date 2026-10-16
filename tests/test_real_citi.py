"""本機 Citi 真實樣本（Issue #220）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/citi-check-rules.md §5）：8 份說明書都辨識為 Citi 範本；只有探勘列出的差異：
舊文件（2024）的受託或銷售機構商品代號空白（它寫的審查通過日期 2024-08-28 在 Issue #261 確認是真實日期、補進清單後通過）；一檔日本 ETF 表上四個價格都是說明書的
1/100（作業人員確認表填錯；各價格同一比例，依共用規則轉人工覆核、不會通過）。表上標的排序和說明書不同的 5 份依代號
配對後通過；期初定價 VWAP 的 1 份表頭寫明 VWAP、和表上相同。參考條件表用含 Citi 列的 data/orders/核對表格式..xlsx。
投資人須知（docs/templates/citi-zh-iis.md）：8 份都辨識為 Citi 投資人須知；只有舊文件受託或銷售機構商品代號空白、
非記憶式那份商品事項寫了「記憶式」（2026-10-14 確認判不一致）。說明書與投資人須知都通過的 5 份回填。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import CITI
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "159*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("159*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_citi(items):
    term_sheets, _ = items
    assert len(term_sheets) == 8
    assert all(i.report.template == CITI.template_id for i in term_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    bad = Counter((r.rule_id, r.reason_code) for i in term_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            ("doc.trustee_product_code", "value_mismatch"): 1,  # 2024 舊文件：受託或銷售機構商品代號 [ ]
            ("field.underlying_prices", "split_suspected"): 4,  # 一檔日本 ETF 表上四個價格是說明書的 1/100（表填錯）
        }
    )
    passed = [i for i in term_sheets if i.report.status == S.PASS]
    assert len(passed) == 6


def test_underlyings_in_a_different_order_pass_by_ticker(items):
    """表上排序和說明書不同的商品：每檔進場價的項目是表上對應代號那一格（不是說明書順序）。VWAP 那份價格不比對。"""
    term_sheets, _ = items

    def reordered(item) -> bool:
        first = [
            r for r in item.report.results if r.rule_id == "field.underlying_prices" and r.field.endswith("進場價")
        ]
        return any(r.item.name != f"UL_{n} 進場價" for n, r in enumerate(first, start=1))

    assert sum(reordered(i) for i in term_sheets) == 4
    for i in term_sheets:
        assert [r.status for r in i.report.results if r.rule_id == "field.underlyings"] == [S.PASS]


def test_initial_pricing_is_read_from_the_table_header(items):
    term_sheets, _ = items
    found = Counter(r.expected for i in term_sheets for r in i.report.results if r.rule_id == "field.initial_pricing")
    assert found == Counter({"收盤價": 7, "VWAP": 1})
    assert all(
        r.status == S.PASS for i in term_sheets for r in i.report.results if r.rule_id == "field.initial_pricing"
    )


def test_investor_sheets_only_differ_where_the_exploration_said(items):
    _, investor_sheets = items
    assert len(investor_sheets) == 8
    assert all(i.report.template == CITI.iis.template_id for i in investor_sheets)
    bad = Counter((r.rule_id, r.reason_code) for i in investor_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            ("iis.product_code", "value_mismatch"): 1,  # 2024 舊文件：受託或銷售機構商品代號 [ ]
            ("iis.memory_wording", "value_mismatch"): 1,  # 非記憶式商品的 m／M 定義句寫「記憶式」
        }
    )


def test_products_whose_two_documents_pass_are_filled(items):
    term_sheets, _ = items
    assert sum(i.fills_sheet for i in term_sheets) == 5
