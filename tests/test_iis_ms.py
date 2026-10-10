"""MS 中文投資人須知（Issue #137）：docs/templates/ms-zh-iis.md、docs/rules/iis-check-rules.md §3 MS 欄。

合成 PDF 經公開批量入口核對（說明書＋投資人須知＋參考條件表一列）；商品、標的與價格皆虛構。
"""

from __future__ import annotations

import datetime as dt

import fitz
import pytest

import hsbc_synth
import synth
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import MS, detect_iis
from fcn_checker.parsers import ms as ms_parser
from harness import MISMATCH, NA, PASS, REVIEW, iis_path
from ms_synth import DIST, ISSUE, TRADE, WARNING, Spec, build_pdf, check_pair
from pdf_writer import Edit, zh_date

S = Spec()
FINAL, FIRST_END, MONTHLY = zh_date(S.ends[-1]), zh_date(S.ends[0]), f"{S.monthly}%"
REDEEM_FROM = zh_date(ISSUE + dt.timedelta(days=1))


def bad(item) -> set[tuple[str, str, str]]:
    """沒通過的結果：(rule_id, 欄位, 狀態)。"""
    return {(r.rule_id, r.field, r.status) for r in item.report.results if r.status not in (PASS, NA)}


def lines_of(path) -> list:
    with fitz.open(path) as doc:
        return extract_lines(doc)


# ---------------------------------------------------------------- 一致時兩份都通過，說明書回填

