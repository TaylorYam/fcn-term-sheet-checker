"""CA 黑箱回歸（Issue #274）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

from decimal import Decimal

import pytest

import bnp_synth
import citi_synth
import gs_synth
import hsbc_synth
import jpm_synth
import ms_synth
import natixis_synth
import nomura_synth
import sg_synth
import synth
from ca_synth import DIST, ISIN, PRODUCT_TYPE, WARNING, WARNING_CH1, Spec, check, check_item
from fcn_checker.issuers import CA
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

VARIANTS = {
    "KO 每日記憶式、Non-Call 1、無 KI、3 檔美股": Spec(),
    "KO 每期記憶式、無 KI": Spec(ko_obs="P"),
    "KO 每期非記憶式、單一標的、Non-Call = 天期": Spec(ko_obs="P", first_callable=6, memory=False, count=1),
    "KO 每日非記憶式、單一標的、KI 到期、歐元 12 個月、Non-Call 11": Spec(
        memory=False, count=1, ki="AM", first_callable=11, tenor=12, currency_zh="歐元"
    ),
    "KO 每日記憶式、KI 每日、5 檔日股、非整數 K %、VWAP": Spec(
        ki="D", market="JT", currency_zh="日圓", count=5, strike=Decimal("77.44"), pricing="vwap"
    ),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass_and_are_filled(tmp_path, spec):
    item = check_item(tmp_path, spec)
    r = item.report
    assert r.template == CA.template_id
    assert r.status == S.PASS, problems(r)
    assert item.fills_sheet
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in CA.not_covered}
    assert [x.status for x in rule(r, "derive.monthly_coupon")] == [S.PASS]
    assert len(rule(r, "doc.coupon_mentions")) == 2  # 最低保證配息率、情境基本假設配息率
    assert len(rule(r, "doc.autocall_terms")) == 2  # 最低保證配息率月數、情境一配息月數
    assert len(rule(r, "doc.isin_consistency")) == 2
    assert rule(r, "standard.fixed_warning")[0].actual == 3  # 封面、第一章第 2 項（去【】）、第三章
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS  # CA 的「受託投資」寫法都在允許片語內
    assert len(rule(r, "standard.issuer_name")) == 3  # 封面、第一章第 3 項、第二章
    skipped = ("standard.guarantor_name", "standard.agent_name", "standard.minimum_issue_size")  # 決定不核對
    assert not [x for x in r.results if x.rule_id in skipped]
    assert len(rule(r, "field.min_amounts")) == 3
    assert len(rule(r, "field.currency")) == 9  # 封面計價幣別＋金額旁的幣別字 8 處
    assert rule(r, "field.initial_pricing")[0].status == S.PASS  # 說明書沒寫：只檢查表上是允許值
    assert rule(r, "field.ki_type")[0].status == S.PASS


def test_vwap_prices_are_filled_from_the_term_sheet(tmp_path):
    spec = Spec(pricing="vwap")
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d for d in r.backfill}
    assert cells["UL_1_進場價"].expected == spec.underlyings[0].initial


def test_daily_autocall_dates_are_the_non_call_and_last_period_ends(tmp_path):
    spec = Spec(first_callable=2)
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d.expected for d in r.backfill if d.column.startswith("比價日_") and d.expected != "-"}
    assert cells == {"比價日_2": spec.ends[1], f"比價日_{spec.tenor}": spec.ends[-1]}


def test_sheet_terms_are_compared(tmp_path):
    r = check(tmp_path, Spec(), **{"KO(memo)": "N", "Non-Call(月)": 3, "KO(Freq)": "P", "Coupon p.a. (%)": 9.5})
    for rid in ("field.ko_memory", "field.first_callable_period", "field.ko_observation", "field.coupon_pa_pct"):
        assert rule(r, rid)[0].status == S.MISMATCH, rid


def test_ki_type_is_compared_with_the_sheet(tmp_path):
    r = check(tmp_path, Spec(ki="AM"), **{"KI(Freq)": "D"})
    assert rule(r, "field.ki_type")[0].status == S.MISMATCH


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (
            Edit("(4)最低保證配息率：第一個月配息 0.7500%", "(4)最低保證配息率：第一個月配息 0.7600%", "ts.art15"),
            "doc.coupon_mentions",
        ),
        (Edit("配息率：0.7500%(年化", "配息率：0.7600%(年化", "ts.art16"), "doc.coupon_mentions"),
        (Edit("第一個月配息", "前二個月配息", "ts.art15"), "doc.autocall_terms"),
        (Edit("• 第 1 個月配息金額", "• 第 1~2 個月配息金額", "ts.art16"), "doc.autocall_terms"),
        (Edit("天期：6 個月", "天期：7 個月", "ts.art16"), "doc.coupon_periods"),
        (Edit("70.3150 美元", "70.3151 美元", "ts.art16.table"), "doc.scenario_table"),
        (Edit("70.00% )", "75.00% )", "ts.art16.table"), "doc.scenario_table"),
        (Edit("每單位面額：10,000 美元", "每單位面額：1,000 美元", "ts.art16"), "doc.scenario_table"),
        (Edit("6 個月美元計價", "7 個月美元計價", "ts.art1"), "doc.name_consistency"),
        (Edit(f"(ISIN：{ISIN})", "(ISIN：XS1999900087)", "ts.cover"), "doc.isin_consistency"),
        (Edit(f"(ISIN：{ISIN} ) 。", "(ISIN：XS1999900087 ) 。", "ts.art1"), "doc.isin_consistency"),
        (Edit("5. 計價幣別：美元(USD)", "5. 計價幣別：美元(JPY)", "ts.art5"), "doc.currency_consistency"),
        (Edit("● 計價幣別：美元(USD)", "● 計價幣別：美元(JPY)", "ts.cover"), "doc.currency_consistency"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_internal_rules_find_the_wrong_repeat(tmp_path, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    assert broken in bad, problems(r)
    assert bad <= {broken, "standard.product_name"}, problems(r)


def test_coupon_rate_must_be_annual_over_twelve(tmp_path):
    """配息表配息率（全部期別）和年化配息率 ÷ 12 不符；其他出處仍等於原來的配息率，所以也不一致。"""
    r = check(tmp_path, edits=[Edit("0.7500%", "0.7600%", "ts.art9.coupon")])
    assert rule(r, "derive.monthly_coupon")[0].status == S.MISMATCH
    assert {x.status for x in rule(r, "doc.coupon_mentions")} == {S.MISMATCH}


def test_annual_coupon_must_match_the_coupon_table(tmp_path):
    """第 16 項年化配息率（說明書唯一的年利率）錯：配息率推算與參考條件表年利率都不一致。"""
    r = check(tmp_path, edits=[Edit("(年化配息率為9.00%)", "(年化配息率為9.12%)", "ts.art16")])
    assert rule(r, "derive.monthly_coupon")[0].status == S.MISMATCH
    assert rule(r, "field.coupon_pa_pct")[0].status == S.MISMATCH


def test_scenario_coupon_amount_is_in_the_product_currency(tmp_path):
    """情境一累積配息金額的幣別 = 承作幣別（2026-10-15 確認：配息金額仍 = 承作幣別）。"""
    r = check(tmp_path, edits=[Edit("累積配息金額 ＝75.00 美元", "累積配息金額 ＝75.00 日圓", "ts.art16")])
    assert rule(r, "field.currency", "scenario_coupon_currency")[0].status == S.MISMATCH


def test_last_payment_date_must_be_the_maturity_date(tmp_path):
    spec = Spec()
    old = f"(5) 到期日：{spec.date(spec.maturity_date)}"
    r = check(tmp_path, spec, edits=[Edit(old, "(5) 到期日：2031 年01 月20 日", "ts.art13")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_schedule_rows_must_equal_the_tenor(tmp_path):
    r = check(tmp_path, edits=[Edit("(1) 商品年期：6 個月", "(1) 商品年期：7 個月", "ts.art13")])
    assert rule(r, "doc.coupon_periods", "coupon_table")[0].status == S.MISMATCH


def test_daily_last_period_end_must_be_the_final_valuation_date(tmp_path):
    spec = Spec()
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[-1]), "2030 年07 月10 日", "ts.art9.coupon")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_periodic_last_observation_must_be_the_final_valuation_date(tmp_path):
    spec = Spec(ko_obs="P")
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[-1]), "2030 年07 月10 日", "ts.art9.ko")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_periodic_table_must_cover_non_call_to_the_last_period(tmp_path):
    """自動提前出場觀察日表第 2 列的出場日不在配息日表：KO 觀察、Non-Call、比價日轉人工覆核。"""
    spec = Spec(ko_obs="P")
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.payments[1]), "2030 年03 月20 日", "ts.art9.ko")])
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_ko_start_outside_the_schedule_needs_review(tmp_path):
    r = check(tmp_path, edits=[Edit("自第1 個配息期結束日", "自第9 個配息期結束日", "ts.art9")])
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_ki_definition_must_match_the_maturity_wording(tmp_path):
    """到期贖回寫「若未發生界限事件」卻沒有界限事件定義：KI 型態轉人工覆核。"""
    edit = Edit("界限事件：若於期末訂價日，依計算代理機構決定，連結標的之收盤價低於下限價。", "", "ts.art9")
    r = check(tmp_path, Spec(ki="AM", count=1, memory=False), edits=[edit])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_multi_underlying_without_memory_wording_needs_review(tmp_path):
    """多標的又沒有記憶式兩句：沒有樣本，轉人工覆核（2026-10-15 確認）。"""
    r = check(tmp_path, Spec(memory=False))
    assert {(rid, f) for rid, f, _ in problems(r)} == {("doc.ko_memory", "ko_memory")}


def test_only_one_memory_sentence_needs_review(tmp_path):
    edit = Edit("為避免疑義，所有連結標的無須於同一自動提前出場觀察日符合上開條件。", "", "ts.art9")
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.ko_memory")[0].status == S.REVIEW_REQUIRED


def test_price_table_rows_must_match_the_ticker_table(tmp_path):
    r = check(tmp_path, edits=[Edit("ZCC UW", "", "ts.art10.table")])
    assert rule(r, "field.underlyings")[0].status == S.REVIEW_REQUIRED


# ---------------------------------------------------------------- 審查標準（CA 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit(WARNING[:20], WARNING[:19] + "X", "ts.ch三"), "standard.fixed_warning"),
        (Edit(WARNING_CH1[:20], WARNING_CH1[:19] + "X", "ts.art2"), "standard.fixed_warning"),
        (Edit(PRODUCT_TYPE, "股權連結結構型債券", "ts.cover"), "standard.product_type"),
        (
            Edit(
                "(1) 事業名稱：Crédit Agricole CIB Financial Solutions", "(1) 事業名稱：Crédit Agricole CIB", "ts.ch二"
            ),
            "standard.issuer_name",
        ),
        (Edit("申購價金的0%~5%", "申購價金的0%~3%", "ts.ch四"), "standard.fees"),
        (Edit(f"地址：{DIST['address']}", "地址：台北市虛構路2號", "ts.cover"), "standard.distributor"),
        (Edit(f"(d) 負責人姓名：{DIST['chairman']}", "(d) 負責人姓名：林晉輝", "ts.ch二"), "standard.chairman"),
        (Edit("2026 年06 月11 日", "2025 年12 月18 日", "ts.cover"), "standard.approval_date"),
        (Edit("最低申購金額： 10,000 美元", "最低申購金額： 50,000 美元", "ts.ch四"), "field.min_amounts"),
        (Edit("商品面額的100%", "商品面額的99%", "ts.art6"), "standard.issue_price"),
        (Edit("Autocall Note (Unsecured)", "Autocall Note (Secured)", "ts.cover"), "standard.product_name"),
        (
            Edit("受託投資，受託買賣或為投資型保單", "受託投資之投資標的或為投資型保單", "ts.warn"),
            "standard.forbidden_wording",
        ),
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
    ],
    ids=["BARC", "HSBC", "MS", "BNP", "SG", "Citi", "JPM", "Nomura", "GS", "Natixis"],
)
def test_other_issuers_are_not_ca(tmp_path, build):
    pdf = build(tmp_path / "086199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    for item in check_all(sheet, [pdf]).items:
        assert item.report.template not in (CA.template_id, CA.iis.template_id)


def test_chapter_seven_title_is_required(tmp_path):
    item = check_item(tmp_path, edits=[Edit("第七章、股權連結商品的額外條款及條件", "第七章、其他", "ts.ch七")])
    assert item.report.template != CA.template_id


def test_initial_price_wrapped_onto_two_lines_stays_in_its_row(tmp_path):
    """價格表第 1 檔期初訂價的幣別字換到下一行（`1,029.2100 美` / `元`，Issue #278）：第 9 項 (2) 兩行垂直置中、
    第 16 項情境表數字高一行；第一行都比同列其他價格高，仍歸到同一列，價格照常讀到並通過。"""
    item = check_item(tmp_path, Spec(wrap_initial=True))
    assert item.report.status == S.PASS, problems(item.report)
    assert item.fills_sheet
