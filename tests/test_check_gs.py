"""GS 黑箱回歸（Issue #248）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

from decimal import Decimal

import pytest

import bnp_synth
import citi_synth
import hsbc_synth
import jpm_synth
import ms_synth
import nomura_synth
import sg_synth
import synth
from fcn_checker.issuers import GS
from fcn_checker.schema import CheckStatus as S
from gs_synth import ADDRESS, ISIN, WARNING, Spec, build_pdf, check, check_item, row
from harness import check_all
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

VARIANTS = {
    "記憶式 3 檔、每日 KI、美元、收盤價（間接寫法）": Spec(),
    "單一標的、無 KI、Non-Call 2、開盤價": Spec(
        count=1, ki="none", first_callable=2, strike=Decimal("85.00"), pricing="open", annual=Decimal("9.00")
    ),
    "到期 KI、VWAP、Non-Call 1、12 個月、非整數 K %": Spec(
        ki="AM",
        first_callable=1,
        ko=Decimal("100.00"),
        pricing="vwap",
        tenor=12,
        strike=Decimal("81.13"),
        ki_pct=Decimal("70.00"),
        annual=Decimal("18.00"),
    ),
    "日圓日股、價格擠在同一行與拆行": Spec(currency_zh="日圓", market="JT", merged_prices=True, split_initial=True),
    "人民幣 4 檔美股、開盤價": Spec(currency_zh="人民幣", count=4, pricing="open", tenor=12, annual=Decimal("13.90")),
}


def _reordered(spec: Spec) -> dict:
    """參考條件表列的標的順序反過來（GS 依代號配對，不看順序）。"""
    out = row(spec)
    n = spec.count
    cols = ("", "_進場價", "_執行價", "_下限價", "_KO價")
    values = {k: [out[f"UL_{i}{k}"] for i in range(1, n + 1)] for k in cols}
    for k in cols:
        for i, v in enumerate(reversed(values[k]), 1):
            out[f"UL_{i}{k}"] = v
    return out


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass_and_are_filled(tmp_path, spec):
    item = check_item(tmp_path, spec)
    r = item.report
    assert r.template == GS.template_id
    assert r.status == S.PASS, problems(r)
    assert item.fills_sheet
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in GS.not_covered}
    assert [x.status for x in rule(r, "derive.monthly_coupon")] == [S.PASS]
    assert len(rule(r, "doc.coupon_mentions")) == 2  # 第 15 項(2) 最低保證收益率、第 17 項情境假設
    assert rule(r, "standard.fixed_warning")[0].actual == 3
    assert len(rule(r, "standard.issuer_name")) == 3  # 第 1 頁、第一章（中文部分）、第二章
    skipped = ("standard.guarantor_name", "standard.agent_name", "standard.minimum_issue_size")  # 決定不核對
    assert not [x for x in r.results if x.rule_id in skipped]
    assert len(rule(r, "field.min_amounts")) == 4
    assert rule(r, "field.initial_pricing")[0].status == S.PASS
    assert rule(r, "standard.product_name", "name_en")[0].status == S.PASS


def test_underlyings_are_matched_by_ticker_not_order(tmp_path):
    spec = Spec()
    path = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [_reordered(spec)])
    item = next(i for i in check_all(sheet, [path]).items if i.term_sheet == path)
    assert item.report.status == S.PASS, problems(item.report)


def test_a_different_exchange_code_still_pairs_prices_by_symbol(tmp_path):
    """表上順序不同且有一檔交易所碼寫錯（`UQ` 寫成 `UN`，Issue #265）：報代碼不符，價格仍依代號和表上同一檔比，
    不會依順序拿不同標的互比而冒出疑似分割的提示。"""
    spec = Spec()
    out = _reordered(spec)
    symbol, exchange = out["UL_1"].split()
    out["UL_1"] = f"{symbol} {'UN' if exchange != 'UN' else 'UQ'}"
    path = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [out])
    r = next(i for i in check_all(sheet, [path]).items if i.term_sheet == path).report
    assert rule(r, "field.underlyings")[0].status == S.MISMATCH
    prices = rule(r, "field.underlying_prices")
    assert prices and all(x.status == S.PASS for x in prices), [(x.field, x.status, x.message) for x in prices]


def test_a_different_ticker_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(), **{"UL_2": "ZZZ UW"})
    assert rule(r, "field.underlyings")[0].status == S.MISMATCH


def test_vwap_prices_are_filled_from_the_term_sheet(tmp_path):
    spec = Spec(pricing="vwap")
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d for d in r.backfill}
    assert cells["UL_1_進場價"].expected == spec.underlyings[0].initial


def test_blank_sheet_pricing_follows_the_term_sheet_vwap(tmp_path):
    """表上期初定價空白、說明書寫 VWAP：當 VWAP 商品處理，價格不比對、通過後以說明書覆寫（Issue #246）。"""
    spec = Spec(pricing="vwap")
    r = check(tmp_path, spec, **{"期初定價": None})
    assert r.status == S.PASS, problems(r)
    assert any(d.column == "UL_1_進場價" for d in r.backfill)


