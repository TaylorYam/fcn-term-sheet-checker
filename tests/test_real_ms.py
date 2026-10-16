"""本機 MS 真實樣本（Issue #135、#137）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與目前樣本內（IS）的核對結果一致：說明書 7 份都辨識為 MS 範本（舊版範本不在樣本內，辨識失敗轉人工覆核見合成測試），
參考條件表與說明書的差異只有下面列出的幾項；投資人須知 7 份全部通過，說明書也通過的商品才回填。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import MS, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "147*_TS.pdf", CHECK_FORMAT),
]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("147*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_ms(items):
    term_sheets, _ = items
    assert len(term_sheets) == 7 and all(i.report.template == MS.template_id for i in term_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    bad = Counter((r.rule_id, r.reason_code) for i in term_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            ("doc.trustee_product_code", "value_mismatch"): 1,  # 受託機構商品代號空白（文件錯誤）
            # 1 份表上 1 檔標的的價格都是說明書的同一比例：疑為分割調整後的價格，轉人工覆核（Issue #191）
            ("field.underlying_prices", "split_suspected"): 3,
            # 配息期數範圍的擷取與原文相符，但與日期表推得的天期不同，屬文件錯誤（Issue #310）
            ("doc.coupon_periods", "value_mismatch"): 1,
        }
    )
    fees = [r for i in term_sheets for r in i.report.results if r.rule_id == "standard.fees"]
    assert len(fees) == 21 and all(r.status == S.PASS for r in fees)
    # 第二章受託機構營業所在地都少「松山區」：審查標準的地址等價寫法（2026-10-09 確認視為正確）
    ch2 = [r for i in term_sheets for r in i.report.results if r.field == "distributor_address_ch2"]
    assert len(ch2) == 7 and all(r.status == S.PASS and "address_equivalents" in r.tolerance for r in ch2)
    # KO% 照常比對，不是不適用（Non-Call = 天期、價格表沒有 KO 欄時 KO 視為 100%，Issue #202）
    ko = [r for i in term_sheets for r in i.report.results if r.rule_id == "field.ko_pct"]
    assert len(ko) == 7 and all(r.status == S.PASS for r in ko)


def test_investor_sheets_pass_and_rows_are_filled_only_when_the_term_sheet_passes_too(items):
    term_sheets, investor_sheets = items
    assert len(investor_sheets) == 7
    assert all(i.report.template == MS.iis.template_id and i.report.status == S.PASS for i in investor_sheets)
    # 說明書與投資人須知都通過的 3 檔回填；其餘說明書有上面列出的差異，不回填
    filled = [i for i in term_sheets if i.fills_sheet]
    assert len(filled) == 3
    assert all(i.report.status == S.PASS and i.partner.report.status == S.PASS for i in filled)


def test_no_other_sample_is_detected_as_an_ms_investor_sheet(items):
    """其他上手的投資人須知與所有說明書都不符合 MS 投資人須知範本。"""
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
    """其他上手的說明書與所有投資人須知都不符合 MS 範本。"""
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
