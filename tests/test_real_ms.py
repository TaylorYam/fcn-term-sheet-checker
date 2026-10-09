"""本機 MS 真實樣本（Issue #135、#137）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致：說明書（docs/rules/ms-check-rules.md §5）新版 6 份辨識為 MS 範本、舊版 2 份範本無法辨識，
參考條件表與說明書的差異只有 §5 列出的幾項；投資人須知（docs/templates/ms-zh-iis.md §7）新版 6 份全部通過、
舊版 2 份範本無法辨識，說明書也通過的商品才回填。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import MS, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import REFERENCE, TERM_SHEETS, pdfs, requires

pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "147*_TS.pdf", REFERENCE),
]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(REFERENCE, pdfs("147*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_new_template_samples_are_ms_and_old_ones_are_not_recognised(items):
    term_sheets, _ = items
    new = [i for i in term_sheets if i.report.template == MS.template_id]
    old = [i for i in term_sheets if i.report.template is None]
    assert len(new) == 6 and len(old) == 2
    for item in old:
        detected = next(r for r in item.report.results if r.rule_id == "template.detect")
        assert detected.reason_code == "template_unknown" and "舊版範本不支援" in detected.message


def test_new_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    new = [i for i in term_sheets if i.report.template == MS.template_id]
    bad = Counter((r.rule_id, r.reason_code) for i in new for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            ("doc.trustee_product_code", "value_mismatch"): 1,  # 受託機構商品代號空白（文件錯誤）
            # 2025 年交易的 3 份沿用 2025 年的舊審查通過日期：2 份交易日早於清單最早的日期、1 份不一致（只以 2026 年後為準）
            ("standard.approval_date", "approval_date_not_configured"): 2,
            ("standard.approval_date", "value_mismatch"): 1,
        }
    )
    # 第二章受託機構營業所在地 6 份都少「松山區」：審查標準的地址等價寫法（2026-10-09 確認視為正確）
    ch2 = [r for i in new for r in i.report.results if r.field == "distributor_address_ch2"]
    assert len(ch2) == 6 and all(r.status == S.PASS and "address_equivalents" in r.tolerance for r in ch2)
    # Non-Call = 天期的 1 份：價格表沒有 KO 欄，KO(%) 與各標的 KO 價為不適用
    no_ko = [
        i for i in new if any(r.rule_id == "field.ko_pct" and r.status == S.NOT_APPLICABLE for r in i.report.results)
    ]
    assert len(no_ko) == 1


def test_new_investor_sheets_pass_and_rows_are_filled_only_when_the_term_sheet_passes_too(items):
    term_sheets, investor_sheets = items
    new = [i for i in investor_sheets if i.report.template == MS.iis.template_id]
    old = [i for i in investor_sheets if i.report.template is None]
    assert len(new) == 6 and len(old) == 2
    assert all(i.report.status == S.PASS for i in new)
    for item in old:
        detected = next(r for r in item.report.results if r.rule_id == "template.detect")
        assert detected.reason_code == "template_unknown" and "舊版範本不支援" in detected.message
    # 說明書與投資人須知都通過的 3 檔回填；其餘 3 份新版說明書有 §5 列出的差異，不回填
    filled = [i for i in term_sheets if i.fills_sheet]
    assert len(filled) == 3
    assert all(i.report.status == S.PASS and i.partner.report.status == S.PASS for i in filled)


def test_no_other_sample_is_detected_as_an_ms_investor_sheet(items):
    """BARC、HSBC 投資人須知、所有說明書與舊版 MS 投資人須知都不符合 MS 投資人須知範本。"""
    _, investor_sheets = items
    new = {i.term_sheet.name for i in investor_sheets if i.report.template == MS.iis.template_id}
    others = [p for p in pdfs() if p.name not in new]
    assert len(others) > 40
    for path in others:
        doc = fitz.open(path)
        try:
            lines = extract_lines(doc)
        finally:
            doc.close()
        issuer, _ = detect_iis(lines, (MS,))
        assert issuer is None, path.name


def test_no_other_sample_is_detected_as_an_ms_term_sheet(items):
    """BARC、HSBC、SG、BNP、Nomura 說明書、所有投資人須知與舊版 MS 說明書都不符合 MS 範本。"""
    term_sheets, _ = items
    new = {i.term_sheet.name for i in term_sheets if i.report.template == MS.template_id}
    others = [p for p in pdfs() if p.name not in new]
    assert len(others) > 40
    for path in others:
        doc = fitz.open(path)
        try:
            lines = extract_lines(doc)
        finally:
            doc.close()
        issuer, _ = detect(lines, (MS,))
        assert issuer is None, path.name
