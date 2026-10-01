"""測試切點 1：核對入口 run_check(說明書, 詢價表, 審查標準, 格式設定) → 完整核對結果。

只用合成資料（tests/synth.py），不直接測擷取或解析的內部函式。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from fcn_checker.checker import run_check
from fcn_checker.schema import CheckStatus
from synth import ORDER_FORMAT, REVIEW_STANDARD, UL, Spec, build_inquiry, build_not_barc_pdf, build_pdf

PASS, MISMATCH, REVIEW, NA, ERROR = (
    CheckStatus.PASS,
    CheckStatus.MISMATCH,
    CheckStatus.REVIEW_REQUIRED,
    CheckStatus.NOT_APPLICABLE,
    CheckStatus.ERROR,
)


def check(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    overrides=None,
    extra_columns=None,
    product_code=None,
):
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / "ts.pdf", pdf_spec or spec)
    inq = build_inquiry(tmp_path / "inquiry.xlsx", spec, overrides, extra_columns, product_code)
    return run_check(pdf, inq, REVIEW_STANDARD, ORDER_FORMAT)


def problems(report) -> set[tuple[str, CheckStatus]]:
    return {(r.rule_id, r.status) for r in report.results if r.status not in (PASS, NA)}


def results(report, rule_id: str, field: str | None = None):
    out = [r for r in report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert out, f"沒有 {rule_id} {field or ''} 的結果"
    return out


# ---------------------------------------------------------------- 全部一致


def test_all_consistent_passes_with_evidence_and_metadata(tmp_path):
    report = check(tmp_path)

    assert problems(report) == set()
    assert report.status == PASS
    trade = results(report, "field.trade_date")[0]
    assert trade.expected == dt.date(2030, 1, 7) and trade.actual == dt.date(2030, 1, 7)
    assert trade.document_evidence and trade.document_evidence[0].page >= 1
    assert "交易日" in trade.document_evidence[0].text
    assert trade.order_source == ["詢價表格!W5"]
    meta = report.metadata
    assert len(meta["inputs"]["term_sheet"]["sha256"]) == 64
    assert len(meta["inputs"]["order"]["sha256"]) == 64
    assert meta["review_standard"]["version"] == 1
    assert meta["order_format"]["issuer"] == "BARC"
    assert meta["program_version"] and meta["extractor"].startswith("PyMuPDF")
    assert report.not_covered, "第二階段規則應列在未涵蓋清單"


@pytest.mark.parametrize(
    "spec",
    [
        Spec(ko_obs="D", memory=True, ki="none"),
        Spec(ko_obs="D", memory=False, ki="AM"),
        Spec(ko_obs="P", memory=True, ki="D"),
        Spec(
            ko_obs="P",
            memory=False,
            ki="none",
            underlyings=(UL("單一標的公司", "紐約證券交易所", "SOLO UN", Decimal("45.6700")),),
        ),
        Spec(currency_zh="日幣", tenor=7, annual=Decimal("9.00")),
        Spec(currency_zh="人民幣", tenor=12, annual=Decimal("10.00"), monthly=Decimal("0.8333")),
    ],
    ids=["daily-memory-noKI", "daily-EKI", "periodend-memory-AKI", "periodend-single", "JPY", "CNH"],
)
def test_supported_variants_pass(tmp_path, spec):
    report = check(tmp_path, spec)
    assert problems(report) == set()
    assert report.status == PASS


def test_no_ki_is_determined_from_document_not_assumed(tmp_path):
    report = check(tmp_path, Spec(ki="none"))
    ki = results(report, "field.ki_type")[0]
    assert ki.status == PASS
    assert ki.actual == "無 KI" and ki.document_evidence, "無 KI 必須有說明書證據"
    assert results(report, "field.ki_pct")[0].status == NA


def test_price_table_cross_page_thousands_and_wrapped_names(tmp_path):
    report = check(tmp_path, Spec(ki="AM", cross_page_price_table=True))
    prices = results(report, "derive.prices")
    assert len(prices) == 3 * 3  # 3 標的 × 執行／KO／下限
    assert {r.status for r in prices} == {PASS}
    assert any("1,234.5600" in e.text for r in prices for e in r.document_evidence)
    assert report.status == PASS


def test_excel_float_tail_is_cleaned(tmp_path):
    spec = Spec(strike=Decimal("63.13"))
    report = check(tmp_path, spec, overrides={"Strike (%)": 63.129999999999995})
    assert results(report, "field.strike_pct")[0].status == PASS


# ---------------------------------------------------------------- 詢價表 vs 說明書：單一欄位不一致


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"Currency": "JPY"}, {"field.currency"}),
        ({"BBG Code 2": "ZQH UQ"}, {"field.underlyings"}),
        ({"BBG Code 3": None}, {"field.underlyings"}),
        ({"Strike (%)": 75.0}, {"field.strike_pct"}),
        ({"KO Barrier (%)": 105.0}, {"field.ko_pct"}),
        # 年利率不同時，由它推算的月配息率也會不符
        ({"Coupon p.a. (%)": 12.5}, {"field.coupon_pa_pct", "derive.monthly_coupon"}),
        ({"Tenor (m)": 9}, {"field.tenor_months"}),
        ({"Trade Date": dt.datetime(2030, 1, 8)}, {"field.trade_date"}),
        ({"Issue Date": dt.datetime(2030, 1, 15)}, {"field.issue_date"}),
        ({"Final Valuation Date": dt.datetime(2030, 7, 9)}, {"field.final_valuation_date"}),
        ({"Maturity Date": dt.datetime(2030, 7, 12)}, {"field.maturity_date"}),
        ({"Effective Date offset": 8}, {"field.issue_date_offset_days"}),
        ({"KO Type": "Daily"}, {"field.ko_type"}),
        ({"KO Type": "Period End Memory"}, {"field.ko_type"}),
        ({"Barrier Type": "EKI", "KI Barrier (%)": 60.0}, {"field.ki_type", "field.ki_pct"}),
        ({"KI Barrier (%)": 60.0}, {"field.ki_pct"}),
    ],
)
def test_single_field_mismatch(tmp_path, overrides, expected):
    report = check(tmp_path, overrides=overrides)
    assert problems(report) == {(rule_id, MISMATCH) for rule_id in expected}
    assert report.status == MISMATCH


def test_ki_pct_mismatch_when_both_have_ki(tmp_path):
    report = check(tmp_path, Spec(ki="AM"), overrides={"KI Barrier (%)": 65.0})
    assert problems(report) == {("field.ki_pct", MISMATCH)}


def test_product_code_mismatch(tmp_path):
    report = check(tmp_path, product_code="029199990002")
    assert problems(report) == {("field.product_code", MISMATCH)}


# ---------------------------------------------------------------- 推算規則


def test_monthly_coupon_within_tolerance_passes(tmp_path):
    spec = Spec(annual=Decimal("10.00"), monthly=Decimal("0.8334"))  # 推算 0.8333，差 0.0001
    report = check(tmp_path, spec)
    r = results(report, "derive.monthly_coupon")[0]
    assert r.status == PASS and r.expected == Decimal("0.8333") and r.actual == Decimal("0.8334")
    assert r.tolerance


def test_monthly_coupon_beyond_tolerance_mismatches(tmp_path):
    spec = Spec(annual=Decimal("10.00"), monthly=Decimal("0.8335"))
    report = check(tmp_path, spec)
    assert problems(report) == {("derive.monthly_coupon", MISMATCH)}


def test_coupon_mentions_must_agree_within_document(tmp_path):
    report = check(tmp_path, Spec(mention_overrides={"§17": "1.0100"}))
    assert problems(report) == {("doc.coupon_consistency", MISMATCH)}
    bad = [r for r in results(report, "doc.coupon_consistency") if r.status == MISMATCH][0]
    assert any("1.0100" in e.text for e in bad.document_evidence)


def test_price_derivation_error(tmp_path):
    report = check(tmp_path, Spec(price_overrides={(2, "strike"): "61.0500"}))
    assert problems(report) == {("derive.prices", MISMATCH)}
    bad = [r for r in results(report, "derive.prices") if r.status == MISMATCH]
    assert len(bad) == 1 and bad[0].expected == Decimal("61.0400") and bad[0].actual == Decimal("61.0500")


def test_non_default_denomination_requires_review(tmp_path):
    report = check(tmp_path, Spec(denomination=50000))
    assert problems(report) == {("doc.denomination", REVIEW)}
    assert report.status == REVIEW


# ---------------------------------------------------------------- 詢價表格式


@pytest.mark.parametrize(
    ("kwargs", "rule_id"),
    [
        ({"overrides": {"KO Type": "Weekly Memory"}}, "field.ko_type"),
        ({"overrides": {"Barrier Type": "XKI"}}, "field.ki_type"),
        ({"extra_columns": {"Coupon Freq": "M"}}, "order.unknown_column"),
    ],
)
def test_unknown_order_vocabulary_requires_review(tmp_path, kwargs, rule_id):
    report = check(tmp_path, **kwargs)
    assert (rule_id, REVIEW) in problems(report)
    assert report.status == REVIEW


def test_duplicate_order_column_requires_review(tmp_path):
    report = check(tmp_path, extra_columns={"Strike (%)": 75.0})  # 第二個同名欄位
    assert ("order.duplicate_column", REVIEW) in problems(report)
    assert report.status == REVIEW


def test_whole_number_excel_values_are_shown_plainly(tmp_path):
    from fcn_checker.reporting import to_markdown

    report = check(tmp_path, Spec(annual=Decimal("10.00"), monthly=Decimal("0.8333")))
    r = results(report, "derive.monthly_coupon")[0]
    assert "E+" not in r.message and "推算：10" in r.message
    assert "E+" not in to_markdown(report)


def test_monthly_ki_is_not_supported_in_phase_one(tmp_path):
    report = check(tmp_path, Spec(ki="M"))
    assert ("field.ki_type", REVIEW) in problems(report)


# ---------------------------------------------------------------- 範本辨識、缺漏、歧義


def test_non_barc_document_requires_review(tmp_path):
    pdf = build_not_barc_pdf(tmp_path / "other.pdf")
    inq = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    report = run_check(pdf, inq, REVIEW_STANDARD, ORDER_FORMAT)
    assert report.status == REVIEW
    assert results(report, "template.barc")[0].status == REVIEW
    assert report.template is None


def test_missing_field_requires_review(tmp_path):
    report = check(tmp_path, pdf_spec=Spec(omit=frozenset({"trade_date"})))
    r = results(report, "field.trade_date")[0]
    assert r.status == REVIEW and r.reason_code == "document_missing"
    assert report.status == REVIEW


def test_ambiguous_field_requires_review(tmp_path):
    report = check(tmp_path, pdf_spec=Spec(extra_strike_def="65.00"))
    r = results(report, "field.strike_pct")[0]
    assert r.status == REVIEW and r.reason_code == "document_ambiguous"


# ---------------------------------------------------------------- 審查標準與文件規則


def test_chairman_archaic_character(tmp_path):
    report = check(tmp_path, Spec(chairman="林晉輝"))
    assert problems(report) == {("standard.chairman", MISMATCH)}


def test_fixed_warning_altered_by_one_character(tmp_path):
    from synth import FIXED_WARNING

    altered = FIXED_WARNING.replace("並不保本", "並未保本")
    report = check(tmp_path, Spec(warnings=(FIXED_WARNING, altered, FIXED_WARNING)))
    assert problems(report) == {("standard.fixed_warning", MISMATCH)}


def test_fixed_warning_wrong_occurrence_count(tmp_path):
    from synth import FIXED_WARNING

    report = check(tmp_path, Spec(warnings=(FIXED_WARNING, FIXED_WARNING, "本商品之風險請參閱銷售說明書。")))
    r = results(report, "standard.fixed_warning")[0]
    assert r.status == MISMATCH and r.actual == 2


def test_risk_level(tmp_path):
    report = check(tmp_path, Spec(extra_text="本商品風險等級為【RR3】。"))
    assert problems(report) == {("standard.risk_level", MISMATCH)}


def test_forbidden_wording_outside_allowed_phrase(tmp_path):
    report = check(tmp_path, Spec(extra_text="本商品僅供受託投資之用。"))
    assert problems(report) == {("standard.forbidden_wording", MISMATCH)}
    r = results(report, "standard.forbidden_wording")[0]
    assert any("受託投資之用" in e.text for e in r.document_evidence)


def test_approval_date(tmp_path):
    report = check(tmp_path, Spec(approval_date=dt.date(2025, 12, 18)))
    assert problems(report) == {("standard.approval_date", MISMATCH)}


def test_chinese_name_without_non_principal_protected_suffix(tmp_path):
    spec = Spec()
    name = spec.expected_name_zh().replace("（不保本）", "")
    report = check(tmp_path, spec, pdf_spec=spec.with_(name_zh=name))
    assert problems(report) == {("standard.product_name", MISMATCH)}


def test_chinese_name_half_width_brackets_are_fine(tmp_path):
    spec = Spec()
    name = spec.expected_name_zh().replace("（無擔保及無保證機構）", "(無擔保及無保證機構)")
    report = check(tmp_path, spec, pdf_spec=spec.with_(name_zh=name))
    assert problems(report) == set()


def test_english_name_format(tmp_path):
    spec = Spec()
    name = spec.expected_name_en().replace("Memory ", "")
    report = check(tmp_path, spec, pdf_spec=spec.with_(name_en=name))
    assert problems(report) == {("standard.product_name", MISMATCH)}


def test_print_date_two_days_after_trade(tmp_path):
    report = check(tmp_path, Spec(print_date=dt.date(2030, 1, 9)))
    assert problems(report) == {("doc.print_date", MISMATCH)}


@pytest.mark.parametrize("offset", [0, 1])
def test_print_date_same_or_next_day(tmp_path, offset):
    report = check(tmp_path, Spec(print_date=dt.date(2030, 1, 7) + dt.timedelta(days=offset)))
    assert results(report, "doc.print_date")[0].status == PASS


def test_subscription_start_must_equal_trade_date(tmp_path):
    report = check(tmp_path, Spec(subscription_date=dt.date(2030, 1, 8)))
    assert problems(report) == {("doc.subscription_start_date", MISMATCH)}


# ---------------------------------------------------------------- 執行錯誤


def test_corrupt_pdf_is_error_not_crash(tmp_path):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"%PDF-1.7\nthis is not a pdf")
    inq = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    report = run_check(pdf, inq, REVIEW_STANDARD, ORDER_FORMAT)
    assert report.status == ERROR
    assert results(report, "input.term_sheet")[0].reason_code == "pdf_unreadable"


def test_encrypted_pdf_is_error(tmp_path):
    import fitz

    src = build_pdf(tmp_path / "plain.pdf", Spec())
    enc = tmp_path / "enc.pdf"
    fitz.open(src).save(enc, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    inq = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    report = run_check(enc, inq, REVIEW_STANDARD, ORDER_FORMAT)
    assert report.status == ERROR
    assert results(report, "input.term_sheet")[0].reason_code == "pdf_encrypted"


def test_same_input_gives_same_result_except_time(tmp_path):
    from fcn_checker.reporting import to_json

    spec = Spec()
    pdf = build_pdf(tmp_path / "ts.pdf", spec)
    inq = build_inquiry(tmp_path / "inquiry.xlsx", spec)
    a = to_json(run_check(pdf, inq, REVIEW_STANDARD, ORDER_FORMAT))
    b = to_json(run_check(pdf, inq, REVIEW_STANDARD, ORDER_FORMAT))
    a["metadata"].pop("generated_at")
    b["metadata"].pop("generated_at")
    assert a == b
