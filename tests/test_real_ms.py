"""本機 MS 真實樣本（Issue #135、#137）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致：說明書（docs/rules/ms-check-rules.md §5）新版 10 份辨識為 MS 範本、舊版 2 份範本無法辨識，
參考條件表與說明書的差異只有 §5 列出的幾項；投資人須知（docs/templates/ms-zh-iis.md §7）新版 10 份除參考條件表沒有的
1 份外全部通過、舊版 2 份範本無法辨識，說明書也通過的商品才回填。新版 10 份含澳幣、南非幣、日圓計價（Issue #181、#185）。
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
    assert len(new) == 10 and len(old) == 2
    for item in old:
        detected = next(r for r in item.report.results if r.rule_id == "template.detect")
        assert detected.reason_code == "template_unknown" and "舊版範本不支援" in detected.message


def test_new_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    new = [i for i in term_sheets if i.report.template == MS.template_id]
    bad = Counter((r.rule_id, r.reason_code) for i in new for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            ("doc.trustee_product_code", "value_mismatch"): 3,  # 受託機構商品代號空白（文件錯誤）
            # 2025 年交易的 5 份沿用 2025 年的舊審查通過日期：4 份交易日早於清單最早的日期、1 份不一致（只以 2026 年後為準）
            ("standard.approval_date", "approval_date_not_configured"): 4,
            ("standard.approval_date", "value_mismatch"): 1,
            ("batch.pairing", "reference_row_missing"): 1,  # 參考條件表沒有這一列
            # Non-Call = 天期的 1 份表上 KO(%) 填 150、4 檔 KO 價都是進場價的 1.5 倍：KO 視為 100%、KO 價 = 期初價格（Issue #202）
            ("field.ko_pct", "value_mismatch"): 1,
            ("field.underlying_prices", "value_mismatch"): 4,
        }
    )
    # 第二章受託機構營業所在地都少「松山區」：審查標準的地址等價寫法（2026-10-09 確認視為正確）；沒有參考條件表列的不核對
    ch2 = [r for i in new for r in i.report.results if r.field == "distributor_address_ch2"]
    assert len(ch2) == 9 and all(r.status == S.PASS and "address_equivalents" in r.tolerance for r in ch2)
    # Non-Call = 天期的 2 份價格表沒有 KO 欄：KO 視為 100%、KO 價 = 期初價格，照常比對，不再是不適用（Issue #202）
    ko = [r for i in new for r in i.report.results if r.rule_id == "field.ko_pct"]
    assert ko and all(r.status != S.NOT_APPLICABLE for r in ko)
    ko_bad = {
        i.term_sheet.name
        for i in new
        for r in i.report.results
        if r.status == S.MISMATCH
        and (r.field == "ko_pct" or (r.rule_id == "field.underlying_prices" and "KO" in r.field))
    }
    assert len(ko_bad) == 1


def test_new_investor_sheets_pass_and_rows_are_filled_only_when_the_term_sheet_passes_too(items):
    term_sheets, investor_sheets = items
    new = [i for i in investor_sheets if i.report.template == MS.iis.template_id]
    old = [i for i in investor_sheets if i.report.template is None]
    assert len(new) == 10 and len(old) == 2
    missing = [i for i in new if any(r.reason_code == "reference_row_missing" for r in i.report.results)]
    assert len(missing) == 1 and all(i.report.status == S.PASS for i in new if i not in missing)
    for item in old:
        detected = next(r for r in item.report.results if r.rule_id == "template.detect")
        assert detected.reason_code == "template_unknown" and "舊版範本不支援" in detected.message
    # 說明書與投資人須知都通過的 3 檔回填；其餘新版說明書有 §5 列出的差異或沒有參考條件表列，不回填
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
