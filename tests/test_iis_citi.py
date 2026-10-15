"""Citi 中文投資人須知（Issue #220）：docs/templates/citi-zh-iis.md、docs/rules/iis-check-rules.md §3 Citi 欄。

合成 PDF 經公開批量入口核對（說明書＋投資人須知＋參考條件表一列）；商品、標的與價格皆虛構。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import bnp_synth
import ms_synth
import sg_synth
import synth
from citi_synth import IIS_GUARANTOR, MINIMUM_ISSUE, Spec, build_iis_pdf, check_item
from fcn_checker.issuers import CITI
from harness import MISMATCH, NA, PASS, REVIEW, check_all
from pdf_writer import Edit, zh_date
from reference_synth import build_reference_sheet, reference_row

S = Spec()


def pair(tmp_path, spec: Spec = S, **kw):
    """（說明書, 投資人須知）兩份的批量核對項目。"""
    ts = check_item(tmp_path, spec, **kw)
    return ts, ts.partner


def bad(item) -> set[tuple[str, str, str]]:
    return {(r.rule_id, r.field, r.status) for r in item.report.results if r.status not in (PASS, NA)}


VARIANTS = {
    "D 記憶式、到期 KI、3 檔": Spec(),
    "D Non-Call 6、每日 KI、日圓": Spec(currency_zh="日圓", ki="D", tenor=12, first_callable=6, ko=Decimal("100.00")),
    "P 記憶式、無 KI、4 檔": Spec(ko_obs="P", ki="none", count=4, tenor=7, ko=Decimal("105.00")),
    "P、每期 KI、舊文件": Spec(ko_obs="P", ki="P", tenor=9, legacy=True),
    "無自動提前出場、CNH": Spec(autocall=False, ki="none", tenor=3, currency_zh="境外人民幣", strike=Decimal("68.85")),
    "單一標的、非記憶式": Spec(memory=False, count=1, tenor=12, first_callable=11, ko=Decimal("100.00")),
    "VWAP": Spec(vwap=True, ki="none", ko=Decimal("91.00")),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_consistent_citi_term_sheet_and_iis_pass_and_the_term_sheet_is_filled(tmp_path, spec):
    ts, iis = pair(tmp_path, spec)
    assert iis.report.template == CITI.iis.template_id
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
        ("field.currency", "payment_currency_maturity"),
        ("field.underlyings", "underlyings"),
        ("field.tenor_months", "tenor_months"),
        ("field.trade_date", "trade_date"),
        ("iis.issue_date", "issue_date"),
        ("field.final_valuation_date", "final_valuation_date"),
        ("field.maturity_date", "maturity_date"),
        ("field.coupon_pa_pct", "coupon_pa_pct"),
        ("iis.monthly_coupon", "monthly_coupon_1"),
        ("field.strike_pct", "strike_pct"),
        ("field.ko_memory", "ko_memory"),
        ("field.ki_type", "ki_type"),
        ("iis.has_autocall", "has_autocall"),
        ("iis.redemption_start", "redemption_start"),
        ("iis.memory_wording", "memory_wording"),
        ("standard.risk_level", "risk_level"),
        ("standard.risk_level", "risk_level_summary"),
        ("standard.fixed_warning", "fixed_warning"),
        ("standard.forbidden_wording", "forbidden_wording"),
        ("standard.issuer_name", "issuer_name_w5"),
        ("standard.issuer_name", "issuer_name_org"),
        ("standard.guarantor_name", "guarantor_name_w5"),
        ("standard.guarantor_name", "guarantor_name_org"),
        ("standard.agent_name", "agent_name_org"),
        ("standard.distributor", "distributor_name_w4a"),
        ("standard.distributor", "distributor_name_w4c"),
        ("standard.distributor", "distributor_name_w5"),
        ("standard.distributor", "distributor_name_org"),
        ("standard.distributor", "distributor_address_org"),
        ("standard.fees", "分銷費用"),
        ("standard.product_type", "product_type"),
        ("standard.minimum_issue_size", "minimum_issue_size"),
        ("doc.print_date", "print_date_iis"),
    ]:
        assert (rule, field) in checked, (rule, field)
    assert (("field.ki_pct", "ki_pct") in checked) == (spec.ki != "none")
    assert (("field.ko_observation", "ko_observation") in checked) == spec.autocall
    assert (("field.first_callable_period", "first_callable_period") in checked) == (
        spec.autocall and spec.ko_obs == "D"
    )  # P 型投資人須知不寫第一個可提前出場期
    assert (("field.currency", "payment_currency_coupon") in checked) == (not spec.autocall or spec.ko_obs == "P")


@pytest.mark.parametrize("n", [1, 2, 3])
def test_ticker_wrapped_between_code_and_exchange_is_read(tmp_path, n):
    """彭博代碼在代號與交易所碼之間換行（`(彭博代碼：XXXX` / `UW)`，Issue #229 OOS 實際寫法）：每一檔都讀得到。"""
    ts, iis = pair(tmp_path, Spec(iis_ticker_break=n), order=(3, 1, 2))
    assert bad(iis) == set(), bad(iis)
    assert ts.fills_sheet


