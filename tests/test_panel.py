"""PANEL 工作流程入口（PanelSession）：參考條件表＋多份說明書的預覽、核對、儲存與失效檢查。

只用合成檔案，不依賴桌面視窗或 parser 內部。核對規則本身見 test_check_barc*.py、test_batch.py。
"""

import datetime as dt
from dataclasses import FrozenInstanceError
from pathlib import Path

import fitz
import openpyxl
import pytest

from fcn_checker.ingestion import IngestionError
from fcn_checker.panel import parse_args
from fcn_checker.panel_workflow import PanelSession
from harness import ISSUER_PREFIXES, REVIEW_STANDARD, ROOT
from reference_synth import REFERENCE_FORMAT, build_reference_sheet
from synth import SYNTH_ISIN, Spec, build_pdf, reference_row

NOW = dt.datetime(2030, 2, 3, 4, 5, 6)


def inputs(tmp_path: Path, *specs: Spec, rows: list[dict] | None = None):
    specs = specs or (Spec(),)
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows or [reference_row(s) for s in specs])
    return sheet, pdfs


def session_for(tmp_path: Path, sheet, pdfs, standard=REVIEW_STANDARD, config_dir=None) -> PanelSession:
    session = PanelSession(standard, config_dir or ROOT / "config")
    session.select(sheet, pdfs)
    return session


# ---------------------------------------------------------------- 預覽


def test_preview_lists_each_pdf_without_writing_anything(tmp_path):
    ok, unknown, other = Spec(), Spec(product_code="029199990002"), Spec(product_code="999199990001")
    sheet, pdfs = inputs(tmp_path, ok, unknown, other, rows=[reference_row(ok)])
    before = set(tmp_path.iterdir())
    session = session_for(tmp_path, sheet, pdfs)

    preview = session.load_preview()
    first, missing, unsupported = preview.rows
    assert (first.issuer, first.product_code, first.reference_row, first.problem) == ("BARC", ok.product_code, 4, "")
    assert first.product_code_evidence[0].page == 1
    assert missing.reference_row is None and "找不到" in missing.problem
    assert unsupported.unsupported and "未支援上手" in unsupported.problem
    assert session.preview == preview
    assert set(tmp_path.iterdir()) == before
    with pytest.raises(FrozenInstanceError):
        first.problem = "modified"


def test_preview_and_result_show_pdfs_sharing_one_reference_row(tmp_path):
    spec = Spec()
    pdfs = [build_pdf(tmp_path / f"{spec.product_code}_{v}.pdf", spec) for v in ("舊版", "新版")]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    session = session_for(tmp_path, sheet, pdfs)
    old, new = session.load_preview().rows
    assert old.reference_row == new.reference_row == 4
    assert "同一批有多份說明書對到同一個 TDCC Code" in old.problem and pdfs[1].name in old.problem
    assert pdfs[0].name in new.problem

    outcome = session.start_check()
    assert "2 份需人工覆核" in outcome.headline
    for item, other in zip(outcome.ordered_items, reversed(pdfs), strict=True):
        assert other.name in outcome.ordered_results(item)[0].message


def test_preview_reports_reference_sheet_column_problems(tmp_path):
    from reference_synth import REFERENCE_HEADERS

    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "ref.xlsx", [reference_row(spec)], [*REFERENCE_HEADERS, "新欄位"])
    preview = session_for(tmp_path, sheet, [pdf]).load_preview()
    assert any("新欄位" in w for w in preview.warnings)


def test_pdf_product_code_is_never_taken_from_the_sheet(tmp_path):
    spec = Spec()
    sheet, pdfs = inputs(tmp_path, spec)
    with fitz.open(pdfs[0]) as doc:
        page = doc[0]
        page.add_redact_annot(page.search_for(spec.product_code)[0])
        page.apply_redactions()
        doc.saveIncr()
    row = session_for(tmp_path, sheet, pdfs).load_preview().rows[0]
    assert row.product_code is None and row.problem


def test_selection_is_required_and_changing_it_clears_preview(tmp_path):
    sheet, pdfs = inputs(tmp_path)
    session = session_for(tmp_path, sheet, pdfs)
    session.load_preview()
    session.select(sheet, [])
    assert session.preview is None
    with pytest.raises(IngestionError, match="請先選取"):
        session.load_preview()


