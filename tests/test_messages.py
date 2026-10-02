"""錯訊（Issue #71）：作業人員看到的每條問題都是看得懂的中文，只寫哪裡對不起來、兩邊各是多少，不含 rule_id／reason_code。

BARC 與 HSBC 都經公開批量入口 check_batch 產生結果，再用同一個錯訊產生器 `problem_message` 取錯訊。
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

import fitz
import pytest

import hsbc_synth
from fcn_checker.batch import check_batch
from fcn_checker.messages import problem_message
from fcn_checker.panel import result_detail
from fcn_checker.schema import CheckResult, CheckStatus
from harness import ISSUER_PREFIXES, REVIEW_STANDARD, check_rows
from reference_synth import REFERENCE_FORMAT
from synth import DEFAULT_ULS, Spec, build_pdf, check, reference_row

# 程式代碼：小寫英文以 . 或 _ 串接（例：doc.scenario_calculations、s1.profit.17、value_mismatch）
CODE = re.compile(r"(?<![A-Za-z0-9])[a-z][a-z0-9]*(?:[._][a-z0-9]+)+")


def issues(report) -> list[CheckResult]:
    return [r for r in report.results if r.status.is_problem]


def assert_plain_chinese(report) -> None:
    assert issues(report), "應該有問題項目"
    for r in issues(report):
        msg = problem_message(r)
        assert re.search(r"[一-鿿]", msg), msg
        assert r.rule_id not in msg, msg
        assert not r.reason_code or r.reason_code not in msg, msg
        assert not CODE.search(msg), msg


def hsbc_check(tmp_path, spec=None, overrides=None):
    s = spec or hsbc_synth.Spec()
    pdf = hsbc_synth.build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    sheet = hsbc_synth.build_inquiry(tmp_path / "order.xlsx", s, overrides)
    outcome = check_batch(
        [pdf],
        sheet,
        REVIEW_STANDARD,
        reference_format=hsbc_synth.ORDER_FORMAT,
        issuer_prefixes=ISSUER_PREFIXES,
    )
    return outcome.items[0].report


# ---------------------------------------------------------------- 參考條件表欄位：欄名＋雙方值


def test_barc_initial_price_difference_names_the_column_and_both_values(tmp_path):
    report = check(tmp_path, overrides={"UL_2_進場價": 123.45})

    [r] = [r for r in issues(report) if r.rule_id == "field.underlying_prices"]
    assert r.status == CheckStatus.MISMATCH
    assert problem_message(r) == f"UL_2 進場價對不起來：參考條件表 123.4500／說明書 {DEFAULT_ULS[1].initial}"


def test_hsbc_initial_price_difference_names_the_column_and_both_values(tmp_path):
    report = hsbc_check(tmp_path, overrides={"underlying_1_initial_price": 101})

    [r] = [r for r in issues(report) if r.rule_id == "field.underlying_prices"]
    assert r.status == CheckStatus.MISMATCH
    assert problem_message(r) == "UL_1 進場價對不起來：參考條件表 101.0000／說明書 100.0000"


@pytest.mark.parametrize(
    "column,value,expected",
    [
        ("K(%)", 71, "K(%)對不起來：參考條件表 71.00／說明書 70.00"),
        ("KO(%)", 101, "KO(%)對不起來：參考條件表 101.00／說明書 100.00"),
        ("Coupon p.a. (%)", 13, "Coupon p.a. (%)對不起來：參考條件表 13.00／說明書 12.00"),
        ("天期(月)", 7, "天期(月)對不起來：參考條件表 7／說明書 6"),
        ("交易日", dt.datetime(2030, 1, 8), "交易日對不起來：參考條件表 2030-01-08／說明書 2030-01-07"),
        ("承作幣別", "JPY", "承作幣別對不起來：參考條件表 JPY／說明書 USD"),
        ("UL_1", "ZZZ UN", None),
        ("KO(Freq)", "P", None),
        ("Non-Call(月)", 3, "Non-Call(月)對不起來：參考條件表 3／說明書 1"),
        ("UL_1_KO價", 1, None),
        ("KI(%)", 60, "KI(%)對不起來：參考條件表 60／說明書 無 KI"),
    ],
)
def test_barc_reference_fields_use_column_name_and_both_values(tmp_path, column, value, expected):
    report = check(tmp_path, overrides={column: value})

    sheet_side = [r for r in issues(report) if r.rule_id.startswith("field.")]
    assert len(sheet_side) == 1, [(r.rule_id, r.field) for r in sheet_side]
    msg = problem_message(sheet_side[0])
    label = {"UL_1": "標的", "UL_1_KO價": "UL_1 KO價"}.get(column, column)
    assert re.fullmatch(re.escape(label) + r"對不起來：參考條件表 .+／說明書 .+", msg), msg
    if expected:
        assert msg == expected


def test_hsbc_backfill_column_difference_names_the_column(tmp_path):
    report = hsbc_check(tmp_path, overrides={"isin": "XS1999900002"})

    [r] = [r for r in issues(report) if r.rule_id == "backfill.isin"]
    assert problem_message(r) == "ISIN Code對不起來：參考條件表 XS1999900002／說明書 XS1999900001"


def test_hsbc_compare_date_difference_names_each_cell(tmp_path):
    report = hsbc_check(tmp_path, overrides={"autocall_date_2": dt.date(2030, 3, 9)})

    [r] = [r for r in issues(report) if r.rule_id == "backfill.compare_dates"]
    assert problem_message(r) == "比價日_2對不起來：參考條件表 2030-03-09／說明書 2030-03-07"


def test_latest_compare_date_check_does_not_label_document_dates_as_sheet_values(tmp_path):
    spec = Spec(ko_overrides={(6, "end"): "2030 年7 月9 日"})  # 最後一期期末日晚於最終評價日 2030-07-08
    report = check(tmp_path, spec)

    [r] = [r for r in issues(report) if r.rule_id == "backfill.compare_dates"]
    assert problem_message(r) == "比價日：說明書最晚的比價日 2030-07-09 不等於最終比價日 2030-07-08，不回填"


def test_pdfs_sharing_one_reference_row_get_a_plain_chinese_message(tmp_path):
    spec = Spec()
    pdfs = [build_pdf(tmp_path / f"{spec.product_code}_{v}.pdf", spec) for v in ("舊版", "新版")]
    sheet = check_rows(tmp_path, pdfs[0], [reference_row(spec)])  # 建好參考條件表
    assert sheet.status == CheckStatus.PASS
    outcome = check_batch(
        pdfs,
        tmp_path / "FCN參考條件.xlsx",
        REVIEW_STANDARD,
        reference_format=REFERENCE_FORMAT,
        issuer_prefixes=ISSUER_PREFIXES,
    )

    for item in outcome.items:
        assert_plain_chinese(item.report)


def test_missing_sheet_value_says_which_column(tmp_path):
    report = check(tmp_path, overrides={"K(%)": None})

    [r] = [r for r in issues(report) if r.rule_id == "field.strike_pct"]
    assert r.status == CheckStatus.REVIEW_REQUIRED
    assert problem_message(r).startswith("K(%)：")
    assert "參考條件表" in problem_message(r)


def test_derived_rule_names_the_blank_sheet_column(tmp_path):
    report = check(tmp_path, overrides={"天期(月)": None})

    [r] = [r for r in issues(report) if r.rule_id == "derive.monthly_coupon"]
    assert problem_message(r) == "天期(月)：參考條件表沒有此欄位或值為空白"


# ---------------------------------------------------------------- 審查標準與說明書內部一致性


def test_barc_review_standard_and_internal_problems_are_plain_chinese(tmp_path):
    spec = Spec(
        chairman="林晉輝",
        rr="RR5",
        price_overrides={(1, "strike"): "99.9999"},
        mention_overrides={"§9": "0.9999%"},
    )
    report = check(tmp_path, spec)

    rules = {r.rule_id for r in issues(report)}
    assert {"standard.chairman", "standard.risk_level", "derive.prices"} <= rules
    assert_plain_chinese(report)


def test_review_standard_difference_shows_both_values(tmp_path):
    report = check(tmp_path, Spec(rr="RR5"))

    [r] = [r for r in issues(report) if r.rule_id == "standard.risk_level"]
    msg = problem_message(r)
    assert "審查標準 RR4" in msg and "說明書 RR5" in msg


@pytest.mark.parametrize(
    "old,new",
    [
        ("固定配息金額=美元10,000×1.0000%=美元100.00", "固定配息金額=美元10,000×1.0000%=美元101.00"),
        ("6個計息期間配息金額共為美元600.00", "5個計息期間配息金額共為美元600.00"),
        ("到期贖回金額為美元10,000×100%=美元10,000.00", "到期贖回金額為美元10,000×100%=美元10,001.00"),
        ("自動提前到期價格為期初股價×100%", "自動提前到期價格為期初股價×101%"),
        ("70.0000", "71.0000"),
        ("配息期數=6", "配息期數=5"),
        ("發行價格：100%", "發行價格：99%"),
        ("0%~5%", "0%~6%"),
    ],
)
def test_hsbc_review_standard_and_internal_problems_are_plain_chinese(tmp_path, old, new):
    report = hsbc_check(tmp_path, hsbc_synth.Spec(replacements={old: new}))
    assert_plain_chinese(report)


def test_hsbc_scenario_calculation_names_the_scenario_and_both_values(tmp_path):
    old, new = "固定配息金額=美元10,000×1.0000%=美元100.00", "固定配息金額=美元10,000×1.0000%=美元101.00"
    report = hsbc_check(tmp_path, hsbc_synth.Spec(replacements={old: new}))

    calc = [r for r in issues(report) if r.rule_id == "doc.scenario_calculations"]
    assert calc
    msg = problem_message(calc[0])
    assert msg.startswith("情境") and "100.00" in msg and "101.00" in msg


# ---------------------------------------------------------------- 配對、未支援上手、讀檔錯誤


def test_pairing_unsupported_and_unreadable_pdf_are_plain_chinese(tmp_path):
    spec = Spec()
    missing_row = build_pdf(tmp_path / "029199990009_TS.pdf", Spec(product_code="029199990009"))
    unsupported = build_pdf(tmp_path / "999199990001_TS.pdf", spec)
    broken = tmp_path / "029199990001_broken.pdf"
    broken.write_bytes(b"not a pdf")
    sheet_report = check_rows(tmp_path, missing_row, [reference_row(spec)])
    outcome = check_batch(
        [unsupported, broken],
        tmp_path / "FCN參考條件.xlsx",
        REVIEW_STANDARD,
        reference_format=REFERENCE_FORMAT,
        issuer_prefixes=ISSUER_PREFIXES,
    )

    for report in (sheet_report, outcome.items[0].report):
        assert_plain_chinese(report)
    assert "找不到" in problem_message(issues(sheet_report)[0])
    assert "未支援上手" in problem_message(issues(outcome.items[0].report)[0])
    [unreadable] = issues(outcome.items[1].report)
    assert unreadable.status == CheckStatus.ERROR
    msg = problem_message(unreadable)  # 後面附 PyMuPDF 的原始錯誤（含檔案路徑），供排查
    assert msg.startswith("說明書 PDF 無法開啟") and unreadable.reason_code not in msg


def test_encrypted_pdf_message_is_plain_chinese(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / "plain.pdf", spec)
    doc = fitz.open(pdf)
    locked = tmp_path / f"{spec.product_code}_TS.pdf"
    doc.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()
    report = check_rows(tmp_path, locked, [reference_row(spec)])
    assert report.status == CheckStatus.ERROR
    assert_plain_chinese(report)


# ---------------------------------------------------------------- 「核對結果」工作表與 PANEL 共用錯訊


def test_panel_detail_uses_the_shared_message_without_rule_id(tmp_path):
    report = check(tmp_path, overrides={"UL_2_進場價": 123.45})

    [r] = [r for r in issues(report) if r.rule_id == "field.underlying_prices"]
    detail = result_detail(r)
    assert detail.startswith("UL_2 進場價｜不一致\n原因：" + problem_message(r))
    assert r.rule_id not in detail and r.reason_code not in detail


# ---------------------------------------------------------------- 新規則沒寫錯訊時的預設


@pytest.mark.parametrize(
    "rule_id,status,expected",
    [
        ("field.new_rule", CheckStatus.MISMATCH, "參考條件表欄位對不起來：參考條件表 1／說明書 2"),
        ("standard.new_rule", CheckStatus.MISMATCH, "審查標準：兩邊的值不同（審查標準 1／說明書 2）"),
        ("doc.new_rule", CheckStatus.MISMATCH, "說明書內部一致性：兩邊的值不同（預期 1／說明書 2）"),
        ("doc.new_rule", CheckStatus.REVIEW_REQUIRED, "說明書內部一致性：需要人工確認（預期 1／說明書 2）"),
    ],
)
def test_rule_without_message_gets_a_chinese_default(rule_id, status, expected):
    r = CheckResult(rule_id, "brand_new_field", status, Decimal(1), Decimal(2), reason_code="new_reason")
    assert problem_message(r) == expected
