"""錯訊（Issue #71）：作業人員看到的每條問題都是看得懂的中文，只寫哪裡對不起來、兩邊各是多少，不含 rule_id／reason_code。

BARC 與 HSBC 都經公開批量入口 check_batch 產生結果，再用同一個錯訊產生器 `problem_message` 取錯訊。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from decimal import Decimal

import fitz
import pytest

import hsbc_synth
from fcn_checker.batch import check_batch
from fcn_checker.issuers import by_code
from fcn_checker.messages import problem_message
from fcn_checker.panel import result_detail
from fcn_checker.rules import kit
from fcn_checker.schema import CheckResult, CheckStatus, Item, ItemSource
from fcn_checker.standard_fields import not_provided
from harness import ISSUER_PREFIXES, REVIEW_STANDARD, check_rows
from reference_synth import REFERENCE_FORMAT, REFERENCE_HEADERS, build_reference_sheet
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


# ---------------------------------------------------------------- 錯誤清單與 PANEL 共用錯訊


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
    name, source = {
        "field": ("參考條件表欄位", ItemSource.REFERENCE),
        "standard": ("審查標準", ItemSource.STANDARD),
        "doc": ("說明書內部一致性", ItemSource.EXPECTED),
    }[rule_id.split(".")[0]]
    r = CheckResult(
        rule_id, "brand_new_field", status, Decimal(1), Decimal(2), reason_code="new_reason", item=Item(name, source)
    )
    assert problem_message(r) == expected


# ---------------------------------------------------------------- 每條錯訊都寫出具體項目（Issue #91）


def test_result_without_an_item_fails():
    with pytest.raises(TypeError):
        CheckResult("doc.new_rule", "brand_new_field", CheckStatus.MISMATCH)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        kit.result("doc.new_rule", "brand_new_field", CheckStatus.MISMATCH)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Item("", ItemSource.EXPECTED)


# 項目名稱不認得時舊版會退回的規則類別名稱
CATEGORY_HEAD = re.compile(
    "(?:參考條件表欄位|回填欄位|參考條件表欄名|審查標準|說明書內部一致性|核對項目)(?:：|對不起來)"
)


def assert_every_problem_names_its_item(report, *, codes_allowed: bool = False) -> None:
    assert issues(report), "應該有問題項目"
    for r in issues(report):
        msg = problem_message(r)
        assert not CATEGORY_HEAD.match(msg), msg
        if not codes_allowed:
            assert not CODE.search(msg.replace("Coupon p.a. (%)", "")), msg  # Excel 欄名照原樣寫出


class _Withheld:
    """讀出結果照常，但每個欄位都不交出：所有規則都走「說明書抓不到」的路徑。"""

    def __init__(self, ts):
        self._ts = ts
        self.full_text = ts.full_text

    def f(self, name):
        return not_provided(name)

    def __getattr__(self, attr):  # 上手專屬資料照常交給該上手規則
        return getattr(self._ts, attr)


def _barc_sheet(tmp_path, spec, overrides):
    return build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **overrides)])


def _run(pdf, sheet, registry=None):
    kw = {"reference_format": REFERENCE_FORMAT, "issuer_prefixes": ISSUER_PREFIXES}
    if registry:
        kw["registry"] = registry
    return check_batch([pdf], sheet, REVIEW_STANDARD, **kw).items[0].report


BROKEN_VALUES = ["壞", 1, None]
# HSBC 合成參考條件表的標準欄位（商品代號用來配對，不改）
HSBC_KEYS = [
    "isin",
    "currency",
    "denomination",
    "trade_date",
    "issue_date",
    "final_valuation_date",
    "maturity_date",
    "ko_pct",
    "strike_pct",
    "ki_pct",
    "ki_type",
    "ko_observation",
    "ko_memory",
    "coupon_pa_pct",
    "tenor_months",
    "first_callable_period",
    *[f"autocall_date_{i}" for i in range(1, 13)],
    *[f"underlying_{i}" for i in range(1, 6)],
    *[f"underlying_{i}_{k}_price" for i in range(1, 6) for k in ("initial", "strike", "ko", "ki")],
]


@pytest.mark.parametrize("value", BROKEN_VALUES)
def test_barc_broken_reference_sheet_messages_name_their_items(tmp_path, value):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    kept = ("TDCC Code", "發行機構", "Product")  # 配對用，改壞就核對不到其他欄位
    columns = [h for h in REFERENCE_HEADERS if h not in kept]
    report = _run(pdf, _barc_sheet(tmp_path, spec, dict.fromkeys(columns, value)))
    assert_every_problem_names_its_item(report)


@pytest.mark.parametrize("value", BROKEN_VALUES)
def test_hsbc_broken_reference_sheet_messages_name_their_items(tmp_path, value):
    assert_every_problem_names_its_item(hsbc_check(tmp_path, overrides=dict.fromkeys(HSBC_KEYS, value)))


def test_reference_sheet_header_problems_name_their_items(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    headers = [h for h in REFERENCE_HEADERS if h != "KO(memo)"] + ["新欄位", "K(%)"]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)], headers)
    assert_every_problem_names_its_item(_run(pdf, sheet))


@pytest.mark.parametrize("issuer", ["BARC", "HSBC"])
def test_term_sheet_missing_every_field_messages_name_their_items(tmp_path, issuer):
    base = by_code(issuer)
    adapter = dataclasses.replace(base, read=lambda lines: _Withheld(base.read(lines)))
    if issuer == "BARC":
        spec = Spec()
        pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
        sheet = _barc_sheet(tmp_path, spec, {})
    else:
        s = hsbc_synth.Spec()
        pdf = hsbc_synth.build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
        sheet = hsbc_synth.build_inquiry(tmp_path / "order.xlsx", s)
    report = _run(pdf, sheet, registry=(adapter,))
    assert report.status == CheckStatus.REVIEW_REQUIRED, "缺欄位轉人工覆核，不是執行錯誤"
    # 上手沒交出欄位是 adapter 的問題，說明刻意寫出標準欄位名稱供維護人員追查；這裡只看項目名稱
    assert_every_problem_names_its_item(report, codes_allowed=True)


BARC_BROKEN_DOCUMENTS = {
    "many": Spec(
        chairman="林晉輝",
        rr="RR5",
        price_overrides={(1, "strike"): "99.9999", (2, "ko"): "1.0000"},
        mention_overrides={"§9": "0.9999%"},
        print_date=dt.date(2030, 3, 1),
        subscription_date=dt.date(2030, 1, 8),
        approval_date=dt.date(2020, 1, 1),
        issue_price="99",
        title_name="錯的標題",
        art1_name="錯的名稱",
        art5_currency="日圓",
        distributor_code="029199990002",
        fees={"申購費用": "0%~6%"},
        scenario_notional=1,
        general_total="9.99",
        general_annualized="9.99",
        favourable_total="9.99",
        t_range_end=5,
        min_subscription=1,
        min_redemption=1,
        distributor_cover=("錯的銀行", "(02)0000-0000", "錯的地址"),
        issuer_ch2="錯的發行機構",
        ko_overrides={(6, "end"): "2030 年7 月9 日", (3, "start"): "2030 年1 月1 日"},
        coupon_overrides={(2, "payment"): "2030 年1 月1 日"},
        scenario_overrides={(1, "strike"): "1.0000"},
        strike_headers={"§15": "71.00"},
        repeat_overrides={"§9(3)": "0.9999"},
        extra_text="受託投資",
    ),
    "omitted": Spec(omit=frozenset({"trade_date", "issue_date"}), extra_strike_def="71.00"),
    "periodic": Spec(ko_obs="P", memory=False, ki="AM", ko_overrides={(3, "trigger"): "99.00%"}),
}


@pytest.mark.parametrize("spec", BARC_BROKEN_DOCUMENTS.values(), ids=BARC_BROKEN_DOCUMENTS.keys())
def test_barc_broken_term_sheet_messages_name_their_items(tmp_path, spec):
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    report = _run(pdf, _barc_sheet(tmp_path, Spec(), {}))
    assert_every_problem_names_its_item(report)


@pytest.mark.parametrize("obs", ["D", "P"])
def test_hsbc_broken_term_sheet_messages_name_their_items(tmp_path, obs):
    replacements = {
        "固定配息金額=美元10,000×1.0000%=美元100.00": "固定配息金額=美元10,000×1.0000%=美元101.00",
        "6個計息期間配息金額共為美元600.00": "5個計息期間配息金額共為美元600.00",
        "到期贖回金額為美元10,000×100%=美元10,000.00": "到期贖回金額為美元10,000×100%=美元10,001.00",
        "自動提前到期價格為期初股價×100%": "自動提前到期價格為期初股價×101%",
        "70.0000": "71.0000",
        "配息期數=6": "配息期數=5",
        "發行價格：100%": "發行價格：99%",
        "0%~5%": "0%~6%",
    }
    report = hsbc_check(tmp_path, hsbc_synth.Spec(obs=obs, replacements=replacements))
    assert_every_problem_names_its_item(report)


def test_price_derivation_names_the_underlying_the_same_way_for_both_issuers(tmp_path):
    (tmp_path / "barc").mkdir()
    (tmp_path / "hsbc").mkdir()
    barc = check(tmp_path / "barc", Spec(price_overrides={(2, "ko"): "1.0000"}))
    hsbc = hsbc_check(tmp_path / "hsbc", hsbc_synth.Spec(replacements={"70.0000": "71.0000"}))

    assert any(problem_message(r).startswith("UL_2 KO價：") for r in issues(barc) if r.rule_id == "derive.prices")
    assert any(problem_message(r).startswith("UL_1 執行價：") for r in issues(hsbc) if r.rule_id == "derive.prices")
