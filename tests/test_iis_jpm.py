"""JPM 中文投資人須知（Issue #233）：docs/templates/jpm-zh-iis.md、docs/rules/iis-check-rules.md §3 JPM 欄。

合成 PDF 經公開批量入口核對（說明書＋投資人須知＋參考條件表一列）；商品、標的與價格皆虛構。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import bnp_synth
import citi_synth
import hsbc_synth
import ms_synth
import sg_synth
import synth
from fcn_checker.issuers import JPM
from harness import MISMATCH, NA, PASS, REVIEW, check_all
from jpm_synth import AGENT, GUARANTOR, Spec, check_item, jdate
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

S = Spec()


def pair(tmp_path, spec: Spec = S, **kw):
    """（說明書, 投資人須知）兩份的批量核對項目。"""
    ts = check_item(tmp_path, spec, **kw)
    return ts, ts.partner


def bad(item) -> set[tuple[str, str, str]]:
    return {(r.rule_id, r.field, r.status) for r in item.report.results if r.status not in (PASS, NA)}


VARIANTS = {
    "D 4 欄、記憶式、無 KI、3 檔": Spec(),
    "D 3 欄、單一標的、每日 KI": Spec(d4=False, ki="D", count=1, tenor=12, first_callable=2, ko=Decimal("120.00")),
    "P 記憶式、到期 KI、日圓": Spec(ko_obs="P", ki="AM", currency_zh="日圓", tenor=8, first_callable=2),
    "P 非記憶式、Non-Call = 天期": Spec(ko_obs="P", memory=False, tenor=3, first_callable=3, strike=Decimal("75.92")),
    "VWAP": Spec(d4=False, vwap=True),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_consistent_jpm_term_sheet_and_iis_pass_and_the_term_sheet_is_filled(tmp_path, spec):
    ts, iis = pair(tmp_path, spec)
    assert iis.report.template == JPM.iis.template_id
    assert bad(iis) == set() and iis.report.status == PASS
    assert ts.report.status == PASS and ts.fills_sheet
    checked = {(r.rule_id, r.field) for r in iis.report.results if r.status == PASS}
    for rule, field in [
        ("iis.pages", "pages"),
        ("iis.page_totals", "page_totals"),
        ("iis.product_code", "product_code_tdcc"),
        ("iis.product_code", "product_code_trustee"),
        ("iis.isin", "isin"),
        ("iis.name_zh", "name_zh"),
        ("iis.name_en", "name_en"),
        ("field.currency", "currency"),
        ("field.underlyings", "underlyings"),
        ("field.tenor_months", "tenor_months"),
        ("field.trade_date", "trade_date"),
        ("iis.issue_date", "issue_date"),
        ("field.final_valuation_date", "final_valuation_date"),
        ("field.maturity_date", "maturity_date"),
        ("field.coupon_pa_pct", "coupon_pa_pct"),
        ("field.strike_pct", "strike_pct"),
        ("field.ko_pct", "ko_pct"),
        ("field.ko_observation", "ko_observation"),
        ("field.first_callable_period", "first_callable_period"),
        ("field.ko_memory", "ko_memory"),
        ("field.ki_type", "ki_type"),
        ("standard.risk_level", "risk_level"),
        ("standard.fixed_warning", "fixed_warning"),
        ("standard.forbidden_wording", "forbidden_wording"),
        ("standard.issuer_name", "issuer_name_w5"),
        ("standard.issuer_name", "issuer_name_org"),
        ("standard.guarantor_name", "guarantor_name_org"),
        ("standard.guarantor_name", "guarantor_name_w5a"),
        ("standard.guarantor_name", "guarantor_name_w5b"),
        ("standard.agent_name", "agent_name_org"),
        ("standard.distributor", "distributor_name_org"),
        ("standard.distributor", "distributor_address_org"),
        ("standard.fees", "分銷費用"),
        ("standard.product_type", "product_type"),
        ("doc.print_date", "print_date_iis"),
    ]:
        assert (rule, field) in checked, (rule, field)
    assert (("field.ki_pct", "ki_pct") in checked) == (spec.ki != "none")
    assert (("iis.coupon_pa_mentions", "coupon_pa_pro_rata") in checked) == (spec.ko_obs == "D")
    prices = [r for r in iis.report.results if r.rule_id == "iis.underlying_prices"]
    assert prices and all(r.status == PASS for r in prices)  # 價格和參考條件表比；VWAP 時和說明書比
    assert not [r for r in iis.report.results if r.rule_id == "iis.redemption_start"]  # 只有文字，不核對


def test_underlyings_in_a_different_order_on_the_sheet_pass(tmp_path):
    """表上標的排序和投資人須知不同：依代號配對，各價格對到表上相同代號那一格。"""
    ts, iis = pair(tmp_path, order=(3, 1, 2))
    assert bad(iis) == set(), bad(iis)
    prices = [r for r in iis.report.results if r.rule_id == "iis.underlying_prices"]
    assert any(
        r.field == "UL_2 進場價" and r.item.name == "UL_2 進場價" for r in prices
    )  # 投資人須知第 1 檔在表上第 2 檔
    assert ts.fills_sheet


def test_bloomberg_code_wrapped_onto_two_lines_is_still_read(tmp_path):
    """投資人須知彭博代碼欄窄，4 碼代號換成「代號」「交易所碼」兩行（Issue #257）：標的與價格仍讀到，兩份都通過。"""
    ts, iis = pair(tmp_path, Spec(iis_ticker_wrap=True))
    assert bad(iis) == set(), bad(iis)
    assert iis.report.status == PASS and ts.report.status == PASS and ts.fills_sheet