VARIANTS = {
    "D 記憶式、到期 KI、2 檔": Spec(),
    "D 非記憶式、單一標的": Spec(count=1, memory=False),
    "P 記憶式、每日 KI": Spec(ko_obs="P", ki="D"),
    "P 非記憶式、Non-Call = 天期、無 KI": Spec(ko_obs="P", memory=False, ki="none", first_callable=4),
    "D 記憶式、無 KI": Spec(ki="none"),
    "每期觀察 KI": Spec(ki="P"),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_consistent_ms_term_sheet_and_iis_pass_and_the_term_sheet_is_filled(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert iis.report.template == "ms-zh-iis"
    assert bad(iis) == set() and iis.report.status == PASS
    assert ts.report.status == PASS and ts.fills_sheet
    checked = {(r.rule_id, r.field) for r in iis.report.results if r.status == PASS}
    for rule, field in [
        ("iis.pages", "pages"),
        ("iis.page_totals", "page_totals"),
        ("iis.isin", "isin"),
        ("iis.name_zh", "name_zh"),
        ("iis.name_en", "name_en"),
        ("iis.underlying_names", "underlying_names"),
        ("field.currency", "currency"),
        ("field.tenor_months", "tenor_months"),
        ("field.trade_date", "trade_date"),
        ("iis.issue_date", "issue_date"),
        ("field.maturity_date", "maturity_date"),
        ("field.final_valuation_date", "final_valuation_date"),
        ("iis.monthly_coupon", "monthly_coupon_fixed"),
        ("field.ko_observation", "ko_observation"),
        ("field.ko_memory", "ko_memory"),
        ("field.first_callable_period", "first_callable_period"),
        ("field.ki_type", "ki_type"),
        ("iis.redemption_start", "redemption_start"),
        ("iis.product_type", "product_type"),
        ("standard.risk_level", "risk_level_summary"),
        ("standard.fixed_warning", "fixed_warning"),
        ("standard.forbidden_wording", "forbidden_wording"),
        ("standard.issuer_name", "issuer_name_org"),
        ("standard.issuer_name", "issuer_name_summary"),
        ("standard.distributor", "distributor_name_w9"),
        ("standard.distributor", "distributor_address_org"),
        ("standard.fees", "分銷費用"),
        ("doc.print_date", "print_date_iis"),
    ]:
        assert (rule, field) in checked, (rule, field)
    daily = {("iis.ko_observation_dates", "ko_observation_start"), ("iis.monthly_coupon", "monthly_coupon_formula")}
    assert daily <= checked if spec.ko_obs == "D" else not daily & {(r.rule_id, r.field) for r in iis.report.results}
    # 範本沒有的項目不核對、不報缺漏
    rules = {r.rule_id for r in iis.report.results}
    assert not rules & {"iis.product_code", "field.denomination", "field.underlyings", "iis.underlying_prices"}


def test_old_template_is_not_recognised_and_nothing_is_filled(tmp_path):
    ts, iis = check_pair(tmp_path, edits=[Edit("International Plc", "International plc", "iis.cover")])
    r = next(r for r in iis.report.results if r.rule_id == "template.detect")
    assert (r.status, r.reason_code) == (REVIEW, "template_unknown")
    assert "舊版範本不支援" in r.message
    assert ts.report.status == PASS and not ts.fills_sheet


@pytest.mark.parametrize(
    ("spec", "rule", "message"),
    [
        (Spec(iis_pages=5), "iis.pages", "預期 4 頁／實際 5 頁"),
        (Spec(iis_page_total=5), "iis.page_totals", "頁首寫「共 5 頁」，實際 4 頁"),
    ],
)
def test_page_count_and_footer_total(tmp_path, spec, rule, message):
    _, iis = check_pair(tmp_path, spec)
    (r,) = [r for r in iis.report.results if r.rule_id == rule]
    assert r.status == MISMATCH and message in r.message


# ---------------------------------------------------------------- 每個檢查點的反例（只改投資人須知）


@pytest.mark.parametrize(
    ("change", "expected", "message"),
    [
        # 參考條件表
        (("summary", "計價幣別：美元 (USD)", "計價幣別：日幣 (JPY)"), ("field.currency", "currency"), None),
        (
            ("summary", "商品年期：4 個月期", "商品年期：5 個月期"),
            ("field.tenor_months", "tenor_months"),
            "天期(月)對不起來：參考條件表 4／投資人須知 5",
        ),
        (
            ("summary", f"交易日：{zh_date(TRADE)}", "交易日：2030 年1 月8 日"),
            ("field.trade_date", "trade_date"),
            "交易日對不起來：參考條件表 2030-01-07／投資人須知 2030-01-08",
        ),
        (
            ("summary", f"期末定價日：{FINAL}", "期末定價日：2030 年5 月20 日"),
            ("field.final_valuation_date", "final_valuation_date"),
            None,
        ),
        (
            ("summary", f"到期日：{zh_date(S.payments[-1])}", "到期日：2031 年1 月2 日"),
            ("field.maturity_date", "maturity_date"),
            None,
        ),
        (("coupon", f"{{{MONTHLY}×", "{1.0100%×"), ("iis.monthly_coupon", "monthly_coupon_formula"), None),
        (
            ("coupon", f"固定配息率 ({MONTHLY})", "固定配息率 (1.0100%)"),
            ("iis.monthly_coupon", "monthly_coupon_fixed"),
            None,
        ),
        (
            ("redeem", "若在期末定價日，", "若在交易日（含）至期末定價日（含）間的任一共同預定交易日，"),
            ("field.ki_type", "ki_type"),
            None,
        ),
        (
            ("redeem", f"（{FIRST_END}）", "（2030 年2 月15 日）"),
            ("iis.ko_observation_dates", "ko_observation_start"),
            None,
        ),
        (
            ("redeem", f"期末定價日（{FINAL}）", "期末定價日（2030 年5 月20 日）"),
            ("iis.ko_observation_dates", "ko_observation_end"),
            "KO 觀察迄日對不起來",
        ),
        # 同商品說明書
        (("cover", "XS1999900001", "XS1999900002"), ("iis.isin", "isin"), None),
        (("cover", "(無保證機構)", "(無保證)"), ("iis.name_zh", "name_zh"), None),
        (("cover", "Fixed Coupon Notes", "Fixed Coupon Note"), ("iis.name_en", "name_en"), None),
        (
            ("summary", "虛構標的2公司", "虛構標的9公司"),
            ("iis.underlying_names", "underlying_names"),
            "標的中文名稱對不起來：說明書",
        ),
        (
            ("summary", f"開始受理贖回日期：{REDEEM_FROM}", "開始受理贖回日期：2030 年1 月16 日"),
            ("iis.redemption_start", "redemption_start"),
            "開始受理贖回日期對不起來：說明書 2030-01-15／投資人須知 2030-01-16",
        ),
        (
            ("cover", "股票與/或指數股票型基金連結結構型債券", "股票或指數股票型基金連結結構型債券"),
            ("iis.product_type", "product_type"),
            None,
        ),
        # 審查標準
        (
            ("summary", "本商品風險程度： RR4", "本商品風險程度： RR3"),
            ("standard.risk_level", "risk_level_summary"),
            None,
        ),
        (("warn", "受託對象僅限", "受託對象只限"), ("standard.fixed_warning", "fixed_warning"), None),
        (
            ("warn", "7) 範本說明。", "7) 受託投資之投資標的。"),
            ("standard.forbidden_wording", "forbidden_wording"),
            None,
        ),
        (
            ("warn", f"9) {DIST['name']}", "9) 虛構證券股份有限公司"),
            ("standard.distributor", "distributor_name_w9"),
            None,
        ),
        (("org", DIST["address"], "台北市虛構路2號"), ("standard.distributor", "distributor_address_org"), None),
        (
            ("summary", "本商品發行機構為英商摩根士丹利國際股份有限公司", "本商品發行機構為英商摩根士丹利股份有限公司"),
            ("standard.issuer_name", "issuer_name_summary"),
            None,
        ),
        (
            ("org", "Morgan Stanley & Co. International Plc.", "Morgan Stanley International Plc."),
            ("standard.issuer_name", "issuer_name_org"),
            None,
        ),
        (
            ("cover", f"刊印日期:{zh_date(TRADE)}", "刊印日期:2030 年1 月9 日"),
            ("doc.print_date", "print_date_iis"),
            None,
        ),
    ],
)
def test_each_ms_iis_check_point_reports_a_wrong_value(tmp_path, change, expected, message):
    section, old, new = change
    ts, iis = check_pair(tmp_path, edits=[Edit(old, new, f"iis.{section}")])
    assert iis.report.template == "ms-zh-iis"
    assert bad(iis) == {(*expected, MISMATCH)}
    if message:
        assert any(message in m for m in iis.problem_messages), iis.problem_messages
    assert ts.report.status == PASS and not ts.fills_sheet, "投資人須知沒通過，說明書不回填"


DIST_PLACES = {  # 受託機構名稱各處（警語 4) 三處、5)、6)）：投資人須知原文 → 欄位
    f"商品雖經{DIST['name']}審查": "distributor_name_w4a",
    f"且{DIST['name']}不負": "distributor_name_w4b",
    f"。{DIST['name']}依法不得": "distributor_name_w4c",
    f"而非由{DIST['name']}所保證": "distributor_name_w5",
    f"受託機構(即{DIST['name']})": "distributor_name_w6",
}


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [(o, o.replace(DIST["name"], "虛構證券股份有限公司"), f) for o, f in DIST_PLACES.items()]
    + [
        ("英商摩根士丹利國際股份有限公司（發行機構）", "英商虛構國際股份有限公司（發行機構）", "issuer_name_w5"),
        ("或由發行機構英商摩根士丹利國際股份有限公司", "或由發行機構英商虛構國際股份有限公司", "issuer_name_w6"),
    ],
)
def test_every_name_place_in_the_warnings_is_checked(tmp_path, old, new, field):
    rule = "standard.distributor" if field.startswith("distributor") else "standard.issuer_name"
    _, iis = check_pair(tmp_path, edits=[Edit(old, new, "iis.warn")])
    assert bad(iis) == {(rule, field, MISMATCH)}


def test_risk_level_inside_the_fixed_warning_is_checked(tmp_path):
    """警語 1) 的 RRn 寫錯：全文風險等級不一致，固定警語也不再逐字相符。"""
    _, iis = check_pair(tmp_path, edits=[Edit("歸類為RR4", "歸類為RR3", "iis.warn")])
    assert bad(iis) == {
        ("standard.risk_level", "risk_level", MISMATCH),
        ("standard.fixed_warning", "fixed_warning", MISMATCH),
    }


def test_fixed_warning_must_appear_exactly_once(tmp_path):
    _, iis = check_pair(tmp_path, edits=[Edit("8) 範本說明。", "8) " + WARNING + "。", "iis.warn")])
    (r,) = [r for r in iis.report.results if r.rule_id == "standard.fixed_warning"]
    assert (r.status, r.expected, r.actual) == (MISMATCH, 1, 2)


def test_unknown_memory_wording_requires_review(tmp_path):
    spec = Spec(count=1, memory=False)
    _, iis = check_pair(
        tmp_path, spec, edits=[Edit("該連結標的之收盤價大於或等於", "該連結標的之收盤價高於", "iis.redeem")]
    )
    assert bad(iis) == {("field.ko_memory", "ko_memory", REVIEW)}


def test_all_fee_rates_are_checked(tmp_path):
    _, iis = check_pair(tmp_path, edits=[Edit("0%~5%", "0%~3%", "iis.fees")])
    assert bad(iis) == {("standard.fees", f, MISMATCH) for f in ("申購費用", "提前贖回費用", "分銷費用")}


def test_reference_sheet_ko_terms_are_compared(tmp_path):
    """KO 觀察方式、記憶式、Non-Call 和參考條件表比（說明書也同時不一致，錯訊各在各的文件）。"""
    _, iis = check_pair(tmp_path, Spec(), **{"KO(Freq)": "P", "KO(memo)": "N", "Non-Call(月)": 2})
    assert {(r, f) for r, f, s in bad(iis) if s == MISMATCH} == {
        ("field.ko_observation", "ko_observation"),
        ("field.ko_memory", "ko_memory"),
        ("field.first_callable_period", "first_callable_period"),
    }


def test_first_callable_period_on_the_iis_is_compared_with_non_call_and_the_term_sheet_schedule(tmp_path):
    _, iis = check_pair(tmp_path, edits=[Edit("自第1 個配息週期終止日", "自第2 個配息週期終止日", "iis.redeem")])
    assert bad(iis) == {
        ("field.first_callable_period", "first_callable_period", MISMATCH),
        ("iis.ko_observation_dates", "ko_observation_start", MISMATCH),  # 起日不是說明書第 2 期終止日
    }


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        (("redeem", "每日觀察", "每日檢查"), {"ko_observation", "first_callable_period"}),
        (("redeem", "「觸及下限事件」：若在期末定價日", "「觸及下限事件」：若在每個月"), {"ki_type"}),
        (("summary", "(2) 連結標的2:", "(3) 連結標的2:"), {"underlying_names"}),
    ],
)
def test_wording_outside_the_template_requires_review(tmp_path, change, fields):
    section, old, new = change
    _, iis = check_pair(tmp_path, edits=[Edit(old, new, f"iis.{section}")])
    found = {f for _, f, s in bad(iis) if s == REVIEW}
    assert fields <= found and not {s for *_, s in bad(iis)} - {REVIEW}


