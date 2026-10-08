"""本機 HSBC 真實樣本；交易資料不進 Git，CI 自動略過。"""

from collections import Counter

import pytest

from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import REFERENCE, TERM_SHEETS, requires, term_sheets

PDFS = term_sheets("325*.pdf")
pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "325*_TS.pdf", REFERENCE),
]


def test_real_hsbc_samples_match_exploration():
    outcome = check_all(REFERENCE, PDFS)
    items = [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")]  # 投資人須知另見批量測試
    reports = [i.report for i in items]
    assert len(reports) == 9 and all(r.template == "hsbc-zh-pd" for r in reports)
    bad = Counter(
        (x.rule_id, x.reason_code) for r in reports for x in r.results if x.status not in (S.PASS, S.NOT_APPLICABLE)
    )
    assert bad == Counter(
        {
            ("batch.pairing", "reference_row_missing"): 1,
        }
    )
    # Issue #82: the issuer's unrounded-coupon total differs from the printed items by 0.01 and now passes.
    assert next(i.report for i in items if i.term_sheet.name.startswith("325000132929")).status == S.PASS
    paired = [r for r in reports if not any(x.reason_code == "reference_row_missing" for x in r.results)]
    assert len(paired) == 8 and all(  # 含 1 份期初定價 VWAP（Issue #122）
        not any(x.status in (S.REVIEW_REQUIRED, S.ERROR) for x in r.results) for r in paired
    )
