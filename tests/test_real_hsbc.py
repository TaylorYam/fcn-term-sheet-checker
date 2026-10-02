"""本機 HSBC 真實樣本；交易資料不進 Git，CI 自動略過。"""

import os
from collections import Counter
from pathlib import Path

import pytest

from fcn_checker.batch import check_batch
from fcn_checker.schema import CheckStatus as S
from harness import REVIEW_STANDARD, ROOT
from hsbc_synth import ORDER_FORMAT

DATA = Path(os.environ.get("FCN_TEST_DATA_DIR", ROOT / "data"))
PDFS = sorted((DATA / "ts").glob("325*.pdf"))
ORDER = DATA / "FCN參考條件_1001.xlsx"
pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(not PDFS or not ORDER.is_file(), reason="本機無 HSBC 真實樣本或整理表"),
]


def test_real_hsbc_samples_match_exploration():
    reports = [
        i.report
        for i in check_batch(
            PDFS,
            ORDER,
            REVIEW_STANDARD,
            reference_format=ORDER_FORMAT,
            issuer_prefixes=ROOT / "config/issuer_prefixes.toml",
        ).items
    ]
    assert len(reports) == 8 and all(r.template == "hsbc-zh-pd" for r in reports)
    bad = Counter(
        (x.rule_id, x.reason_code) for r in reports for x in r.results if x.status not in (S.PASS, S.NOT_APPLICABLE)
    )
    assert bad == Counter(
        {
            ("standard.approval_date", "value_mismatch"): 2,
            ("batch.pairing", "reference_row_missing"): 1,
            ("backfill.compare_dates", "value_mismatch"): 4,
            ("doc.scenario_calculations", "value_mismatch"): 1,
        }
    )
    paired = [r for r in reports if not any(x.reason_code == "reference_row_missing" for x in r.results)]
    assert len(paired) == 7 and all(
        not any(x.status in (S.REVIEW_REQUIRED, S.ERROR) for x in r.results) for r in paired
    )
