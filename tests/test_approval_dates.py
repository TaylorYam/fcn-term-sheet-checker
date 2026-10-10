"""審查通過日期清單（Issue #120）：依交易日核對、設定檔載入檢查，以及 PANEL 新增／修改／刪除最新一筆。

只用合成檔案；PANEL 只測工作階段（PanelSession），不開桌面視窗。
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from fcn_checker.approval_dates import parse_input_date
from fcn_checker.config import load_review_standard
from fcn_checker.ingestion import IngestionError
from fcn_checker.panel_workflow import PanelSession
from harness import MISMATCH, PASS, REVIEW, REVIEW_STANDARD, ROOT, check_sheet, load_config, results
from pdf_writer import Edit, zh_date
from reference_synth import build_reference_sheet, reference_row
from synth import APPROVAL_DATE, Spec, build_pdf

TODAY = dt.date(2031, 3, 4)
D = dt.date


def standard_with(tmp_path: Path, dates: str) -> Path:
    """repo 審查標準的副本，只把 approval_dates 換成 dates（TOML 陣列內容）。"""
    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    path = tmp_path / "review_standard.toml"
    path.write_text(re.sub(r"(?m)^approval_dates = \[.*\]$", f"approval_dates = [{dates}]", text), encoding="utf-8")
    return path


def approval_result(tmp_path: Path, dates: str, spec: Spec, edits: tuple[Edit, ...] = ()):
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    config = load_config(review_standard=standard_with(tmp_path, dates))
    [r] = results(check_sheet(pdf, sheet, config), "standard.approval_date")
    return r


# ---------------------------------------------------------------- 依交易日核對（合成說明書交易日 2030-01-07）


@pytest.mark.parametrize(
    "dates,printed,status,expected",
    [
        ("2026-06-11, 2031-01-01", D(2026, 6, 11), PASS, D(2026, 6, 11)),  # 重新核對舊說明書：用當時的日期
        ("2025-12-18, 2026-06-11", D(2025, 12, 18), MISMATCH, D(2026, 6, 11)),  # 新審查後仍印舊日期
        ("2026-06-11, 2030-01-07", D(2026, 6, 11), MISMATCH, D(2030, 1, 7)),  # 交易日當天就是新日期
        ("2026-06-11, 2030-01-07", D(2030, 1, 7), PASS, D(2030, 1, 7)),
    ],
)
def test_expected_date_is_the_latest_on_or_before_the_trade_date(tmp_path, dates, printed, status, expected):
    r = approval_result(tmp_path, dates, Spec(), (Edit(zh_date(APPROVAL_DATE), zh_date(printed), "ts.cover"),))
    assert (r.status, r.expected, r.actual) == (status, expected, printed)
    assert any("交易日" in e.text for e in r.document_evidence)


def test_trade_date_before_every_approval_date_needs_review(tmp_path):
    r = approval_result(tmp_path, "2031-01-01", Spec())
    assert (r.status, r.reason_code) == (REVIEW, "approval_date_not_configured")
    assert "2031-01-01" in r.message and r.expected is None


def test_missing_trade_date_needs_review(tmp_path):
    r = approval_result(tmp_path, "2025-12-18, 2026-06-11", Spec(omit=frozenset({"trade_date"})))
    assert (r.status, r.reason_code) == (REVIEW, "trade_date_unavailable")
    assert "交易日" in r.message


# ---------------------------------------------------------------- 設定檔載入


def test_repo_standard_keeps_both_known_approval_dates():
    assert load_review_standard(REVIEW_STANDARD).approval_dates == (D(2025, 12, 18), D(2026, 6, 11))


def test_dates_are_sorted_when_loaded(tmp_path):
    assert load_review_standard(standard_with(tmp_path, "2026-06-11, 2025-12-18")).approval_dates == (
        D(2025, 12, 18),
        D(2026, 6, 11),
    )


@pytest.mark.parametrize(
    "dates,message",
    [
        ("", "至少一個"),
        ("2026-06-11, 2026-06-11", "重複"),
        ('"2026-06-11"', "只能放日期"),
        ("2026-06-11T09:00:00", "只能放日期"),
    ],
)
def test_invalid_approval_dates_fail_to_load(tmp_path, dates, message):
    with pytest.raises(IngestionError, match=message) as raised:
        load_review_standard(standard_with(tmp_path, dates))
    assert raised.value.reason_code == "config_invalid"


# ---------------------------------------------------------------- PANEL：新增、修改或刪除最新一筆


def session_for(tmp_path: Path, dates: str = "2025-12-18, 2026-06-11") -> PanelSession:
    return PanelSession(standard_with(tmp_path, dates), ROOT / "config", install_root=tmp_path)


def test_adding_a_date_rewrites_only_the_dates_version_and_effective_date(tmp_path):
    session = session_for(tmp_path)
    before = session.review_standard.read_text(encoding="utf-8")
    old = load_review_standard(session.review_standard)

    message = session.add_approval_date(D(2026, 12, 10), today=TODAY)

    after = session.review_standard.read_text(encoding="utf-8")
    new = load_review_standard(session.review_standard)
    assert session.approval_dates() == (D(2025, 12, 18), D(2026, 6, 11), D(2026, 12, 10))
    assert (new.version, new.effective_date) == (old.version + 1, TODAY)
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
    assert changed == [
        (f"version = {old.version}", f"version = {old.version + 1}"),
        (f"effective_date = {old.effective_date}", f"effective_date = {TODAY}"),
        ("approval_dates = [2025-12-18, 2026-06-11]", "approval_dates = [2025-12-18, 2026-06-11, 2026-12-10]"),
    ]
    assert "2026-12-10" in message and "提交到 main" in message and session.message == message


def test_line_endings_are_kept(tmp_path):
    session = session_for(tmp_path)
    path = session.review_standard
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

    session.add_approval_date(D(2026, 12, 10), today=TODAY)
    data = path.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")


@pytest.mark.parametrize("new", [D(2026, 6, 11), D(2026, 1, 1)])
def test_new_date_must_be_later_than_the_latest(tmp_path, new):
    session = session_for(tmp_path)
    before = session.review_standard.read_bytes()
    with pytest.raises(IngestionError, match="必須晚於目前最新的 2026-06-11"):
        session.add_approval_date(new, today=TODAY)
    assert session.review_standard.read_bytes() == before


def test_future_dates_can_be_added(tmp_path):
    session = session_for(tmp_path)
    session.add_approval_date(D(2099, 1, 1), today=TODAY)
    assert session.approval_dates()[-1] == D(2099, 1, 1)


def test_only_the_latest_date_can_be_changed(tmp_path):
    session = session_for(tmp_path, "2025-12-18, 2026-06-11, 2026-12-01")
    session.change_latest_approval_date(D(2026, 12, 10), today=TODAY)
    assert session.approval_dates() == (D(2025, 12, 18), D(2026, 6, 11), D(2026, 12, 10))

    before = session.review_standard.read_bytes()
    with pytest.raises(IngestionError, match="沒有變更"):
        session.change_latest_approval_date(D(2026, 12, 10), today=TODAY)
    assert session.review_standard.read_bytes() == before, "改成同一天不寫檔、不升版"
    with pytest.raises(IngestionError, match="必須晚於前一次的 2026-06-11"):
        session.change_latest_approval_date(D(2026, 6, 1), today=TODAY)
    assert session.approval_dates() == (D(2025, 12, 18), D(2026, 6, 11), D(2026, 12, 10))


def test_removing_the_latest_keeps_at_least_one_date(tmp_path):
    session = session_for(tmp_path)
    session.remove_latest_approval_date(today=TODAY)
    assert session.approval_dates() == (D(2025, 12, 18),)
    with pytest.raises(IngestionError, match="至少要保留一筆"):
        session.remove_latest_approval_date(today=TODAY)
    assert session.approval_dates() == (D(2025, 12, 18),)


def test_changing_the_dates_clears_the_preview_and_result(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    session = session_for(tmp_path)
    session.select(sheet, [pdf])
    session.load_preview()
    session.start_check()

    session.add_approval_date(D(2026, 12, 10), today=TODAY)
    assert session.preview is None and session.outcome is None
    assert "重新載入預覽" in session.message
    session.load_preview()  # 改過的設定檔可以直接載入
    assert (
        session.start_check().batch.items[0].report.metadata["review_standard"]["version"]
        == load_review_standard(REVIEW_STANDARD).version + 1
    )


def test_unexpected_file_layout_is_refused_without_writing(tmp_path):
    path = standard_with(tmp_path, "2025-12-18, 2026-06-11")
    text = path.read_text(encoding="utf-8").replace("version = ", "version=", 1) + "\n[extra]\nversion = 1\n"
    path.write_text(text, encoding="utf-8")
    session = PanelSession(path, ROOT / "config", install_root=tmp_path)
    with pytest.raises(IngestionError, match="無法自動修改"):
        session.add_approval_date(D(2026, 12, 10), today=TODAY)
    assert path.read_text(encoding="utf-8") == text
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.parametrize("text,date", [("2026-12-10", D(2026, 12, 10)), (" 2026/12/10 ", D(2026, 12, 10))])
def test_panel_date_input_accepts_dashes_or_slashes(text, date):
    assert parse_input_date(text) == date


def test_panel_date_input_rejects_other_text():
    with pytest.raises(IngestionError, match="YYYY-MM-DD"):
        parse_input_date("12/10")


def test_write_failure_is_reported_and_the_file_is_kept(tmp_path, monkeypatch):
    session = session_for(tmp_path)
    before = session.review_standard.read_bytes()

    def locked(*_):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("fcn_checker.approval_dates.os.replace", locked)
    with pytest.raises(IngestionError, match="無法寫入審查標準設定檔") as raised:
        session.add_approval_date(D(2026, 12, 10), today=TODAY)
    assert raised.value.reason_code == "output_unwritable"
    assert session.review_standard.read_bytes() == before
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
