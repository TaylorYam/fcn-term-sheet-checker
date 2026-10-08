"""回填欄位（TS、IIS、ISIN Code、發行日、比價日_1～12）：TS、IIS 打勾、缺欄時的處理、顯示標籤、儲存前的參考條件表變更檢查。

測試切點是批量入口（預覽＋核對）與 save_batch；回填與日期格式的一般流程見 test_batch.py。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from fcn_checker.backfill import BackfillAction
from fcn_checker.ingestion import sha256_of
from fcn_checker.panel import ResultPane
from fcn_checker.saving import save_batch
from harness import CONFIG, MISMATCH, PASS, REVIEW, check_all, load_config, results
from reference_synth import REFERENCE_FORMAT, REFERENCE_HEADERS, build_reference_sheet
from synth import Spec, build_pdf, reference_row

NOW = dt.datetime(2030, 2, 3, 4, 5, 6)


def run(tmp_path: Path, *, headers=None, reference_format: Path = REFERENCE_FORMAT, overrides=None):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **(overrides or {}))], headers)
    config = CONFIG if reference_format == REFERENCE_FORMAT else load_config(reference_format=reference_format)
    return check_all(sheet, [pdf], config)


def assert_column_missing(outcome, rule_id: str, name: str, tmp_path: Path) -> None:
    report = outcome.items[0].report
    assert not [r for r in report.results if r.rule_id == "batch.unexpected"]
    r = results(report, rule_id)[0]
    assert (r.status, r.reason_code) == (REVIEW, "backfill_column_missing")
    assert name in r.message
    assert all(d.cell != "?" for d in report.backfill)
    receipt = save_batch(outcome, tmp_path / "reports", root=tmp_path, now=NOW)
    assert not receipt.filled(outcome.items[0])


# ---------------------------------------------------------------- TS、IIS（Issue #127）


def saved_sheet(outcome, tmp_path: Path):
    receipt = save_batch(outcome, tmp_path / "reports", root=tmp_path, now=NOW)
    return receipt, openpyxl.load_workbook(receipt.output)


def test_passing_product_gets_v_in_ts_and_iis(tmp_path):
    outcome = run(tmp_path)
    item = outcome.items[0]
    assert item.report.status == PASS and item.fills_sheet
    assert not [r for r in item.report.results if r.rule_id.startswith("order.")], "表頭有 TS、IIS 不算未知欄名"
    assert results(item.report, "backfill.checked")[0].status == PASS
    marks = [(d.column, d.cell, d.sheet_value, d.expected, d.action) for d in item.report.backfill[:2]]
    assert marks == [("TS", "A4", None, "V", BackfillAction.FILL), ("IIS", "B4", None, "V", BackfillAction.FILL)]
    receipt, wb = saved_sheet(outcome, tmp_path)
    assert receipt.filled(item)
    assert (wb["回填後"]["A4"].value, wb["回填後"]["B4"].value) == ("V", "V")


def test_existing_v_is_the_same_value(tmp_path):
    outcome = run(tmp_path, overrides={"TS": "V", "IIS": " V "})
    item = outcome.items[0]
    assert item.report.status == PASS
    assert [d.action for d in item.report.backfill[:2]] == [BackfillAction.MATCH, BackfillAction.MATCH]
    _, wb = saved_sheet(outcome, tmp_path)
    assert wb["回填後"]["A4"].value == "V"


@pytest.mark.parametrize("value", ["X", "v", "-", "✓"])
def test_other_value_in_ts_is_a_mismatch_kept_and_not_filled(tmp_path, value):
    outcome = run(tmp_path, overrides={"TS": value})
    item = outcome.items[0]
    r = results(item.report, "backfill.checked")[0]
    assert (item.report.status, r.status) == (MISMATCH, MISMATCH)
    assert [d.action for d in item.report.backfill[:2]] == [BackfillAction.MISMATCH, BackfillAction.FILL]
    assert f"TS對不起來：參考條件表 {value}／說明書 V" in item.problem_messages
    assert item.release_problem == "參考條件表回填欄位已有不同的值，請先修正參考條件表再核對"
    receipt, wb = saved_sheet(outcome, tmp_path)
    assert not receipt.filled(item) and wb["回填後"].max_row == 3, "沒回填的商品不出現在「回填後」"


def test_sheet_without_ts_column_requires_review_naming_it(tmp_path):
    headers = [h for h in REFERENCE_HEADERS if h != "TS"]
    outcome = run(tmp_path, headers=headers)
    assert_column_missing(outcome, "backfill.checked", "TS", tmp_path)
    assert results(outcome.items[0].report, "order.missing_column")[0].message.endswith("「TS」")


# ---------------------------------------------------------------- 缺欄


def test_sheet_without_a_compare_date_column_requires_review_naming_it(tmp_path):
    headers = [h for h in REFERENCE_HEADERS if h != "比價日_3"]
    assert_column_missing(run(tmp_path, headers=headers), "backfill.compare_dates", "比價日_3", tmp_path)


def test_sheet_without_isin_column_requires_review_naming_it(tmp_path):
    headers = [h for h in REFERENCE_HEADERS if h != "ISIN Code"]
    assert_column_missing(run(tmp_path, headers=headers), "backfill.isin", "ISIN Code", tmp_path)


def test_sheet_without_issue_date_column_requires_review_naming_it(tmp_path):
    headers = [h for h in REFERENCE_HEADERS if h != "發行日"]
    assert_column_missing(run(tmp_path, headers=headers), "backfill.issue_date", "發行日", tmp_path)


def test_format_without_issue_date_column_name_requires_review_naming_the_field(tmp_path):
    fmt = tmp_path / "reference_sheet.toml"
    text = REFERENCE_FORMAT.read_text(encoding="utf-8")
    assert '"發行日" = "issue_date"\n' in text
    fmt.write_text(text.replace('"發行日" = "issue_date"\n', ""), encoding="utf-8")
    headers = [h for h in REFERENCE_HEADERS if h != "發行日"]
    outcome = run(tmp_path, headers=headers, reference_format=fmt)
    assert_column_missing(outcome, "backfill.issue_date", "issue_date", tmp_path)


def test_format_without_a_compare_date_column_name_requires_review_naming_the_field(tmp_path):
    fmt = tmp_path / "reference_sheet.toml"
    text = REFERENCE_FORMAT.read_text(encoding="utf-8")
    assert '"比價日_3" = "autocall_date_3"\n' in text
    fmt.write_text(text.replace('"比價日_3" = "autocall_date_3"\n', ""), encoding="utf-8")
    headers = [h for h in REFERENCE_HEADERS if h != "比價日_3"]
    outcome = run(tmp_path, headers=headers, reference_format=fmt)
    assert_column_missing(outcome, "backfill.compare_dates", "autocall_date_3", tmp_path)


# ---------------------------------------------------------------- 顯示標籤


def test_panel_shows_the_backfill_labels(tmp_path):
    outcome = run(tmp_path, overrides={"比價日_12": "-"})
    item = outcome.items[0]
    assert item.report.status == PASS
    actions = {d.action for d in item.report.backfill}
    assert actions == {BackfillAction.FILL, BackfillAction.MATCH}  # 空白格與已填「-」的格子
    panel = ResultPane._backfill_text(item)
    for action in actions:
        assert action.label in panel


# ---------------------------------------------------------------- 儲存前檢查


def test_save_checks_the_reference_sheet_hash_recorded_on_the_batch(tmp_path):
    outcome = run(tmp_path)
    assert outcome.snapshot.sha256(outcome.reference_sheet) == sha256_of(outcome.reference_sheet)
    # 每份說明書 metadata 裡的檔案資訊只是記錄，不作為儲存前的檢查依據
    outcome.items[0].report.metadata["inputs"]["reference_sheet"]["sha256"] = "0" * 64
    receipt = save_batch(outcome, tmp_path / "reports", root=tmp_path, now=NOW)
    assert receipt.output is not None and receipt.filled(outcome.items[0])
