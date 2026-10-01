"""本機真實樣本測試：只在本機 data/ 有真實說明書與詢價表時執行，CI 自動略過。

測試碼不含任何真實商品代號或數值；只驗證結果與探勘結論一致。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from fcn_checker.checker import run_check
from fcn_checker.schema import CheckStatus
from synth import ORDER_FORMAT, REVIEW_STANDARD

ROOT = Path(__file__).resolve().parents[1]
TS_DIR = ROOT / "data" / "ts"
INQUIRY = ROOT / "data" / "BARC詢價格式.xlsx"
PDFS = sorted(TS_DIR.glob("*.pdf")) if TS_DIR.is_dir() else []

pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(not PDFS or not INQUIRY.is_file(), reason="本機沒有真實樣本（data/ 被 Git 忽略）"),
]

# 只看說明書本身的規則（不受詢價表影響）
DOC_ONLY_PREFIXES = ("standard.", "doc.", "derive.prices", "template.", "schedule.")


@pytest.fixture(scope="module")
def reports():
    return {p.name: run_check(p, INQUIRY, REVIEW_STANDARD, ORDER_FORMAT) for p in PDFS}


def test_inquiry_sample_is_fully_parsed_against_its_term_sheet(reports):
    """詢價表樣本是作業中的工作檔，內容可能被換成其他交易或含真實差異；
    這裡只要求對應的說明書與詢價表都能完整解析（無 REVIEW／ERROR），不要求內容全部一致。"""
    matched = [
        r
        for r in reports.values()
        if any(x.rule_id == "field.product_code" and x.status == CheckStatus.PASS for x in r.results)
    ]
    assert len(matched) == 1, "詢價表樣本應恰好對應一份說明書"
    bad = [
        (x.rule_id, x.field, x.status.value)
        for x in matched[0].results
        if x.status in (CheckStatus.REVIEW_REQUIRED, CheckStatus.ERROR)
    ]
    assert bad == []
    assert matched[0].status in (CheckStatus.PASS, CheckStatus.MISMATCH)


def test_non_barc_samples_are_not_detected(reports):
    others = [r for r in reports.values() if r.template is None]
    assert others, "本機應有其他上手的負面樣本"
    for r in others:
        assert r.status == CheckStatus.REVIEW_REQUIRED
        assert [x.rule_id for x in r.results if x.status != CheckStatus.PASS][0] == "template.barc"


def test_document_rules_only_flag_old_format_documents(reports):
    barc = {k: r for k, r in reports.items() if r.template}
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
