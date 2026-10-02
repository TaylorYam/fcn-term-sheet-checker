"""測試切點：`fcn-batch` CLI 的參數、結束碼與畫面輸出。核對細節見 test_batch.py。"""

from __future__ import annotations

from fcn_checker.cli import batch_main
from synth import ROOT, Spec, build_pdf, build_reference_sheet, reference_row


def run(tmp_path, monkeypatch, capsys, specs, rows, extra=()):
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows)
    monkeypatch.chdir(ROOT)  # 預設設定檔路徑相對於工作目錄
    code = batch_main([str(sheet), *map(str, pdfs), "--out", str(tmp_path / "reports"), *extra])
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
