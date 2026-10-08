"""期初定價（Issue #122）：參考條件表「期初定價」欄，VWAP 商品的四個價格欄不比對、改以說明書覆寫。

測試切點是批量入口（run_batch／check_all、save_batch）與 PANEL 結果窗格；回填結果讀核對結果檔「回填後」。
只用合成資料（tests/synth.py）；預期價格是依合成說明書的期初價格與百分比手算的字面值。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from fcn_checker.panel import ResultPane
from fcn_checker.saving import run_batch, save_batch
from harness import CONFIG, check_all, load_record
from reference_synth import REFERENCE_HEADERS, build_reference_sheet
from synth import Spec, build_pdf, reference_row
from test_batch import row_of

NOW = dt.datetime(2030, 2, 3, 4, 5, 6)
PRICES = ("進場價", "執行價", "下限價", "KO價")
BLANK = {f"UL_{i}_{p}": None for i in range(1, 4) for p in PRICES}
# 合成說明書（執行 70%、KO 100%、無 KI）價格表上的值
DOC_PRICES = {
    "UL_1": (123.45, 86.415, "-", 123.45),
    "UL_2": (87.2, 61.04, "-", 87.2),
    "UL_3": (1234.56, 864.192, "-", 1234.56),
}


def run(tmp_path: Path, spec: Spec | None = None, **overrides):
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **overrides)])
    outcome, receipt = run_batch(CONFIG, sheet, [pdf], tmp_path / "reports", root=tmp_path, now=NOW)
    return outcome.items[0], receipt, spec


def prices_of(row: dict) -> dict[str, tuple]:
    return {ul: tuple(row[f"{ul}_{p}"] for p in PRICES) for ul in DOC_PRICES}


def test_vwap_with_blank_prices_passes_and_fills_the_document_prices(tmp_path):
    item, receipt, spec = run(tmp_path, 期初定價="VWAP", **BLANK)
    assert item.report.status.value == "PASS", [r for r in item.report.results if r.status.is_problem]
    assert prices_of(row_of(receipt.output, spec.product_code)) == DOC_PRICES


def test_vwap_overwrites_different_sheet_prices_and_records_the_old_values(tmp_path):
    stale = {f"UL_{i}_{p}": 1.0 for i in range(1, 4) for p in PRICES}
    item, receipt, spec = run(tmp_path, 期初定價="VWAP", **stale)
    assert item.report.status.value == "PASS", [r for r in item.report.results if r.status.is_problem]
    assert prices_of(row_of(receipt.output, spec.product_code)) == DOC_PRICES
    [doc] = load_record(tmp_path)["items"]
    ul1 = {d["column"]: (d["sheet_value"], d["action"]) for d in doc["backfill"] if d["column"].startswith("UL_1_")}
    assert ul1 == {c: ("1", "overwrite") for c in ("UL_1_進場價", "UL_1_執行價", "UL_1_下限價", "UL_1_KO價")}


def test_vwap_term_sheet_with_other_problems_can_be_released_and_is_overwritten(tmp_path):
    spec = Spec()
    stale = {f"UL_{i}_{p}": 1.0 for i in range(1, 4) for p in PRICES}
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    rows = [reference_row(spec, **{"K(%)": 71, "期初定價": "VWAP", **stale})]
    outcome = check_all(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), [pdf])
    (item,) = outcome.items
    assert item.report.status.value == "MISMATCH"
    assert item.release_problem == "", "覆寫的價格欄不算回填欄位已有不同的值"
    outcome.release(item)
    receipt = save_batch(outcome, tmp_path / "reports", root=tmp_path, now=NOW)
    assert prices_of(row_of(receipt.output, spec.product_code)) == DOC_PRICES


@pytest.mark.parametrize("pricing", ["開盤價", "收盤價"])
def test_open_or_close_pricing_still_compares_the_sheet_prices(tmp_path, pricing):
    item, receipt, spec = run(tmp_path, 期初定價=pricing, UL_1_進場價=1.0)
    assert item.report.status.value == "MISMATCH"
    assert any(m.startswith("UL_1 進場價對不起來") for m in item.problem_messages), item.problem_messages
    assert not [d for d in item.report.backfill if d.column.startswith("UL_")], "價格欄不是回填欄位"


@pytest.mark.parametrize(("pricing", "shown"), [(None, "空白"), ("均價", "均價")])
def test_blank_or_unknown_pricing_requires_review_naming_the_column(tmp_path, pricing, shown):
    item, receipt, spec = run(tmp_path, 期初定價=pricing)
    assert item.report.status.value == "REVIEW_REQUIRED"
    [message] = item.problem_messages
    assert message.startswith("期初定價") and shown in message, message
    assert not receipt.filled(item)


def test_sheet_without_the_pricing_column_requires_review_naming_it(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    headers = [h for h in REFERENCE_HEADERS if h != "期初定價"]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)], headers)
    (item,) = check_all(sheet, [pdf]).items
    assert item.report.status.value == "REVIEW_REQUIRED"
    assert any("期初定價" in m for m in item.problem_messages), item.problem_messages


def test_vwap_with_ki_fills_the_ki_prices_and_leaves_absent_underlyings_alone(tmp_path):
    spec = Spec(ki="AM")
    item, receipt, spec = run(tmp_path, spec, 期初定價="VWAP", **BLANK)
    assert item.report.status.value == "PASS", [r for r in item.report.results if r.status.is_problem]
    row = row_of(receipt.output, spec.product_code)
    # KI 60%：123.45 × 60% = 74.07、87.2 × 60% = 52.32、1234.56 × 60% = 740.736
    assert [row[f"UL_{i}_下限價"] for i in (1, 2, 3)] == [74.07, 52.32, 740.736]
    assert [row[f"UL_4_{p}"] for p in PRICES] == ["-"] * 4


def test_panel_shows_vwap_prices_as_overwritten(tmp_path):
    item, receipt, spec = run(tmp_path, 期初定價="VWAP", UL_1_進場價=1.0)
    panel = ResultPane._backfill_text(item)
    assert "UL_1_進場價（" in panel and "→ VWAP，核對通過後以說明書覆寫" in panel, panel


def test_vwap_fills_blank_price_cells_of_absent_underlyings_with_the_empty_value(tmp_path):
    absent = {f"UL_{i}_{p}": None for i in (4, 5) for p in PRICES}
    item, receipt, spec = run(tmp_path, 期初定價="VWAP", **BLANK, **absent)
    row = row_of(receipt.output, spec.product_code)
    assert [row[f"UL_{i}_{p}"] for i in (4, 5) for p in PRICES] == ["-"] * 8
