"""本機真實樣本測試（批量）：只在本機 data/ 有說明書與參考條件表時執行，CI 自動略過。

測試碼不含任何真實商品代號或數值；只驗證結果與 2026-10-02 討論的結論一致（Issue #43）。
說明書與投資人須知一起核對（ADR 0007，Issue #124）：兩份都通過或放行的商品才回填。
"""

from __future__ import annotations

import shutil
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import openpyxl
import pytest

from fcn_checker.batch import DocKind
from fcn_checker.saving import run_batch
from fcn_checker.schema import CheckStatus
from harness import CONFIG
from real_data import REFERENCE, TERM_SHEETS, TO_FILL, pdfs, requires

PDFS = pdfs()  # 說明書與投資人須知
BACKFILL_COLUMNS = ("ISIN Code", "發行日", *(f"比價日_{i}" for i in range(1, 13)))
CHECKED_COLUMNS = ("TS", "IIS")  # 兩份都通過或放行才打 V（Issue #127）
PROBLEMS = (CheckStatus.MISMATCH, CheckStatus.REVIEW_REQUIRED, CheckStatus.ERROR)

pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "*.pdf", REFERENCE),
]


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    """整批核對並儲存一次：(批量核對結果, 儲存收據)。"""
    tmp = tmp_path_factory.mktemp("real_batch")
    sheet = shutil.copy(REFERENCE, tmp / REFERENCE.name)  # 不在 data/ 產生檔案
    return run_batch(CONFIG, Path(sheet), PDFS, tmp / "reports", root=tmp)


def problems(item) -> Counter[tuple[str, str]]:
    return Counter((r.rule_id, r.reason_code) for r in item.report.results if r.status in PROBLEMS)


@pytest.fixture(scope="module")
def outcome(saved):
    return saved[0]


def test_other_issuers_are_unsupported(saved):
    outcome, receipt = saved
    others = [i for i in outcome.items if not i.term_sheet.name.startswith(("029", "325", "147"))]
    assert others and all(i.unsupported and not receipt.filled(i) for i in others)
    ms = [i for i in outcome.items if i.term_sheet.name.startswith("147")]  # MS 投資人須知未支援，說明書不回填
    assert ms and not any(receipt.filled(i) for i in ms)


def term_sheets(outcome):
    return [i for i in outcome.items if i.kind == DocKind.TERM_SHEET]


def test_barc_rows_match_every_prefilled_field(outcome):
    paired = [
        i
        for i in term_sheets(outcome)
        if i.issuer == "BARC"
        and any(r.rule_id == "batch.pairing" and r.status == CheckStatus.PASS for r in i.report.results)
    ]
    assert len(paired) == 8, "參考條件表有 8 列 BARC"
    allowed = {
        ("standard.product_name", "value_mismatch"),  # 較早的中文名稱沒有「（不保本）」
    }
    for item in paired:
        assert set(problems(item)) <= allowed, item.term_sheet.name


def test_every_compare_date_on_the_sheet_matches_the_fill_rule(outcome):
    # D 型填 Non-Call 那期與最後一期、P 型從 Non-Call 那期起每期都填（Issue #69）
    assert not [
        (i.term_sheet.name, d.column) for i in outcome.items for d in i.report.backfill if d.action == "mismatch"
    ]


def test_passing_barc_rows_are_marked_filled(saved):
    outcome, receipt = saved
    passed = [i for i in term_sheets(outcome) if i.issuer == "BARC" and i.report.status == CheckStatus.PASS]
    assert len(passed) == 5, "3 列 P 型＋2 列審查日期與名稱樣板都是新版的 D 型"
    assert all(receipt.filled(i) for i in passed), "同商品投資人須知也都通過"
    # 表上已確認的回填值都相同；期初定價 VWAP 的價格欄一律以說明書覆寫（表上有未四捨五入的值，Issue #122）；
    # TS、IIS 表上空白或已是 V（Issue #127）
    assert all(
        d.action == "match"
        or (d.column.startswith("UL_") and d.action == "overwrite")
        or (d.column in CHECKED_COLUMNS and d.action == "fill")
        for i in passed
        for d in i.report.backfill
    )


def vwap_codes(path: Path) -> set[str]:
    return {code for code, row in rows_by_code(path).items() if row.get("期初定價") == "VWAP"}


