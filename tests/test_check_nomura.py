"""Nomura 黑箱回歸（Issue #239）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import bnp_synth
import citi_synth
import hsbc_synth
import jpm_synth
import ms_synth
import sg_synth
import synth
from fcn_checker.issuers import NOMURA
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from harness import field_problems as problems
from harness import results as rule
from nomura_synth import ISIN, PHONE, WARNING, Spec, check, check_item
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

VARIANTS = {
    "P 記憶式、無 KI、3 檔、美元": Spec(),
    "D 記憶式 Non-Call 2、每日 KI": Spec(ko_obs="D", ki="D", first_callable=2, ko=Decimal("105.00")),
    "D 單一標的、到期 KI": Spec(
        count=1, ko_obs="D", ki="AM", tenor=4, ko=Decimal("95.00"), strike=Decimal("60.00"), ki_pct=Decimal("50.00")
    ),
    "P 單一標的、每期 KI、澳幣日股、2025 年文件": Spec(
        count=1,
        ki="P",
        tenor=4,
        currency_zh="澳幣",
        market="JT",
        strike=Decimal("64.50"),
        spaced=True,
        merged_prices=True,
    ),
    "P 非記憶式、Non-Call = 天期、日圓、日期表 KO 價欄": Spec(
        memory=False,
        tenor=3,
        first_callable=3,
        currency_zh="日圓",
        market="JT",
        ko=Decimal("105.00"),
        strike=Decimal("81.10"),
        annual=Decimal("6.00"),
        ko_column=True,
        merged_prices=True,
    ),
    "D VWAP、Non-Call 3": Spec(ko_obs="D", first_callable=3, tenor=4, vwap=True, count=2),
    "P 非記憶式 2 檔、Non-Call 2": Spec(memory=False, count=2, first_callable=2, annual=Decimal("12.35")),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass_and_are_filled(tmp_path, spec):
    item = check_item(tmp_path, spec)
    r = item.report
    assert r.template == NOMURA.template_id
    assert r.status == S.PASS, problems(r)
    assert item.fills_sheet
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in NOMURA.not_covered}
    assert [x.status for x in rule(r, "derive.monthly_coupon")] == [S.PASS]
    assert len(rule(r, "doc.coupon_mentions")) == 2  # 第 14 項設定參數、第 15 項(1)
    assert rule(r, "standard.fixed_warning")[0].actual == 2  # 上手別說明書次數
    assert len(rule(r, "standard.guarantor_name")) == 3
    assert len(rule(r, "standard.agent_name")) == 2
    assert len(rule(r, "standard.issuer_name")) == 2  # 封面、第二章；第一章沒有出處
    assert len(rule(r, "field.min_amounts")) == 7
    assert rule(r, "standard.distributor", "distributor_phone_ch4")[0].status == S.PASS
    assert rule(r, "field.coupon_pa_pct")[0].status == S.PASS  # 年利率 = 情境一、二年化報酬率
    assert rule(r, "standard.product_name", "name_en")[0].status == S.PASS


def test_vwap_prices_are_filled_from_the_term_sheet(tmp_path):
    spec = Spec(vwap=True)
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d for d in r.backfill}
    assert cells["UL_1_進場價"].expected == spec.underlyings[0].initial


# ---------------------------------------------------------------- 年利率（情境一、二年化報酬率）與每期配息率（年利率 ÷ 12）


def test_annual_rate_from_the_scenarios_is_compared_with_the_sheet(tmp_path):
    r = check(tmp_path, Spec(), **{"Coupon p.a. (%)": 10.5})
    assert rule(r, "field.coupon_pa_pct")[0].status == S.MISMATCH
    assert rule(r, "derive.monthly_coupon")[0].status == S.PASS  # 說明書內部一致


def test_period_rate_must_be_the_annual_rate_divided_by_12(tmp_path):
    """年利率 10.00%、每期配息率 0.8337%：× 12 四捨五入仍是 10.00%，但 ÷ 12 四捨五入到 4 位是 0.8333%，判不一致（Issue #297）。"""
    edit = Edit("固定配息率：就每配息日而言，0.8333%", "固定配息率：就每配息日而言，0.8337%", "ts.art7")
    r = check(tmp_path, Spec(), edits=[edit])
    found = rule(r, "derive.monthly_coupon")[0]
    assert (found.status, found.expected, found.actual) == (S.MISMATCH, Decimal("0.8333"), Decimal("0.8337"))


