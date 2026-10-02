"""HSBC 黑箱回歸：合成資料經 check_batch、CLI、PANEL；不直接測 parser。"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from decimal import Decimal

import fitz
import openpyxl
import pytest

from fcn_checker.batch import check_batch
from fcn_checker.cli import main
from fcn_checker.issuers import HSBC, REGISTRY
from fcn_checker.panel_workflow import PanelSession
from fcn_checker.schema import CheckReport
from fcn_checker.schema import CheckStatus as S
from hsbc_synth import ORDER_FORMAT, REVIEW_STANDARD, ROOT, Spec, build_inquiry, build_pdf


def run_check(pdf, excel, standard, fmt, registry=REGISTRY):
    """單份測試仍經公開批量入口，不直接呼叫 parser／規則。"""
    out = check_batch(
        [pdf],
        excel,
        standard,
        reference_format=fmt,
        issuer_prefixes=ROOT / "config/issuer_prefixes.toml",
        registry=registry,
    )
    return out.items[0].report if out.items else CheckReport(out.status, None, list(out.errors), [])


def check(tmp_path, spec=None, overrides=None):
    s = spec or Spec()
    return run_check(
        build_pdf(tmp_path / f"{s.code}_TS.pdf", s),
        build_inquiry(tmp_path / "order.xlsx", s, overrides),
        REVIEW_STANDARD,
        ORDER_FORMAT,
    )


@pytest.mark.parametrize(
    "obs,memory,ki,count",
    [
        ("D", True, "AM", 2),
        ("D", True, "none", 2),
        ("D", False, "none", 1),
        ("P", False, "none", 1),
        ("P", False, "AM", 2),
        ("P", True, "AM", 2),
        ("D", False, "D", 2),
    ],
)
def test_supported_types_pass(tmp_path, obs, memory, ki, count):
    r = check(tmp_path, Spec(obs=obs, memory=memory, ki=ki, count=count))
    assert r.template == HSBC.template_id
    assert r.status == S.PASS, [
        (x.rule_id, x.field, x.reason_code) for x in r.results if x.status not in (S.PASS, S.NOT_APPLICABLE)
    ]
    assert all(x.document_evidence for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule.")))
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in HSBC.not_covered}


@pytest.mark.parametrize(
    "field,value,rule",
    [
        ("isin", "XS1999900002", "backfill.isin"),
        ("denomination", 20000, "field.denomination"),
        ("trade_date", "2031-01-07", "field.trade_date"),
        ("issue_date", "2031-01-14", "field.issue_date"),
        ("final_valuation_date", "2031-07-07", "field.final_valuation_date"),
        ("maturity_date", "2031-07-10", "field.maturity_date"),
        ("ko_pct", 101, "field.ko_pct"),
        ("strike_pct", 71, "field.strike_pct"),
        ("ki_pct", 61, "field.ki_pct"),
        ("coupon_pa_pct", 13, "field.coupon_pa_pct"),
        ("tenor_months", 7, "field.tenor_months"),
        ("first_callable_period", 3, "field.first_callable_period"),
        ("currency", "JPY", "field.currency"),
        ("ko_memory", "N", "field.ko_memory"),
        ("ko_observation", "P", "field.ko_observation"),
        ("ki_type", "D", "field.ki_type"),
        ("underlying_1", "ZZ9 UW", "field.underlyings"),
        ("underlying_1", "ZZ1 UN", "field.underlyings"),
        ("underlying_1_initial_price", 101, "field.prices"),
        ("underlying_1_strike_price", 71, "field.prices"),
        ("underlying_1_ki_price", 61, "field.prices"),
        ("underlying_1_ko_price", 101, "field.prices"),
        ("underlying_5", "ZZ5 UW", "field.underlyings"),
        ("autocall_date_3", "2030-04-07", "backfill.compare_dates"),
        ("autocall_date_2", "-", "backfill.compare_dates"),
        ("autocall_date_6", "2030-07-08", "backfill.compare_dates"),
    ],
)
def test_order_difference_is_reported(tmp_path, field, value, rule):
    r = check(tmp_path, overrides={field: value})
    assert any(x.rule_id == rule and x.status in (S.MISMATCH, S.REVIEW_REQUIRED) for x in r.results)


@pytest.mark.parametrize(
    "old,new,rule",
    [
        ("最低交易金額：美元10,000元", "最低交易金額：美元20,000元", "doc.minimum_amounts"),
        ("商品開始受理申購日：2030年1月7日", "商品開始受理申購日：2030年1月8日", "doc.subscription_dates"),
        ("(最終版)刊印日期：2030年1月7日", "(最終版)刊印日期：2030年1月9日", "doc.print_date"),
        (
            "[受託或銷售機構]審查通過之日期：2026年6月11日",
            "[受託或銷售機構]審查通過之日期：2026年6月12日",
            "standard.approval_date",
        ),
        ("0%~5%", "0%~6%", "standard.fees"),
        (
            "固定配息金額=美元10,000×1.0000%=美元100.00",
            "固定配息金額=美元10,000×1.0000%=美元101.00",
            "doc.scenario_calculations",
        ),
        ("6個計息期間配息金額共為美元600.00", "5個計息期間配息金額共為美元600.00", "doc.scenario_parameters"),
        (
            "到期贖回金額為美元10,000×100%=美元10,000.00",
            "到期贖回金額為美元10,000×100%=美元10,001.00",
            "doc.scenario_calculations",
        ),
        (
            "平均年化報酬率(以簡單平均年化報酬率之方式計算)為12.00%",
            "平均年化報酬率(以簡單平均年化報酬率之方式計算)為13.00%",
            "doc.scenario_general_annualized",
        ),
        ("自動提前到期價格為期初股價×100%", "自動提前到期價格為期初股價×101%", "doc.price_header_pct"),
        ("70.0000", "71.0000", "derive.prices"),
        ("配息期數=6", "配息期數=5", "doc.coupon_periods"),
        ("發行價格：100%", "發行價格：99%", "standard.issue_price"),
        (
            "商品天期為6個月期，每單位面額為美元10,000元",
            "商品天期為7個月期，每單位面額為美元10,000元",
            "doc.scenario_parameters",
        ),
    ],
)
def test_document_difference_is_reported(tmp_path, old, new, rule):
    r = check(tmp_path, Spec(replacements={old: new}))
    assert any(x.rule_id == rule and x.status in (S.MISMATCH, S.REVIEW_REQUIRED) for x in r.results)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ko_observation", "Q"),
        ("ko_memory", "X"),
        ("ki_type", "M"),
        ("coupon_pa_pct", "?"),
        ("underlying_1", "-"),
    ],
)
def test_unknown_or_missing_order_value_requires_review(tmp_path, field, value):
    r = check(tmp_path, overrides={field: value})
    assert any(x.status == S.REVIEW_REQUIRED and x.rule_id.startswith("field.") for x in r.results)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing_row", "reference_row_missing"),
        ("duplicate_row", "reference_row_duplicate"),
        ("issuer", "reference_issuer_mismatch"),
        ("missing_key", "reference_key_missing"),
    ],
)
def test_table_pairing_failures_stop_rules(tmp_path, change, reason):
    s = Spec()
    pdf = build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    excel = build_inquiry(tmp_path / "order.xlsx", s)
    wb = openpyxl.load_workbook(excel)
    ws = wb.active
    if change == "missing_row":
        ws.cell(4, 1, "325199990002")
    elif change == "duplicate_row":
        ws.append([c.value for c in ws[4]])
    elif change == "issuer":
        ws.cell(4, ws.max_column, "BARC")
    else:
        ws.cell(3, 1, "不存在的欄位")
    wb.save(excel)
    wb.close()
    r = run_check(pdf, excel, REVIEW_STANDARD, ORDER_FORMAT)
    assert any(x.reason_code == reason for x in r.results), [(x.reason_code, x.message) for x in r.results]
    assert not any(x.rule_id.startswith("field.") for x in r.results)


def test_wrong_prefix_and_unknown_template_require_review(tmp_path):
    r = check(tmp_path, Spec(code="029199990001"))
    assert any(x.reason_code == "issuer_prefix_mismatch" for x in r.results)
    other = tmp_path / "other"
    other.mkdir()
    r = check(other, Spec(replacements={"中文產品說明書(最終版)": "未知說明書"}))
    assert r.status == S.REVIEW_REQUIRED and r.template is None


def test_partial_coupon_and_unrounded_total(tmp_path):
    s = Spec(annual=Decimal("11.99"))
    monthly = "0.9992"
    s.replacements = {
        "固定配息率為1.0000%，配息期數=6，且假設": f"固定配息率為{monthly}%，配息期數=6，且假設",
        "固定配息金額=美元10,000×1.0000%=美元100.00": f"固定配息金額=美元10,000×{monthly}%=美元99.92",
        "6個計息期間配息金額共為美元600.00": "6個計息期間配息金額共為美元599.50",
        "損益=美元10,000.00+美元200.00-美元10,000.00=美元200.00": "損益=美元10,000.00+美元199.83-美元10,000.00=美元199.83",
        "損益=美元10,000.00+美元600.00-美元10,000.00=美元600.00": "損益=美元10,000.00+美元599.50-美元10,000.00=美元599.50",
        "平均年化報酬率(以簡單平均年化報酬率之方式計算)為12.00%": "平均年化報酬率(以簡單平均年化報酬率之方式計算)為11.99%",
    }
    assert check(tmp_path, s).status == S.PASS


def test_cli_and_panel_select_hsbc(tmp_path, monkeypatch):
    s = Spec()
    pdf = build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    excel = build_inquiry(tmp_path / "order.xlsx", s)
    monkeypatch.chdir(ROOT)
    out = tmp_path / "reports"
    assert main([str(excel), str(pdf), "--out", str(out)]) == 0
    data = json.loads(next(out.glob("*.check.json")).read_text(encoding="utf-8"))
    assert data["template"] == HSBC.template_id
    assert "HSBC" in [x.code for x in REGISTRY]
    session = PanelSession(REVIEW_STANDARD, ROOT / "config")
    session.select(excel, [pdf])
    session.load_preview()
    assert session.start_check().batch.status == S.PASS


def test_ambiguous_registry_and_damaged_pdf(tmp_path):
    s = Spec()
    pdf = build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    excel = build_inquiry(tmp_path / "order.xlsx", s)
    fake = dataclasses.replace(HSBC, code="FAKE")
    r = run_check(pdf, excel, REVIEW_STANDARD, ORDER_FORMAT, registry=(HSBC, fake))
    assert any(x.reason_code == "template_ambiguous" for x in r.results)
    pdf.write_bytes(b"broken pdf")
    assert run_check(pdf, excel, REVIEW_STANDARD, ORDER_FORMAT).status == S.ERROR


def test_encrypted_pdf_is_not_checked(tmp_path):
    s = Spec()
    plain = build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    excel = build_inquiry(tmp_path / "order.xlsx", s)
    encrypted = tmp_path / f"{s.code}_encrypted.pdf"
    with fitz.open(plain) as d:
        d.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="fake-owner", user_pw="fake-user")
    assert run_check(encrypted, excel, REVIEW_STANDARD, ORDER_FORMAT).status == S.ERROR


@pytest.mark.parametrize(
    "field,new,rule",
    [
        ("chairman", "不同負責人", "standard.chairman"),
        ("name", "不同銷售機構", "standard.distributor"),
        ("address", "不同地址", "standard.distributor"),
    ],
)
def test_distributor_standard_difference(tmp_path, field, new, rule):
    from hsbc_synth import STD

    old = STD["distributor"][field]
    # Change all places containing this value, including longer labels.
    s = Spec()
    if field == "chairman":
        oldline = "(c)營業所在地：" + STD["distributor"]["address"] + "(d)負責人姓名：" + old
    elif field == "name":
        oldline = "受託或銷售機構之名稱、電話及地址：" + old
    else:
        oldline = old
    s.replacements = {oldline: oldline.replace(old, new)}
    r = check(tmp_path, s)
    assert any(x.rule_id == rule and x.status == S.MISMATCH for x in r.results)


def test_warning_risk_wording_and_name_rules(tmp_path):
    from hsbc_synth import STD

    warning = STD["risk"]["fixed_warning_by_issuer"]["hsbc"]
    s = Spec(replacements={warning: warning.replace("RR4", "RR3")})
    r = check(tmp_path, s)
    assert any(x.rule_id == "standard.fixed_warning" and x.status == S.MISMATCH for x in r.results)
    assert any(x.rule_id == "standard.risk_level" and x.status == S.MISMATCH for x in r.results)
    other = tmp_path / "wording"
    other.mkdir()
    r = check(other, Spec(replacements={"其他說明": "受託投資"}))
    assert any(x.rule_id == "standard.forbidden_wording" and x.status == S.MISMATCH for x in r.results)
    other = tmp_path / "name"
    other.mkdir()
    s = Spec()
    s.replacements = {
        f"商品英文名稱：{s.en}": "商品英文名稱：Autocallable Fixed Coupon Notes (Non Guaranteed, Unsecured)"
    }
    r = check(other, s)
    assert any(x.rule_id == "standard.product_name" and x.status == S.MISMATCH for x in r.results)
    assert any(x.rule_id == "doc.name_consistency" and x.status == S.MISMATCH for x in r.results)


@pytest.mark.parametrize(
    "old,new,rule",
    [
        ("70.0000", "71.0000", "doc.scenario_table"),
        ("執行價(即期初股價的70%)", "執行價(即期初股價的71%)", "doc.scenario_header_pct"),
    ],
)
def test_scenario_table_is_independently_compared(tmp_path, old, new, rule):
    r = check(tmp_path, Spec(scenario_replacements={old: new}))
    assert any(x.rule_id == rule and x.status == S.MISMATCH for x in r.results)


@pytest.mark.parametrize(
    "old,new,rule",
    [
        ("固定配息率=12%×1/12", "固定配息率=12%×1/12固定配息率=13%×1/12", "field.coupon_pa_pct"),
        ("固定配息率=12%×1/12", "", "field.coupon_pa_pct"),
        ("每單位面額：美元10,000元", "每單位面額：美元20,000元", "doc.denomination"),
        ("註1：假設日期", "未知表格結尾", "schedule.coupon_dates"),
        ("固定配息金額=美元10,000×1.0000%=美元100.00", "未知算式", "doc.scenario_calculations"),
    ],
)
def test_missing_ambiguous_or_unknown_document_requires_review(tmp_path, old, new, rule):
    r = check(tmp_path, Spec(replacements={old: new}))
    assert any(x.rule_id == rule and x.status == S.REVIEW_REQUIRED for x in r.results)


@pytest.mark.parametrize(
    "mode,rule",
    [
        ("unknown", "order.unknown_column"),
        ("missing", "order.missing_column"),
        ("duplicate", "order.duplicate_column"),
        ("no_header", None),
    ],
)
def test_header_validation(tmp_path, mode, rule):
    s = Spec()
    pdf = build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    excel = build_inquiry(tmp_path / "order.xlsx", s)
    wb = openpyxl.load_workbook(excel)
    ws = wb.active
    if mode == "missing":
        ws.cell(3, 2).value = None
    else:
        col = ws.max_column + 1
        ws.cell(3, col, {"unknown": "未知新欄位", "duplicate": "ISIN Code", "no_header": None}[mode])
        ws.cell(4, col, "額外資料")
    wb.save(excel)
    wb.close()
    r = run_check(pdf, excel, REVIEW_STANDARD, ORDER_FORMAT)
    if rule:
        assert any(x.rule_id == rule and x.status == S.REVIEW_REQUIRED for x in r.results)
    else:
        assert r.status == S.PASS


def test_panel_preview_selects_the_matching_table_row(tmp_path):
    s = Spec()
    pdf = build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    excel = build_inquiry(tmp_path / "order.xlsx", s)
    wb = openpyxl.load_workbook(excel)
    ws = wb.active
    ws.insert_rows(4)
    ws.cell(4, 1, "325199990002")
    ws.cell(4, ws.max_column, "HSBC")
    wb.save(excel)
    wb.close()
    session = PanelSession(REVIEW_STANDARD, ROOT / "config")
    session.select(excel, [pdf])
    preview = session.load_preview()
    assert preview.rows[0].product_code == s.code
    assert preview.rows[0].reference_row == 5
    assert session.start_check().batch.status == S.PASS


@pytest.mark.parametrize(
    "field,value,rule", [("ko_pct", 100.004, "field.ko_pct"), ("strike_pct", 70.004, "field.strike_pct")]
)
def test_threshold_percentages_are_not_rounded_to_two_places(tmp_path, field, value, rule):
    r = check(tmp_path, overrides={field: value})
    assert any(x.rule_id == rule and x.status == S.MISMATCH for x in r.results)


@pytest.mark.parametrize(
    "old,new,field",
    [
        ("到期贖回金額為美元10,000×100%=美元10,000.00", "", "principal_formula"),
        ("到期贖回金額為美元10,000×100%=美元10,000.00", "到期贖回金額為未知算式", "principal_formula"),
        ("假設標的（虛構標的1），執行價美元70.0000", "假設標的（虛構標的1），執行價未知", "reference_strike"),
        ("觸及不保本價格美元60.0000", "觸及不保本價格未知", "reference_ki"),
    ],
)
def test_each_required_scenario_formula_or_reference_must_be_present(tmp_path, old, new, field):
    r = check(tmp_path, Spec(replacements={old: new}))
    assert any(x.status == S.REVIEW_REQUIRED and field in x.field for x in r.results)


def test_daily_schedule_end_must_not_precede_start(tmp_path):
    s = Spec()
    # Third period's start follows period 2, but its end is moved before that start.
    s.replacements = {"2030 年4 月7 日": "2030 年3 月6 日"}
    r = check(tmp_path, s)
    assert any(x.rule_id == "schedule.autocall_dates" and x.status == S.MISMATCH for x in r.results)


@pytest.mark.parametrize("amount,status", [("25.00", S.PASS), ("26.00", S.MISMATCH)])
def test_partial_period_coupon_arithmetic(tmp_path, amount, status):
    s = Spec(partial_coupon=True)
    s.replacements = {
        "第3個計息期間配息金額=美元10,000×1.0000%×5/20=美元25.00": f"第3個計息期間配息金額=美元10,000×1.0000%×5/20=美元{amount}"
    }
    r = check(tmp_path, s)
    assert r.status == status
    assert any(
        x.rule_id == "doc.scenario_calculations"
        and "coupon_amount" in x.field
        and x.actual == Decimal(amount)
        and x.status == status
        for x in r.results
    )


@pytest.mark.parametrize("obs", ["D", "P"])
def test_hsbc_batch_backfills_shared_reference_sheet(tmp_path, obs):
    from fcn_checker.batch import run_batch

    s = Spec(obs=obs, ki="none")
    pdf = build_pdf(tmp_path / f"{s.code}_TS.pdf", s)
    excel = build_inquiry(tmp_path / "order.xlsx", s)
    wb = openpyxl.load_workbook(excel)
    ws = wb.active
    for c in ws[3]:
        if c.value == "ISIN Code" or str(c.value).startswith("比價日_"):
            ws.cell(4, c.column).value = None
    wb.save(excel)
    wb.close()
    r = run_batch(
        [pdf],
        excel,
        REVIEW_STANDARD,
        tmp_path / "reports",
        reference_format=ROOT / "config/reference_sheet.toml",
        issuer_prefixes=ROOT / "config/issuer_prefixes.toml",
    )
    assert r.status == S.PASS, [
        (x.rule_id, x.field, x.status, x.reason_code)
        for x in r.items[0].report.results
        if x.status not in (S.PASS, S.NOT_APPLICABLE)
    ]
    assert r.items[0].filled
    wb = openpyxl.load_workbook(r.output)
    ws = wb["樣本清單"]
    vals = {c.value: ws.cell(4, c.column).value for c in ws[3]}
    assert vals["ISIN Code"] == "XS1999900001"
    assert vals["比價日_2"].date() == s.ends[1]
    assert vals["比價日_6"] == ("-" if obs == "D" else dt.datetime.combine(s.ends[-1], dt.time()))
    assert vals["比價日_1"] == "-"
    wb.close()
    wb = openpyxl.load_workbook(excel)
    ws = wb.active
    assert all(
        ws.cell(4, c.column).value is None
        for c in ws[3]
        if c.value == "ISIN Code" or str(c.value).startswith("比價日_")
    )
    wb.close()
