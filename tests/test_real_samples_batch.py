"""本機真實樣本測試（批量）：只在本機 data/ 有說明書與參考條件表時執行，CI 自動略過。

測試碼不含任何真實商品代號或數值；只驗證結果與 2026-10-02 討論的結論一致（Issue #43）。
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from pathlib import Path

import pytest

from fcn_checker.batch import run_batch
from fcn_checker.schema import CheckStatus
from synth import ISSUER_PREFIXES, REFERENCE_FORMAT, REVIEW_STANDARD

ROOT = Path(__file__).resolve().parents[1]
PDFS = (
    sorted((Path(os.environ.get("FCN_TEST_DATA_DIR", ROOT / "data")) / "ts").glob("*.pdf"))
    if (Path(os.environ.get("FCN_TEST_DATA_DIR", ROOT / "data")) / "ts").is_dir()
    else []
)
REFERENCE = Path(os.environ.get("FCN_TEST_DATA_DIR", ROOT / "data")) / "FCN參考條件_1001.xlsx"
PROBLEMS = (CheckStatus.MISMATCH, CheckStatus.REVIEW_REQUIRED, CheckStatus.ERROR)

pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(not PDFS or not REFERENCE.is_file(), reason="本機沒有真實樣本（data/ 被 Git 忽略）"),
]


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("real_batch")
    sheet = shutil.copy(REFERENCE, tmp / REFERENCE.name)  # 新檔寫在原檔旁邊，不在 data/ 產生檔案
    return run_batch(
        PDFS,
        Path(sheet),
        REVIEW_STANDARD,
        tmp / "reports",
        reference_format=REFERENCE_FORMAT,
        issuer_prefixes=ISSUER_PREFIXES,
    )


def problems(item) -> Counter[tuple[str, str]]:
    return Counter((r.rule_id, r.reason_code) for r in item.report.results if r.status in PROBLEMS)


def test_other_issuers_are_unsupported(outcome):
    others = [i for i in outcome.items if not i.term_sheet.name.startswith(("029", "325"))]
    assert others and all(i.unsupported and not i.filled for i in others)


def test_barc_rows_match_every_prefilled_field(outcome):
    paired = [
        i
        for i in outcome.items
        if i.issuer == "BARC"
        and any(r.rule_id == "batch.pairing" and r.status == CheckStatus.PASS for r in i.report.results)
    ]
    assert len(paired) == 8, "參考條件表有 8 列 BARC"
    allowed = {
        ("backfill.compare_dates", "value_mismatch"),  # 舊填法：D 型多填了最後一期
        ("standard.approval_date", "value_mismatch"),  # 舊系列沿用前一次審查日期
        ("standard.product_name", "value_mismatch"),  # 較早的中文名稱沒有「（不保本）」
    }
    for item in paired:
        assert set(problems(item)) <= allowed, item.term_sheet.name


def test_daily_rows_differ_only_in_the_old_last_period_compare_date(outcome):
    for item in outcome.items:
        mismatched = [d for d in item.report.backfill if d.action == "mismatch"]
        if not mismatched:
            continue
        assert len(mismatched) == 1, item.term_sheet.name
        assert mismatched[0].expected == "-" and mismatched[0].sheet_value is not None


def test_period_end_rows_pass_and_are_marked_filled(outcome):
    passed = [i for i in outcome.items if i.issuer == "BARC" and i.report.status == CheckStatus.PASS]
    assert len(passed) == 3
    assert all(i.filled for i in passed)
    assert all(d.action == "match" for i in passed for d in i.report.backfill)