def test_a_wrong_price_on_the_sheet_is_found_after_matching_by_ticker(tmp_path):
    ts, iis = pair(tmp_path, order=(3, 1, 2), **{"UL_2_KO價": 1.0})  # 表上第 2 檔是投資人須知第 1 檔
    assert ("iis.underlying_prices", "UL_2 KO 價", MISMATCH) in bad(iis)
    assert not ts.fills_sheet


def test_daily_ko_written_as_valuation_date_matches_a_three_column_term_sheet(tmp_path):
    """說明書 3 欄日期表寫「觀察期間終止日(t)」、投資人須知寫「評價日(t)」：只核對 D 型與 k（2026-10-15 決定）。"""
    _, iis = pair(tmp_path, Spec(d4=False, tenor=7, first_callable=2))
    assert bad(iis) == set(), bad(iis)


# ---------------------------------------------------------------- 每個檢查點都抓得到錯


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("受託機構商品代號076199990001", "受託機構商品代號076199990002", "iis.cover"), ("iis.product_code", MISMATCH)),
        (Edit("摩根大通6個月", "摩根大通7個月", "iis.cover"), ("iis.name_zh", MISMATCH)),
        (Edit("ZJB UN", "ZJX UN", "iis.summary"), ("field.underlyings", MISMATCH)),
        (Edit("100.5000", "100.4000", "iis.summary"), ("iis.underlying_prices", MISMATCH)),
        (Edit("商品年期：6個月", "商品年期：7個月", "iis.summary"), ("field.tenor_months", MISMATCH)),
        (Edit(f"交易日/期初評價日：{jdate(S.trade_date)}", "交易日/期初評價日：2030年01月08日", "iis.summary"), ("field.trade_date", MISMATCH)),
        (Edit(f"到期日：{jdate(S.maturity_date)}", "到期日：2031年01月01日", "iis.summary"), ("field.maturity_date", MISMATCH)),
        (Edit("每單位面額 x 12.34% x 1/12", "每單位面額 x 12.43% x 1/12", "iis.coupon"), ("field.coupon_pa_pct", MISMATCH)),
        (Edit("執行價：70.00%", "執行價：71.00%", "iis.redeem"), ("field.strike_pct", MISMATCH)),
        (Edit("自動提前出場價：100.00%", "自動提前出場價：101.00%", "iis.redeem"), ("field.ko_pct", MISMATCH)),
        (Edit("(t=1)(含)起", "(t=2)(含)起", "iis.redeem"), ("field.first_callable_period", MISMATCH)),
        (Edit("(包括OSU客戶)以下稱", "(包括OSU客戶)(以下稱", "iis.warn"), ("standard.fixed_warning", MISMATCH)),
        (Edit("受託投資/受託買賣", "受託投資", "iis.warn"), ("standard.forbidden_wording", MISMATCH)),
        (Edit(f"2. 保證機構：{GUARANTOR['cover']}", "2. 保證機構：摩根大通銀行", "iis.org"), ("standard.guarantor_name", MISMATCH)),
        (Edit(f"係由{GUARANTOR['ch1']}保證", "係由摩根大通銀行保證", "iis.warn"), ("standard.guarantor_name", MISMATCH)),
        (Edit(f"3. 總代理人：{AGENT}", "3. 總代理人：摩根大通台北分公司", "iis.org"), ("standard.agent_name", MISMATCH)),
        (Edit("4. 受託機構：玉山綜合證券股份有限公司", "4. 受託機構：玉山證券公司", "iis.org"), ("standard.distributor", MISMATCH)),
        (Edit("0%~5%", "0%~3%", "iis.fees"), ("standard.fees", MISMATCH)),
        (Edit(f"刊印日期：{jdate(S.trade_date)}", "刊印日期：2030年02月01日", "iis.cover"), ("doc.print_date", MISMATCH)),
    ],
    ids=lambda x: x[0] if isinstance(x, tuple) else x.old[:14],
)  # fmt: skip
def test_each_checkpoint_catches_a_wrong_investor_sheet(tmp_path, edit, broken):
    ts, iis = pair(tmp_path, edits=[edit])
    assert broken in {(rid, st) for rid, _, st in bad(iis)}, bad(iis)
    assert ts.report.status == PASS and not ts.fills_sheet  # 投資人須知沒通過：說明書不回填


