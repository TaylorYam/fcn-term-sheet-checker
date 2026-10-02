"""本機真實樣本測試（說明書內部規則）：只在本機 data/ 有真實說明書時執行，CI 自動略過。

所有 BARC 樣本配一份只有商品代號與發行機構的合成參考條件表，讓說明書內部規則與審查標準對每份樣本都跑過；
表上事先填好的欄位比對見 test_real_samples_batch.py。測試碼不含任何真實商品代號或數值。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from fcn_checker.batch import check_batch
from fcn_checker.schema import CheckStatus
from synth import ISSUER_PREFIXES, REFERENCE_FORMAT, REVIEW_STANDARD, build_reference_sheet

ROOT = Path(__file__).resolve().parents[1]
TS_DIR = ROOT / "data" / "ts"
PDFS = sorted(TS_DIR.glob("*.pdf")) if TS_DIR.is_dir() else []

pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(not PDFS, reason="本機沒有真實樣本（data/ 被 Git 忽略）"),
]

# 只看說明書本身的規則（不受參考條件表影響）
DOC_ONLY_PREFIXES = ("standard.", "doc.", "derive.prices", "template.", "schedule.")


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("real_doc_rules")
    rows = [{"TDCC Code": p.name[:12], "發行機構": "Barclays"} for p in PDFS if p.name.startswith("029")]
    sheet = build_reference_sheet(tmp / "codes_only.xlsx", rows)
    outcome = check_batch(
        PDFS, sheet, REVIEW_STANDARD, reference_format=REFERENCE_FORMAT, issuer_prefixes=ISSUER_PREFIXES
    )
    return {i.term_sheet.name: i for i in outcome.items}


def test_other_issuers_are_unsupported(reports):
    others = [i for i in reports.values() if not i.term_sheet.name.startswith("029")]
    assert others, "本機應有其他上手的負面樣本"
    assert all(i.unsupported and i.report.status == CheckStatus.REVIEW_REQUIRED for i in others)


def test_document_rules_only_flag_old_format_documents(reports):
    barc = {k: i.report for k, i in reports.items() if i.report.template}
    assert len(barc) == 14
    flagged: Counter[tuple[str, str]] = Counter()
    for r in barc.values():
        for x in r.results:
            if x.rule_id.startswith(DOC_ONLY_PREFIXES) and x.status not in (
                CheckStatus.PASS,
                CheckStatus.NOT_APPLICABLE,
            ):
                flagged[(x.rule_id, x.field)] += 1
    # 探勘結論：舊系列 6 份沿用前一次審查日期；較早 7 份中文名稱尚未加「（不保本）」；其餘全部成立
    assert flagged == Counter(
        {
            ("standard.approval_date", "approval_date"): 6,
            ("standard.product_name", "name_zh"): 7,
        }
    )


def test_every_barc_sample_yields_isin_and_compare_dates_or_explicit_review(reports):
    """ISIN 一律可擷取；比價日除 D 型第 1 期即開始比價（2 份）外都推得出來。"""
    barc = [i for i in reports.values() if i.report.template]
    review = Counter()
    for item in barc:
        isin = next(r for r in item.report.results if r.rule_id == "backfill.isin")
        assert isin.actual, item.term_sheet.name
        dates = next(r for r in item.report.results if r.rule_id == "backfill.compare_dates")
        if dates.status == CheckStatus.REVIEW_REQUIRED:
            review[dates.reason_code] += 1
    assert review == Counter({"document_invalid": 2})
