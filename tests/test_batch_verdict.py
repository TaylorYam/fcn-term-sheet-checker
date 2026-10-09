"""測試切點：每份 PDF 的判定（BatchItem）只看辨識結果與核對報告 → 類別、狀態標籤、能否回填、能否人工放行與原因。

手刻辨識結果與結果清單直接建 BatchItem，不讀任何 PDF 或 Excel；整批流程的同類行為見 test_batch.py。
結果清單只放一筆不帶原因碼的結果，確認判定不靠回頭翻原因碼。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcn_checker.backfill import BackfillAction, CellDecision
from fcn_checker.batch import BatchItem, BatchOutcome, Category, DocKind, Identification, PairingProblem
from fcn_checker.ingestion import IngestionError
from fcn_checker.schema import CheckReport, CheckResult, CheckStatus, Item

PASS, MISMATCH, REVIEW, ERROR = (
    CheckStatus.PASS,
    CheckStatus.MISMATCH,
    CheckStatus.REVIEW_REQUIRED,
    CheckStatus.ERROR,
)
TS, IIS = DocKind.TERM_SHEET, DocKind.IIS
P = PairingProblem
ROW = 4

NO_ROW = "沒有對到參考條件表的列，沒有地方可以回填"
BAD_NAME = "檔名無法辨識，請修正檔名後重新載入"
UNCERTAIN = "回填值無法確定，請人工處理"
CONFLICT = "參考條件表回填欄位已有不同的值，請先修正參考條件表再核對"


def fill(action: BackfillAction = BackfillAction.FILL) -> CellDecision:
    return CellDecision("ISIN Code", "F4", None, "XS0000000001", action)


def make_item(
    kind: DocKind | None,
    problem: PairingProblem | None,
    status: CheckStatus,
    *,
    row: int | None = None,
    checked: bool = False,
    certain: bool = False,
    decisions: tuple[CellDecision, ...] = (),
) -> BatchItem:
    result = CheckResult("rule", "field", status, message="某個問題", item=Item.note("項目"))
    report = CheckReport(status, None, [result], [], backfill=list(decisions), backfill_certain=certain)
    ident = Identification(kind, "BARC", "029199990001", row, problem, checked)
    return BatchItem(Path(f"029199990001_{'IIS' if kind == IIS else 'TS'}.pdf"), report, ident)


def paired_with_passing(item: BatchItem) -> BatchItem:
    other_kind = IIS if item.kind == TS else TS
    partner = make_item(other_kind, None, PASS, row=ROW, checked=True, certain=other_kind == TS, decisions=(fill(),))
    item.partner, partner.partner = partner, item
    return item


# 每個配對問題 × 典型的報告狀態 → 類別、狀態標籤、不能放行的原因
PROBLEM_CASES = [
    # kind, problem, status, row, checked, category, label, release_problem
    (None, P.NAME_UNRECOGNIZED, REVIEW, None, False, Category.REVIEW, "檔名無法辨識", BAD_NAME),
    (TS, P.PDF_UNREADABLE, ERROR, None, False, Category.ERROR, "執行錯誤", "執行錯誤，沒有可以回填的值"),
    (TS, P.UNSUPPORTED, REVIEW, None, False, Category.UNSUPPORTED, "未支援上手", "未支援上手，沒有可以回填的值"),
    (IIS, P.UNSUPPORTED, REVIEW, None, False, Category.UNSUPPORTED, "未支援上手", "未支援上手，沒有可以回填的值"),
    (TS, P.TEMPLATE_UNKNOWN, REVIEW, None, False, Category.REVIEW, "需人工覆核", NO_ROW),
    (IIS, P.TEMPLATE_AMBIGUOUS, REVIEW, None, False, Category.REVIEW, "需人工覆核", NO_ROW),
    (TS, P.PREFIX_MISMATCH, REVIEW, None, False, Category.REVIEW, "檔名上手編號不符", NO_ROW),
    (TS, P.UNEXPECTED, ERROR, None, False, Category.ERROR, "執行錯誤", "執行錯誤，沒有可以回填的值"),
    (TS, P.PRODUCT_CODE_UNREADABLE, REVIEW, None, False, Category.REVIEW, "需人工覆核", NO_ROW),
    (TS, P.CODE_MISMATCH, REVIEW, None, False, Category.REVIEW, "檔名商品代號不符", "檔名的商品代號與說明書封面不同"),
    (TS, P.ROW_MISSING, REVIEW, None, False, Category.REVIEW, "條件表找不到這筆", NO_ROW),
    (IIS, P.ROW_DUPLICATE, REVIEW, None, False, Category.REVIEW, "條件表有重複列", NO_ROW),
    (TS, P.ISSUER_MISMATCH, REVIEW, None, False, Category.REVIEW, "條件表發行機構不符", NO_ROW),
    (TS, P.SHARED_ROW, REVIEW, ROW, False, Category.REVIEW, "多份對到同一列", "同一批有多份文件對到同一列"),
    (IIS, P.SHARED_ROW, REVIEW, ROW, False, Category.REVIEW, "多份對到同一列", "同一批有多份文件對到同一列"),
    (TS, P.MISSING_IIS, REVIEW, ROW, True, Category.REVIEW, "這批缺投資人須知", "這批缺同商品的投資人須知"),
    (IIS, P.MISSING_TS, REVIEW, ROW, True, Category.REVIEW, "這批缺說明書", "這批缺同商品的說明書"),
    # 規則照常跑、出現不一致時，狀態標籤寫不一致，不被配對原因蓋掉；仍不能放行
    (TS, P.MISSING_IIS, MISMATCH, ROW, True, Category.MISMATCH, "不一致", "這批缺同商品的投資人須知"),
    # 執行錯誤不被配對原因蓋掉
    (TS, P.MISSING_IIS, ERROR, ROW, True, Category.ERROR, "執行錯誤", "執行錯誤，沒有可以回填的值"),
]


def test_every_pairing_problem_has_a_case():
    assert {case[1] for case in PROBLEM_CASES} == set(PairingProblem)


@pytest.mark.parametrize(("kind", "problem", "status", "row", "checked", "category", "label", "reason"), PROBLEM_CASES)
def test_pairing_problem_decides_label_and_release(kind, problem, status, row, checked, category, label, reason):
    item = paired_with_passing(
        make_item(kind, problem, status, row=row, checked=checked, certain=True, decisions=(fill(),))
    )
    outcome = BatchOutcome([item, item.partner], Path("FCN參考條件.xlsx"))

    assert item.category == category
    assert item.status_label == label
    assert item.unsupported == (problem == P.UNSUPPORTED)
    assert not item.fillable and not item.fills_sheet
    assert reason in item.release_problem
    with pytest.raises(IngestionError) as refused:
        outcome.release(item)
    assert refused.value.reason_code == "release_refused" and not item.released


# 配對乾淨：狀態 × 種類 × 回填確定性 × 回填決策 → 類別、能否回填與放行
CLEAN_CASES = [
    # kind, status, certain, decisions, category, release_problem
    (TS, PASS, True, (fill(),), Category.PASSED, "已經通過，不需要人工放行"),
    (IIS, PASS, False, (), Category.PASSED, "已經通過，不需要人工放行"),
    (TS, REVIEW, True, (fill(),), Category.REVIEW, ""),
    (TS, MISMATCH, True, (fill(), fill(BackfillAction.MATCH)), Category.MISMATCH, ""),
    (TS, REVIEW, False, (fill(),), Category.REVIEW, UNCERTAIN),
    (TS, REVIEW, False, (), Category.REVIEW, UNCERTAIN),
    (TS, MISMATCH, False, (fill(), fill(BackfillAction.MISMATCH)), Category.MISMATCH, CONFLICT),
    (IIS, REVIEW, False, (), Category.REVIEW, ""),  # 投資人須知不回填，沒有回填值要確認
    (IIS, MISMATCH, False, (), Category.MISMATCH, ""),
    (TS, ERROR, False, (), Category.ERROR, "執行錯誤，沒有可以回填的值"),
]


@pytest.mark.parametrize(("kind", "status", "certain", "decisions", "category", "reason"), CLEAN_CASES)
def test_clean_pairing_releases_only_with_certain_backfill(kind, status, certain, decisions, category, reason):
    item = paired_with_passing(
        make_item(kind, None, status, row=ROW, checked=True, certain=certain, decisions=decisions)
    )

    assert item.category == category
    assert item.status_label == {PASS: "通過", REVIEW: "需人工覆核", MISMATCH: "不一致", ERROR: "執行錯誤"}[status]
    assert item.fillable == (status == PASS)
    assert item.fills_sheet == (status == PASS and kind == TS)
    assert item.release_problem == reason


@pytest.mark.parametrize(("kind", "certain"), [(TS, True), (IIS, False)])
def test_released_item_is_fillable_and_keeps_original_status_in_label(kind, certain):
    item = paired_with_passing(
        make_item(kind, None, REVIEW, row=ROW, checked=True, certain=certain, decisions=(fill(),))
    )
    outcome = BatchOutcome([item, item.partner], Path("FCN參考條件.xlsx"))

    outcome.release(item)

    assert item.category == Category.RELEASED and item.status == PASS
    assert item.status_label == "人工放行（原：需人工覆核）"
    assert item.fillable and item.fills_sheet == (kind == TS)
    assert item.release_problem == ""  # 已人工放行的仍依原判定檢查，可重複放行
    outcome.cancel_release(item)
    assert item.category == Category.REVIEW and not item.fillable


def test_term_sheet_is_not_filled_when_partner_is_missing_or_not_fillable():
    alone = make_item(TS, None, PASS, row=ROW, checked=True, certain=True, decisions=(fill(),))
    assert alone.fillable and not alone.fills_sheet
    assert alone.not_filled_reason == "這批沒有同商品的投資人須知，不回填。"

    ts = make_item(TS, None, PASS, row=ROW, checked=True, certain=True, decisions=(fill(),))
    iis = make_item(IIS, None, REVIEW, row=ROW, checked=True)
    ts.partner, iis.partner = iis, ts
    assert ts.fillable and not ts.fills_sheet
    assert ts.not_filled_reason == "同商品的投資人須知尚未通過或人工放行，不回填。"


def test_item_reads_identity_from_identification():
    item = make_item(IIS, None, PASS, row=ROW, checked=True)

    assert (item.kind, item.issuer, item.product_code, item.reference_row) == (IIS, "BARC", "029199990001", ROW)
    assert item.document == "投資人須知"
    assert make_item(None, P.NAME_UNRECOGNIZED, REVIEW).document == "說明書"
