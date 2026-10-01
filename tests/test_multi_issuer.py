"""多上手分派：範本辨識經上手註冊表、詢價格式依上手選擇、審查標準依上手讀取名稱樣板。

測試切點仍是核對入口 run_check 與 `fcn-check` CLI；只用合成資料。
"""

from __future__ import annotations

import dataclasses
import json

from fcn_checker.checker import run_check
from fcn_checker.cli import main
from fcn_checker.issuers import BARC
from fcn_checker.schema import CheckStatus
from synth import ORDER_FORMAT, REVIEW_STANDARD, ROOT, Spec, build_inquiry, build_not_barc_pdf, build_pdf

PASS, REVIEW = CheckStatus.PASS, CheckStatus.REVIEW_REQUIRED


def inputs(tmp_path):
    spec = Spec()
    return build_pdf(tmp_path / "ts.pdf", spec), build_inquiry(tmp_path / "inquiry.xlsx", spec)


def only(report, rule_id, field=None):
    out = [r for r in report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert len(out) == 1, f"{rule_id} {field or ''} 應恰好一筆"
    return out[0]


def test_detected_issuer_is_recorded_and_its_rules_run(tmp_path):
    report = run_check(*inputs(tmp_path), REVIEW_STANDARD, ORDER_FORMAT)
    assert report.status == PASS
    assert report.template == BARC.template_id
    assert only(report, "template.detect").actual == BARC.template_id
    assert report.metadata["parser"] == {"template": BARC.template_id, "version": BARC.parser_version}
    assert {n["rule_id"] for n in report.not_covered} == {n["rule_id"] for n in BARC.not_covered}


def test_no_issuer_detected_requires_review_and_runs_no_rules(tmp_path):
    pdf = build_not_barc_pdf(tmp_path / "other.pdf")
    inq = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    report = run_check(pdf, inq, REVIEW_STANDARD)  # 未指定格式設定也不得猜上手
    r = only(report, "template.detect")
    assert (r.status, r.reason_code) == (REVIEW, "template_unknown")
    assert "BARC" in r.message
    assert report.template is None and report.status == REVIEW
    assert not any(x.rule_id.startswith(("field.", "standard.")) for x in report.results)
    assert report.metadata["parser"] is None


def test_two_issuers_detected_requires_review(tmp_path):
    fake = dataclasses.replace(BARC, code="FAKE", template_id="fake-zh-pd", label="FAKE 測試範本")
    report = run_check(*inputs(tmp_path), REVIEW_STANDARD, ORDER_FORMAT, registry=(BARC, fake))
    r = only(report, "template.detect")
    assert (r.status, r.reason_code) == (REVIEW, "template_ambiguous")
    assert set(r.actual) == {BARC.template_id, "fake-zh-pd"}
    assert report.template is None and report.status == REVIEW
    assert not any(x.rule_id.startswith("field.") for x in report.results)


def test_order_format_of_another_issuer_requires_review(tmp_path):
    fmt = tmp_path / "other.toml"
    fmt.write_text(
        ORDER_FORMAT.read_text(encoding="utf-8").replace('issuer = "BARC"', 'issuer = "FAKE"', 1), encoding="utf-8"
    )
    report = run_check(*inputs(tmp_path), REVIEW_STANDARD, fmt)
    r = only(report, "order.issuer")
    assert (r.status, r.reason_code, r.expected, r.actual) == (REVIEW, "issuer_mismatch", "BARC", "FAKE")
    assert report.status == REVIEW
    assert not any(x.rule_id.startswith("field.") for x in report.results)


def test_review_standard_reads_product_name_per_issuer(tmp_path):
    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    extra = tmp_path / "with_other.toml"
    extra.write_text(
        text + '\n[product_name.fake]\nzh = "假{tenor}"\nen = "Fake {ccy}"\nmemory_zh = ""\nmemory_en = ""\n',
        encoding="utf-8",
    )
    report = run_check(*inputs(tmp_path), extra, ORDER_FORMAT)
    assert report.status == PASS, "其他上手的名稱樣板不影響 BARC"

    missing = tmp_path / "without_barc.toml"
    missing.write_text(text.replace("[product_name.barc]", "[product_name.fake]"), encoding="utf-8")
    report = run_check(*inputs(tmp_path), missing, ORDER_FORMAT)
    for field in ("name_zh", "name_en"):
        r = only(report, "standard.product_name", field)
        assert (r.status, r.reason_code) == (REVIEW, "standard_missing")


def test_cli_picks_order_format_from_detected_issuer(tmp_path, monkeypatch):
    pdf, inq = inputs(tmp_path)
    out = tmp_path / "reports"
    monkeypatch.chdir(ROOT)  # 預設設定檔路徑相對於工作目錄
    code = main([str(pdf), str(inq), "--review-standard", str(REVIEW_STANDARD), "--out", str(out)])
    assert code == 0
    data = json.loads((out / "ts.check.json").read_text(encoding="utf-8"))
    assert data["status"] == "PASS"
    assert data["metadata"]["order_format"]["file"] == "barc.toml"
    assert data["metadata"]["order_format"]["issuer"] == "BARC"
