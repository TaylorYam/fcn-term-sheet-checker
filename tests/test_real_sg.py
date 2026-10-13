"""本機 SG 真實樣本（Issue #194）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/sg-check-rules.md §5）：6 份（含 2 份舊版面）都辨識為 SG 範本；只有 §5 列出的差異
（舊範本的「受託投資」、舊負責人、交易日早於審查標準最早日期的審查通過日期、一檔標的除權息同比例轉人工覆核；
沿用舊字的 2026 年文件也判「受託投資」）。SG 投資人須知尚無範本，是未支援上手，所以 SG 商品都不回填。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import SG, detect
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import REFERENCE, TERM_SHEETS, pdfs, requires

pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "037*_TS.pdf", REFERENCE),
]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(REFERENCE, pdfs("037*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_sg(items):
    term_sheets, _ = items
    assert len(term_sheets) == 6
    assert all(i.report.template == SG.template_id for i in term_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    bad = Counter((r.rule_id, r.reason_code) for i in term_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            (
                "field.underlying_prices",
                "split_suspected",
            ): 4,  # 舊範本一檔標的的四個價格欄都是說明書的同一比例（除權息調整）
            ("standard.approval_date", "approval_date_not_configured"): 2,  # 2023、2025 年交易，早於審查標準最早的日期
            ("standard.chairman", "value_mismatch"): 1,  # 舊範本的前任負責人
            ("standard.fixed_warning", "occurrence_count"): 1,  # 舊範本的警語用「受託投資」
            ("standard.forbidden_wording", "forbidden_wording"): 2,  # 舊範本 4 處；沿用舊字的 2026 年舊版面文件 1 處
        }
    )
    passed = [i for i in term_sheets if i.report.status == S.PASS]
    assert len(passed) == 3  # 新版面 2026 年的 3 份（含單一標的、VWAP）全部通過
    # 封面受託機構地址「3段」與第二章簡稱「玉山證券」都是審查標準的等價寫法
    for field in ("distributor_address_cover", "distributor_name_ch2"):
        found = [r for i in term_sheets for r in i.report.results if r.field == field]
        assert len(found) == 6 and all(r.status == S.PASS and "equivalents" in (r.tolerance or "") for r in found), (
            field
        )
    # 無自動提前出場的 1 份：KO % 與各標的 KO 價不適用，其餘參考條件表欄位照常核對
    no_autocall = [
        i
        for i in term_sheets
        if any(r.rule_id == "field.ko_pct" and r.status == S.NOT_APPLICABLE for r in i.report.results)
    ]
    assert len(no_autocall) == 1
    assert all(
        r.status in (S.PASS, S.NOT_APPLICABLE)
        for i in no_autocall
        for r in i.report.results
        if r.rule_id.startswith("field.")
    )


def test_sg_investor_sheets_are_unsupported_and_nothing_is_filled(items):
    term_sheets, investor_sheets = items
    assert len(investor_sheets) == 6
    assert all(i.unsupported for i in investor_sheets)
    assert not any(i.fills_sheet for i in term_sheets)


def test_no_other_sample_is_detected_as_an_sg_term_sheet(items):
    """BARC、HSBC、MS、BNP、UBS 等說明書與所有投資人須知（含 SG 投資人須知）都不符合 SG 範本。"""
    term_sheets, _ = items
    sg = {i.term_sheet.name for i in term_sheets}
    others = [p for p in pdfs() if p.name not in sg]
    assert len(others) > 40
    for path in others:
        doc = fitz.open(path)
        try:
            lines = extract_lines(doc)
        finally:
            doc.close()
        issuer, _ = detect(lines, (SG,))
        assert issuer is None, path.name
