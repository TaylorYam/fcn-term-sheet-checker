"""本機真實樣本測試（批量）：只在本機 data/ 有說明書與參考條件表時執行，CI 自動略過。

測試碼不含任何真實商品代號或數值；只驗證結果與 2026-10-02 討論的結論一致（Issue #43）。
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import openpyxl
import pytest

from fcn_checker.saving import run_batch
from fcn_checker.schema import CheckStatus
from harness import CONFIG

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("FCN_TEST_DATA_DIR", ROOT / "data"))
# 投資人須知（_IIS）不是說明書
PDFS = sorted(p for p in (DATA / "ts").glob("*.pdf") if "_IIS" not in p.name) if (DATA / "ts").is_dir() else []
REFERENCE = DATA / "FCN參考條件_1001.xlsx"
TO_FILL = DATA / "FCN參考條件_待回補_v.1.xlsx"  # 同一張表，回填欄位與 VWAP 商品的價格欄空白（Issue #69、#122）
BACKFILL_COLUMNS = ("ISIN Code", "發行日", *(f"比價日_{i}" for i in range(1, 13)))
PROBLEMS = (CheckStatus.MISMATCH, CheckStatus.REVIEW_REQUIRED, CheckStatus.ERROR)

pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(not PDFS or not REFERENCE.is_file(), reason="本機沒有真實樣本（data/ 被 Git 忽略）"),
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
    others = [i for i in outcome.items if not i.term_sheet.name.startswith(("029", "325"))]
    assert others and all(i.unsupported and not receipt.filled(i) for i in others)


def test_barc_rows_match_every_prefilled_field(outcome):
    paired = [
        i
        for i in outcome.items
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
    passed = [i for i in outcome.items if i.issuer == "BARC" and i.report.status == CheckStatus.PASS]
    assert len(passed) == 5, "3 列 P 型＋2 列審查日期與名稱樣板都是新版的 D 型"
    assert all(receipt.filled(i) for i in passed)
    # 表上已確認的回填值都相同；期初定價 VWAP 的價格欄一律以說明書覆寫（表上有未四捨五入的值，Issue #122）
    assert all(
        d.action == "match" or (d.column.startswith("UL_") and d.action == "overwrite")
        for i in passed
        for d in i.report.backfill
    )


def vwap_codes(path: Path) -> set[str]:
    return {code for code, row in rows_by_code(path).items() if row.get("期初定價") == "VWAP"}


def test_vwap_rows_skip_price_comparison_and_take_the_document_prices(outcome):
    vwap = [i for i in outcome.items if i.product_code in vwap_codes(REFERENCE)]
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


@pytest.mark.skipif(not TO_FILL.is_file(), reason="本機沒有待回補的參考條件表")
def test_back_filled_rows_equal_the_confirmed_sheet(tmp_path):
    sheet = shutil.copy(TO_FILL, tmp_path / TO_FILL.name)
    filled, receipt = run_batch(CONFIG, Path(sheet), PDFS, tmp_path / "reports", root=tmp_path)
    codes = [i.product_code for i in filled.items if receipt.filled(i)]
    assert len(codes) >= 9, "BARC 5 份＋HSBC 至少 4 份通過並回填"
    got, want = rows_by_code(receipt.output, "回填後"), rows_by_code(REFERENCE)
    assert set(got) == set(codes), "「回填後」只有通過且回填的列"
    for code in codes:
        assert {c: got[code][c] for c in BACKFILL_COLUMNS} == {c: want[code][c] for c in BACKFILL_COLUMNS}, code
    # 期初定價 VWAP：價格欄在待回補表上空白，回填後等於已確認表上的價格（四捨五入到 4 位，Issue #122）
    vwap = vwap_codes(TO_FILL)
    assert len(vwap) == 2 and vwap <= set(codes)
    for code in vwap:
        for c in (f"UL_{n}_{p}" for n in range(1, 6) for p in ("進場價", "執行價", "下限價", "KO價")):
            assert rounded(got[code][c]) == rounded(want[code][c]), (code, c)


def rounded(v):
    """表上價格先清掉浮點尾數（9 位）再四捨五入（half-up）到 4 位，與核對時相同。"""
    if isinstance(v, float | int):
        return Decimal(repr(round(v, 9))).quantize(Decimal("0.0001"), ROUND_HALF_UP)
    return v
