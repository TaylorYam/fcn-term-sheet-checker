"""SG 中文投資人須知（Issue #213）：docs/templates/sg-zh-iis.md、docs/rules/iis-check-rules.md §3 SG 欄。

合成 PDF 經公開批量入口核對（說明書＋投資人須知＋參考條件表一列）；商品、標的與價格皆虛構。
"""

from __future__ import annotations

from decimal import Decimal

import fitz
import pytest

import bnp_synth
import ms_synth
import synth
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import SG, detect_iis
from fcn_checker.parsers import sg as sg_parser
from harness import MISMATCH, NA, PASS, REVIEW, REVIEW_STANDARD, check_all, iis_path, load_config
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row
from sg_synth import (
    ADDRESS_COVER,
    AGENT,
    DIST,
    DIST_SHORT,
    GUARANTOR,
    GUARANTOR_ORG,
    ISSUER,
    WARNING,
    Spec,
    build_iis_pdf,
    build_pdf,
    check_pair,
)

S = Spec()
TRADE, ISSUE, FINAL, MATURITY, OBS = (
    S.date(d) for d in (S.trade_date, S.issue_date, S.final_date, S.maturity_date, S.obs_start)
)


def bad(item) -> set[tuple[str, str, str]]:
    """沒通過的結果：(rule_id, 欄位, 狀態)。"""
    return {(r.rule_id, r.field, r.status) for r in item.report.results if r.status not in (PASS, NA)}


def lines_of(path) -> list:
    with fitz.open(path) as doc:
        return extract_lines(doc)


# ---------------------------------------------------------------- 一致時兩份都通過，說明書回填