@pytest.mark.parametrize("kind", ["broken_excel", "result_sheet", "missing_sheet"])
def test_invalid_reference_sheet_never_leaves_valid_preview(tmp_path, kind):
    sheet, pdfs = inputs(tmp_path)
    session = session_for(tmp_path, sheet, pdfs)
    session.load_preview()
    if kind == "broken_excel":
        sheet.write_bytes(b"not an Excel")
    else:
        wb = openpyxl.load_workbook(sheet)
        if kind == "result_sheet":
            wb.create_sheet("核對結果")
        else:
            wb["樣本清單"].title = "其他"
        wb.save(sheet)
    with pytest.raises(IngestionError):
        session.load_preview()
    assert session.preview is None and session.message


# ---------------------------------------------------------------- 核對


def test_check_requires_preview_and_writes_nothing(tmp_path):
    ok, bad = Spec(), Spec(product_code="029199990002")
    sheet, pdfs = inputs(tmp_path, ok, bad, rows=[reference_row(ok), reference_row(bad, **{"K(%)": 71})])
    session = session_for(tmp_path, sheet, pdfs)
    with pytest.raises(IngestionError, match="預覽"):
        session.start_check()
    session.load_preview()
    before = set(tmp_path.rglob("*"))

    outcome = session.start_check()
    assert set(tmp_path.rglob("*")) == before
    assert session.outcome is outcome
    assert [i.term_sheet for i in outcome.ordered_items] == [pdfs[1], pdfs[0]], "有問題的排前面"
    assert "1 份通過" in outcome.headline and "1 份不一致" in outcome.headline
    first = outcome.ordered_results(outcome.ordered_items[0])
    assert first[0].status.value == "MISMATCH"
    assert not outcome.batch.output and not any(i.filled for i in outcome.batch.items)