def test_vwap_rows_skip_price_comparison_and_take_the_document_prices(outcome):
    vwap = [i for i in term_sheets(outcome) if i.product_code in vwap_codes(REFERENCE)]
    assert len(vwap) == 2, "表上有 2 列期初定價 VWAP（BARC、HSBC 各 1）"
    for item in vwap:
        assert item.report.status == CheckStatus.PASS, (item.term_sheet.name, problems(item))
        assert not [r for r in item.report.results if r.rule_id == "field.underlying_prices"]
        prices = [d for d in item.report.backfill if d.column.startswith("UL_")]
        assert prices and all(d.action in ("match", "overwrite") for d in prices)


def rows_by_code(path: Path, sheet: str = "樣本清單") -> dict[str, dict]:
    ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    headers = [c.value for c in ws[3]]
    rows = (dict(zip(headers, r, strict=False)) for r in ws.iter_rows(min_row=4, values_only=True))
    return {str(d["TDCC Code"]): d for d in rows if d.get("TDCC Code")}


@requires(TO_FILL)  # 回填欄位與 VWAP 商品的價格欄空白（Issue #69、#122）
def test_back_filled_rows_equal_the_confirmed_sheet(tmp_path):
    sheet = shutil.copy(TO_FILL, tmp_path / TO_FILL.name)
    filled, receipt = run_batch(CONFIG, Path(sheet), PDFS, tmp_path / "reports", root=tmp_path)
    codes = [i.product_code for i in filled.items if receipt.filled(i)]
    both = [i.product_code for i in filled.items if i.fills_sheet]
    assert codes == both and len(codes) >= 5, "說明書與投資人須知都通過的商品才回填（BARC 5 份）"
    got, want = rows_by_code(receipt.output, "回填後"), rows_by_code(REFERENCE)
    assert set(got) == set(codes), "「回填後」只有通過且回填的列"
    for code in codes:
        assert {c: got[code][c] for c in BACKFILL_COLUMNS} == {c: want[code][c] for c in BACKFILL_COLUMNS}, code
        assert [got[code][c] for c in CHECKED_COLUMNS] == ["V", "V"], code
    # 期初定價 VWAP：價格欄在待回補表上空白，回填後等於已確認表上的價格（四捨五入到 4 位，Issue #122）
    vwap = vwap_codes(TO_FILL) & set(codes)
    assert len(vwap) >= 1, "BARC 的期初定價 VWAP 那列有回填"
    for code in vwap:
        for c in (f"UL_{n}_{p}" for n in range(1, 6) for p in ("進場價", "執行價", "下限價", "KO價")):
            assert rounded(got[code][c]) == rounded(want[code][c]), (code, c)


def rounded(v):
    """表上價格先清掉浮點尾數（9 位）再四捨五入（half-up）到 4 位，與核對時相同。"""
    if isinstance(v, float | int):
        return Decimal(repr(round(v, 9))).quantize(Decimal("0.0001"), ROUND_HALF_UP)
    return v


# ---------------------------------------------------------------- 投資人須知（Issue #124）


def investor_sheets(outcome, prefix: str):
    return [i for i in outcome.items if i.kind == DocKind.IIS and i.term_sheet.name.startswith(prefix)]


def test_barc_investor_sheets_all_pass_with_their_term_sheets(outcome):
    sheets = investor_sheets(outcome, "029")
    assert len(sheets) >= 8
    for item in sheets:
        assert item.report.status == CheckStatus.PASS, (item.term_sheet.name, problems(item))
        assert item.partner is not None and item.report.metadata["inputs"]["term_sheet"]["pages"] == 4


def test_hsbc_investor_sheets_only_differ_by_the_forbidden_wording(outcome):
    # 2026-10-08 使用者決定：第七節「受託投資本商品」兩處判不一致（docs/rules/iis-check-rules.md §5）
    sheets = investor_sheets(outcome, "325")
    assert len(sheets) >= 8
    for item in sheets:
        assert problems(item) == Counter({("standard.forbidden_wording", "forbidden_wording"): 1}), item.term_sheet.name
        assert item.partner is not None and not item.partner.fills_sheet


def test_investor_sheets_of_unsupported_issuers_are_unsupported(outcome):
    others = [i for i in outcome.items if i.kind == DocKind.IIS and not i.term_sheet.name.startswith(("029", "325"))]
    assert others and all(i.unsupported for i in others)