VARIANTS = {
    "D 記憶式、到期 KI、3 檔": Spec(),
    "D 非記憶式、單一標的、無 KI": Spec(memory=False, ki="none", count=1, tenor=3, first_callable=1),
    "P 記憶式、每日 KI": Spec(ko_obs="P", memory=True, ki="D", tenor=10, first_callable=8),
    "P 非記憶式、每期 KI、舊版面": Spec(
        ko_obs="P", memory=False, ki="P", tenor=7, first_callable=1, legacy=True, ko=Decimal("110.00")
    ),
    "無自動提前出場、CNH quanto、舊版面、執行價表頭沒有 %": Spec(
        autocall=False,
        ki="none",
        tenor=3,
        currency_zh="境外人民幣",
        strike=Decimal("86.21"),
        units=1,
        fx=Decimal("0.0400"),
        legacy=True,
        iis_strike_pct=False,
    ),
    "日圓、4 檔": Spec(currency_zh="日圓", ko=Decimal("105.00"), count=4),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_consistent_sg_term_sheet_and_iis_pass_and_the_term_sheet_is_filled(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert iis.report.template == "sg-zh-iis"
    assert bad(iis) == set() and iis.report.status == PASS
    assert ts.report.status == PASS and ts.fills_sheet
    checked = {(r.rule_id, r.field) for r in iis.report.results if r.status == PASS}
    for rule, field in [
        ("iis.pages", "pages"),
        ("iis.product_code", "product_code_tdcc"),
        ("iis.product_code", "product_code_trustee"),
        ("iis.isin", "isin"),
        ("iis.name_zh", "name_zh"),
        ("iis.name_en", "name_en"),
        ("field.currency", "currency"),
        ("field.currency", "denomination_currency"),
        ("field.currency", "payment_currency_coupon"),
        ("field.currency", "payment_currency_maturity"),
        ("field.denomination", "denomination"),
        ("standard.issue_price", "issue_price_pct"),
        ("field.underlyings", "underlyings"),
        ("field.tenor_months", "tenor_months"),
        ("field.trade_date", "trade_date"),
        ("iis.issue_date", "issue_date"),
        ("field.final_valuation_date", "final_valuation_date"),
        ("field.maturity_date", "maturity_date"),
        ("field.coupon_pa_pct", "coupon_pa_pct"),
        ("iis.monthly_coupon", "monthly_coupon_rate"),
        ("field.ko_memory", "ko_memory"),
        ("field.ki_type", "ki_type"),
        ("iis.has_autocall", "has_autocall"),
        ("doc.subscription_start_date", "subscription_start"),
        ("standard.risk_level", "risk_level"),
        ("standard.risk_level", "risk_level_summary"),
        ("standard.fixed_warning", "fixed_warning"),
        ("standard.forbidden_wording", "forbidden_wording"),
        ("standard.issuer_name", "issuer_name_w5a"),
        ("standard.issuer_name", "issuer_name_w5b"),
        ("standard.issuer_name", "issuer_name_w6"),
        ("standard.issuer_name", "issuer_name_org"),
        ("standard.guarantor_name", "guarantor_name_w5a"),
        ("standard.guarantor_name", "guarantor_name_w5b"),
        ("standard.guarantor_name", "guarantor_name_org"),
        ("standard.agent_name", "agent_name_org"),
        ("standard.distributor", "distributor_name_w4a"),
        ("standard.distributor", "distributor_name_w6"),
        ("standard.distributor", "distributor_name_org"),
        ("standard.distributor", "distributor_address_org"),
        ("standard.fees", "分銷費用"),
        ("standard.product_type", "product_type"),
        ("doc.print_date", "print_date_iis"),
    ]:
        assert (rule, field) in checked, (rule, field)
    prices = {r.item.name for r in iis.report.results if r.rule_id == "iis.underlying_prices" and r.status == PASS}
    assert {"UL_1 進場價", "UL_1 執行價"} <= prices
    assert ("UL_1 KO價" in prices) == spec.autocall
    assert ("UL_1 下限價" in prices) == (spec.ki != "none")
    assert (("field.ki_pct", "ki_pct") in checked) == (spec.ki != "none")
    assert (("field.ko_pct", "ko_pct") in checked) == spec.autocall
    assert (("field.strike_pct", "strike_pct") in checked) == spec.iis_strike_pct
    assert (("field.ko_observation", "ko_observation") in checked) == spec.autocall
    assert (("field.currency", "payment_currency_autocall") in checked) == spec.autocall
    # 只有 P 型寫出第一個可提前出場期；D 型改核對觀察起日（說明書第 8 項）
    assert (("field.first_callable_period", "first_callable_period") in checked) == (
        spec.autocall and spec.ko_obs == "P"
    )
    assert (("iis.ko_observation_start", "ko_observation_start") in checked) == (spec.autocall and spec.ko_obs == "D")
    # 範本沒有的項目不核對、不報缺漏
    rules = {r.rule_id for r in iis.report.results}
    assert not rules & {"iis.page_totals", "iis.underlying_names", "iis.min_subscription", "iis.ko_observation_dates"}


def test_guarantor_name_without_brackets_is_an_equivalent(tmp_path):
    """相關機構壹 2. 的英文名沒有括號：審查標準 guarantor_name_equivalents.sg 的等價寫法，照常通過並寫出依據。"""
    _, iis = check_pair(tmp_path)
    r = next(r for r in iis.report.results if r.field == "guarantor_name_org")
    assert (r.status, r.actual) == (PASS, GUARANTOR_ORG.replace(" ", ""))  # 讀出值去空白
    assert "guarantor_name_equivalents.sg" in (r.tolerance or "")


@pytest.mark.parametrize(
    ("spec", "rule", "message"),
    [(Spec(iis_pages=5), "iis.pages", "預期 4 頁／實際 5 頁")],
)
def test_page_count(tmp_path, spec, rule, message):
    _, iis = check_pair(tmp_path, spec)
    (r,) = [r for r in iis.report.results if r.rule_id == rule]
    assert r.status == MISMATCH and message in r.message


# ---------------------------------------------------------------- 每個檢查點的反例（只改投資人須知）


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        # 文件本身
        (
            ("cover", f"TDCC商品代號{S.product_code}", "TDCC商品代號037199990009"),
            ("iis.product_code", "product_code_tdcc"),
        ),
        (
            ("cover", f"受託機構商品代號 {S.product_code}", "受託機構商品代號 037199990009"),
            ("iis.product_code", "product_code_trustee"),
        ),
        # 參考條件表
        (("summary", "計價幣別：美元", "計價幣別：日圓"), ("field.currency", "currency")),
        (("summary", "每單位面額：10,000美元", "每單位面額：10,000日圓"), ("field.currency", "denomination_currency")),
        (("summary", "每單位面額：10,000", "每單位面額：20,000"), ("field.denomination", "denomination")),
        (
            ("coupon", "■於配息日，發行機構將依下列計算公式以美元", "■於配息日，發行機構將依下列計算公式以日圓"),
            ("field.currency", "payment_currency_coupon"),
        ),
        (("summary.table", "ZS2 UW", "ZS9 UW"), ("field.underlyings", "underlyings")),
        (("summary.table", "140.0000美元", "140.0001美元"), ("iis.underlying_prices", "UL_2 執行價")),
        (
            ("summary.table", "200.0000美元200.0000美元", "200.0000美元200.0001美元"),
            ("iis.underlying_prices", "UL_2 KO 價"),
        ),
        (("summary.table", "120.0000美元", "120.0001美元"), ("iis.underlying_prices", "UL_2 下限價")),
        (("summary.table", "期初價格的100.00%", "期初價格的101.00%"), ("field.ko_pct", "ko_pct")),
        (("summary.table", "期初價格的60.00%", "期初價格的61.00%"), ("field.ki_pct", "ki_pct")),
        (("summary.table", "期初價格的70.00%", "期初價格的71.00%"), ("field.strike_pct", "strike_pct")),
        (("summary", "商品年期：6個月", "商品年期：7個月"), ("field.tenor_months", "tenor_months")),
        (("summary", f"訂價日：{TRADE}", "訂價日：2030年01月08日"), ("field.trade_date", "trade_date")),
        (
            ("summary", f"最終評價日：{FINAL}", "最終評價日：2030年07月20日"),
            ("field.final_valuation_date", "final_valuation_date"),
        ),
        (("summary", f"到期日：{MATURITY}", "到期日：2030年07月25日"), ("field.maturity_date", "maturity_date")),
        (("coupon", "（相當於年率13.2000%）", "（相當於年率13.3000%）"), ("field.coupon_pa_pct", "coupon_pa_pct")),
        (("coupon", "配息率 = 1.1000%", "配息率 = 1.1001%"), ("iis.monthly_coupon", "monthly_coupon_rate")),
        # 同商品說明書
        (("cover", "XS1999900001", "XS1999900002"), ("iis.isin", "isin")),
        (("cover", "（記憶式自動提前出場）", "可自動提前出場"), ("iis.name_zh", "name_zh")),
        (("cover", "Memory Autocallable", "Memory  Callable"), ("iis.name_en", "name_en")),
        (("summary", f"發行日/交割日：{ISSUE}", "發行日/交割日：2030年01月15日"), ("iis.issue_date", "issue_date")),
        (
            ("coupon", f"觀察期間：自{OBS}起", "觀察期間：自2030年04月09日起"),
            ("iis.ko_observation_start", "ko_observation_start"),
        ),
        # 審查標準
        (
            ("summary", f"商品開始受理申購日期：{TRADE}起", "商品開始受理申購日期：2030年01月08日起"),
            ("doc.subscription_start_date", "subscription_start"),
        ),
        (("summary", "本商品風險程度為RR4", "本商品風險程度為RR3"), ("standard.risk_level", "risk_level_summary")),
        (
            ("summary", "發行價格：每單位面額的100%", "發行價格：每單位面額的99%"),
            ("standard.issue_price", "issue_price_pct"),
        ),
        (("org", f"{ISSUER}(地址：", "SG ISSUER LTD(地址："), ("standard.issuer_name", "issuer_name_org")),
        (("org", GUARANTOR_ORG, "法商法國興業銀行Societe Generale"), ("standard.guarantor_name", "guarantor_name_org")),
        (
            ("org", f"3. 總代理人：{AGENT['cover']}", "3. 總代理人：虛構銀行台北分行"),
            ("standard.agent_name", "agent_name_org"),
        ),
        (
            ("org", f"受託或銷售機構：{DIST['name']}", "受託或銷售機構：虛構證券股份有限公司"),
            ("standard.distributor", "distributor_name_org"),
        ),
        (("org", ADDRESS_COVER, "台北市虛構路2號"), ("standard.distributor", "distributor_address_org")),
        (
            ("cover", "商品種類：股權連結結構型債券", "商品種類：股權連結結構型商品"),
            ("standard.product_type", "product_type"),
        ),
        (("cover", "刊印日期：2030年1月7日", "刊印日期：2030年1月9日"), ("doc.print_date", "print_date_iis")),
        (
            ("warn", "為於中華民國境內受託買賣", "為於中華民國境內受託投資"),
            ("standard.forbidden_wording", "forbidden_wording"),
        ),
    ],
)
def test_each_sg_iis_check_point_reports_a_wrong_value(tmp_path, change, expected):
    section, old, new = change
    ts, iis = check_pair(tmp_path, edits=[Edit(old, new, f"iis.{section}")])
    assert iis.report.template == "sg-zh-iis"
    status = REVIEW if expected[0] == "standard.issue_price" else MISMATCH  # 發行價格不同時轉人工覆核（同說明書）
    assert bad(iis) == {(*expected, status)}
    assert ts.report.status == PASS and not ts.fills_sheet, "投資人須知沒通過，說明書不回填"


