"""本機 BNP 真實樣本（Issue #191）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/bnp-check-rules.md §5）：7 份都辨識為 BNP 範本；有參考條件表列的 6 份只有 §5 列出的差異
（一份一檔標的的價格欄疑似分割調整轉人工覆核、同一份情境一累積配息與每期配息 × 期數不符）；沒有列的 1 份為找不到列。
BNP 投資人須知尚無範本，是未支援上手，所以 BNP 商品都不回填。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BNP, detect
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import REFERENCE, TERM_SHEETS, pdfs, requires

pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "082*_TS.pdf", REFERENCE),
]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(REFERENCE, pdfs("082*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_bnp(items):
    term_sheets, _ = items
    assert len(term_sheets) == 7
    assert all(i.report.template == BNP.template_id for i in term_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    bad = Counter((r.rule_id, r.reason_code) for i in term_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            (
                "field.underlying_prices",
                "split_suspected",
            ): 3,  # 一檔標的的進場價、執行價、KO 價都是說明書的同一比例（分割調整）
            (
                "doc.scenario_calculations",
                "value_mismatch",
            ): 1,  # 同一份情境一累積總月配息金額 ≠ 每期配息 × 期數（文件錯誤）
            ("batch.pairing", "reference_row_missing"): 1,  # 參考條件表沒有這一列
        }
    )
    passed = [i for i in term_sheets if i.report.status == S.PASS]
    assert len(passed) == 5  # 有列的 6 份只有 1 份（分割調整＋累積配息）不是全部通過
    # 封面與第二章的受託機構電話、地址都是審查標準的等價寫法
    for field in ("distributor_phone_cover", "distributor_address_cover", "distributor_address_ch2"):
        found = [r for i in term_sheets for r in i.report.results if r.field == field]
        assert len(found) == 6 and all(r.status == S.PASS and "equivalents" in (r.tolerance or "") for r in found), (
            field
        )
    # Non-Call = 天期的 1 份：KO % 與各標的 KO 價照常核對（與 MS 不同）
    with_row = [i for i in term_sheets if i.report.results and len(i.report.results) > 2]
    assert all(any(r.rule_id == "field.ko_pct" and r.status == S.PASS for r in i.report.results) for i in with_row)


def test_bnp_investor_sheets_are_unsupported_and_nothing_is_filled(items):
    term_sheets, investor_sheets = items
    assert len(investor_sheets) == 6
    assert all(i.unsupported for i in investor_sheets)
    assert not any(i.fills_sheet for i in term_sheets)


def test_no_other_sample_is_detected_as_a_bnp_term_sheet(items):
    """BARC、HSBC、MS、SG、UBS 等說明書與所有投資人須知（含 BNP 投資人須知）都不符合 BNP 範本。"""
    term_sheets, _ = items
    bnp = {i.term_sheet.name for i in term_sheets}
    others = [p for p in pdfs() if p.name not in bnp]
    assert len(others) > 40
    for path in others:
        doc = fitz.open(path)
        try:
            lines = extract_lines(doc)
        finally:
            doc.close()
        issuer, _ = detect(lines, (BNP,))
        assert issuer is None, path.name