def test_initial_pricing_differs_from_the_sheet(tmp_path):
    r = check(tmp_path, Spec(), **{"期初定價": "開盤價"})  # 說明書是收盤價（間接寫法）
    assert rule(r, "field.initial_pricing")[0].status == S.MISMATCH


def test_unknown_initial_pricing_wording_needs_review(tmp_path):
    edit = Edit("於交易日之參考價格(i)。", "於交易日之結算價格(i)。", "ts.art15")
    r = check(tmp_path, Spec(), edits=[edit])
    assert rule(r, "field.initial_pricing")[0].status == S.REVIEW_REQUIRED


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("（約為1.0000%）", "（約為1.0100%）", "ts.art14"), "derive.monthly_coupon"),
        (Edit("觀察期間年利率12.00%", "觀察期間年利率12.50%", "ts.art15"), "doc.coupon_mentions"),
        (Edit("配息率:年利率12.00%", "配息率:年利率12.50%", "ts.art17"), "doc.coupon_mentions"),
        (Edit("首3個觀察期間", "首2個觀察期間", "ts.art15"), "doc.autocall_terms"),
        (Edit("（且落在第3個觀察期間結束日）", "（且落在第2個觀察期間結束日）", "ts.art17"), "doc.autocall_terms"),
        (Edit("初始參考價格(1): 100.4500", "初始參考價格(1): 100.5500", "ts.art17"), "doc.scenario_table"),
        (Edit("格(i)之65.00", "格(i)之66.00", "ts.art15.table"), "doc.table_header_pct"),
        (Edit("本商品名稱:6個月", "本商品名稱:7個月", "ts.art1"), "doc.name_consistency"),
        (Edit("TDCC Product Code: 148199990001", "TDCC Product Code: 148199990002", "ts.cover"), "doc.product_code"),
        (Edit(f"ISIN:{ISIN})", "ISIN:XS1999900049)", "ts.art1"), "doc.isin_consistency"),
        (Edit("計價幣別:美元（USD）", "計價幣別:美元（JPY）", "ts.art5"), "doc.currency_consistency"),
        (Edit("ZGB UW Equity", "ZGX UW Equity", "ts.art10"), "doc.underlying_tickers"),
        (Edit("Goldman Sachs International）。截至", "Goldman Sachs Intl）。截至", "ts.art3"), "doc.issuer_name"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_internal_rules_find_the_wrong_repeat(tmp_path, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    assert broken in bad, problems(r)
    assert bad <= {broken, "standard.product_name"}, problems(r)


def test_ko_start_outside_the_schedule_needs_review(tmp_path):
    r = check(tmp_path, edits=[Edit("自第3個觀察期間結束日開始", "自第9個觀察期間結束日開始", "ts.art16")])
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_memory_wording_in_the_name_must_match_article_16(tmp_path):
    """名稱寫記憶式、第 16 項卻是單一標的句型：轉人工覆核。"""
    spec = VARIANTS["單一標的、無 KI、Non-Call 2、開盤價"]
    r = check(tmp_path, spec, edits=[Edit("固定配息自動提前出場", "固定配息記憶式自動提前出場", "ts.cover")])
    assert rule(r, "doc.ko_memory", "name_zh")[0].status == S.REVIEW_REQUIRED


def test_daily_ki_without_its_observation_period_needs_review(tmp_path):
    edit = Edit("「下限觸發觀察期間」指自交易日開始（不含）至評價日結束（含）之期間。", "", "ts.art15")
    r = check(tmp_path, Spec(ki="D"), edits=[edit])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_ki_definition_must_match_the_maturity_terms(tmp_path):
    """到期買回比下限觸發價格（AM）、卻沒有「下限觸發價格」定義：轉人工覆核。"""
    edit = Edit("「下限觸發價格(i)」:初始參考價格(i)之55.00%，詳見下方之連結標的價格表。", "", "ts.art15")
    r = check(tmp_path, Spec(ki="AM"), edits=[edit])
    assert rule(r, "doc.ki_consistency", "ki_def_pct")[0].status == S.REVIEW_REQUIRED


def test_last_payment_date_must_be_the_maturity_date(tmp_path):
    spec = Spec()
    old = f"現預定為{spec.date(spec.maturity_date)}"
    r = check(tmp_path, spec, edits=[Edit(old, "現預定為2031年1月20日", "ts.art13")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_schedule_rows_must_equal_the_tenor(tmp_path):
    """年期寫 7 個月、觀察時程表 6 列：期數不符。"""
    r = check(tmp_path, edits=[Edit("則為6個月", "則為7個月", "ts.art13")])
    assert rule(r, "doc.coupon_periods", "date_table")[0].status == S.MISMATCH


def test_single_wording_with_a_basket_table_needs_review(tmp_path):
    """第 16 項寫成單一標的句型、價格表卻有 3 檔：轉人工覆核。"""
    memory = "如於任一提前出場觀察日，各連結標的(i)至少發生過一次記憶提前出場事件(i)，則視為已發生「自動提前出場事件」"
    single = "如於一提前出場觀察日之參考價格等於或高於提前出場價格，則視為已發生「自動提前出場事件」"
    r = check(tmp_path, edits=[Edit(memory, single, "ts.art16")])
    assert rule(r, "doc.underlying_count", "memory_style")[0].status == S.REVIEW_REQUIRED


def test_indexed_table_header_with_one_underlying_needs_review(tmp_path):
    """單一標的的價格表表頭寫了多標的的「(i)」：轉人工覆核。"""
    spec = VARIANTS["單一標的、無 KI、Non-Call 2、開盤價"]
    r = check(tmp_path, spec, edits=[Edit("彭博代", "彭博代(i)", "ts.art15.table")])
    assert rule(r, "doc.underlying_count", "price_table_header")[0].status == S.REVIEW_REQUIRED


def test_scenario_units_must_equal_the_minimum_subscription_units(tmp_path):
    r = check(tmp_path, edits=[Edit("最低申購單位數（5單位）", "最低申購單位數（1單位）", "ts.art17")])
    assert rule(r, "doc.scenario_table")[0].status == S.MISMATCH


def test_sheet_non_call_is_compared_with_article_16(tmp_path):
    r = check(tmp_path, Spec(), **{"Non-Call(月)": 2})
    assert rule(r, "field.first_callable_period")[0].status == S.MISMATCH


# ---------------------------------------------------------------- 審查標準（GS 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit(WARNING[:20], WARNING[:19] + "X", "ts.ch三"), "standard.fixed_warning"),
        (Edit("3. 商品種類:股權連結結構型債券", "3. 商品種類:股權連結結構型商品", "ts.cover"), "standard.product_type"),
        (
            Edit("(1) 事業名稱: 英商高盛國際公司", "(1) 事業名稱: 英商高盛國際有限公司", "ts.ch二"),
            "standard.issuer_name",
        ),
        (Edit("申購價金的0%~5%", "申購價金的0%~3%", "ts.ch四"), "standard.fees"),
        (
            Edit(f"地址:{ADDRESS}(營業活動所在地)", "地址:台北市虛構路2號(營業活動所在地)", "ts.cover"),
            "standard.distributor",
        ),
        (Edit("2026年6月11日", "2025年12月18日", "ts.cover"), "standard.approval_date"),
        (Edit("最低申購為5單位", "最低申購為1單位", "ts.ch四"), "field.min_amounts"),
        (Edit("× 100%", "× 99%", "ts.ch四"), "standard.issue_price"),
        (Edit("股權連結結構型商品（無擔保）", "股權連結結構型債券（無擔保）", "ts.cover"), "standard.product_name"),
        (Edit("於中華民國境內受託買賣", "於中華民國境內受託投資", "ts.warn"), "standard.forbidden_wording"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:12],
)
def test_review_standard_items(tmp_path, edit, broken):
    r = check(tmp_path, edits=[edit])
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_cny_in_the_term_sheet_is_the_cnh_of_the_sheet(tmp_path):
    r = check(tmp_path, VARIANTS["人民幣 4 檔美股、開盤價"])
    assert r.status == S.PASS, problems(r)
    assert rule(r, "doc.currency_consistency", "currency_art5_iso")[0].actual == "CNY"


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
    ],
    ids=["BARC", "HSBC", "MS", "BNP", "SG", "Citi", "JPM", "Nomura"],
)
def test_other_issuers_are_not_gs(tmp_path, build):
    pdf = build(tmp_path / "148199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    for item in check_all(sheet, [pdf]).items:
        assert item.report.template not in (GS.template_id, GS.iis.template_id)
