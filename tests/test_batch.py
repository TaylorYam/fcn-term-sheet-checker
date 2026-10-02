"""測試切點：批量核對入口 run_batch(說明書們, 參考條件表, 審查標準, 報告資料夾) → 結果與回填後的新檔。

只用合成資料（tests/synth.py）；以 openpyxl 讀回產出的 Excel 觀察回填結果。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl

from fcn_checker.batch import check_batch, run_batch, save_batch
from fcn_checker.issuers import BARC
from fcn_checker.schema import CheckStatus, DetectionResult
from harness import ISSUER_PREFIXES, REVIEW_STANDARD
from reference_synth import DATE_FORMAT, REFERENCE_FORMAT, REFERENCE_HEADERS, build_reference_sheet
from synth import SYNTH_ISIN, Spec, barc_adapter, build_pdf, reference_row, schedule_rows

PASS, MISMATCH, REVIEW, ERROR = (
    CheckStatus.PASS,
    CheckStatus.MISMATCH,
    CheckStatus.REVIEW_REQUIRED,
    CheckStatus.ERROR,
)
NOW = dt.datetime(2030, 2, 3, 4, 5, 6)


def pdf_for(tmp_path: Path, spec: Spec, name: str | None = None) -> Path:
    return build_pdf(tmp_path / (name or f"{spec.product_code}_TS.pdf"), spec)


def batch(tmp_path: Path, pdfs: list[Path], rows: list[dict], **kw):
    sheet = kw.pop("sheet", None) or build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows)
    kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES, "now": NOW, **kw}
    outcome = run_batch(pdfs, sheet, REVIEW_STANDARD, tmp_path / "reports", **kw)
    return outcome, sheet


def row_of(path: Path, product_code: str) -> dict:
    ws = openpyxl.load_workbook(path)["樣本清單"]
    headers = [c.value for c in ws[3]]
    for r in ws.iter_rows(min_row=4, values_only=True):
        d = dict(zip(headers, r, strict=False))
        if d.get("TDCC Code") == product_code:
            return d
    raise AssertionError(f"新檔找不到 {product_code}")


def result_rows(path: Path) -> list[dict]:
    ws = openpyxl.load_workbook(path)["核對結果"]
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


def problems(item) -> list[tuple[str, str, str]]:
    return [(r.rule_id, r.field, r.message) for r in item.report.results if r.status in (MISMATCH, REVIEW, ERROR)]


def only(item, rule_id: str, field: str | None = None):
    out = [r for r in item.report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert len(out) == 1, f"{rule_id} {field or ''} 應恰好一筆：{out}"
    return out[0]


def slots(d: dict) -> list:
    return [v.date() if isinstance(v, dt.datetime) else v for v in (d[f"比價日_{i}"] for i in range(1, 13))]


# ---------------------------------------------------------------- 全部一致 → 回填


def test_consistent_daily_term_sheet_passes_and_back_fills_isin_issue_date_and_first_compare_date(tmp_path):
    spec = Spec()  # D 型、天期 6、第 1 期期末日起可提前出場
    outcome, sheet = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    item = outcome.items[0]
    assert item.report.status == PASS, problems(item)
    assert item.filled
    assert outcome.output == tmp_path / "FCN參考條件_回填_20300203-040506.xlsx"
    d = row_of(outcome.output, spec.product_code)
    assert d["ISIN Code"] == SYNTH_ISIN
    assert d["發行日"] == dt.datetime.combine(spec.issue_date, dt.time())
    assert row_of(sheet, spec.product_code)["發行日"] is None, "原檔不動"
    first = schedule_rows(spec)[0]["valuation"]
    assert slots(d) == [first] + ["-"] * 11


def test_period_end_back_fills_every_compare_date_from_first_callable_period(tmp_path):
    spec = Spec(ko_obs="P", memory=False, guaranteed=2)  # 前 2 期不可提前出場 → Non-Call = 3
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    item = outcome.items[0]
    assert item.report.status == PASS, problems(item)
    valuation = [r["valuation"] for r in schedule_rows(spec)]
    assert slots(row_of(outcome.output, spec.product_code)) == ["-", "-", *valuation[2:6]] + ["-"] * 6


def test_period_end_with_non_call_equal_to_tenor_fills_only_the_last_period(tmp_path):
    spec = Spec(ko_obs="P", memory=False, guaranteed=5)  # 只有第 6 期可提前出場
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    assert outcome.items[0].report.status == PASS, problems(outcome.items[0])
    last = schedule_rows(spec)[-1]["valuation"]
    assert slots(row_of(outcome.output, spec.product_code)) == ["-"] * 5 + [last] + ["-"] * 6


def test_period_end_memory_uses_autocall_valuation_dates(tmp_path):
    spec = Spec(ko_obs="P", memory=True)  # 自動提前出場評價日表，每期都可提前出場
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    assert outcome.items[0].report.status == PASS, problems(outcome.items[0])
    valuation = [r["valuation"] for r in schedule_rows(spec)]
    assert slots(row_of(outcome.output, spec.product_code)) == valuation + ["-"] * 6


def test_already_filled_matching_values_pass_and_stay_unchanged(tmp_path):
    spec = Spec()
    first = schedule_rows(spec)[0]["valuation"]
    issue = dt.datetime.combine(spec.issue_date, dt.time())
    filled = {"ISIN Code": SYNTH_ISIN, "發行日": issue, "比價日_1": dt.datetime.combine(first, dt.time())}
    filled |= {f"比價日_{i}": "-" for i in range(2, 13)}
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **filled)])

    item = outcome.items[0]
    assert item.report.status == PASS, problems(item)
    assert {d.action for d in item.report.backfill} == {"match"}
    d = row_of(outcome.output, spec.product_code)
    assert d["ISIN Code"] == SYNTH_ISIN and d["發行日"] == issue and slots(d) == [first] + ["-"] * 11


def test_wrong_existing_compare_date_is_mismatch_and_nothing_is_back_filled(tmp_path):
    spec = Spec()
    wrong = dt.datetime(2030, 1, 1)
    outcome, _ = batch(
        tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"比價日_1": wrong, "比價日_6": "-"})]
    )

    item = outcome.items[0]
    r = only(item, "backfill.compare_dates")
    assert r.status == MISMATCH
    assert item.report.status == MISMATCH and not item.filled
    d = row_of(outcome.output, spec.product_code)
    assert d["比價日_1"] == wrong, "不一致的格子保留原值"
    assert d["ISIN Code"] is None and d["比價日_2"] is None, "沒通過就不回填任何格子"


def test_dash_where_a_compare_date_is_expected_is_mismatch(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"比價日_1": "-"})])
    assert only(outcome.items[0], "backfill.compare_dates").status == MISMATCH


def test_wrong_isin_is_mismatch_and_keeps_original(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"ISIN Code": "XS9999999999"})])
    r = only(outcome.items[0], "backfill.isin")
    assert (r.status, r.expected, r.actual) == (MISMATCH, "XS9999999999", SYNTH_ISIN)
    assert row_of(outcome.output, spec.product_code)["ISIN Code"] == "XS9999999999"


def test_wrong_issue_date_is_mismatch_and_keeps_original(tmp_path):
    spec = Spec()
    wrong = dt.datetime(2030, 1, 15)
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, 發行日=wrong)])
    item = outcome.items[0]
    r = only(item, "backfill.issue_date")
    assert (r.status, r.expected, r.actual) == (MISMATCH, wrong.date(), spec.issue_date)
    assert [d.action for d in item.report.backfill if d.column == "發行日"] == ["mismatch"]
    assert item.report.status == MISMATCH and not item.filled
    assert row_of(outcome.output, spec.product_code)["發行日"] == wrong


def test_issue_date_missing_from_term_sheet_requires_review_and_is_not_back_filled(tmp_path):
    spec = Spec(omit=frozenset({"issue_date"}))
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])
    item = outcome.items[0]
    assert only(item, "backfill.issue_date").status == REVIEW
    assert not [d for d in item.report.backfill if d.column == "發行日"]
    assert not item.filled
    assert row_of(outcome.output, spec.product_code)["發行日"] is None


def test_non_call_must_be_the_first_callable_period(tmp_path):
    spec = Spec()  # 第 1 期可提前出場
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"Non-Call(月)": 0})])
    r = only(outcome.items[0], "field.first_callable_period")
    assert (r.status, r.expected, r.actual) == (MISMATCH, 0, 1)
    assert not outcome.items[0].filled


def test_daily_observation_from_first_day_requires_review_without_back_fill(tmp_path):
    spec = Spec(guaranteed=0)  # 第 1 期期始日就有日期（S08、S10 型），比價日填法未確認
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"Non-Call(月)": 1})])

    item = outcome.items[0]
    assert only(item, "backfill.compare_dates").status == REVIEW
    assert only(item, "field.first_callable_period").status == REVIEW
    assert item.report.status == REVIEW and not item.filled


def test_more_than_twelve_periods_requires_review(tmp_path):
    spec = Spec(tenor=13, final_date=dt.date(2031, 2, 10), maturity_date=dt.date(2031, 2, 13))
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])
    r = only(outcome.items[0], "backfill.compare_dates")
    assert (r.status, r.reason_code) == (REVIEW, "compare_dates_unmapped")
    assert not outcome.items[0].filled


# ---------------------------------------------------------------- 上手編號與配對


def prefixes_file(tmp_path: Path, extra: str) -> Path:
    p = tmp_path / "prefixes.toml"
    p.write_text(ISSUER_PREFIXES.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return p


def test_prefix_not_in_table_is_unsupported_issuer(tmp_path):
    spec = Spec(product_code="999199990001")
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    item = outcome.items[0]
    r = only(item, "batch.issuer_prefix")
    assert (r.status, r.reason_code) == (REVIEW, "issuer_unsupported")
    assert item.unsupported and item.report.status == REVIEW
    assert not any(x.rule_id.startswith("field.") for x in item.report.results)
    result = result_rows(outcome.output)[0]
    assert result["整體狀態"] == "未支援上手" and result["已回填"] == "否"


def test_issuer_in_prefix_table_without_template_is_unsupported(tmp_path):
    spec = Spec(product_code="325199990001")  # 325 = HSBC：對照表有，本測試 registry 刻意不註冊 HSBC
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, 發行機構="HSBC")], registry=(BARC,))
    item = outcome.items[0]
    assert item.unsupported and item.issuer == "HSBC"
    assert "HSBC" in only(item, "batch.issuer_prefix").message


def test_term_sheet_content_of_another_issuer_requires_review(tmp_path):
    other = barc_adapter(code="FAKE", template_id="fake-zh-pd", detect=lambda lines: DetectionResult(False))
    spec = Spec(product_code="777199990001")
    outcome, _ = batch(
        tmp_path,
        [pdf_for(tmp_path, spec)],
        [reference_row(spec)],
        issuer_prefixes=prefixes_file(tmp_path, '"777" = "FAKE"\n'),
        registry=(BARC, other),
    )
    r = only(outcome.items[0], "batch.issuer_prefix")
    assert (r.status, r.reason_code, r.actual) == (REVIEW, "issuer_prefix_mismatch", "BARC")


def test_file_name_only_needs_the_same_issuer_prefix_as_the_product_code(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec, name="029 任意檔名.pdf")], [reference_row(spec)])
    assert outcome.items[0].report.status == PASS, problems(outcome.items[0])


def test_product_code_prefix_differs_from_file_name_requires_review(tmp_path):
    spec = Spec(product_code="028199990001")  # 檔名 029、說明書商品代號 028
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec, name="029199990001_TS.pdf")], [reference_row(spec)])
    r = only(outcome.items[0], "batch.issuer_prefix")
    assert (r.status, r.reason_code, r.actual) == (REVIEW, "issuer_prefix_mismatch", "028199990001")


def test_missing_or_duplicate_reference_row_requires_review(tmp_path):
    spec = Spec()
    pdf = pdf_for(tmp_path, spec)
    outcome, _ = batch(tmp_path, [pdf], [reference_row(Spec(product_code="029199990002"))])
    assert only(outcome.items[0], "batch.pairing").reason_code == "reference_row_missing"

    (tmp_path / "dup").mkdir()
    outcome, _ = batch(tmp_path / "dup", [pdf], [reference_row(spec), reference_row(spec)])
    r = only(outcome.items[0], "batch.pairing")
    assert (r.status, r.reason_code) == (REVIEW, "reference_row_duplicate")
    assert len(r.order_source) == 2


def test_pdfs_sharing_one_reference_row_all_require_review_and_others_still_back_fill(tmp_path):
    spec, other = Spec(), Spec(product_code="029199990002")
    old = pdf_for(tmp_path, spec, name=f"{spec.product_code}_舊版.pdf")
    new = pdf_for(tmp_path, spec, name=f"{spec.product_code}_新版.pdf")
    ok = pdf_for(tmp_path, other)
    outcome, _ = batch(tmp_path, [old, ok, new], [reference_row(spec), reference_row(other)])

    first, second, third = outcome.items
    for item, another in ((first, new), (third, old)):
        r = only(item, "batch.pairing")
        assert (r.status, r.reason_code) == (REVIEW, "reference_row_shared")
        assert "同一批有多份說明書對到同一個 TDCC Code" in r.message and another.name in r.message
        assert item.term_sheet.name not in r.message
        assert item.report.status == REVIEW and not item.filled
        assert not any(x.rule_id.startswith(("field.", "backfill.")) for x in item.report.results)
    assert second.report.status == PASS and second.filled
    assert row_of(outcome.output, spec.product_code)["ISIN Code"] is None
    assert row_of(outcome.output, other.product_code)["ISIN Code"] == SYNTH_ISIN
    rows = result_rows(outcome.output)
    assert new.name in rows[0]["問題摘要"] and old.name in rows[2]["問題摘要"]


def test_reference_row_of_another_issuer_requires_review(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, 發行機構="HSBC")])
    r = only(outcome.items[0], "batch.pairing")
    assert (r.status, r.reason_code, r.expected, r.actual) == (REVIEW, "reference_issuer_mismatch", "Barclays", "HSBC")
    assert not any(x.rule_id.startswith("field.") for x in outcome.items[0].report.results)


def test_rows_without_selected_pdf_are_untouched(tmp_path):
    spec, other = Spec(), Spec(product_code="029199990002")
    outcome, sheet = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec), reference_row(other)])
    assert outcome.items[0].filled
    d = row_of(outcome.output, other.product_code)
    assert d["ISIN Code"] is None and slots(d) == [None] * 12


# ---------------------------------------------------------------- 表上事先填好的欄位


def test_knock_in_term_sheet_checks_ki_columns_and_prices(tmp_path):
    spec = Spec(ki="AM")
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])
    item = outcome.items[0]
    assert item.report.status == PASS, problems(item)
    prices = [r for r in item.report.results if r.rule_id == "field.underlying_prices"]
    assert len(prices) == 12 and {r.status for r in prices} == {PASS}


def test_prefilled_fields_that_differ_are_mismatches(tmp_path):
    spec = Spec()
    wrong = {
        "KO(Freq)": "P",
        "KO(memo)": "N",
        "KI(%)": 60,
        "單位面額": 5000,
        "UL_2_進場價": 87.25,
        "UL_1_執行價": 86.4151,  # 86.41500 → 4 位小數後仍不同
        "Coupon p.a. (%)": 12.5,
    }
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **wrong)])
    item = outcome.items[0]
    mismatched = {(r.rule_id, r.field) for r in item.report.results if r.status == MISMATCH}
    assert {
        ("field.ko_observation", "ko_observation"),
        ("field.ko_memory", "ko_memory"),
        ("field.ki_pct", "ki_pct"),
        ("field.denomination", "denomination"),
        ("field.underlying_prices", "ZQH UW 進場價"),
        ("field.underlying_prices", "ZZA UN 執行價"),
        ("field.coupon_pa_pct", "coupon_pa_pct"),
    } <= mismatched
    assert not item.filled


def test_prices_with_excel_float_tails_round_to_four_decimals(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"UL_1_執行價": 86.41499999999999})])
    assert outcome.items[0].report.status == PASS, problems(outcome.items[0])


def test_non_default_denomination_still_requires_review_by_standard(tmp_path):
    spec = Spec(denomination=50000)
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])
    item = outcome.items[0]
    assert only(item, "field.denomination").status == PASS
    assert only(item, "doc.denomination").status == REVIEW
    assert not item.filled


def test_unknown_reference_column_requires_review(tmp_path):
    spec = Spec()
    sheet = build_reference_sheet(
        tmp_path / "FCN參考條件.xlsx", [reference_row(spec)], headers=[*REFERENCE_HEADERS, "新欄位"]
    )
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [], sheet=sheet)
    r = only(outcome.items[0], "order.unknown_column")
    assert r.status == REVIEW and "參考條件表" in r.message and "新欄位" in r.message


# ---------------------------------------------------------------- 輸出新檔


def test_original_file_is_untouched_and_other_sheets_and_formats_are_kept(tmp_path):
    spec = Spec()
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    before = sheet.read_bytes()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [], sheet=sheet)

    assert outcome.items[0].filled
    assert sheet.read_bytes() == before
    out = openpyxl.load_workbook(outcome.output)
    assert out.sheetnames == ["樣本清單", "詢價表格", "核對結果"]
    assert out["詢價表格"]["B3"].value == "其他工作表（回填時不得改動）"
    ws = out["樣本清單"]
    assert ws["L4"].number_format == DATE_FORMAT, "回填的日期沿用表上既有日期格式"
    assert ws["M4"].value == "-" and ws["M4"].number_format == "General"


def test_result_sheet_lists_every_pdf(tmp_path):
    ok, bad = Spec(), Spec(product_code="029199990002")
    pdfs = [pdf_for(tmp_path, ok), pdf_for(tmp_path, bad)]
    outcome, _ = batch(tmp_path, pdfs, [reference_row(ok), reference_row(bad, **{"K(%)": 71})])

    rows = result_rows(outcome.output)
    assert [r["PDF 檔名"] for r in rows] == [p.name for p in pdfs]
    assert rows[0] | {"問題摘要": None} == {
        "PDF 檔名": pdfs[0].name,
        "商品代號": ok.product_code,
        "上手": "BARC",
        "整體狀態": "PASS（通過）",
        "問題數": 0,
        "問題摘要": None,
        "已回填": "是",
        "報告檔名": f"{pdfs[0].stem}_20300203-040506.check.md",
    }
    assert rows[1]["整體狀態"] == "MISMATCH（不一致）" and rows[1]["已回填"] == "否"
    assert "field.strike_pct" in rows[1]["問題摘要"]
    assert (tmp_path / "reports" / f"{pdfs[1].stem}_20300203-040506.check.json").is_file()


def test_existing_output_file_is_never_overwritten(tmp_path):
    spec = Spec()
    taken = tmp_path / "FCN參考條件_回填_20300203-040506.xlsx"
    taken.write_bytes(b"keep")
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])
    assert taken.read_bytes() == b"keep"
    assert outcome.output is None and outcome.status == ERROR
    assert outcome.errors[0].rule_id == "output.reference_sheet"
    assert not outcome.items[0].filled


def test_reference_sheet_with_result_sheet_is_rejected_before_checking(tmp_path):
    spec = Spec()
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)], extra_sheets=("核對結果",))
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [], sheet=sheet)
    assert outcome.status == ERROR and outcome.items == [] and outcome.output is None
    assert outcome.errors[0].reason_code == "reference_result_sheet_exists"
    assert list(tmp_path.glob("*_回填_*.xlsx")) == []


def test_unreadable_pdf_does_not_stop_the_batch(tmp_path):
    spec = Spec()
    broken = tmp_path / "029199990009_TS.pdf"
    broken.write_bytes(b"not a pdf")
    outcome, _ = batch(tmp_path, [broken, pdf_for(tmp_path, spec)], [reference_row(spec)])
    assert outcome.items[0].report.status == ERROR
    assert outcome.items[1].report.status == PASS and outcome.items[1].filled
    assert outcome.status == ERROR
    assert [r["整體狀態"] for r in result_rows(outcome.output)] == ["ERROR（執行錯誤）", "PASS（通過）"]


def test_unexpected_error_in_one_pdf_is_reported_and_the_batch_continues(tmp_path):
    def broken_read(lines):
        raise IndexError("synthetic parser failure")

    broken = barc_adapter(read=broken_read)
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)], registry=(broken,))
    item = outcome.items[0]
    assert item.report.status == ERROR
    assert only(item, "batch.unexpected").message == "IndexError: synthetic parser failure"
    assert (
        outcome.output is not None
        and (tmp_path / "reports" / f"{spec.product_code}_TS_20300203-040506.check.md").is_file()
    )


def test_check_writes_nothing_until_saved(tmp_path):
    spec = Spec()
    pdf = pdf_for(tmp_path, spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    before = set(tmp_path.rglob("*"))
    kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES}
    outcome = check_batch([pdf], sheet, REVIEW_STANDARD, **kw)
    assert outcome.status == PASS and outcome.output is None and not outcome.items[0].filled
    assert set(tmp_path.rglob("*")) == before

    save_batch(outcome, tmp_path / "reports", now=NOW)
    assert outcome.items[0].filled and outcome.output.is_file()
    assert {p.name for p in outcome.items[0].report_paths} == {
        f"{pdf.stem}_20300203-040506.check.json",
        f"{pdf.stem}_20300203-040506.check.md",
    }


def test_existing_reports_are_never_overwritten(tmp_path):
    spec = Spec()
    pdf = pdf_for(tmp_path, spec)
    taken = tmp_path / "reports" / f"{pdf.stem}_20300203-040506.check.json"
    taken.parent.mkdir()
    taken.write_text("keep", encoding="utf-8")
    outcome, _ = batch(tmp_path, [pdf], [reference_row(spec)])
    item = outcome.items[0]
    assert taken.read_text(encoding="utf-8") == "keep"
    assert item.save_error and item.report_paths == ()
    assert outcome.output is not None, "報告寫不進去不影響回填新檔"


def test_reference_sheet_changed_after_check_is_not_back_filled(tmp_path):
    spec = Spec()
    pdf = pdf_for(tmp_path, spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES}
    outcome = check_batch([pdf], sheet, REVIEW_STANDARD, **kw)
    build_reference_sheet(sheet, [reference_row(spec, **{"K(%)": 71})])  # 核對後被改過
    save_batch(outcome, tmp_path / "reports", now=NOW)
    assert outcome.output is None and outcome.status == ERROR
    assert outcome.errors[0].reason_code == "reference_changed"
    assert not outcome.items[0].filled