def test_different_scenario_annual_rates_need_review(tmp_path):
    """情境一、二的年化報酬率不同：年利率歧義，轉人工覆核。"""
    r = check(tmp_path, edits=[Edit("本情境中年化報酬率為10.00%", "本情境中年化報酬率為10.50%", "ts.art14")])
    assert rule(r, "field.coupon_pa_pct")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "derive.monthly_coupon")[0].status == S.REVIEW_REQUIRED


@pytest.mark.parametrize(
    "edit",
    [
        Edit("本情境中年化報酬率為10.00%", "本情境中年化報酬率約為10.00%", "ts.art14"),
        Edit("情境一（自動提前出場）", "情境1（自動提前出場）", "ts.art14"),
    ],
    ids=["年化報酬率寫法不在範本規格內", "找不到情境一"],
)
def test_missing_scenario_annual_rate_needs_review(tmp_path, edit):
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.coupon_pa_pct")[0].status == S.REVIEW_REQUIRED


def test_non_monthly_coupon_needs_review(tmp_path):
    """日期表列數 ≠ 天期（不是每月配息）：無法由年利率換算每期配息率，轉人工覆核。"""
    r = check(tmp_path, Spec(), edits=[Edit("(1) 商品年期：6個月", "(1) 商品年期：12個月", "ts.art11")])
    found = rule(r, "derive.monthly_coupon")[0]
    assert (found.status, found.reason_code) == (S.REVIEW_REQUIRED, "coupon_frequency_unknown")


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (
            Edit(
                "(1)    固定配息率：就每配息日而言，0.8333%。",
                "(1)    固定配息率：就每配息日而言，0.8334%。",
                "ts.art15",
            ),
            "doc.coupon_mentions",
        ),
        (Edit("就每配息日而言，0.8333%", "就每配息日而言，0.8334%", "ts.art14.table"), "doc.coupon_mentions"),
        (Edit("70.00%", "75.00%", "ts.art14.table"), "doc.scenario_table"),
        (Edit("10,000美元", "1,000美元", "ts.art14.table"), "doc.scenario_params"),
        (
            Edit(
                "商品名稱：Nomura International Funding Pte. Ltd.發行6個月",
                "商品名稱：Nomura International Funding Pte. Ltd.發行7個月",
                "ts.art1",
            ),
            "doc.name_consistency",
        ),
        (Edit("Pte. Ltd. 6-Month", "Pte. Ltd. 7-Month", "ts.art1"), "doc.name_consistency"),
        (Edit(f"(ISIN No. {ISIN})", "(ISIN No. XS1999900002)", "ts.art1"), "doc.isin_consistency"),
        (Edit("TDCC No. 156199990001", "TDCC No. 156199990002", "ts.cover"), "doc.product_code"),
        (Edit("計價幣別：美元(USD)", "計價幣別：美元(JPY)", "ts.art5"), "doc.currency_consistency"),
        (Edit("◎ 計價幣別：美元(USD)", "◎ 計價幣別：美元(JPY)", "ts.cover"), "doc.currency_consistency"),
        (Edit(f"(ISIN No. {ISIN} / TDCC No.", "(ISIN No. XS1999900002 / TDCC No.", "ts.cover"), "doc.isin_consistency"),
        (Edit("6個月", "7個月", "ts.art14.table"), "doc.coupon_periods"),
        (Edit("彭博代碼：ZNB UW Equity", "彭博代碼：ZNX UW Equity", "ts.art9"), "doc.underlying_tickers"),
        (Edit("每單位商品面額x 100.00%", "每單位商品面額x 99.00%", "ts.art7"), "doc.redemption_pct"),
        # Issue #316：說明文字、情境三、設定參數投資金額重複寫出的幣別與金額
        (Edit("以美元為計價單位給付（四捨五入", "以日圓為計價單位給付（四捨五入", "ts.art7"), "field.currency"),
        (Edit("本商品係以美元計價", "本商品係以日圓計價", "ts.ch三"), "field.currency"),
        (Edit("支付30.00美元", "支付30.00日圓", "ts.art14"), "field.currency"),
        (Edit("x 100% x 1 = 10,000美元", "x 100% x 1 = 20,000美元", "ts.art14.table"), "doc.scenario_params"),
        (Edit("x 100% x 1 = 10,000美元", "x 99% x 1 = 10,000美元", "ts.art14.table"), "doc.scenario_params"),
        (Edit("x 100% x 1 = 10,000美元", "x 100% x 1 = 10,000日圓", "ts.art14.table"), "field.currency"),
        (Edit("轉換成美元進行投資", "轉換成日圓進行投資", "ts.ch三"), "field.currency"),
        (Edit("若美元利率上漲", "若日圓利率上漲", "ts.ch三"), "field.currency"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where,
)
def test_internal_rules_find_the_wrong_repeat(tmp_path, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    assert broken in bad, problems(r)
    assert bad <= {broken, "standard.issue_price", "standard.product_name"}, problems(r)


def test_daily_ko_start_must_be_an_observation_period_end(tmp_path):
    """D 型 KO 起日不是日期表任何一期的觀察期間終止日：KO 觀察方式與 Non-Call 轉人工覆核。"""
    spec = Spec(ko_obs="D")
    start = spec.date(spec.ends[0])
    r = check(
        tmp_path, spec, edits=[Edit(f"自動提前出場決定日：自{start}", "自動提前出場決定日：自2030年1月20日", "ts.art7")]
    )
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_ko_wording_must_match_the_date_table_layout(tmp_path):
    """第 7 項寫每期觀察、日期表卻是每日觀察排法：轉人工覆核。"""
    spec = Spec(ko_obs="D")
    start = spec.date(spec.ends[0])
    periodic = "自動提前出場決定日：各自動提前出場決定日即下述第11項(6)「自動提前出場決定日」之表格所示。"
    r = check(
        tmp_path,
        spec,
        edits=[Edit(f"自動提前出場決定日：自{start}起（含）至最終評價日止（含）之各預定交易日。", periodic, "ts.art7")],
    )
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED


def test_memory_wording_in_the_name_must_match_article_7(tmp_path):
    """名稱寫記憶式、第 7 項卻是非記憶式句型：寫法超出範本規格，轉人工覆核。"""
    r = check(
        tmp_path, Spec(memory=False), edits=[Edit("固定配息(自動提前出場)", "固定配息(記憶式自動提前出場)", "ts.cover")]
    )
    assert rule(r, "doc.ko_memory", "name_zh")[0].status == S.REVIEW_REQUIRED


def test_date_table_ko_column_must_equal_the_table_header(tmp_path):
    spec = VARIANTS["P 非記憶式、Non-Call = 天期、日圓、日期表 KO 價欄"]
    r = check(tmp_path, spec, edits=[Edit("105.00%", "100.00%", "ts.art11.table")])
    assert rule(r, "doc.date_table_ko")[0].status == S.REVIEW_REQUIRED


def test_ki_periodic_dates_must_equal_the_ko_dates(tmp_path):
    spec = Spec(count=1, ki="P", tenor=4)
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[1]), "2030年3月12日", "ts.art11.ki")])
    assert rule(r, "doc.ki_consistency", "ki_table")[0].status == S.REVIEW_REQUIRED


