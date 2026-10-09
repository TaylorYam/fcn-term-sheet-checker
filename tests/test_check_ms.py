"""MS 黑箱回歸（Issue #135）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import hsbc_synth
import synth
from fcn_checker.backfill import BackfillAction
from fcn_checker.issuers import MS
from fcn_checker.messages import problem_message
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from ms_synth import Spec, build_pdf, check, reference_row
from pdf_writer import zh_date
from reference_synth import build_reference_sheet

PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


def problems(report) -> list[tuple[str, str, S]]:
    return [(r.rule_id, r.field, r.status) for r in report.results if r.status in PROBLEMS]


def rule(report, rule_id, field=None):
    out = [r for r in report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert out, f"沒有 {rule_id} {field or ''} 的結果"
    return out


def flagged(report, rule_id, status=None) -> bool:
    return any(r.rule_id == rule_id and r.status in ((status,) if status else PROBLEMS) for r in report.results)


@pytest.mark.parametrize(
    "spec",
    [
        Spec(obs="D", memory=True, ki="AM", count=2),
        Spec(obs="D", memory=True, ki="D", count=4),
        Spec(obs="D", memory=True, ki="P", count=3),
        Spec(obs="D", memory=True, ki="none", count=4, tenor=3),
        Spec(obs="D", memory=False, ki="AM", count=1),
        Spec(obs="D", memory=True, ki="AM", count=3, tenor=12, annual=Decimal("15")),
        Spec(obs="P", memory=True, ki="AM", count=4),
        Spec(obs="P", memory=True, ki="AM", count=2, non_call=2),
        Spec(obs="P", memory=False, ki="none", count=4, tenor=6, non_call=6),
        Spec(obs="P", memory=False, ki="AM", count=1, tenor=6, non_call=6),
    ],
    ids=lambda s: f"{s.obs}-{'mem' if s.memory else 'plain'}-KI{s.ki}-{s.count}UL-{s.tenor}m-NC{s.non_call}",
)
def test_supported_types_pass(tmp_path, spec):
    r = check(tmp_path, spec)
    assert r.template == MS.template_id
    assert r.status == S.PASS, problems(r)
    ms_rules = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert ms_rules and all(x.document_evidence for x in ms_rules if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in MS.not_covered}
    # 年利率由 MS 專屬規則核對（說明書沒有年利率）；受理申購日範本沒有，不核對
    assert rule(r, "field.coupon_pa_pct")[0].status == S.NOT_APPLICABLE
    assert not [x for x in r.results if x.rule_id == "doc.subscription_start_date"]
    assert {x.status for x in rule(r, "derive.monthly_coupon")} == {S.PASS}


def test_monthly_kept_annual_and_scenario_annualized_follow_the_sheet(tmp_path):
    r = check(tmp_path, Spec(annual=Decimal("15.8")))  # 15.8 ÷ 12 = 1.31666… → 1.3167
    assert r.status == S.PASS, problems(r)
    assert rule(r, "derive.monthly_coupon")[0].expected == Decimal("1.3167")
    assert all(x.actual == Decimal("15.80") for x in rule(r, "derive.annualized_return"))


@pytest.mark.parametrize(
    "column,value,rule_id",
    [
        ("Coupon p.a. (%)", 12.12, "derive.monthly_coupon"),
        ("Coupon p.a. (%)", 12.12, "derive.annualized_return"),
        ("KO(%)", 101, "field.ko_pct"),
        ("K(%)", 71, "field.strike_pct"),
        ("KI(%)", 61, "field.ki_pct"),
        ("KI(Freq)", "D", "field.ki_type"),
        ("KI(Freq)", "P", "field.ki_type"),
        ("KO(Freq)", "P", "field.ko_observation"),
        ("KO(memo)", "N", "field.ko_memory"),
        ("天期(月)", 5, "field.tenor_months"),
        ("Non-Call(月)", 2, "field.first_callable_period"),
        ("交易日", dt.datetime(2030, 1, 8), "field.trade_date"),
        ("最終比價日", dt.datetime(2030, 5, 15), "field.final_valuation_date"),
        ("到期日", dt.datetime(2030, 5, 21), "field.maturity_date"),
        ("單位面額", 20000, "field.denomination"),
        ("單位面額", 20000, "field.min_amounts"),
        ("承作幣別", "JPY", "field.currency"),
        ("UL_1", "ZZ9 UW", "field.underlyings"),
        ("UL_1_執行價", 71, "field.underlying_prices"),
        ("UL_2_KO價", 201, "field.underlying_prices"),
        ("ISIN Code", "XS1999900002", "backfill.isin"),
        ("發行日", dt.datetime(2030, 1, 15), "backfill.issue_date"),
        ("比價日_2", dt.datetime(2030, 3, 14), "backfill.compare_dates"),
    ],
)
def test_sheet_difference_is_reported(tmp_path, column, value, rule_id):
    assert flagged(check(tmp_path, **{column: value}), rule_id)


def test_issuer_column_must_be_ms(tmp_path):
    r = check(tmp_path, **{"發行機構": "HSBC"})
    assert r.status == S.REVIEW_REQUIRED and not [x for x in r.results if x.rule_id.startswith("doc.")]


S0 = Spec()  # 預設：D 型、記憶式、到期 KI、2 檔、4 個月、Non-Call 1


@pytest.mark.parametrize(
    "replace,rule_id,status",
    [
        # 月配息率與年化報酬率：正式條款正確、重印處錯誤也要抓到（核對規則 §3.8）
        (("art15", "固定配息率 (1.0000%)", "固定配息率 (1.0100%)"), "derive.monthly_coupon", S.MISMATCH),
        (("art15", "{1.0000%×n(j)/N(j)}", "{1.0100%×n(j)/N(j)}"), "derive.monthly_coupon", S.MISMATCH),
        (("art18", "月配息率=1.0000%", "月配息率=1.0100%"), "derive.monthly_coupon", S.MISMATCH),
        (("scen.二", "×1.0000%", "×1.0100%"), "derive.monthly_coupon", S.MISMATCH),
        (("scen.一", "年化報酬率為12.00%", "年化報酬率為12.10%"), "derive.annualized_return", S.MISMATCH),
        (("scen.三", "年化報酬率為12.00%", "報酬率另計"), "derive.annualized_return", S.REVIEW_REQUIRED),
        # 封面與第一章交叉驗證
        (("cover", "商品種類：股票與/或", "商品種類：股票或"), "doc.product_type", S.MISMATCH),
        (("art1", "固定配息", "固定收益"), "doc.name_consistency", S.MISMATCH),
        (("art5", "計價幣別：美元", "計價幣別：日幣"), "doc.currency_consistency", S.MISMATCH),
        (("art15", "j 係為2 至4", "j 係為1 至4"), "doc.coupon_periods", S.MISMATCH),
        (("cover", "發行4個月期", "發行5個月期"), "doc.coupon_periods", S.MISMATCH),
        (("art16.table", "ZZ2 UW", "ZZ3 UW"), "doc.underlying_tickers", S.MISMATCH),
        # 日期表與提前出場
        (("art14.table", zh_date(S0.starts[1]), "2030 年2 月21 日"), "schedule.period_starts", S.MISMATCH),
        (("art14.table", zh_date(S0.starts[0]), "2030 年1 月15 日"), "schedule.period_starts", S.MISMATCH),
        (("art14.table", zh_date(S0.payments[-1]), "2030 年6 月20 日"), "schedule.coupon_dates", S.MISMATCH),
        (("art14.table", zh_date(S0.payments[1]), "2030 年3 月13 日"), "schedule.coupon_dates", S.MISMATCH),
        (
            ("art17", f"終止日（{zh_date(S0.ends[0])}）", f"終止日（{zh_date(S0.ends[1])}）"),
            "schedule.autocall_dates",
            S.MISMATCH,
        ),
        (
            ("art17", f"期末定價日（{zh_date(S0.ends[-1])}）", "期末定價日（2030 年5 月16 日）"),
            "schedule.autocall_dates",
            S.MISMATCH,
        ),
        # 價格表
        (("art16.table", "60.00%）", "61.00%）"), "derive.prices", S.MISMATCH),
        (("art16.table", "140.0000", "140.0001"), "derive.prices", S.MISMATCH),
        (("art16", "若在期末定價日", "若在每個觀察日"), "field.ki_type", S.REVIEW_REQUIRED),
        # 第 18 項情境（§16 正確、§18 重印錯）
        (("art18.table", "70.0000", "70.0001"), "doc.scenario_table", S.MISMATCH),
        (("art18.table", "60.00%）", "61.00%）"), "doc.scenario_header_pct", S.MISMATCH),
        (("scen.四", "小於其執行價=70.0000", "小於其執行價=70.0001"), "doc.scenario_strike", S.MISMATCH),
        (("art18", "年期：4 個月", "年期：5 個月"), "doc.scenario_parameters", S.MISMATCH),
        (("art18", "面額 = 10,000.00 美元", "面額 = 20,000.00 美元"), "doc.scenario_parameters", S.MISMATCH),
        (("scen.二", "=200.00 美元", "=201.00 美元"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.二", "100.00 美元×2", "100.00 美元×3"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.二", "=100.00 美元", "=101.00 美元"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.四", "=-3,600.00 美元", "=-3,500.00 美元"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.四", "100.00 美元×4+", "100.00 美元×3+"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.四", "較差情況", "保守情況"), "doc.scenario_parameters", S.REVIEW_REQUIRED),
        # 第四章
        (
            ("ch四", "受理贖回日期：2030 年1 月15 日", "受理贖回日期：2030 年1 月16 日"),
            "doc.redemption_start_date",
            S.MISMATCH,
        ),
        (("ch四", "商品面額的 100%", "商品面額的 99%"), "standard.issue_price", S.REVIEW_REQUIRED),
        (
            ("ch四", "最低贖回金額為1 單位商品面額，即美元10,000", "最低贖回金額為1 單位商品面額，即美元20,000"),
            "field.min_amounts",
            S.MISMATCH,
        ),
        (
            ("ch四", "10,000.00元(1 單位商品面額)為累加", "20,000.00元(1 單位商品面額)為累加"),
            "field.min_amounts",
            S.MISMATCH,
        ),
        (("ch四", "0%~5%", "0%~6%"), "standard.fees", S.MISMATCH),
        # 審查標準（MS 版本）
        (("ch三", "歸類為RR4", "歸類為【RR4】"), "standard.fixed_warning", S.MISMATCH),
        (("ch三", "歸類為RR4", "歸類為RR3"), "standard.risk_level", S.MISMATCH),
        (("art3", "發行機構為英商摩根士丹利國際", "發行機構為英商摩根士丹利證券"), "standard.issuer_name", S.MISMATCH),
        (("ch二", "International Plc，係依", "International plc，係依"), "standard.issuer_name", S.MISMATCH),
        (("cover", "電話: +886 2 5556 1313", "電話: +886 2 5556 1314"), "standard.distributor", S.MISMATCH),
        (("cover", "連結一籃子股票與/或", "連結股票或"), "standard.product_name", S.MISMATCH),
        (("cover", "Worst of Shares", "Shares"), "standard.product_name", S.MISMATCH),
    ],
)
def test_document_difference_is_reported(tmp_path, replace, rule_id, status):
    r = check(tmp_path, S0, pdf_spec=Spec(replace=[replace]))
    assert any(x.rule_id == rule_id and x.status == status for x in r.results), problems(r)


@pytest.mark.parametrize(
    "replace",
    [
        ("art1", "結構型商品(無保證機構)", "結構型商品（無保證機構）"),  # 括號全半形不計
        ("art2", "本商品風險程度為RR4。", "本商品風險程度等級為RR4。"),  # 第一章第 2 項兩種開頭都視為正確
        ("ch二", "International Plc，係依", "International Plc.，係依"),  # 英文名結尾句點不計
        ("ch二", "股份有限公司Morgan", "股份有限公司（Morgan"),  # 括號不計
    ],
)
def test_allowed_variants_still_pass(tmp_path, replace):
    r = check(tmp_path, S0, pdf_spec=Spec(replace=[replace]))
    assert r.status == S.PASS, problems(r)


def test_fixed_warning_counts_both_openings_and_risk_level_without_brackets(tmp_path):
    r = check(tmp_path)
    warning, level = rule(r, "standard.fixed_warning")[0], rule(r, "standard.risk_level")[0]
    assert (warning.status, warning.actual) == (S.PASS, 3)
    assert level.status == S.PASS and level.actual == ["RR4"]
    names = {x.field: x for x in rule(r, "standard.issuer_name")}
    assert set(names) == {"issuer_name_cover", "issuer_name_ch2", "issuer_name_ch1"}
    assert all(x.status == S.PASS for x in names.values())


def test_blank_trustee_product_code_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(trustee_code=""))
    x = rule(r, "doc.trustee_product_code")[0]
    assert x.status == S.MISMATCH and x.actual == "（空白）"
    assert "受託機構商品代號" in problem_message(x) and x.document_evidence


def test_non_call_equal_to_tenor_without_ko_column_marks_ko_fields_not_applicable(tmp_path):
    spec = Spec(obs="P", memory=False, ki="none", count=2, tenor=6, non_call=6)
    r = check(tmp_path, spec, **{"KO(%)": 150, "UL_1_KO價": 150, "UL_2_KO價": 300})
    assert r.status == S.PASS, problems(r)
    ko = rule(r, "field.ko_pct")[0]
    assert ko.status == S.NOT_APPLICABLE and "沒有自動提前出場價" in ko.message
    kos = [x for x in rule(r, "field.underlying_prices") if x.item.name.endswith("KO價")]
    assert len(kos) == 2 and all(x.status == S.NOT_APPLICABLE and "沒有自動提前出場價" in x.message for x in kos)
    assert all(x.status == S.PASS for x in rule(r, "field.underlying_prices") if "執行價" in x.item.name)


def test_ko_column_missing_before_maturity_requires_review(tmp_path):
    r = check(tmp_path, S0, pdf_spec=Spec(ko_column=False))
    assert rule(r, "field.ko_pct")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "doc.ko_column")[0].status == S.REVIEW_REQUIRED


def test_vwap_without_ko_column_leaves_ko_price_cells_alone(tmp_path):
    spec = Spec(obs="P", memory=False, ki="none", count=2, tenor=6, non_call=6)
    r = check(tmp_path, spec, **{"期初定價": "VWAP", "UL_1_KO價": 150})
    assert r.status == S.PASS, problems(r)
    columns = {d.column for d in r.backfill}
    assert "UL_1_進場價" in columns and "UL_1_KO價" not in columns and "UL_2_KO價" not in columns
    assert "UL_3_KO價" in columns  # 說明書沒有的標的仍寫空值寫法


def test_observation_wording_must_match_the_date_table(tmp_path):
    d_sentence = f"記憶事件觀察日：每日觀察，為自第1 個配息週期終止日（{zh_date(S0.ends[0])}）"
    r = check(
        tmp_path,
        S0,
        pdf_spec=Spec(replace=[("art17", d_sentence, "記憶事件觀察日：每一個定價日自第1 個定價日開始觀察（")]),
    )
    assert rule(r, "doc.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED


def test_memory_in_name_must_match_article_17(tmp_path):
    plain = [
        (
            "art17",
            "自動提前出場事件：若於記憶事件觀察日所有連結標的皆發生記憶事件成為自動提前出場標的，",
            "自動提前出場事件：若於任一觀察日所有連結標的之收盤價皆等於或高於其自動提前出場價，",
        ),
        ("art17", "記憶事件：任何連結標的於記憶事件觀察日之收盤價大於或等於其自動提前出場價，則發生記憶事件。", ""),
        ("art17", "記憶事件觀察日：每日觀察", "觀察日：每日觀察"),
    ]
    r = check(tmp_path, S0, pdf_spec=Spec(replace=plain))
    assert rule(r, "doc.ko_memory")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.ko_memory")[0].status == S.MISMATCH


def test_periodic_autocall_dates_follow_non_call(tmp_path):
    spec = Spec(obs="P", count=2, non_call=2)
    r = check(
        tmp_path,
        spec,
        pdf_spec=Spec(obs="P", count=2, non_call=2, replace=[("art14.table", "無", zh_date(spec.payments[0]))]),
    )
    assert rule(r, "schedule.autocall_dates")[0].status == S.MISMATCH


def test_tables_split_across_pages_are_still_read(tmp_path):
    r = check(tmp_path, Spec(count=3, breaks={"date": 2, "price": 1}))
    assert r.status == S.PASS, problems(r)


def test_old_template_is_not_recognised(tmp_path):
    old = Spec(
        replace=[
            ("cover", "International Plc issuance", "International plc issuance"),
            ("art1", "商品中文名稱：", "商品名稱："),
        ]
    )
    r = check(tmp_path, S0, pdf_spec=old)
    x = rule(r, "template.detect")[0]
    assert (x.status, x.reason_code) == (S.REVIEW_REQUIRED, "template_unknown")
    assert "MS：" in x.message and "舊版範本不支援" in x.message


def test_ms_and_other_issuers_are_never_confused(tmp_path):
    """MS 說明書只符合 MS 範本；BARC、HSBC 說明書也不符合 MS 範本（檔名上手編號故意給錯）。"""
    ms = build_pdf(tmp_path / "029199990004_TS.pdf", Spec(code="029199990004"))
    barc = synth.build_pdf(tmp_path / "147199990002_TS.pdf", synth.Spec(product_code="147199990002"))
    hsbc = hsbc_synth.build_pdf(tmp_path / "147199990003_TS.pdf", hsbc_synth.Spec(code="147199990003"))
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    outcome = check_all(sheet, [ms, barc, hsbc])
    detected = {
        i.term_sheet.name: next(r for r in i.report.results if r.rule_id == "template.detect")
        for i in outcome.items
        if i.term_sheet.name.endswith("_TS.pdf")
    }
    assert {name: (r.status, r.actual) for name, r in detected.items()} == {
        ms.name: (S.PASS, "ms-zh-pd"),
        barc.name: (S.PASS, "barc-zh-pd"),
        hsbc.name: (S.PASS, "hsbc-zh-pd"),
    }
    assert all(i.report.status == S.REVIEW_REQUIRED for i in outcome.items)  # 內容上手與檔名不符


def test_ms_investor_sheet_is_unsupported_so_nothing_is_filled(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    outcome = check_all(sheet, [pdf])
    ts, iis = sorted(outcome.items, key=lambda i: i.term_sheet.name.endswith("_IIS.pdf"))
    assert ts.report.status == S.PASS and iis.unsupported
    assert not ts.fills_sheet and ts.not_filled_reason
    assert all(d.action in (BackfillAction.FILL, BackfillAction.MATCH) for d in ts.report.backfill)