def test_guarantor_name_in_chinese_only_is_an_equivalent(tmp_path):
    _, iis = pair(tmp_path)
    found = [r for r in iis.report.results if r.field == "guarantor_name_org"]
    assert found[0].actual == IIS_GUARANTOR and "equivalents" in (found[0].tolerance or "")


# ---------------------------------------------------------------- 每個檢查點都抓得到錯


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (
            Edit("受託或銷售機構商品代號：159199990001", "受託或銷售機構商品代號：159199990002", "iis.cover"),
            ("iis.product_code", MISMATCH),
        ),
        (Edit("CGMHI 6 個月", "CGMHI 7 個月", "iis.cover"), ("iis.name_zh", MISMATCH)),
        (Edit("彭博代碼：ZCB UN", "彭博代碼：ZCX UN", "iis.summary"), ("field.underlyings", MISMATCH)),
        (Edit("商品年期：6 個月", "商品年期：7 個月", "iis.summary"), ("field.tenor_months", MISMATCH)),
        (
            Edit(f"交易日：{zh_date(S.trade_date)}", "交易日：2030 年1 月8 日", "iis.summary"),
            ("field.trade_date", MISMATCH),
        ),
        (
            Edit(f"到期日：{zh_date(S.maturity_date)}", "到期日：2031 年1 月1 日", "iis.summary"),
            ("field.maturity_date", MISMATCH),
        ),
        (Edit("× m/M × 0.8233%", "× m/M × 0.8333%", "iis.coupon"), ("iis.monthly_coupon", MISMATCH)),
        (Edit("約等同年息為9.88%", "約等同年息為9.98%", "iis.coupon"), ("field.coupon_pa_pct", MISMATCH)),
        (Edit("期初股價的65.00%。", "期初股價的66.00%。", "iis.redeem"), ("field.strike_pct", MISMATCH)),
        (Edit("期初股價的55.00%。", "期初股價的50.00%。", "iis.redeem"), ("field.ki_pct", MISMATCH)),
        (
            Edit("於第1 個配息觀察期間", "於第2 個配息觀察期間", "iis.summary"),
            ("field.first_callable_period", MISMATCH),
        ),
        (
            Edit("開始受理贖回日期：", "開始受理贖回日期：2029 年1 月1 日，原訂", "iis.summary"),
            ("iis.redemption_start", MISMATCH),
        ),
        (Edit("本商品之風險程度：RR4", "本商品之風險程度：RR5", "iis.summary"), ("standard.risk_level", MISMATCH)),
        (
            Edit("證券交易所交易證券，再綜合評量", "證券交易所交易證券，在綜合評量", "iis.warn"),
            ("standard.fixed_warning", MISMATCH),
        ),
        (Edit("受託投資或銷售本商品", "受託投資本商品", "iis.warn"), ("standard.forbidden_wording", MISMATCH)),
        (Edit(f"2. 保證機構：{IIS_GUARANTOR}", "2. 保證機構：花旗", "iis.org"), ("standard.guarantor_name", MISMATCH)),
        (
            Edit("3. 總代理人：花旗環球證券股份有限公司", "3. 總代理人：花旗證券", "iis.org"),
            ("standard.agent_name", MISMATCH),
        ),
        (Edit("且玉山證券對本商品", "且玉山對本商品", "iis.warn"), ("standard.distributor", MISMATCH)),
        (Edit(MINIMUM_ISSUE, "等值20 萬美元", "iis.redeem"), ("standard.minimum_issue_size", MISMATCH)),
        (Edit("0%~5%", "0%~3%", "iis.fees"), ("standard.fees", MISMATCH)),
        (
            Edit(f"刊印日期：{zh_date(S.trade_date)}", "刊印日期：2030 年2 月1 日", "iis.cover"),
            ("doc.print_date", MISMATCH),
        ),
    ],
    ids=lambda x: x[0] if isinstance(x, tuple) else x.old[:14],
)
def test_each_checkpoint_catches_a_wrong_investor_sheet(tmp_path, edit, broken):
    ts, iis = pair(tmp_path, edits=[edit])
    assert broken in {(rid, st) for rid, _, st in bad(iis)}, bad(iis)
    assert ts.report.status == PASS and not ts.fills_sheet  # 投資人須知沒通過：說明書不回填


