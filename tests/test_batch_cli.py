"""測試切點：`fcn-batch` CLI 的參數、結束碼與畫面輸出。核對細節見 test_batch.py。"""

from __future__ import annotations

from fcn_checker.cli import main
from harness import ROOT, cli_root, load_record
from reference_synth import build_reference_sheet
from synth import Spec, build_pdf, reference_row


def run(tmp_path, monkeypatch, capsys, specs, rows, extra=()):
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows)
    cli_root(tmp_path, monkeypatch)  # 預設設定檔與核對紀錄都相對於執行目錄
    code = main([str(sheet), *map(str, pdfs), "--out", str(tmp_path / "reports"), *extra])
    return code, capsys.readouterr(), pdfs


def test_all_pass_exit_code_zero_and_prints_each_pdf_and_result_file(tmp_path, monkeypatch, capsys):
    spec = Spec()
    code, out, pdfs = run(tmp_path, monkeypatch, capsys, [spec], [reference_row(spec)])
    assert code == 0
    assert f"PASS（通過）  {pdfs[0].name}" in out.out
    assert "已回填" in out.out
    new = list((tmp_path / "reports").glob("FCN參考條件_核對結果_*.xlsx"))
    assert len(new) == 1 and f"核對結果檔：{new[0]}" in out.out
    assert list(tmp_path.glob("*_回填_*.xlsx")) == []
    [record] = (tmp_path / "runtime" / "核對紀錄").glob("*.json")
    assert f"核對紀錄：{record.resolve()}" in out.out


def test_record_failure_is_reported_and_the_result_file_is_kept(tmp_path, monkeypatch, capsys):
    spec = Spec()
    (tmp_path / "runtime").write_text("不是資料夾", encoding="utf-8")
    code, out, _ = run(tmp_path, monkeypatch, capsys, [spec], [reference_row(spec)])
    assert code == 2
    assert "核對紀錄" in out.err
    assert len(list((tmp_path / "reports").glob("FCN參考條件_核對結果_*.xlsx"))) == 1


def test_mismatch_or_unsupported_issuer_exit_code_one(tmp_path, monkeypatch, capsys):
    spec, other = Spec(), Spec(product_code="999199990001")
    code, out, _ = run(tmp_path, monkeypatch, capsys, [spec, other], [reference_row(spec, **{"K(%)": 71})])
    assert code == 1
    assert "MISMATCH（不一致）" in out.out and "未支援上手" in out.out


def test_batch_error_exit_code_two_and_no_result_file(tmp_path, monkeypatch, capsys):
    spec = Spec()
    code, out, _ = run(
        tmp_path, monkeypatch, capsys, [spec], [reference_row(spec)], extra=["--reference-format", "missing.toml"]
    )
    assert code == 2
    assert "missing.toml" in out.err
    assert not (tmp_path / "reports").exists() and not (tmp_path / "runtime").exists()


def test_corrupt_or_missing_pdf_gives_error_exit_code_and_record(tmp_path, monkeypatch, capsys):
    spec = Spec()
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    bad = tmp_path / "029199990001_TS.pdf"
    bad.write_bytes(b"not a pdf at all")
    cli_root(tmp_path, monkeypatch)
    out = tmp_path / "reports"
    assert main([str(sheet), str(bad), str(tmp_path / "029199990009_TS.pdf"), "--out", str(out)]) == 2
    reasons = {i["pdf"].split("_TS")[0]: i["results"][0]["reason_code"] for i in load_record(tmp_path)["items"]}
    assert reasons == {"029199990001": "pdf_unreadable", "029199990009": "pdf_not_found"}
    assert not list(out.glob("*.check.*")), "輸出資料夾沒有每份說明書的報告"


def test_console_script_entry_point(tmp_path):
    import shutil
    import subprocess
    import sys

    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    shutil.copytree(ROOT / "config", tmp_path / "config")  # 執行目錄是根目錄：核對紀錄寫在 tmp_path/runtime/
    proc = subprocess.run(
        [sys.executable, "-m", "fcn_checker.cli", str(sheet), str(pdf), "--out", str(tmp_path / "r")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "整體狀態：PASS" in proc.stdout