def test_ki_clause_count_must_match_the_ki_terms(tmp_path):
    r = check(tmp_path, Spec(ki="AM"), edits=[Edit("(3) 如於最終評價日", "(3) 如於期末評價日", "ts.art7")])
    assert rule(r, "doc.ki_consistency", "maturity_cases")[0].status == S.REVIEW_REQUIRED


def test_single_or_basket_wording_must_match_the_table(tmp_path):
    """第 7 項自動提前出場事件寫成單一標的句型、標的表卻有 2 檔：轉人工覆核。"""
    single = "連結標的之收盤價高於或等於自動提前出場價時"
    edit = Edit("所有連結標的之收盤價均高於或等於其個別自動提前出場價時", single, "ts.art7")
    r = check(tmp_path, Spec(memory=False, count=2), edits=[edit])
    assert rule(r, "doc.underlying_count", "memory_style")[0].status == S.REVIEW_REQUIRED


def test_ki_clause_without_a_ki_column_needs_review(tmp_path):
    """第 7 項有下限觸發事件條款、標的表卻沒有下限觸發價欄：轉人工覆核。"""
    clause = "下限觸發事件：若任一連結標的於下限觸發決定日之收盤價小於其下限觸發價格，則視為發生下限觸發事件。下限觸發決定日：最終評價日。"
    r = check(tmp_path, Spec(), edits=[Edit("股票交割股數：", clause + "股票交割股數：", "ts.art7")])
    assert rule(r, "doc.ki_consistency", "underlying_table")[0].status == S.REVIEW_REQUIRED


def test_unreadable_ki_type_sends_the_redemption_check_to_review(tmp_path):
    """KI 型態讀不到時，到期贖回款數的預期也不確定：轉人工覆核而不是判不一致。"""
    r = check(
        tmp_path, Spec(ki="AM"), edits=[Edit("下限觸發決定日：最終評價日", "下限觸發決定日：每月最後交易日", "ts.art7")]
    )
    assert rule(r, "doc.redemption_pct")[0].status == S.REVIEW_REQUIRED