NAME_PLACES = {  # 警語裡的名稱各處：投資人須知原文 → (rule_id, 欄位, 改成)
    f"本商品雖經{DIST_SHORT}審查": ("standard.distributor", "distributor_name_w4a", "虛構證券"),
    f"且{DIST_SHORT}不負": ("standard.distributor", "distributor_name_w4b", "虛構證券"),
    f"。{DIST_SHORT}依法不得": ("standard.distributor", "distributor_name_w4c", "虛構證券"),
    f"而非由{DIST_SHORT}所保證": ("standard.distributor", "distributor_name_w5", "虛構證券"),
    f"（即{DIST_SHORT}）": ("standard.distributor", "distributor_name_w6", "虛構證券"),
    f"係由{ISSUER}（發行機構）": ("standard.issuer_name", "issuer_name_w5a", "SG ISSUANCE"),
    f"所保證。{ISSUER}（發行機構）": ("standard.issuer_name", "issuer_name_w5b", "SG ISSUANCE"),
    f"應由{ISSUER}（發行機構）": ("standard.issuer_name", "issuer_name_w6", "SG ISSUANCE"),
    f"或{GUARANTOR['cover']}（保證機構）保證": ("standard.guarantor_name", "guarantor_name_w5a", "法商興業銀行"),
    f"與{GUARANTOR['cover']}（保證機構）有": ("standard.guarantor_name", "guarantor_name_w5b", "法商興業銀行"),
}


