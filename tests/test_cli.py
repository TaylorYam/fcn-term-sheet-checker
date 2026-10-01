"""測試切點 2：`fcn-check` CLI 的報告檔案與結束碼。"""

from __future__ import annotations

import json
import subprocess
import sys

from fcn_checker.cli import main
from synth import ORDER_FORMAT, REVIEW_STANDARD, Spec, build_inquiry, build_pdf


def run_cli(tmp_path, pdf, inq):
    out = tmp_path / "reports"
    code = main(
        [
            str(pdf),
            str(inq),
            "--review-standard",
            str(REVIEW_STANDARD),
            "--order-format",
            str(ORDER_FORMAT),
            "--out",
            str(out),
        ]
    )
    return code, out


def inputs(tmp_path, spec=None, pdf_spec=None):
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / "ts.pdf", pdf_spec or spec)
    inq = build_inquiry(tmp_path / "inquiry.xlsx", spec)
    return pdf, inq


def test_pass_exit_code_and_both_reports(tmp_path):
    code, out = run_cli(tmp_path, *inputs(tmp_path))
    assert code == 0
    data = json.loads((out / "ts.check.json").read_text(encoding="utf-8"))
    assert data["status"] == "PASS"
    assert data["not_covered"]
    assert data["metadata"]["inputs"]["term_sheet"]["sha256"]
    md = (out / "ts.check.md").read_text(encoding="utf-8")
    assert "整體狀態：PASS" in md and "未涵蓋規則" in md


def test_mismatch_exit_code_and_markdown_lists_problems_first(tmp_path):
    code, out = run_cli(tmp_path, *inputs(tmp_path, Spec(), pdf_spec=Spec(chairman="林晉輝")))
    assert code == 1
    md = (out / "ts.check.md").read_text(encoding="utf-8")
    problems = md.index("## 問題項目")
    assert md.index("standard.chairman") > problems
    assert md.index("standard.chairman") < md.index("## 通過與不適用項目")
    assert "林晉輝" in md and "林晋輝" in md


def test_review_required_exit_code(tmp_path):
    code, _ = run_cli(tmp_path, *inputs(tmp_path, Spec(denomination=50000)))
    assert code == 1


def test_corrupt_pdf_gives_error_exit_code_and_report(tmp_path):
    _, inq = inputs(tmp_path)
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    code, out = run_cli(tmp_path, bad, inq)
    assert code == 2
    data = json.loads((out / "broken.check.json").read_text(encoding="utf-8"))
    assert data["status"] == "ERROR"
    assert data["results"][0]["reason_code"] == "pdf_unreadable"


def test_missing_input_file_is_error(tmp_path):
    _, inq = inputs(tmp_path)
    code, _ = run_cli(tmp_path, tmp_path / "nope.pdf", inq)
    assert code == 2


def test_console_script_entry_point(tmp_path):
    pdf, inq = inputs(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "fcn_checker.cli",
            str(pdf),
            str(inq),
            "--review-standard",
            str(REVIEW_STANDARD),
            "--order-format",
            str(ORDER_FORMAT),
            "--out",
            str(tmp_path / "r"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert "整體狀態：PASS" in proc.stdout
