"""測試切點：批量核對入口 run_batch(說明書們, 參考條件表, 審查標準, 輸出資料夾) → 結果、報告與核對結果檔。

只用合成資料（tests/synth.py）；以 openpyxl 讀回產出的 Excel 觀察回填結果。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest
from openpyxl.formatting.rule import CellIsRule
from openpyxl.worksheet.datavalidation import DataValidation

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


def sheet_rows(path: Path, sheet: str = "回填後") -> dict[str, dict]:
    """TDCC Code → 該列（第 3 列表頭、第 4 列起資料）；預設讀核對結果檔的「回填後」。"""
    ws = openpyxl.load_workbook(path)[sheet]
    headers = [c.value for c in ws[3]]
    rows = (dict(zip(headers, r, strict=False)) for r in ws.iter_rows(min_row=4, values_only=True))
    return {d["TDCC Code"]: d for d in rows if d.get("TDCC Code")}


def row_of(path: Path, product_code: str) -> dict:
    rows = sheet_rows(path)
    assert product_code in rows, f"「回填後」找不到 {product_code}"
    return rows[product_code]


def error_rows(path: Path) -> list[dict]:
    ws = openpyxl.load_workbook(path)["錯誤清單"]
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


def header_cell(ws, header: str, row: int):
    return ws.cell(row, next(c.column for c in ws[3] if c.value == header))


def problems(item) -> list[tuple[str, str, str]]:
    return [(r.rule_id, r.field, r.message) for r in item.report.results if r.status in (MISMATCH, REVIEW, ERROR)]


def only(item, rule_id: str, field: str | None = None):
    out = [r for r in item.report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert len(out) == 1, f"{rule_id} {field or ''} 應恰好一筆：{out}"
    return out[0]


def slots(d: dict) -> list:
    return [v.date() if isinstance(v, dt.datetime) else v for v in (d[f"比價日_{i}"] for i in range(1, 13))]


# ---------------------------------------------------------------- 全部一致 → 回填


def test_consistent_daily_term_sheet_passes_and_back_fills_first_and_last_compare_dates(tmp_path):
    spec = Spec()  # D 型、天期 6、第 1 期期末日起可提前出場
    outcome, sheet = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    item = outcome.items[0]
    assert item.report.status == PASS, problems(item)
    assert item.filled
    assert outcome.output == tmp_path / "reports" / "FCN參考條件_核對結果_20300203-040506.xlsx"
    d = row_of(outcome.output, spec.product_code)
    assert d["ISIN Code"] == SYNTH_ISIN
    assert d["發行日"] == dt.datetime.combine(spec.issue_date, dt.time())
    assert sheet_rows(sheet, "樣本清單")[spec.product_code]["發行日"] is None, "原檔不動"
    first = schedule_rows(spec)[0]["valuation"]
    assert slots(d) == [first] + ["-"] * 4 + [spec.final_date] + ["-"] * 6, "D 型：Non-Call 那期與最後一期"


def test_daily_with_non_call_equal_to_tenor_fills_only_the_last_period(tmp_path):
    spec = Spec(guaranteed=6)  # 第 6 期期末日起才可提前出場 → Non-Call = 天期
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    assert outcome.items[0].report.status == PASS, problems(outcome.items[0])
    assert slots(row_of(outcome.output, spec.product_code)) == ["-"] * 5 + [spec.final_date] + ["-"] * 6


def test_latest_compare_date_must_equal_final_valuation_date(tmp_path):
    spec = Spec(ko_overrides={(6, "end"): "2030 年7 月9 日"})  # 最後一期期末日晚於最終評價日 2030-07-08
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])

    item = outcome.items[0]
    r = only(item, "backfill.compare_dates")
    assert (r.status, r.reason_code) == (REVIEW, "compare_dates_max_mismatch")
    assert "2030-07-09" in r.message and "2030-07-08" in r.message
    assert not item.filled
    assert spec.product_code not in sheet_rows(outcome.output)


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
    last = dt.datetime.combine(spec.final_date, dt.time())
    filled = {"ISIN Code": SYNTH_ISIN, "發行日": issue, "比價日_1": dt.datetime.combine(first, dt.time())}
    filled |= {f"比價日_{i}": "-" for i in range(2, 13)} | {"比價日_6": last}
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **filled)])

    item = outcome.items[0]
    assert item.report.status == PASS, problems(item)
    assert {d.action for d in item.report.backfill} == {"match"}
    d = row_of(outcome.output, spec.product_code)
    assert d["ISIN Code"] == SYNTH_ISIN and d["發行日"] == issue
    assert slots(d) == [first] + ["-"] * 4 + [spec.final_date] + ["-"] * 6


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
    assert spec.product_code not in sheet_rows(outcome.output), "沒通過就不回填，也不出現在「回填後」"


def test_dash_where_a_compare_date_is_expected_is_mismatch(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"比價日_1": "-"})])
    assert only(outcome.items[0], "backfill.compare_dates").status == MISMATCH


def test_wrong_isin_is_mismatch_and_keeps_original(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, **{"ISIN Code": "XS9999999999"})])
    r = only(outcome.items[0], "backfill.isin")
    assert (r.status, r.expected, r.actual) == (MISMATCH, "XS9999999999", SYNTH_ISIN)
    assert spec.product_code not in sheet_rows(outcome.output)


def test_wrong_issue_date_is_mismatch_and_keeps_original(tmp_path):
    spec = Spec()
    wrong = dt.datetime(2030, 1, 15)
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, 發行日=wrong)])
    item = outcome.items[0]
    r = only(item, "backfill.issue_date")
    assert (r.status, r.expected, r.actual) == (MISMATCH, wrong.date(), spec.issue_date)
    assert [d.action for d in item.report.backfill if d.column == "發行日"] == ["mismatch"]
    assert item.report.status == MISMATCH and not item.filled
    assert spec.product_code not in sheet_rows(outcome.output)


def test_issue_date_missing_from_term_sheet_requires_review_and_is_not_back_filled(tmp_path):
    spec = Spec(omit=frozenset({"issue_date"}))
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])
    item = outcome.items[0]
    assert only(item, "backfill.issue_date").status == REVIEW
    assert not [d for d in item.report.backfill if d.column == "發行日"]
    assert not item.filled
    assert spec.product_code not in sheet_rows(outcome.output)


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
    [error] = error_rows(outcome.output)
    assert error["TDCC Code"] == spec.product_code, "取不到封面商品代號時用檔名前 12 碼"
    assert "未支援上手" in error["錯訊"]


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
    assert list(sheet_rows(outcome.output)) == [other.product_code]
    assert row_of(outcome.output, other.product_code)["ISIN Code"] == SYNTH_ISIN
    errors = error_rows(outcome.output)
    assert [e["PDF 檔名"] for e in errors] == [old.name, new.name]
    assert new.name in errors[0]["錯訊"] and old.name in errors[1]["錯訊"]


def test_same_pdf_selected_twice_says_so_and_same_names_show_full_paths(tmp_path):
    spec = Spec()
    pdf = pdf_for(tmp_path, spec)
    outcome, _ = batch(tmp_path, [pdf, pdf], [reference_row(spec)])
    for item in outcome.items:
        assert f"{pdf.name}（同一個檔案重複選取）" in only(item, "batch.pairing").message

    (tmp_path / "v2").mkdir()
    copy = pdf_for(tmp_path / "v2", spec)
    outcome, _ = batch(tmp_path / "v2", [pdf, copy], [reference_row(spec)])
    assert str(copy) in only(outcome.items[0], "batch.pairing").message
    assert str(pdf) in only(outcome.items[1], "batch.pairing").message


def test_reference_row_of_another_issuer_requires_review(tmp_path):
    spec = Spec()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec, 發行機構="HSBC")])
    r = only(outcome.items[0], "batch.pairing")
    assert (r.status, r.reason_code, r.expected, r.actual) == (REVIEW, "reference_issuer_mismatch", "Barclays", "HSBC")
    assert not any(x.rule_id.startswith("field.") for x in outcome.items[0].report.results)


def test_rows_without_selected_pdf_are_left_out_of_the_result_file(tmp_path):
    spec, other = Spec(), Spec(product_code="029199990002")
    outcome, sheet = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec), reference_row(other)])
    assert outcome.items[0].filled
    assert list(sheet_rows(outcome.output)) == [spec.product_code]
    assert error_rows(outcome.output) == []


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


# ---------------------------------------------------------------- 核對結果檔


def test_result_file_keeps_only_passing_rows_in_sheet_order_with_the_original_layout(tmp_path):
    a, b, c, d = (Spec(product_code=f"02919999000{n}") for n in (1, 2, 3, 4))
    rows = [reference_row(a), reference_row(b, **{"K(%)": 71}), reference_row(c), reference_row(d)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows)
    wb = openpyxl.load_workbook(sheet)
    ws = wb["樣本清單"]
    ws["A1"] = "FCN 參考條件"
    ws.column_dimensions["E"].width = 17
    for r in range(4, 8):
        ws.row_dimensions[r].height = 20 + r
    wb.save(sheet)
    before = sheet.read_bytes()
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, s) for s in (c, b, a)], [], sheet=sheet)  # d 這批沒選到

    assert [i.filled for i in outcome.items] == [True, False, True]
    assert outcome.output == tmp_path / "reports" / "FCN參考條件_核對結果_20300203-040506.xlsx"
    assert sheet.read_bytes() == before, "原檔不動"
    assert list(tmp_path.glob("*_回填_*.xlsx")) == []
    out = openpyxl.load_workbook(outcome.output)
    assert out.sheetnames == ["回填後", "錯誤清單"], "原檔其他工作表不帶入"
    ws, original = out["回填後"], openpyxl.load_workbook(sheet)["樣本清單"]
    assert [[x.value for x in r] for r in ws.iter_rows(max_row=3)] == [
        [x.value for x in r] for r in original.iter_rows(max_row=3)
    ]
    assert ws.column_dimensions["E"].width == 17
    assert list(sheet_rows(outcome.output)) == [a.product_code, c.product_code], "只留通過的列，順序照原表"
    assert ws.max_row == 5
    assert [ws.row_dimensions[r].height for r in (4, 5)] == [24, 26], "列高跟著列走"
    for r in (4, 5):
        assert header_cell(ws, "發行日", r).number_format == DATE_FORMAT, "回填的日期沿用表上既有日期格式"
        assert header_cell(ws, "比價日_2", r).value == "-"


def test_error_list_has_one_row_per_failing_pdf_in_input_order(tmp_path):
    ok, bad, missing = Spec(), Spec(product_code="029199990002"), Spec(product_code="029199990003")
    unsupported = Spec(product_code="999199990001")
    unreadable = tmp_path / "029199990009_TS.pdf"
    unreadable.write_bytes(b"not a pdf")
    nameless = tmp_path / "說明書.pdf"
    nameless.write_bytes(b"not a pdf")
    pdfs = [pdf_for(tmp_path, s) for s in (bad, ok, missing, unsupported)] + [unreadable, nameless]
    rows = [reference_row(ok), reference_row(bad, **{"K(%)": 71, "KO(%)": 101})]
    outcome, _ = batch(tmp_path, pdfs, rows)

    errors = error_rows(outcome.output)
    assert [(e["TDCC Code"], e["PDF 檔名"]) for e in errors] == [
        (bad.product_code, pdfs[0].name),
        (missing.product_code, pdfs[2].name),
        (unsupported.product_code, pdfs[3].name),
        ("029199990009", unreadable.name),
        (None, nameless.name),
    ], "每份沒通過的 PDF 一列，依輸入順序；TDCC Code 取封面商品代號、檔名前 12 碼，都取不到時留白"
    lines = errors[0]["錯訊"].split("\n")
    assert len(lines) == 2 and "K(%)對不起來：參考條件表 71.00／說明書 70.00" in lines, "多條錯訊在同一格，以換行分隔"
    assert all(e["錯訊"] for e in errors)
    assert openpyxl.load_workbook(outcome.output)["錯誤清單"]["C2"].alignment.wrap_text
    assert list(sheet_rows(outcome.output)) == [ok.product_code]


def edit_sheet(sheet: Path, change) -> None:
    wb = openpyxl.load_workbook(sheet)
    change(wb)
    wb.save(sheet)


def test_filter_range_shrinks_with_the_kept_rows_and_the_file_opens_on_the_filled_sheet(tmp_path):
    a, b = Spec(), Spec(product_code="029199990002")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(a), reference_row(b, **{"K(%)": 71})])

    def change(wb):
        wb["樣本清單"].auto_filter.ref = "A3:F5"
        wb.active = wb["詢價表格"]

    edit_sheet(sheet, change)
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, s) for s in (a, b)], [], sheet=sheet)
    out = openpyxl.load_workbook(outcome.output)
    assert out["回填後"].auto_filter.ref == "A3:F4"
    assert out.active.title == "回填後"


@pytest.mark.parametrize(
    "feature,change",
    [
        ("合併儲存格", lambda ws: ws.merge_cells("A4:B4")),
        ("格式化條件", lambda ws: ws.conditional_formatting.add("H4:H9", CellIsRule(operator="equal", formula=["0"]))),
        ("資料驗證", lambda ws: ws.add_data_validation(DataValidation(type="list", formula1='"Y,N"', sqref="T4:T9"))),
        ("公式", lambda ws: ws.cell(1, 1, "=COUNTA(E4:E9)")),
    ],
)
def test_layout_that_cannot_be_trimmed_safely_writes_no_result_file(tmp_path, feature, change):
    spec = Spec()
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    edit_sheet(sheet, lambda wb: change(wb["樣本清單"]))
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [], sheet=sheet)
    assert outcome.output is None and outcome.status == ERROR and not outcome.items[0].filled
    e = outcome.errors[0]
    assert (e.rule_id, e.reason_code) == ("output.result_file", "result_layout_unsupported")
    assert feature in e.message
    assert list((tmp_path / "reports").glob("*_核對結果_*.xlsx")) == []


def test_existing_result_file_is_never_overwritten(tmp_path):
    spec = Spec()
    taken = tmp_path / "reports" / "FCN參考條件_核對結果_20300203-040506.xlsx"
    taken.parent.mkdir()
    taken.write_bytes(b"keep")
    outcome, _ = batch(tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)])
    assert taken.read_bytes() == b"keep"
    assert outcome.output is None and outcome.status == ERROR
    assert (outcome.errors[0].rule_id, outcome.errors[0].reason_code) == ("output.result_file", "output_exists")
    assert not outcome.items[0].filled


def test_batch_error_writes_no_result_file(tmp_path):
    spec = Spec()
    outcome, _ = batch(
        tmp_path, [pdf_for(tmp_path, spec)], [reference_row(spec)], reference_format=tmp_path / "missing.toml"
    )
    assert outcome.status == ERROR and outcome.items == [] and outcome.output is None
    assert not (tmp_path / "reports").exists()


def test_unreadable_pdf_does_not_stop_the_batch(tmp_path):
    spec = Spec()
    broken = tmp_path / "029199990009_TS.pdf"
    broken.write_bytes(b"not a pdf")
    outcome, _ = batch(tmp_path, [broken, pdf_for(tmp_path, spec)], [reference_row(spec)])
    assert outcome.items[0].report.status == ERROR
    assert outcome.items[1].report.status == PASS and outcome.items[1].filled
    assert outcome.status == ERROR
    assert [e["PDF 檔名"] for e in error_rows(outcome.output)] == [broken.name]


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
    assert outcome.output is not None, "報告寫不進去不影響核對結果檔"


def test_reference_sheet_changed_after_check_writes_nothing(tmp_path):
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
    assert not (tmp_path / "reports").exists(), "核對結果檔與報告都不寫"
