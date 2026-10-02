"""測試切點：`fcn-batch` CLI 的參數、結束碼與畫面輸出。核對細節見 test_batch.py。"""

from __future__ import annotations

from fcn_checker.cli import main
from synth import ROOT, Spec, build_pdf, build_reference_sheet, reference_row


def run(tmp_path, monkeypatch, capsys, specs, rows, extra=()):
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows)
    monkeypatch.chdir(ROOT)  # 預設設定檔路徑相對於工作目錄
    code = main([str(sheet), *map(str, pdfs), "--out", str(tmp_path / "reports"), *extra])
    return code, capsys.readouterr(), pdfs


def test_all_pass_exit_code_zero_and_prints_each_pdf_and_new_file(tmp_path, monkeypatch, capsys):
    spec = Spec()
    code, out, pdfs = run(tmp_path, monkeypatch, capsys, [spec], [reference_row(spec)])
    assert code == 0
    assert f"PASS（通過）  {pdfs[0].name}" in out.out
    assert "已回填" in out.out
    new = list(tmp_path.glob("FCN參考條件_回填_*.xlsx"))
    assert len(new) == 1 and str(new[0]) in out.out


def test_mismatch_or_unsupported_issuer_exit_code_one(tmp_path, monkeypatch, capsys):
    spec, other = Spec(), Spec(product_code="999199990001")
    code, out, _ = run(tmp_path, monkeypatch, capsys, [spec, other], [reference_row(spec, **{"K(%)": 71})])
    assert code == 1
    assert "MISMATCH（不一致）" in out.out and "未支援上手" in out.out


def test_batch_error_exit_code_two_and_no_new_file(tmp_path, monkeypatch, capsys):
    spec = Spec()
    code, out, _ = run(
        tmp_path, monkeypatch, capsys, [spec], [reference_row(spec)], extra=["--reference-format", "missing.toml"]
    )
    assert code == 2
    assert "missing.toml" in out.err
    assert list(tmp_path.glob("*_回填_*.xlsx")) == []


def test_corrupt_or_missing_pdf_gives_error_exit_code_and_report(tmp_path, monkeypatch, capsys):
    import json

    spec = Spec()
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    bad = tmp_path / "029199990001_TS.pdf"
    bad.write_bytes(b"not a pdf at all")
    monkeypatch.chdir(ROOT)
    out = tmp_path / "reports"
    assert main([str(sheet), str(bad), str(tmp_path / "029199990009_TS.pdf"), "--out", str(out)]) == 2
    reasons = {
        p.name.split("_TS_")[0]: json.loads(p.read_text(encoding="utf-8"))["results"][0]["reason_code"]
        for p in out.glob("*.check.json")
    }
    assert reasons == {"029199990001": "pdf_unreadable", "029199990009": "pdf_not_found"}


def test_console_script_entry_point(tmp_path):
    import subprocess
    import sys

    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    proc = subprocess.run(
        [sys.executable, "-m", "fcn_checker.cli", str(sheet), str(pdf), "--out", str(tmp_path / "r")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "整體狀態：PASS" in proc.stdout