@pytest.mark.parametrize("old", NAME_PLACES, ids=[f for _, f, _ in NAME_PLACES.values()])
def test_every_name_place_in_the_warnings_is_checked(tmp_path, old):
    rule, field, name = NAME_PLACES[old]
    target = {"standard.distributor": DIST_SHORT, "standard.issuer_name": ISSUER}.get(rule, GUARANTOR["cover"])
    _, iis = check_pair(tmp_path, edits=[Edit(old, old.replace(target, name), "iis.warn")])
    assert bad(iis) == {(rule, field, MISMATCH)}


def test_fixed_warning_appears_once_in_warning_one(tmp_path):
    _, iis = check_pair(tmp_path)
    (r,) = [r for r in iis.report.results if r.rule_id == "standard.fixed_warning"]
    assert (r.status, r.actual) == (PASS, 1)
    (tmp_path / "bad").mkdir()
    _, iis = check_pair(tmp_path / "bad", edits=[Edit("受託對象僅限", "受託對象只限", "iis.warn")])
    (r,) = [r for r in iis.report.results if r.rule_id == "standard.fixed_warning"]
    assert (r.status, r.expected, r.actual) == (MISMATCH, 1, 0)
    assert WARNING.count("受託對象僅限") == 1


def test_all_fee_rates_are_checked(tmp_path):
    _, iis = check_pair(tmp_path, edits=[Edit("0%~5%", "0%~3%", "iis.fees")])
    assert bad(iis) == {("standard.fees", f, MISMATCH) for f in ("申購費用", "提前贖回費用", "分銷費用")}


def test_reference_sheet_ko_and_ki_terms_are_compared(tmp_path):
    """P 型：KO 觀察方式、記憶式、Non-Call、KI 型態和參考條件表比（說明書也同時不一致，錯訊各在各的文件）。"""
    spec = Spec(ko_obs="P", memory=True, ki="D", tenor=10, first_callable=8)
    _, iis = check_pair(tmp_path, spec, **{"KO(Freq)": "D", "KO(memo)": "N", "Non-Call(月)": 7, "KI(Freq)": "AM"})
    assert {(r, f) for r, f, s in bad(iis) if s == MISMATCH} == {
        ("field.ko_observation", "ko_observation"),
        ("field.ko_memory", "ko_memory"),
        ("field.first_callable_period", "first_callable_period"),
        ("field.ki_type", "ki_type"),
    }


def test_vwap_iis_prices_are_compared_with_the_term_sheet(tmp_path):
    blank = {f"UL_{i}_{c}": None for i in range(1, 4) for c in ("進場價", "執行價", "下限價", "KO價")}
    vwap = {"期初定價": "VWAP", **blank}
    _, iis = check_pair(tmp_path, Spec(), **vwap)
    assert iis.report.status == PASS, iis.problem_messages
    (tmp_path / "bad").mkdir()
    _, iis = check_pair(
        tmp_path / "bad", Spec(), edits=[Edit("140.0000美元", "140.0001美元", "iis.summary.table")], **vwap
    )
    assert list(iis.problem_messages) == ["UL_2 執行價對不起來：說明書 140.0000／投資人須知 140.0001"]


# ---------------------------------------------------------------- 範本專屬規則：有無自動提前出場、D 型觀察起日


