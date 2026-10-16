"""本機 UBS 真實樣本（Issue #279）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/ubs-check-rules.md §5）：新版 5 份說明書與 5 份投資人須知都辨識為 UBS 範本；舊版 1 組
（「股權結構型債券」）辨識失敗、轉人工覆核。新版中 KO 每日卻寫「定期觀察」的 2 份說明書名稱判不一致，其中 1 份的
參考條件表計價幣別填錯（表上 USD、文件是日圓），說明書計價幣別與金額旁的幣別字、投資人須知幣別都判不一致；其餘
3 份說明書與投資人須知都通過並回填。參考條件表用 data/orders/核對表格式..xlsx（含 6 列 UBS）。
"""

from collections import Counter

import pytest

from fcn_checker.issuers import UBS
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [pytest.mark.real_samples, requires(TERM_SHEETS / "092*_TS.pdf", CHECK_FORMAT)]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("092*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_new_samples_are_ubs_and_the_old_one_is_not(items):
    term_sheets, investor_sheets = items
    assert len(term_sheets) == 6 and len(investor_sheets) == 6
    for docs, template in ((term_sheets, UBS.template_id), (investor_sheets, UBS.iis.template_id)):
        assert Counter(i.report.template for i in docs) == Counter({template: 5, None: 1})
        old = next(i for i in docs if i.report.template is None)
        assert not old.unsupported  # 已支援的上手、範本辨識失敗
        assert "舊版範本" in next(r.message for r in old.report.results if r.rule_id == "template.detect")


def test_only_the_confirmed_differences_are_reported(items):
    term_sheets, investor_sheets = items
    new = [i for i in term_sheets + investor_sheets if i.report.template is not None]
    bad = Counter((r.rule_id, r.status) for i in new for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            ("standard.product_name", S.MISMATCH): 4,  # 2 份 KO 每日寫「定期觀察」：中英文名稱
            ("field.currency", S.MISMATCH): 13,  # 表上幣別填錯：說明書計價幣別與金額旁幣別字 12 處、投資人須知 1 處
        }
    ), bad
    assert sum(i.fills_sheet for i in term_sheets) == 3


def test_template_variants_match_the_sample_table(items):
    """KO 觀察方式、記憶式、KI 型態與範本規格 §4.12 樣本總表（新版 5 份）一致。"""
    term_sheets, _ = items

    def actual(item, rule_id):
        return next((r.actual for r in item.report.results if r.rule_id == rule_id), None)

    found = Counter(
        (actual(i, "field.ko_observation"), actual(i, "field.ko_memory"), actual(i, "field.ki_type"))
        for i in term_sheets
        if i.report.template is not None
    )
    assert found == Counter(
        {
            ("期間每日觀察", "記憶式", "每日觀察"): 1,
            ("期末定日觀察", "非記憶式", "無 KI"): 1,
            ("期間每日觀察", "非記憶式", "無 KI"): 1,
            ("期末定日觀察", "記憶式", "無 KI"): 1,
            ("期間每日觀察", "記憶式", "到期觀察"): 1,
        }
    )


def test_fixed_warning_and_issuing_branch(items):
    """固定警語說明書 3 次、投資人須知 1 次；發行分行說明書 3 處、投資人須知 1 處都是倫敦分行。"""
    term_sheets, investor_sheets = items
    for docs, n, branch in ((term_sheets, 3, "doc.issuing_branch"), (investor_sheets, 1, "iis.issuing_branch")):
        for i in (x for x in docs if x.report.template is not None):
            assert next(r.actual for r in i.report.results if r.rule_id == "standard.fixed_warning") == n
            assert {r.status for r in i.report.results if r.rule_id == branch} == {S.PASS}
