"""多上手分派：範本辨識經上手註冊表、審查標準依上手讀取名稱樣板、CLI 預設設定檔。

測試切點是批量核對入口與 `fcn-batch` CLI；只用合成資料。
"""

from __future__ import annotations

import dataclasses
import json

from fcn_checker.cli import main
from fcn_checker.issuers import BARC
from fcn_checker.schema import CheckStatus
from harness import REVIEW_STANDARD, ROOT
from reference_synth import build_reference_sheet
from synth import Spec, build_not_barc_pdf, build_pdf, check, check_pdf, reference_row

PASS, REVIEW = CheckStatus.PASS, CheckStatus.REVIEW_REQUIRED


def only(report, rule_id, field=None):
    out = [r for r in report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert len(out) == 1, f"{rule_id} {field or ''} 應恰好一筆"
    return out[0]


def test_detected_issuer_is_recorded_and_its_rules_run(tmp_path):
    report = check(tmp_path)
    assert report.status == PASS
    assert report.template == BARC.template_id
    assert only(report, "template.detect").actual == BARC.template_id
    assert report.metadata["parser"] == {"template": BARC.template_id, "version": BARC.parser_version}
    assert {n["rule_id"] for n in report.not_covered} == {n["rule_id"] for n in BARC.not_covered}


def test_no_issuer_detected_requires_review_and_runs_no_rules(tmp_path):
    report = check_pdf(tmp_path, build_not_barc_pdf(tmp_path / "029199990001_TS.pdf"))
    r = only(report, "template.detect")
    assert (r.status, r.reason_code) == (REVIEW, "template_unknown")
    assert "BARC" in r.message
    assert report.template is None and report.status == REVIEW
    assert not any(x.rule_id.startswith(("field.", "standard.")) for x in report.results)
    assert report.metadata["parser"] is None


def test_two_issuers_detected_requires_review(tmp_path):
    from fcn_checker.batch import check_batch
    from harness import ISSUER_PREFIXES
    from reference_synth import REFERENCE_FORMAT

    fake = dataclasses.replace(BARC, code="FAKE", template_id="fake-zh-pd", label="FAKE 測試範本")
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "ref.xlsx", [reference_row(spec)])
    outcome = check_batch(
        [pdf],
        sheet,
        REVIEW_STANDARD,
        reference_format=REFERENCE_FORMAT,
        issuer_prefixes=ISSUER_PREFIXES,
        registry=(BARC, fake),
    )
    report = outcome.items[0].report
    r = only(report, "template.detect")
    assert (r.status, r.reason_code) == (REVIEW, "template_ambiguous")
    assert set(r.actual) == {BARC.template_id, "fake-zh-pd"}
    assert report.template is None and report.status == REVIEW
    assert not any(x.rule_id.startswith("field.") for x in report.results)


def test_adapter_missing_a_standard_field_requires_review_naming_the_field(tmp_path):
    from fcn_checker.batch import check_batch
    from harness import ISSUER_PREFIXES
    from reference_synth import REFERENCE_FORMAT

    def parse_without_prices(lines):
        det, ts = BARC.parse(lines)
        del ts.fields["underlying_prices"]
        return det, ts

    adapter = dataclasses.replace(BARC, parse=parse_without_prices)
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "ref.xlsx", [reference_row(spec)])
    kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES}
    report = check_batch([pdf], sheet, REVIEW_STANDARD, registry=(adapter,), **kw).items[0].report
    r = only(report, "field.underlying_prices")
    assert (r.status, r.reason_code) == (REVIEW, "document_missing")
    assert "underlying_prices" in r.message
    assert report.status == REVIEW, "缺標準欄位轉人工覆核，不是執行錯誤"


def test_review_standard_reads_product_name_per_issuer(tmp_path):
    from fcn_checker.batch import check_batch
    from harness import ISSUER_PREFIXES
    from reference_synth import REFERENCE_FORMAT

    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "ref.xlsx", [reference_row(spec)])

    def run(standard):
        kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES}
        return check_batch([pdf], sheet, standard, **kw).items[0].report

    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    extra = tmp_path / "with_other.toml"
    extra.write_text(
        text + '\n[product_name.fake]\nzh = "假{tenor}"\nen = "Fake {ccy}"\nmemory_zh = ""\nmemory_en = ""\n',
        encoding="utf-8",
    )
    assert run(extra).status == PASS, "其他上手的名稱樣板不影響 BARC"

    missing = tmp_path / "without_barc.toml"
    missing.write_text(text.replace("[product_name.barc]", "[product_name.fake]"), encoding="utf-8")
    report = run(missing)
    for field in ("name_zh", "name_en"):
        r = only(report, "standard.product_name", field)
        assert (r.status, r.reason_code) == (REVIEW, "standard_missing")


def test_cli_uses_default_config_files(tmp_path, monkeypatch):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "ref.xlsx", [reference_row(spec)])
    out = tmp_path / "reports"
    monkeypatch.chdir(ROOT)  # 預設設定檔路徑相對於工作目錄
    assert main([str(sheet), str(pdf), "--out", str(out)]) == 0
    data = json.loads(next(out.glob("*.check.json")).read_text(encoding="utf-8"))
    assert data["status"] == "PASS"
    assert data["metadata"]["reference_format"]["file"] == "reference_sheet.toml"
    assert data["metadata"]["review_standard"]["file"] == "review_standard.toml"