def _pair_from(tmp_path, ts_spec: Spec, iis_spec: Spec):
    """說明書與投資人須知用不同規格合成（參考條件表依說明書）。"""
    ts = build_pdf(tmp_path / f"{ts_spec.product_code}_TS.pdf", ts_spec, iis=False)
    build_iis_pdf(iis_path(ts), iis_spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(ts_spec)])
    outcome = check_all(sheet, [ts, iis_path(ts)])
    return next(i for i in outcome.items if i.term_sheet != ts)


def test_iis_without_autocall_for_an_autocallable_term_sheet_is_reported(tmp_path):
    iis = _pair_from(tmp_path, Spec(ki="none", tenor=3), Spec(autocall=False, ki="none", tenor=3))
    assert ("iis.has_autocall", "has_autocall", MISMATCH) in bad(iis)
    r = next(r for r in iis.report.results if r.rule_id == "iis.has_autocall")
    assert (r.expected, r.actual) == ("有", "沒有")


def test_ko_observation_start_is_compared_with_the_term_sheet(tmp_path):
    """D 型觀察起日 = 說明書第 8 項觀察期間起日；投資人須知寫成下一期的日期時只報這一項。"""
    iis = _pair_from(tmp_path, Spec(), Spec(first_callable=3))
    assert bad(iis) == {("iis.ko_observation_start", "ko_observation_start", MISMATCH)}


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        (("coupon", "記憶事件觀察期間：自", "記憶事件觀察期：自"), {"ko_observation", "first_callable_period"}),
        (("coupon", "所有連結標的 發生記憶事件", "全部連結標的 發生記憶事件"), {"ko_memory"}),
        (("coupon", "(1) 若於最終評價日", "(1) 若於評價日"), {"ki_type"}),
        (("summary.table", "200.0000美元200.0000美元", "200.0000美元"), {"price_table"}),
        (("fees", "申購價金的", "申購價款的"), {"申購費用", "分銷費用"}),
    ],
)
def test_wording_outside_the_template_requires_review(tmp_path, change, fields):
    section, old, new = change
    _, iis = check_pair(tmp_path, edits=[Edit(old, new, f"iis.{section}")])
    found = {f for _, f, s in bad(iis) if s == REVIEW}
    assert fields <= found, bad(iis)


def test_agent_name_missing_from_the_review_standard_requires_review(tmp_path):
    standard = tmp_path / "standard.toml"
    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    old = f'[agent_name.sg]\ncover = "{AGENT["cover"]}"\n'
    assert old in text
    standard.write_text(text.replace(old, "[agent_name.sg]\n"), encoding="utf-8")
    (tmp_path / "pair").mkdir()
    _, iis = check_pair(tmp_path / "pair", config=load_config(review_standard=standard))
    assert ("standard.agent_name", "agent_name_org", REVIEW) in bad(iis)
    assert any("agent_name.sg.cover" in m for m in iis.problem_messages)


# ---------------------------------------------------------------- 範本辨識


def test_sg_iis_template_is_not_confused_with_other_templates(tmp_path):
    ts = build_pdf(tmp_path / f"{S.product_code}_TS.pdf", S)
    sg_iis_lines = lines_of(iis_path(ts))
    issuer, r = detect_iis(sg_iis_lines)
    assert issuer is SG and r.actual == "sg-zh-iis"
    assert not sg_parser.detect(sg_iis_lines).matched, "SG 投資人須知不是 SG 說明書"
    assert detect_iis(lines_of(ts), (SG,))[0] is None, "SG 說明書不是 SG 投資人須知"
    barc = synth.build_iis_pdf(tmp_path / "029199990001_IIS.pdf", synth.Spec())
    ms_ts = ms_synth.build_pdf(tmp_path / "147199990001_TS.pdf", ms_synth.Spec())
    bnp_ts = bnp_synth.build_pdf(tmp_path / "082199990001_TS.pdf", bnp_synth.Spec())
    for other in (barc, iis_path(ms_ts), iis_path(bnp_ts)):
        assert detect_iis(lines_of(other), (SG,))[0] is None, other.name


def test_a_different_issuer_in_the_institutions_is_not_an_sg_iis(tmp_path):
    ts, iis = check_pair(tmp_path, edits=[Edit(f"{ISSUER}(地址：", "Fictional Issuer(地址：", "iis.org")])
    r = next(r for r in iis.report.results if r.rule_id == "template.detect")
    assert (r.status, r.reason_code) == (REVIEW, "template_unknown")
    assert "相關機構的發行機構不是SG" in r.message
    assert ts.report.status == PASS and not ts.fills_sheet
