"""多上手分派：範本辨識經上手註冊表、審查標準依上手讀取名稱樣板、CLI 預設設定檔。

測試切點是批量核對入口與 `fcn-batch` CLI；只用合成資料。
"""

from __future__ import annotations

from collections import Counter

import pytest

from fcn_checker.cli import main
from fcn_checker.issuers import BARC
from fcn_checker.schema import CheckStatus
from harness import REVIEW_STANDARD, cli_root, load_record
from reference_synth import build_reference_sheet
from synth import Spec, barc_adapter, build_not_barc_pdf, build_pdf, check, check_pdf, reference_row

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

    fake = barc_adapter(code="FAKE", template_id="fake-zh-pd", label="FAKE 測試範本")
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
    report = _check_withholding(tmp_path, ["underlying_prices"], raises=True)
    r = only(report, "field.underlying_prices")
    assert (r.status, r.reason_code) == (REVIEW, "document_missing")
    assert "underlying_prices" in r.message
    assert report.status == REVIEW, "缺標準欄位轉人工覆核，不是執行錯誤"


class _Withheld:
    """上手讀出結果，但不交出 `names`；`raises=True` 模擬以字典查找、缺欄時丟 KeyError 的寫法。"""

    def __init__(self, ts, names, *, raises):
        self._ts, self._names, self._raises = ts, set(names), raises
        self.full_text = ts.full_text

    def f(self, name):
        if name not in self._names:
            return self._ts.f(name)
        if self._raises:
            raise KeyError(name)
        from fcn_checker.standard_fields import not_provided

        return not_provided(name)

    def __getattr__(self, attr):  # 上手專屬資料照常交給該上手規則
        return getattr(self._ts, attr)


def _check_withholding(tmp_path, names, *, raises):
    from fcn_checker.batch import check_batch
    from fcn_checker.parsers import barc as parser
    from harness import ISSUER_PREFIXES
    from reference_synth import REFERENCE_FORMAT

    # 只看各上手共用的規則：上手自己的說明書內部規則依自己的讀出結果寫，不在此契約內
    adapter = barc_adapter(read=lambda lines: _Withheld(parser.read(lines), names, raises=raises), rules=lambda ctx: [])
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "ref.xlsx", [reference_row(spec)])
    kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES}
    return check_batch([pdf], sheet, REVIEW_STANDARD, registry=(adapter,), **kw).items[0].report


# 審查標準規則讀的說明書欄位（#88）：上手漏交任一個都只轉人工覆核
REVIEW_STANDARD_FIELDS = [
    "approval_date",
    "chairman",
    "issue_price_pct",
    "name_zh",
    "name_en",
    "issuer_name_cover",
    "issuer_name_ch2",
    "distributor_name_cover",
    "distributor_phone_cover",
    "distributor_address_cover",
    "distributor_name_ch2",
    "distributor_address_ch2",
    "fee_申購費用",
    "tenor_months",
    "currency_zh",
    "ko_memory",
]


@pytest.mark.parametrize("raises", [False, True], ids=["returns_missing", "raises_key_error"])
@pytest.mark.parametrize("name", REVIEW_STANDARD_FIELDS)
def test_adapter_withholding_a_review_standard_field_requires_review_naming_it(tmp_path, name, raises):
    report = _check_withholding(tmp_path, [name], raises=raises)
    assert not [r for r in report.results if r.status == CheckStatus.ERROR], "缺標準欄位不是執行錯誤"
    assert report.status == REVIEW
    named = [r for r in report.results if r.status == REVIEW and name in r.message]
    assert named, f"人工覆核的錯訊要寫出缺的欄位 {name}"


@pytest.mark.parametrize("issuer", ["BARC", "HSBC"])
def test_read_term_sheet_gives_missing_for_a_field_it_does_not_provide(tmp_path, issuer):
    import hsbc_synth
    import synth
    from fcn_checker.extraction import extract_lines
    from fcn_checker.ingestion import open_pdf
    from fcn_checker.issuers import by_code
    from fcn_checker.schema import FieldStatus

    module = synth if issuer == "BARC" else hsbc_synth
    pdf = module.build_pdf(tmp_path / "TS.pdf", module.Spec())
    doc = open_pdf(pdf)
    try:
        ts = by_code(issuer).read(extract_lines(doc))
    finally:
        doc.close()
    pf = ts.f("no_such_field")
    assert pf.status == FieldStatus.MISSING
    assert "no_such_field" in pf.note


def test_shared_rules_can_only_read_standard_fields():
    from fcn_checker.rules.common import read_standard

    class AnyTermSheet:
        full_text = None

        def f(self, name):
            raise AssertionError("不應讀到非標準欄位")

    with pytest.raises(ValueError, match="price_table"):
        read_standard(AnyTermSheet(), "price_table")  # BARC 專屬欄位，共用規則不能讀


def test_each_term_sheet_is_detected_and_read_once_per_check(tmp_path):
    from fcn_checker.batch import check_batch
    from fcn_checker.parsers import barc as parser
    from harness import ISSUER_PREFIXES
    from reference_synth import REFERENCE_FORMAT

    calls: Counter[str] = Counter()

    def counted(name, fn):
        def wrapper(lines):
            calls[name] += 1
            return fn(lines)

        return wrapper

    adapter = barc_adapter(
        detect=counted("detect", lambda lines: parser.detect(parser.document(lines))),
        read=counted("read", parser.read),
    )
    specs = [Spec(), Spec(product_code="029199990002")]
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    sheet = build_reference_sheet(tmp_path / "ref.xlsx", [reference_row(s) for s in specs])
    kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES}
    outcome = check_batch(pdfs, sheet, REVIEW_STANDARD, registry=(adapter,), **kw)
    assert [i.report.status for i in outcome.items] == [PASS, PASS]
    assert calls == {"detect": 2, "read": 2}, "每份說明書辨識與讀出各只做一次"


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
    cli_root(tmp_path, monkeypatch)  # 預設設定檔路徑相對於工作目錄
    assert main([str(sheet), str(pdf), "--out", str(tmp_path / "reports")]) == 0
    data = load_record(tmp_path)["items"][0]
    assert data["status"] == "PASS"
    assert data["metadata"]["reference_format"]["file"] == "reference_sheet.toml"
    assert data["metadata"]["review_standard"]["file"] == "review_standard.toml"