def test_page_count_and_header_total_must_be_four(tmp_path):
    _, iis = pair(tmp_path, Spec(iis_pages=5))
    assert {("iis.pages", "pages", MISMATCH)} <= bad(iis)


def test_non_memory_investor_sheet_must_not_mention_memory(tmp_path):
    """CT8 型：非記憶式商品的收益分配事項寫了「記憶式」（2026-10-14 確認判不一致）。"""
    spec = Spec(memory=False, count=1, tenor=12, first_callable=11, ko=Decimal("100.00"))
    _, iis = pair(tmp_path, spec, edits=[Edit("× m/M ×", "× m/M（記憶式自動提前出場事件）×", "iis.coupon")])
    assert bad(iis) == {("iis.memory_wording", "memory_wording", MISMATCH)}


def test_memory_sentences_must_agree(tmp_path):
    _, iis = pair(
        tmp_path, edits=[Edit("到期給付金額：本商品若未發生記憶式", "到期給付金額：本商品若未發生", "iis.redeem")]
    )
    assert ("field.ko_memory", "ko_memory", REVIEW) in bad(iis)


def test_has_autocall_must_match_the_term_sheet(tmp_path):
    """說明書有自動提前出場、投資人須知寫成沒有：有無自動提前出場不一致。"""
    ts_spec = Spec(ki="none")
    iis_only = Spec(autocall=False, ki="none", tenor=6)
    ts, iis = pair(tmp_path, ts_spec)
    build_iis_pdf(iis.term_sheet, iis_only)  # 換掉投資人須知
    sheet = tmp_path / "FCN參考條件.xlsx"
    items = check_all(sheet, [ts.term_sheet]).items
    redo = next(i for i in items if i.term_sheet == iis.term_sheet)
    assert ("iis.has_autocall", "has_autocall", MISMATCH) in bad(redo)


# ---------------------------------------------------------------- 範本辨識


@pytest.mark.parametrize(
    "build",
    [
        lambda p: synth.build_pdf(p, synth.Spec()),
        lambda p: ms_synth.build_pdf(p, ms_synth.Spec()),
        lambda p: bnp_synth.build_pdf(p, bnp_synth.Spec()),
        lambda p: sg_synth.build_pdf(p, sg_synth.Spec()),
    ],
    ids=["BARC", "MS", "BNP", "SG"],
)
def test_other_issuers_investor_sheets_are_not_citi(tmp_path, build):
    ts = build(tmp_path / "159199990001_TS.pdf")
    iis = ts.with_name("159199990001_IIS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    r = next(i.report for i in check_all(sheet, [ts, iis]).items if i.term_sheet == iis)
    assert r.template != CITI.iis.template_id


def test_blank_sheet_pricing_with_a_vwap_term_sheet_compares_iis_prices_with_the_term_sheet(tmp_path):
    """表上期初定價空白、說明書表頭寫 VWAP：投資人須知的價格改和說明書比，表上價格空白也通過（Issue #246）。"""
    blank = {f"UL_{i}_{p}": None for i in range(1, 4) for p in ("進場價", "執行價", "下限價", "KO價")}
    ts, iis = pair(tmp_path, VARIANTS["VWAP"], 期初定價=None, **blank)
    assert bad(iis) == set(), bad(iis)
    assert ts.report.status == PASS and ts.fills_sheet