def test_monthly_coupon_is_compared_with_the_annual_rate_on_the_sheet(tmp_path):
    _, iis = check_pair(tmp_path, Spec(), **{"Coupon p.a. (%)": 12.6})
    assert {f for r, f, s in bad(iis) if r == "iis.monthly_coupon"} == {
        "monthly_coupon_fixed",
        "monthly_coupon_formula",
    }


def test_term_sheet_values_unavailable_require_review_on_the_iis(tmp_path):
    """同商品說明書讀不到開始受理贖回日期時，投資人須知那項轉人工覆核（說明書本身另有錯訊）。"""
    ts, iis = check_pair(tmp_path, edits=[Edit("開始受理贖回日期：", "開始受理日期：", "ts.ch四")])
    r = next(r for r in iis.report.results if r.rule_id == "iis.redemption_start")
    assert (r.status, r.reason_code) == (REVIEW, "term_sheet_unavailable")
    assert ts.report.status != PASS


# ---------------------------------------------------------------- 範本辨識


def test_ms_iis_template_is_not_confused_with_other_templates(tmp_path):
    spec = Spec()
    ts = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    ms_iis_lines = lines_of(iis_path(ts))
    issuer, r = detect_iis(ms_iis_lines)
    assert issuer is MS and r.actual == "ms-zh-iis"
    assert not ms_parser.detect(ms_iis_lines).matched, "MS 投資人須知不是 MS 說明書"
    assert detect_iis(lines_of(ts), (MS,))[0] is None, "MS 說明書不是 MS 投資人須知"
    barc = synth.build_iis_pdf(tmp_path / "029199990001_IIS.pdf", synth.Spec())
    hsbc = hsbc_synth.build_iis_pdf(tmp_path / "325199990001_IIS.pdf", hsbc_synth.Spec())
    for other in (barc, hsbc):
        assert detect_iis(lines_of(other), (MS,))[0] is None, other.name
