"""DBS 黑箱回歸（Issue #280）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

from decimal import Decimal

import pytest

import bbva_synth
import bnp_synth
import ca_synth
import citi_synth
import gs_synth
import hsbc_synth
import jpm_synth
import ms_synth
import natixis_synth
import nomura_synth
import sg_synth
import synth
from dbs_synth import DIST_ADDRESS, PRODUCT_TYPE, WARNING, Spec, check, check_item
from fcn_checker.issuers import DBS
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

VARIANTS = {
    "美元、4 檔、無 KI、Non-Call 1": Spec(),
    "澳幣 Quanto、KI 到期、Non-Call 2": Spec(currency_zh="澳幣", ki="AM", first_callable=2),
    "日圓 Quanto 9 個月、3 檔、KI 到期、非整數 K %": Spec(
        currency_zh="日圓", tenor=9, count=3, ki="AM", strike=Decimal("79.32")
    ),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass_and_are_filled(tmp_path, spec):
    item = check_item(tmp_path, spec)
    r = item.report
    assert r.template == DBS.template_id
    assert r.status == S.PASS, problems(r)
    assert item.fills_sheet
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in DBS.not_covered}
    assert [x.status for x in rule(r, "derive.monthly_coupon")] == [S.PASS]
    assert len(rule(r, "doc.coupon_mentions")) == 2  # 情境最大利率值、情境二年化報酬率
    assert len(rule(r, "doc.autocall_terms")) == 4  # 配息第1~k、情境第1~k、情境 KO 決定日、情境一出場期
    assert rule(r, "standard.fixed_warning")[0].actual == 2  # 封面警語 1.、商品主要風險揭露 五、1.
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS
    assert len(rule(r, "standard.issuer_name")) == 2  # 封面、相關機構事業概況 1.(1)
    assert [x.status for x in rule(r, "standard.product_name")] == [S.PASS, S.PASS]
    skipped = ("standard.guarantor_name", "standard.agent_name", "standard.minimum_issue_size")
    assert not [x for x in r.results if x.rule_id in skipped]
    assert len(rule(r, "field.min_amounts")) == 4
    assert len(rule(r, "field.currency")) == 6  # 封面計價幣別＋金額旁的幣別字 5 處
    assert rule(r, "field.initial_pricing")[0].status == S.PASS  # 說明書沒寫：只檢查表上是允許值
    assert rule(r, "field.ki_type")[0].status == S.PASS


def test_autocall_dates_are_the_non_call_and_last_period_ends(tmp_path):
    spec = Spec(first_callable=2)
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d.expected for d in r.backfill if d.column.startswith("比價日_") and d.expected != "-"}
    assert cells == {"比價日_2": spec.ends[1], f"比價日_{spec.tenor}": spec.ends[-1]}


def test_sheet_terms_are_compared(tmp_path):
    r = check(tmp_path, Spec(), **{"Non-Call(月)": 3, "Coupon p.a. (%)": 10.5, "K(%)": 75})
    for rid in ("field.first_callable_period", "field.coupon_pa_pct", "field.strike_pct"):
        assert rule(r, rid)[0].status == S.MISMATCH, rid


def test_ki_type_is_compared_with_the_sheet(tmp_path):
    r = check(tmp_path, Spec(ki="AM"), **{"KI(Freq)": "-", "KI(%)": "-"})
    assert rule(r, "field.ki_type")[0].status == S.MISMATCH


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("最大利率值：0.8334%", "最大利率值：0.8335%", "ts.scenario"), "doc.coupon_mentions"),
        (Edit("年化平均報酬率約為10.00%", "年化平均報酬率約為10.50%", "ts.scenario"), "doc.coupon_mentions"),
        (Edit("對於第1個觀察期間而言", "對於第1~2個觀察期間而言", "ts.basic.coupon"), "doc.autocall_terms"),
        (Edit("利率：第1個觀察期間之利率", "利率：第1~2個觀察期間之利率", "ts.scenario"), "doc.autocall_terms"),
        (Edit("自動提前出場決定日：從第1個", "自動提前出場決定日：從第2個", "ts.scenario"), "doc.autocall_terms"),
        (Edit("於第2個觀察期間的第2個觀察日", "於第3個觀察期間的第2個觀察日", "ts.scenario"), "doc.autocall_terms"),
        (Edit("星展銀行6個月期", "星展銀行7個月期", "ts.basic"), "doc.name_consistency"),
        (
            Edit("受託或銷售機構商品代號：078199990001", "受託或銷售機構商品代號：078199990002", "ts.cover"),
            "doc.product_code_consistency",
        ),
        (Edit("美元(USD)", "美元(JPY)", "ts.basic"), "doc.currency_consistency"),
        (Edit("計價幣別：美元(USD)", "計價幣別：美元(JPY)", "ts.cover"), "doc.currency_consistency"),
        (Edit("假設：商品年期：6個月", "假設：商品年期：7個月", "ts.scenario"), "doc.coupon_periods"),
        (Edit("70.3150", "70.3151", "ts.scenario.table"), "doc.scenario_table"),
        (Edit("70.00%）", "75.00%）", "ts.scenario.table"), "doc.scenario_table"),
        (Edit("商品面額 = 美元10,000", "商品面額 = 美元1,000", "ts.scenario"), "doc.scenario_table"),
        (Edit("發行價格 = 100.00%", "發行價格 = 99.00%", "ts.scenario"), "doc.scenario_table"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_internal_rules_find_the_wrong_repeat(tmp_path, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    assert broken in bad, problems(r)
    assert bad <= {broken, "standard.product_name"}, problems(r)


def test_max_rate_rounds_up(tmp_path):
    """最大利率值寫成四捨五入（10.00 ÷ 12 = 0.8333…，應無條件進位為 0.8334）：推算與情境出處都不一致。"""
    r = check(tmp_path, edits=[Edit("為0.8334%（即", "為0.8333%（即", "ts.basic.coupon")])
    assert rule(r, "derive.monthly_coupon")[0].status == S.MISMATCH
    assert rule(r, "doc.coupon_mentions", "scenario_monthly_pct")[0].status == S.MISMATCH


def test_annual_coupon_must_match_the_max_rate(tmp_path):
    """括號內年利率（說明書唯一的年利率）錯：最大利率值推算與參考條件表年利率都不一致。"""
    r = check(tmp_path, edits=[Edit("（即10.00%/12）", "（即10.12%/12）", "ts.basic.coupon")])
    assert rule(r, "derive.monthly_coupon")[0].status == S.MISMATCH
    assert rule(r, "field.coupon_pa_pct")[0].status == S.MISMATCH


def test_last_table_a_row_must_be_the_final_valuation_date(tmp_path):
    spec = Spec()
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[-1]), "2030年07月19日", "ts.basic.dates.table")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_table_a_rows_must_equal_the_tenor(tmp_path):
    r = check(tmp_path, edits=[Edit("商品年期： 6個月。", "商品年期： 7個月。", "ts.basic.dates")])
    assert rule(r, "doc.coupon_periods", "coupon_table")[0].status == S.MISMATCH


def test_ko_start_outside_the_schedule_needs_review(tmp_path):
    r = check(tmp_path, edits=[Edit("自動提前出場決定日: 從第1個", "自動提前出場決定日: 從第9個", "ts.basic.ko")])
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_non_memory_wording_needs_review(tmp_path):
    """沒有「記憶事件」定義（非記憶式是不支援樣態）：記憶式轉人工覆核。"""
    edit = Edit("記憶事件：就每一連結標的而言", "觀察事件：就每一連結標的而言", "ts.basic.ko")
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.ko_memory")[0].status == S.REVIEW_REQUIRED


def test_ki_definition_must_match_the_maturity_wording(tmp_path):
    """到期 (a) 寫「如果未發生下限觸發事件」卻沒有下限觸發事件定義：KI 型態轉人工覆核。"""
    edit = Edit(
        "下限觸發事件：若任一連結標的於定價日的參考價格小於其下限觸發價格，則視為發生下限觸發事件。",
        "",
        "ts.basic.maturity",
    )
    r = check(tmp_path, Spec(ki="AM"), edits=[edit])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_price_table_tickers_must_match_the_ticker_table(tmp_path):
    r = check(tmp_path, edits=[Edit("ZDC UW", "ZDX UW", "ts.basic.table")])
    assert rule(r, "field.underlyings")[0].status == S.REVIEW_REQUIRED


def test_minimum_units_must_match_the_amount(tmp_path):
    edit = Edit("最低申購金額為5單位商品面額，即美元50,000", "最低申購金額為5單位商品面額，即美元60,000", "ts.trading")
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.min_amounts", "minimum_subscription")[0].status == S.REVIEW_REQUIRED


def test_minimum_units_follow_the_currency(tmp_path):
    """最低申購單位數依幣別（USD 5）：寫 7 單位判不一致。"""
    edit = Edit("最低申購金額為5單位商品面額，即美元50,000", "最低申購金額為7單位商品面額，即美元70,000", "ts.trading")
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.min_amounts", "minimum_subscription")[0].status == S.MISMATCH


# ---------------------------------------------------------------- 名稱樣板（Quanto、KI 段、單一標的）


def test_quanto_must_appear_when_the_quote_currency_differs(tmp_path):
    r = check(tmp_path, Spec(currency_zh="澳幣"), edits=[Edit("澳幣計價Quanto", "澳幣計價", "ts.cover")])
    assert rule(r, "standard.product_name", "name_zh")[0].status == S.MISMATCH


def test_ki_segment_must_follow_the_ki_type(tmp_path):
    edit = Edit("自動提前出場一籃子", "自動提前出場及定價日觸發(歐式)一籃子", "ts.cover")
    r = check(tmp_path, edits=[edit])
    assert rule(r, "standard.product_name", "name_zh")[0].status == S.MISMATCH


def test_single_underlying_name_needs_review(tmp_path):
    """單一標的沒有樣本，名稱寫法未知：中英文名稱都轉人工覆核，其他照常（2026-10-15 確認）。"""
    r = check(tmp_path, Spec(count=1))
    names = rule(r, "standard.product_name")
    assert [x.status for x in names] == [S.REVIEW_REQUIRED, S.REVIEW_REQUIRED]
    assert {x.reason_code for x in names} == {"variant_not_covered"}
    assert {rid for rid, _, _ in problems(r)} == {"standard.product_name"}


# ---------------------------------------------------------------- 審查標準（DBS 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit(WARNING[:20], WARNING[:19] + "X", "ts.risk"), "standard.fixed_warning"),
        (Edit("(下稱「OSU相關規範」)", "", "ts.warn"), "standard.fixed_warning"),
        (Edit(PRODUCT_TYPE, "股權連結結構型商品", "ts.cover"), "standard.product_type"),
        (
            Edit("(1) 事業名稱：新加坡商星展銀行股份有限公司(DBS Bank Ltd)", "(1) 事業名稱：星展銀行", "ts.org"),
            "standard.issuer_name",
        ),
        (Edit("申購價金的", "申購價金的3%~", "ts.trading.fees"), "standard.fees"),
        (Edit(f"地址：{DIST_ADDRESS}", "地址：台北市虛構路2號", "ts.cover"), "standard.distributor"),
        (Edit("電話：(02)5556-1313", "電話：(02)5556-1314", "ts.cover"), "standard.distributor"),
        (Edit("(4) 負責人姓名：林晋輝", "(4) 負責人姓名：林晉輝", "ts.org"), "standard.chairman"),
        (Edit("2026年6月11日", "2025年12月18日", "ts.cover"), "standard.approval_date"),
        (Edit("商品面額的100.00%", "商品面額的99.00%", "ts.basic"), "standard.issue_price"),
        (Edit("商品風險程度：", "商品風險程度：RR5", "ts.basic"), "standard.risk_level"),
        (Edit("受託投資或受託買賣，投資人", "受託投資，投資人", "ts.warn"), "standard.forbidden_wording"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:12],
)
def test_review_standard_items(tmp_path, edit, broken):
    r = check(tmp_path, edits=[edit])
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


# ---------------------------------------------------------------- 範本辨識


@pytest.mark.parametrize(
    "build",
    [
        lambda p: synth.build_pdf(p, synth.Spec()),
        lambda p: hsbc_synth.build_pdf(p, hsbc_synth.Spec()),
        lambda p: ms_synth.build_pdf(p, ms_synth.Spec()),
        lambda p: bnp_synth.build_pdf(p, bnp_synth.Spec()),
        lambda p: sg_synth.build_pdf(p, sg_synth.Spec()),
        lambda p: citi_synth.build_pdf(p, citi_synth.Spec()),
        lambda p: jpm_synth.build_pdf(p, jpm_synth.Spec()),
        lambda p: nomura_synth.build_pdf(p, nomura_synth.Spec()),
        lambda p: gs_synth.build_pdf(p, gs_synth.Spec()),
        lambda p: natixis_synth.build_pdf(p, natixis_synth.Spec()),
        lambda p: ca_synth.build_pdf(p, ca_synth.Spec()),
        lambda p: bbva_synth.build_pdf(p, bbva_synth.Spec()),
    ],
    ids=["BARC", "HSBC", "MS", "BNP", "SG", "Citi", "JPM", "Nomura", "GS", "Natixis", "CA", "BBVA"],
)
def test_other_issuers_are_not_dbs(tmp_path, build):
    pdf = build(tmp_path / "078199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    for item in check_all(sheet, [pdf]).items:
        assert item.report.template not in (DBS.template_id, DBS.iis.template_id)


def test_draft_title_is_not_recognised(tmp_path):
    """標題不是「中文產品說明書(最終版本)」：沒有樣本的版本，範本不符。"""
    item = check_item(tmp_path, edits=[Edit("中文產品說明書(最終版本)", "中文產品說明書(初步版本)", "ts.cover")])
    assert item.report.template != DBS.template_id
