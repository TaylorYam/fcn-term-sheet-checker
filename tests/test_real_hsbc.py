"""本機 HSBC 真實樣本；交易資料不進 Git，CI 自動略過。"""

import os
from collections import Counter
from pathlib import Path

import pytest

from fcn_checker.schema import CheckStatus as S
from harness import ROOT, check_all

DATA = Path(os.environ.get("FCN_TEST_DATA_DIR", ROOT / "data"))
PDFS = sorted(p for p in (DATA / "ts").glob("325*.pdf") if "_IIS" not in p.name)  # 投資人須知不是說明書
ORDER = DATA / "FCN參考條件_1001.xlsx"
pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(not PDFS or not ORDER.is_file(), reason="本機無 HSBC 真實樣本或整理表"),
]


def test_real_hsbc_samples_match_exploration():
    items = check_all(ORDER, PDFS).items
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