def test_pro_rata_coupon_rate_must_equal_the_coupon_rate(tmp_path):
    """D 型贖回價金段自動提前出場配息金額的年利率寫錯：逐處比對判不一致（B16「每處 = 年利率」）。"""
    _, iis = pair(tmp_path, edits=[Edit("x 12.34% x 1/12 x n/N", "x 12.43% x 1/12 x n/N", "iis.redeem")])
    assert bad(iis) == {("iis.coupon_pa_mentions", "coupon_pa_pro_rata", MISMATCH)}


def test_unreadable_ki_percentage_requires_review(tmp_path):
    """有觸碰事件、觸碰價 % 卻讀不到：KI % 轉人工覆核（不會默默不核對）。"""
    _, iis = pair(tmp_path, Spec(ki="AM"), edits=[Edit("觸碰價：60.00% x 期初價格", "觸碰價：見說明書", "iis.redeem")])
    assert ("field.ki_pct", "ki_pct", REVIEW) in bad(iis)


def test_ki_percentage_is_checked_when_present(tmp_path):
    _, iis = pair(tmp_path, Spec(ki="AM"), edits=[Edit("觸碰價：60.00%", "觸碰價：61.00%", "iis.redeem")])
    assert ("field.ki_pct", "ki_pct", MISMATCH) in bad(iis)


def test_memory_wording_is_compared_with_the_sheet(tmp_path):
    _, iis = pair(tmp_path, edits=[Edit("同時於該自動提前出場評價日或分別於不同自動提前出場評價日（包括之前自動提前出場評價日）之股票收盤價至少有一次大於或等於", "於該自動提前出場評價日之股票收盤價大於或等於", "iis.redeem")])  # fmt: skip
    assert ("field.ko_memory", "ko_memory", MISMATCH) in bad(iis)


def test_page_count_must_be_four(tmp_path):
    _, iis = pair(tmp_path, Spec(iis_pages=5))
    assert {("iis.pages", "pages", MISMATCH)} <= bad(iis)


# ---------------------------------------------------------------- 範本辨識


@pytest.mark.parametrize(
    "build",
    [
        lambda p: synth.build_pdf(p, synth.Spec()),
        lambda p: ms_synth.build_pdf(p, ms_synth.Spec()),
        lambda p: bnp_synth.build_pdf(p, bnp_synth.Spec()),
        lambda p: sg_synth.build_pdf(p, sg_synth.Spec()),
        lambda p: citi_synth.build_pdf(p, citi_synth.Spec()),
        lambda p: hsbc_synth.build_pdf(p, hsbc_synth.Spec()),
    ],
    ids=["BARC", "MS", "BNP", "SG", "Citi", "HSBC"],
)
def test_other_issuers_investor_sheets_are_not_jpm(tmp_path, build):
    ts = build(tmp_path / "076199990001_TS.pdf")
    iis = ts.with_name("076199990001_IIS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    r = next(i.report for i in check_all(sheet, [ts, iis]).items if i.term_sheet == iis)
    assert r.template != JPM.iis.template_id
