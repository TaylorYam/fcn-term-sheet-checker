"""多上手分派：上手註冊表、範本辨識分派、詢價格式設定選擇與依上手讀取審查標準。"""

from __future__ import annotations

import dataclasses
import json

from fcn_checker.checker import run_check
from fcn_checker.cli import main
from fcn_checker.config import load_review_standard
from fcn_checker.issuers import ISSUERS
from fcn_checker.panel_workflow import SUPPORTED_TEMPLATES, PanelSession
from fcn_checker.schema import CheckStatus
from synth import ORDER_FORMAT, REVIEW_STANDARD, ROOT, Spec, build_inquiry, build_not_barc_pdf, build_pdf

REVIEW = CheckStatus.REVIEW_REQUIRED
BARC = ISSUERS[0]


def inputs(tmp_path, *, barc=True):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec()) if barc else build_not_barc_pdf(tmp_path / "ts.pdf")
    return pdf, build_inquiry(tmp_path / "inquiry.xlsx", Spec())


def template_result(report):
    (r,) = [r for r in report.results if r.rule_id == "template.detect"]
    return r


def test_no_issuer_matched_requires_review_without_order_format(tmp_path):
    pdf, inq = inputs(tmp_path, barc=False)
    report = run_check(pdf, inq, REVIEW_STANDARD)
    assert report.status == REVIEW and report.template is None
    r = template_result(report)
    assert r.status == REVIEW and r.reason_code == "template_unknown"
    assert BARC.label in r.message
    assert report.metadata["parser"] == {"template": None, "version": None}
    assert report.not_covered == []


def test_two_issuers_matched_requires_review_as_ambiguous(tmp_path):
    fake = dataclasses.replace(BARC, code="FAKE", template_id="fake-zh-pd", label="假上手說明書")
    pdf, inq = inputs(tmp_path)
    report = run_check(pdf, inq, REVIEW_STANDARD, ORDER_FORMAT, issuers=(BARC, fake))
    assert report.status == REVIEW and report.template is None
    r = template_result(report)
    assert r.status == REVIEW and r.reason_code == "template_ambiguous"
    assert r.actual == [BARC.template_id, "fake-zh-pd"]
    assert not any(x.rule_id.startswith("field.") for x in report.results)


def test_order_format_issuer_differs_from_detected_issuer(tmp_path):
    fmt = tmp_path / "other.toml"
    fmt.write_text(
        ORDER_FORMAT.read_text(encoding="utf-8").replace('issuer = "BARC"', 'issuer = "HSBC"'), encoding="utf-8"
    )
    pdf, inq = inputs(tmp_path)
    report = run_check(pdf, inq, REVIEW_STANDARD, fmt)
    assert report.status == REVIEW and report.template is None
    assert template_result(report).status == CheckStatus.PASS
    (r,) = [r for r in report.results if r.rule_id == "order.issuer"]
    assert r.status == REVIEW and r.reason_code == "order_format_issuer_mismatch"
    assert (r.expected, r.actual) == ("BARC", "HSBC")
    assert not any(x.rule_id.startswith("field.") for x in report.results)


def test_cli_without_order_format_uses_detected_issuer_config(tmp_path, monkeypatch):
    pdf, inq = inputs(tmp_path)
    out = tmp_path / "reports"
    monkeypatch.chdir(ROOT)
    code = main([str(pdf), str(inq), "--review-standard", str(REVIEW_STANDARD), "--out", str(out)])
    assert code == 0
    data = json.loads((out / "ts.check.json").read_text(encoding="utf-8"))
    assert data["status"] == "PASS"
    assert data["template"] == BARC.template_id
    assert data["metadata"]["order_format"]["file"] == "barc.toml"
    assert data["metadata"]["order_format"]["issuer"] == "BARC"
    assert data["metadata"]["parser"] == {"template": BARC.template_id, "version": BARC.parser_version}


def test_review_standard_reads_product_name_section_per_issuer():
    std = load_review_standard(REVIEW_STANDARD)
    assert {"barc", "hsbc"} <= std.product_names.keys()
    barc, hsbc = std.product_names["barc"], std.product_names["hsbc"]
    assert barc.zh.startswith("英商巴克萊銀行") and barc.placeholders["memory_zh"] == "記憶式"
    assert hsbc.zh.startswith("香港上海滙豐銀行") and hsbc.placeholders["maxi_en"] == "Maxi "
    assert hsbc.ignore_whitespace_en and not barc.ignore_whitespace_en
    assert std.fixed_warning_for("BARC") == std.fixed_warning
    assert std.fixed_warning_for("HSBC") != std.fixed_warning


def test_panel_choices_and_default_order_format_come_from_registry(tmp_path, monkeypatch):
    assert [(c.issuer, c.template, c.label) for c in SUPPORTED_TEMPLATES] == [
        (i.code, i.template_id, i.label) for i in ISSUERS
    ]
    monkeypatch.chdir(ROOT)
    pdf, inq = inputs(tmp_path)
    session = PanelSession(review_standard=REVIEW_STANDARD)
    assert session.order_format == ORDER_FORMAT.resolve()
    session.select(pdf, inq)
    session.load_preview()
    outcome = session.start_check()
    assert not outcome.stopped and outcome.report.template == BARC.template_id
