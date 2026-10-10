"""錯訊（Issue #71）：作業人員看到的每條問題都是看得懂的中文，只寫哪裡對不起來、兩邊各是多少，不含 rule_id／reason_code。

BARC 與 HSBC 都經公開批量入口（預覽＋核對）產生結果，再用同一個錯訊產生器 `problem_message` 取錯訊。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

import hsbc_synth
from fcn_checker.issuers import by_code
from fcn_checker.messages import problem_message
from fcn_checker.panel_workflow import PanelOutcome
from fcn_checker.rules import kit
from fcn_checker.schema import CheckResult, CheckStatus, DocKind, Item, ItemSource
from fcn_checker.standard_fields import not_provided
from harness import CONFIG, check_all
from pdf_writer import Edit
from reference_synth import DEFAULT_ULS, REFERENCE_HEADERS, as_headers, build_reference_sheet, reference_row
from synth import ISSUER_NAME, Spec, build_pdf, check, check_rows

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


RR5 = [Edit("RR4", "RR5", "ts"), Edit("RR4", "RR5", "iis.p2")]  # BARC 風險等級：說明書三處警語與投資人須知商品簡介


def hsbc_check(tmp_path, spec=None, overrides=None, edits=()):
    """HSBC 合成說明書（`edits` 改字）× 與之一致的參考條件表（正式版面）；overrides 以標準欄位名覆寫。"""
    s = spec or hsbc_synth.Spec()
    pdf = hsbc_synth.build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s, edits=edits)
    row = reference_row(s, **as_headers(overrides or {}))
    return check_all(build_reference_sheet(tmp_path / "order.xlsx", [row]), [pdf]).items[0].report


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
    if column == "承作幣別":  # 金額旁每一處幣別字也和承作幣別比（Issue #170）：封面那筆之外各處也都對不起來
        others = [r for r in sheet_side if r.field != "currency"]
        assert others and all(r.rule_id == "field.currency" and r.status == CheckStatus.MISMATCH for r in others)
        for r in others:  # 單一出處顯示 ISO 代碼；情境列出該情境裡每一處不符的金額
            assert re.search(r"幣別對不起來：參考條件表 JPY／說明書 (USD|.*美元.*)$", problem_message(r)), (
                problem_message(r)
            )
        sheet_side = [r for r in sheet_side if r.field == "currency"]
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
    # 最後一期期末日晚於最終評價日 2030-07-08
    report = check(tmp_path, edits=[Edit("2030 年7 月8 日", "2030 年7 月9 日", "ts.art13.ko.6.end")])

    [r] = [r for r in issues(report) if r.rule_id == "backfill.compare_dates"]
    assert problem_message(r) == "比價日：說明書最晚的比價日 2030-07-09 不等於最終比價日 2030-07-08，不回填"


def test_pdfs_sharing_one_reference_row_get_a_plain_chinese_message(tmp_path, monkeypatch):
    spec = Spec()
    monkeypatch.chdir(tmp_path)  # 同名檔案的錯訊列出選取時的路徑；用相對路徑，不帶測試暫存資料夾名稱
    for v in ("舊", "新"):
        Path(v).mkdir()
    pdfs = [build_pdf(Path(v) / f"{spec.product_code}_TS.pdf", spec) for v in ("舊", "新")]
    sheet = check_rows(tmp_path, pdfs[0], [reference_row(spec)])  # 建好參考條件表
    assert sheet.status == CheckStatus.PASS
    outcome = check_all(tmp_path / "FCN參考條件.xlsx", pdfs)
    assert [i.status_label for i in outcome.items] == ["多份對到同一列"] * 4

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
    edits = [
        Edit("林晋輝", "林晉輝", "ts.ch二.5"),
        *RR5,
        *(Edit("86.4150", "99.9999", f"ts.{art}.price.1.strike") for art in ("art15", "art16")),
        Edit("為1.0000%(顯示", "為0.9999%%(顯示", "ts.art9"),
    ]
    report = check(tmp_path, edits=edits)

    rules = {r.rule_id for r in issues(report)}
    assert {"standard.chairman", "standard.risk_level", "derive.prices"} <= rules
    assert_plain_chinese(report)


def test_review_standard_difference_shows_both_values(tmp_path):
    report = check(tmp_path, edits=RR5)

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
    report = hsbc_check(tmp_path, edits=[Edit(old, new, "ts")])
    assert_plain_chinese(report)


def test_hsbc_scenario_calculation_names_the_scenario_and_both_values(tmp_path):
    old, new = "固定配息金額=美元10,000×1.0000%=美元100.00", "固定配息金額=美元10,000×1.0000%=美元101.00"
    report = hsbc_check(tmp_path, edits=[Edit(old, new, "ts")])

    calc = [r for r in issues(report) if r.rule_id == "doc.scenario_calculations"]
    assert calc
    msg = problem_message(calc[0])
    assert msg.startswith("情境") and "100.00" in msg and "101.00" in msg


# ---------------------------------------------------------------- 配對、未支援上手、讀檔錯誤


def test_pairing_unsupported_and_unreadable_pdf_are_plain_chinese(tmp_path):
    spec = Spec()
    missing_row = build_pdf(tmp_path / "029199990009_TS.pdf", Spec(product_code="029199990009"))
    unsupported = build_pdf(tmp_path / "999199990001_TS.pdf", spec)
    broken = tmp_path / "029199990001_TS.pdf"
    broken.write_bytes(b"not a pdf")
    sheet_report = check_rows(tmp_path, missing_row, [reference_row(spec)])
    outcome = check_all(tmp_path / "FCN參考條件.xlsx", [unsupported, broken])

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
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, UL_2_進場價=123.45)])
    item = check_all(sheet, [pdf]).items[0]

    [r] = [r for r in issues(item.report) if r.rule_id == "field.underlying_prices"]
    detail = PanelOutcome.detail(r, item)
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
    config = CONFIG.with_registry(registry) if registry else CONFIG
    return check_all(sheet, [pdf], config).items[0].report


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
        pdf = hsbc_synth.build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s)
        sheet = build_reference_sheet(tmp_path / "order.xlsx", [reference_row(s)])
    report = _run(pdf, sheet, registry=(adapter,))
    assert report.status == CheckStatus.REVIEW_REQUIRED, "缺欄位轉人工覆核，不是執行錯誤"
    # 上手沒交出欄位是 adapter 的問題，說明刻意寫出標準欄位名稱供維護人員追查；這裡只看項目名稱
    assert_every_problem_names_its_item(report, codes_allowed=True)


_TITLE = Spec().expected_name_zh().replace("（下稱「本商品」）", "")
_DISTRIBUTOR = "玉山綜合證券股份有限公司，電話：02-5556-1313，地址：台北市松山區民生東路三段158號6樓"
BARC_BROKEN_DOCUMENTS = {  # 說明書規格與改字
    "many": (
        Spec(extra_text="受託投資"),
        [
            Edit("林晋輝", "林晉輝", "ts.ch二.5"),
            *RR5,
            Edit("86.4150", "99.9999", "ts.art15.price.1.strike"),
            *(Edit("87.2000", "1.0000", f"ts.{art}.price.2.ko") for art in ("art15", "art16")),
            Edit("為1.0000%(顯示", "為0.9999%%(顯示", "ts.art9"),
            Edit("刊印日期：2030 年1 月8 日", "刊印日期：2030 年3 月1 日", "ts.cover"),
            Edit("申購日期：2030 年1 月7 日", "申購日期：2030 年1 月8 日", "ts.ch四"),
            Edit("2026 年6 月11 日", "2020 年1 月1 日", "ts.cover.approval_date"),
            Edit("面額之100%", "面額之99%"),
            Edit(_TITLE, "錯的標題", "ts.title"),
            Edit(Spec().expected_name_zh(), "錯的名稱", "ts.art1"),
            Edit("計價幣別：美元", "計價幣別：日圓", "ts.art5"),
            Edit("029199990001", "029199990002", "ts.cover.distributor_code"),
            Edit("受託或銷售機構商品代號:029199990001", "受託或銷售機構商品代號:029199990002", "iis.p1"),
            *(Edit("0%~5%", "0%~6%", f"{fees}.申購費用") for fees in ("ts.ch四.fees", "iis.p3.fees")),
            Edit("10,000 美元", "1 美元", "ts.art16"),
            Edit("= 6.0000%", "= 9.99%", "ts.art16.ii"),
            Edit("報酬率：12.00%", "報酬率：9.99%", "ts.art16.ii"),
            Edit("- 1 = 1.0000%", "- 1 = 9.99%", "ts.art16.i"),
            Edit("至6 的情況", "至5 的情況", "ts.art13"),
            Edit("至少為10,000", "至少為1", "ts.ch四"),
            Edit("最低申購金額為10,000", "最低申購金額為1", "iis.p2"),
            Edit("贖回商品面額為10,000", "贖回商品面額為1", "ts.ch四"),
            Edit(_DISTRIBUTOR, "錯的銀行，電話：(02)0000-0000，地址：錯的地址", "ts.cover.distributor"),
            Edit(ISSUER_NAME, "錯的發行機構", "ts.ch二.1"),
            Edit("2030 年7 月8 日", "2030 年7 月9 日", "ts.art13.ko.6.end"),
            Edit("2030 年3 月11 日", "2030 年1 月1 日", "ts.art13.ko.3.start"),
            Edit("2030 年3 月11 日", "2030 年1 月1 日", "ts.art13.coupon.2.payment"),
            Edit("86.4150", "1.0000", "ts.art16.price.1.strike"),
            Edit("格的70.00%", "格的71.00%", "ts.art15.price"),
            Edit("相關配息率為1.0000%", "相關配息率為0.9999%", "ts.art9"),
        ],
    ),
    "omitted": (Spec(omit=frozenset({"trade_date", "issue_date"}), extra_strike_def="71.00"), []),
    "periodic": (Spec(ko_obs="P", memory=False, ki="AM"), []),  # 定期非記憶式沒有提前出場表，原本的改字不曾生效
}


@pytest.mark.parametrize(("spec", "edits"), BARC_BROKEN_DOCUMENTS.values(), ids=BARC_BROKEN_DOCUMENTS.keys())
def test_barc_broken_term_sheet_messages_name_their_items(tmp_path, spec, edits):
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    report = _run(pdf, _barc_sheet(tmp_path, Spec(), {}))
    assert_every_problem_names_its_item(report)


@pytest.mark.parametrize("obs", ["D", "P"])
def test_hsbc_broken_term_sheet_messages_name_their_items(tmp_path, obs):
    changes = {
        "固定配息金額=美元10,000×1.0000%=美元100.00": "固定配息金額=美元10,000×1.0000%=美元101.00",
        "6個計息期間配息金額共為美元600.00": "5個計息期間配息金額共為美元600.00",
        "到期贖回金額為美元10,000×100%=美元10,000.00": "到期贖回金額為美元10,000×100%=美元10,001.00",
        "自動提前到期價格為期初股價×100%": "自動提前到期價格為期初股價×101%",
        "70.0000": "71.0000",
        "配息期數=6": "配息期數=5",
        "發行價格：100%": "發行價格：99%",
        "0%~5%": "0%~6%",
    }
    edits = [Edit(old, new, "ts") for old, new in changes.items()]
    report = hsbc_check(tmp_path, hsbc_synth.Spec(ko_obs=obs), edits=edits)
    assert_every_problem_names_its_item(report)


def test_each_minimum_amount_names_itself(tmp_path):
    edits = [
        Edit("至少為10,000", "至少為1", "ts.ch四"),
        Edit("最低申購金額為10,000", "最低申購金額為1", "iis.p2"),
        Edit("贖回商品面額為10,000", "贖回商品面額為1", "ts.ch四"),
    ]
    report = check(tmp_path, edits=edits)

    msgs = [problem_message(r) for r in issues(report) if r.rule_id == "field.min_amounts"]
    assert msgs == [
        "最低申購金額對不起來：參考條件表 10000／說明書 1",
        "最低贖回商品面額對不起來：參考條件表 10000／說明書 1",
    ]


def test_monthly_coupon_derived_from_the_sheet_shows_the_sheet_side(tmp_path):
    report = check(tmp_path, overrides={"Coupon p.a. (%)": 13})

    [r] = [r for r in issues(report) if r.rule_id == "derive.monthly_coupon"]
    msg = problem_message(r)
    assert msg.startswith("月配息率 %：推算：13") and msg.endswith("（參考條件表 1.0833／說明書 1.0000）"), msg


def test_price_derivation_names_the_underlying_the_same_way_for_both_issuers(tmp_path):
    (tmp_path / "barc").mkdir()
    (tmp_path / "hsbc").mkdir()
    barc = check(
        tmp_path / "barc", edits=[Edit("87.2000", "1.0000", f"ts.{art}.price.2.ko") for art in ("art15", "art16")]
    )
    hsbc = hsbc_check(tmp_path / "hsbc", edits=[Edit("70.0000", "71.0000", "ts")])

    assert any(problem_message(r).startswith("UL_2 KO價：") for r in issues(barc) if r.rule_id == "derive.prices")
    assert any(problem_message(r).startswith("UL_1 執行價：") for r in issues(hsbc) if r.rule_id == "derive.prices")


# ---------------------------------------------------------------- 文件那一邊的稱呼（Issue #142）


def test_message_names_the_document_recorded_on_the_result():
    pf = not_provided("isin")
    ts_side = kit.doc_review("field.isin", "isin", pf, item=Item.note("ISIN"))
    iis_side = kit.doc_review("field.isin", "isin", pf, item=Item.note("ISIN"), document=DocKind.IIS)
    assert ts_side.document == DocKind.TERM_SHEET and ts_side.message.startswith("說明書抓不到此欄位")
    assert iis_side.document == DocKind.IIS and iis_side.message.startswith("投資人須知抓不到此欄位")

    mismatch = CheckResult(
        "iis.isin", "isin", CheckStatus.MISMATCH, "XS1", "XS2", item=Item.term_sheet("ISIN"), document=DocKind.IIS
    )
    assert problem_message(mismatch) == "ISIN對不起來：說明書 XS1／投資人須知 XS2"
    silent = CheckResult(
        "iis.x",
        "x",
        CheckStatus.REVIEW_REQUIRED,
        reason_code="document_missing",
        item=Item.note("X"),
        document=DocKind.IIS,
    )
    assert problem_message(silent) == "X：投資人須知抓不到此欄位"