def test_ki_type_without_the_no_ki_redemption_case_needs_review(tmp_path):
    """沒有下限觸發字樣、也沒有無 KI 的到期贖回第 (2) 款：KI 型態轉人工覆核，不推定為無 KI（Issue #297）。"""
    edit = Edit("之最終價格低於個別執行價時", "之最終價格跌破個別執行價時", "ts.art7")
    r = check(tmp_path, Spec(), edits=[edit])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_article_8_count_must_match_the_table(tmp_path):
    r = check(tmp_path, edits=[Edit("連結標的3：ALPHA 3 HOLDINGS INC", "", "ts.art8")])
    assert rule(r, "doc.underlying_count", "art8_names")[0].status == S.REVIEW_REQUIRED


def test_last_payment_date_must_be_the_maturity_date(tmp_path):
    spec = Spec()
    r = check(
        tmp_path,
        spec,
        edits=[Edit(f"(4) 預定到期日：{spec.date(spec.maturity_date)}", "(4) 預定到期日：2031年1月20日", "ts.art11")],
    )
    assert rule(r, "schedule.last_payment")[0].status == S.MISMATCH


def test_periodic_non_call_rows_must_be_blank_only_before_the_first_callable_period(tmp_path):
    spec = Spec(first_callable=2)
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[3]), "不適用", "ts.art11.table")])
    assert rule(r, "schedule.autocall_dates")[0].status == S.MISMATCH


def test_sheet_memory_flag_is_compared_with_article_7(tmp_path):
    r = check(tmp_path, Spec(), pdf_spec=Spec(memory=False))  # 表上記憶式、說明書非記憶式
    assert rule(r, "field.ko_memory")[0].status == S.MISMATCH


# ---------------------------------------------------------------- 審查標準（Nomura 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit(WARNING[:20], WARNING[:19] + "X", "ts.ch三"), "standard.fixed_warning"),
        (Edit("商品風險程度：RR4", "商品風險程度：RR3", "ts.art2"), "standard.risk_level"),
        (Edit("連結股權之結構型債券", "股權連結結構型債券", "ts.cover"), "standard.product_type"),
        (Edit("三十萬美元", "三十萬元", "ts.ch四"), "standard.minimum_issue_size"),
        (
            Edit("保證機構名稱：Nomura Holdings, Inc.", "保證機構名稱：Nomura Holdings Inc.", "ts.art3"),
            "standard.guarantor_name",
        ),
        (
            Edit("事業名稱：香港商野村國際證券有限公司台北分公司", "事業名稱：野村國際證券台北分公司", "ts.ch二"),
            "standard.agent_name",
        ),
        (
            Edit(
                "(1) 事業名稱：Nomura International Funding Pte. Ltd.",
                "(1) 事業名稱：Nomura International Funding Ltd.",
                "ts.ch二",
            ),
            "standard.issuer_name",
        ),
        (Edit("0%~5%", "0%~3%", "ts.ch四"), "standard.fees"),
        (Edit(f"受託機構聯絡方式：{PHONE}", "受託機構聯絡方式：+886 2 5556 1314", "ts.ch四"), "standard.distributor"),
        (Edit("2026年6月11日", "2025年12月18日", "ts.cover"), "standard.approval_date"),
        (Edit("連結一籃子股票結構型商品", "連結一籃子股權結構型商品", "ts.cover"), "standard.product_name"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:12],
)
def test_review_standard_items(tmp_path, edit, broken):
    r = check(tmp_path, edits=[edit])
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_approval_date_2025_06_24_is_a_known_review_date(tmp_path):
    """2025-06-24 是受託機構真實的歷次審查日期（Issue #239）：2025 年下半年交易的文件寫這一天通過。"""
    spec = Spec(trade_date=dt.date(2025, 9, 22), issue_date=dt.date(2025, 10, 7), approval=dt.date(2025, 6, 24))
    r = check(tmp_path, spec)
    found = rule(r, "standard.approval_date")[0]
    assert (found.status, found.expected) == (S.PASS, dt.date(2025, 6, 24))


def test_other_issuers_keep_three_fixed_warnings(tmp_path):
    """上手別說明書固定警語次數只給 Nomura：其他上手沿用 3 次。"""
    r = citi_synth.check(tmp_path, citi_synth.Spec())
    assert rule(r, "standard.fixed_warning")[0].actual == 3


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
    ],
    ids=["BARC", "HSBC", "MS", "BNP", "SG", "Citi", "JPM"],
)
def test_other_issuers_are_not_nomura(tmp_path, build):
    pdf = build(tmp_path / "156199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    for item in check_all(sheet, [pdf]).items:
        assert item.report.template not in (NOMURA.template_id, NOMURA.iis.template_id)