@pytest.mark.parametrize("changed", ["selection", "pdf", "sheet", "standard", "config"])
def test_changing_sources_clears_result_and_requires_new_preview(tmp_path, changed):
    sheet, pdfs = inputs(tmp_path)
    standard = tmp_path / "standard.toml"
    standard.write_bytes(REVIEW_STANDARD.read_bytes())
    config = tmp_path / "config"
    config.mkdir()
    for f in (REFERENCE_FORMAT, ISSUER_PREFIXES):
        (config / f.name).write_bytes(f.read_bytes())
    session = session_for(tmp_path, sheet, pdfs, standard, config)
    session.load_preview()
    session.start_check()
    if changed == "selection":
        session.select(sheet, pdfs)
    elif changed == "pdf":
        build_pdf(pdfs[0], Spec(tenor=7))
    elif changed == "sheet":
        build_reference_sheet(sheet, [reference_row(Spec(), **{"K(%)": 71})])
    elif changed == "standard":
        standard.write_text(standard.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")
    else:
        prefixes = config / ISSUER_PREFIXES.name
        prefixes.write_text(prefixes.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")
    assert session.outcome is None and session.preview is None
    assert "重新載入" in session.message or changed == "selection"
    with pytest.raises(IngestionError, match="預覽"):
        session.start_check()
    assert session.load_preview() is not None


# ---------------------------------------------------------------- 儲存


def test_save_writes_reports_and_back_filled_copy_only_when_asked(tmp_path):
    spec = Spec()
    sheet, pdfs = inputs(tmp_path, spec)
    original = sheet.read_bytes()
    session = session_for(tmp_path, sheet, pdfs)
    with pytest.raises(IngestionError):
        session.save(tmp_path / "reports", now=NOW)
    session.load_preview()
    outcome = session.start_check()

    assert session.save(None).cancelled
    assert not (tmp_path / "reports").exists()
    receipt = session.save(tmp_path / "reports", now=NOW)
    assert receipt.complete
    assert receipt.output == tmp_path / "FCN參考條件_回填_20300203-040506.xlsx"
    assert str(receipt.output) in receipt.summary
    assert {p.name for p in (tmp_path / "reports").iterdir()} == {
        f"{pdfs[0].stem}_20300203-040506.check.json",
        f"{pdfs[0].stem}_20300203-040506.check.md",
    }
    ws = openpyxl.load_workbook(receipt.output)["樣本清單"]
    assert ws["F4"].value == SYNTH_ISIN
    assert sheet.read_bytes() == original
    assert session.outcome is outcome

    again = session.save(tmp_path / "reports", now=NOW + dt.timedelta(seconds=1))
    assert again.complete and again.output != receipt.output


def test_save_failure_is_reported_and_result_is_kept(tmp_path):
    sheet, pdfs = inputs(tmp_path)
    session = session_for(tmp_path, sheet, pdfs)
    session.load_preview()
    outcome = session.start_check()
    blocked = tmp_path / "blocked"
    blocked.write_text("keep")

    receipt = session.save(blocked, now=NOW)
    assert not receipt.complete
    assert "報告未儲存" in receipt.summary
    assert blocked.read_text() == "keep"
    assert session.outcome is outcome


def test_save_after_sources_changed_is_refused(tmp_path):
    sheet, pdfs = inputs(tmp_path)
    session = session_for(tmp_path, sheet, pdfs)
    session.load_preview()
    session.start_check()
    build_reference_sheet(sheet, [reference_row(Spec(), **{"K(%)": 71})])
    with pytest.raises(IngestionError):
        session.save(tmp_path / "reports", now=NOW)
    assert not (tmp_path / "reports").exists()
    assert list(tmp_path.glob("*_回填_*.xlsx")) == []


# ---------------------------------------------------------------- 設定檔與啟動參數


def test_config_falls_back_to_builtin_files_when_root_config_lacks_them(tmp_path):
    old_install = tmp_path / "config"
    old_install.mkdir()
    (old_install / "review_standard.toml").write_bytes(REVIEW_STANDARD.read_bytes())
    session = PanelSession(old_install / "review_standard.toml", old_install)
    assert session.reference_format == REFERENCE_FORMAT.resolve()
    assert session.issuer_prefixes == ISSUER_PREFIXES.resolve()
    assert str(REFERENCE_FORMAT.resolve()) in "\n".join(str(p) for _, p in session.config_paths)

    own = old_install / "issuer_prefixes.toml"
    own.write_bytes(ISSUER_PREFIXES.read_bytes())
    assert PanelSession(old_install / "review_standard.toml", old_install).issuer_prefixes == own.resolve()


def test_panel_accepts_legacy_launcher_arguments(tmp_path):
    args = parse_args(
        [
            "--order-formats-dir",
            str(tmp_path / "config" / "order_formats"),
            "--review-standard",
            str(tmp_path / "config" / "review_standard.toml"),
            "--install-root",
            str(tmp_path),
        ]
    )
    assert args.config_dir == tmp_path / "config", "舊啟動器只給 order_formats 資料夾：取其上一層 config"
    args = parse_args(["--config-dir", str(tmp_path / "cfg")])
    assert args.config_dir == tmp_path / "cfg"


def test_builtin_config_comes_from_the_launched_release_not_the_package_location(tmp_path, monkeypatch):
    """PANEL 安裝是 `pip install .`（非 editable）：內建設定必須由啟動的版本資料夾指定，不能靠套件位置推算。"""
    import importlib.util

    root, release = tmp_path / "install", tmp_path / "install" / ".local" / "releases" / "abc"
    (release / "config").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "review_standard.toml").write_bytes(REVIEW_STANDARD.read_bytes())
    for f in (REFERENCE_FORMAT, ISSUER_PREFIXES):
        (release / "config" / f.name).write_bytes(f.read_bytes())

    captured = {}
    monkeypatch.setattr("fcn_checker.panel.main", lambda argv, on_ready=None: captured.setdefault("argv", argv))
    spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "panel_bootstrap.py")
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    bootstrap.run(str(release / "launch_panel.pyw"), ["--managed-root", str(root)])

    args = parse_args(captured["argv"])
    assert args.config_dir == root / "config"
    assert args.builtin_config_dir == release / "config"
    session = PanelSession(args.review_standard, args.config_dir, builtin_config_dir=args.builtin_config_dir)
    assert session.reference_format == (release / "config" / REFERENCE_FORMAT.name).resolve()
    assert session.issuer_prefixes == (release / "config" / ISSUER_PREFIXES.name).resolve()
