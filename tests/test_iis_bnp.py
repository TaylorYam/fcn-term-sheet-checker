"""BNP 中文投資人須知（Issue #197）：docs/templates/bnp-zh-iis.md、docs/rules/iis-check-rules.md §3 BNP 欄。

合成 PDF 經公開批量入口核對（說明書＋投資人須知＋參考條件表一列）；商品、標的與價格皆虛構。
"""

from __future__ import annotations

from decimal import Decimal

import fitz
import pytest

import ms_synth
import synth
from bnp_synth import ADDRESS, DIST, GUARANTOR, ISSUER, PHONE, SPLIT, WARNING, Spec, build_pdf, check_pair
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BNP, detect_iis
from fcn_checker.parsers import bnp as bnp_parser
from harness import MISMATCH, NA, PASS, REVIEW, REVIEW_STANDARD, iis_path, load_config
from pdf_writer import Edit, zh_date

S = Spec()
TRADE, ISSUE, FINAL, MATURITY = (zh_date(d) for d in (S.trade_date, S.issue_date, S.ends[-1], S.payments[-1]))
SHORT = GUARANTOR["ch1"]


def bad(item) -> set[tuple[str, str, str]]:
    """沒通過的結果：(rule_id, 欄位, 狀態)。"""
    return {(r.rule_id, r.field, r.status) for r in item.report.results if r.status not in (PASS, NA)}


def lines_of(path) -> list:
    with fitz.open(path) as doc:
        return extract_lines(doc)


# ---------------------------------------------------------------- 一致時兩份都通過，說明書回填

VARIANTS = {
    "D 記憶式、無 KI、3 檔": Spec(),
    "D 非記憶式、單一標的、每期 KI": Spec(count=1, memory=False, ki="P"),
    "P 記憶式、到期 KI": Spec(ko_obs="P", ki="AM", first_callable=1),
    "P 非記憶式、Non-Call = 天期": Spec(ko_obs="P", memory=False, first_callable=6),
    "D 記憶式、每日 KI": Spec(ki="D"),
    "日圓（月配息率 6 位）": Spec(currency_zh="日圓", ko_obs="P", memory=False, first_callable=6),
    "英鎊、美元標的": Spec(currency_zh="英鎊", fx=Decimal("1.2500"), ki="AM", first_callable=1),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_consistent_bnp_term_sheet_and_iis_pass_and_the_term_sheet_is_filled(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert iis.report.template == "bnp-zh-iis"
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
        ("field.currency", "denomination_currency"),
        ("field.currency", "min_subscription_currency"),
        ("field.denomination", "denomination"),
        ("iis.min_subscription", "min_subscription"),
        ("field.underlyings", "underlyings"),
        ("field.strike_pct", "strike_pct"),
        ("field.ko_pct", "ko_pct"),
        ("field.tenor_months", "tenor_months"),
        ("field.trade_date", "trade_date"),
        ("iis.issue_date", "issue_date"),
        ("field.final_valuation_date", "final_valuation_date"),
        ("field.maturity_date", "maturity_date"),
        ("iis.monthly_coupon", "monthly_coupon_shown_pct"),
        ("field.ko_observation", "ko_observation"),
        ("field.ko_memory", "ko_memory"),
        ("field.ki_type", "ki_type"),
        ("doc.subscription_start_date", "subscription_start"),
        ("doc.subscription_start_date", "subscription_end"),
        ("standard.risk_level", "risk_level"),
        ("standard.fixed_warning", "fixed_warning"),
        ("standard.forbidden_wording", "forbidden_wording"),
        ("standard.issuer_name", "issuer_name_w5a"),
        ("standard.issuer_name", "issuer_name_w5b"),
        ("standard.issuer_name", "issuer_name_org"),
        ("standard.guarantor_name", "guarantor_name_org"),
        ("standard.guarantor_name", "guarantor_name_w5a"),
        ("standard.guarantor_name", "guarantor_name_w5b"),
        ("standard.guarantor_name", "guarantor_name_risk"),
        ("standard.distributor", "distributor_name_w6"),
        ("standard.distributor", "distributor_address_org"),
        ("standard.distributor", "distributor_phone_rights"),
        ("standard.fees", "分銷費用"),
        ("standard.product_type", "product_type"),
        ("standard.minimum_issue_size", "minimum_issue_size"),
        ("doc.print_date", "print_date_iis"),
    ]:
        assert (rule, field) in checked, (rule, field)
    prices = {r.item.name for r in iis.report.results if r.rule_id == "iis.underlying_prices" and r.status == PASS}
    assert {"UL_1 進場價", "UL_1 執行價", "UL_1 KO價"} <= prices
    assert ("UL_1 下限價" in prices) == (spec.ki != "none")
    assert (("field.ki_pct", "ki_pct") in checked) == (spec.ki != "none")
    # 只有 D 型寫出第一個可提前出場期；範本沒有的項目不核對、不報缺漏
    rules = {(r.rule_id, r.field) for r in iis.report.results}
    assert (("field.first_callable_period", "first_callable_period") in checked) == (spec.ko_obs == "D")
    assert not {r for r, _ in rules} & {"iis.underlying_names", "standard.risk_level_summary", "iis.issue_price"}


def test_warning_5_is_split_into_single_characters_like_the_real_samples(tmp_path):
    """真實樣本把警語 5 的「(發行機構)與法國巴黎銀行(保證機構)」匯出成一字一行；合成文件同樣如此，照常讀出。"""
    ts = build_pdf(tmp_path / f"{S.product_code}_TS.pdf", S)
    texts = [ln.text for ln in lines_of(iis_path(ts))]
    assert all(ch in texts for ch in SPLIT), "警語 5 那一段每個字各自一行"
    (tmp_path / "pair").mkdir()
    _, iis = check_pair(tmp_path / "pair", S)
    r = next(r for r in iis.report.results if r.field == "guarantor_name_w5b")
    assert r.status == PASS and r.actual == SHORT


@pytest.mark.parametrize(
    ("spec", "rule", "message"),
    [
        (Spec(iis_pages=5), "iis.pages", "預期 4 頁／實際 5 頁"),
        (Spec(iis_page_total=5), "iis.page_totals", "頁首寫「共 5 頁」，實際 4 頁"),
    ],
)
def test_page_count_and_header_total(tmp_path, spec, rule, message):
    _, iis = check_pair(tmp_path, spec)
    (r,) = [r for r in iis.report.results if r.rule_id == rule]
    assert r.status == MISMATCH and message in r.message


# ---------------------------------------------------------------- 每個檢查點的反例（只改投資人須知）


@pytest.mark.parametrize(
    ("change", "expected", "message"),
    [
        # 文件本身
        (
            ("cover", f"TDCC 商品代號為{S.product_code}", "TDCC 商品代號為082199990009"),
            ("iis.product_code", "product_code_tdcc"),
            None,
        ),
        (
            ("cover", f"受託機構商品代號為{S.product_code}", "受託機構商品代號為082199990009"),
            ("iis.product_code", "product_code_trustee"),
            None,
        ),
        # 參考條件表
        (("summary", "計價幣別：美元", "計價幣別：日圓"), ("field.currency", "currency"), None),
        (
            ("summary", "每單位商品10,000 美元", "每單位商品10,000 英鎊"),
            ("field.currency", "denomination_currency"),
            None,
        ),
        (("summary", "每單位商品10,000", "每單位商品20,000"), ("field.denomination", "denomination"), None),
        (("summary.table", "ZB2 UW", "ZB9 UW"), ("field.underlyings", "underlyings"), None),
        (("summary.table", "140.0000,", "140.0001,"), ("iis.underlying_prices", "UL_2 執行價"), None),
        (("summary.table", "即期初價格之100.00%", "即期初價格之101.00%"), ("field.ko_pct", "ko_pct"), None),
        (
            ("summary", "商品年期：6 個月期", "商品年期：7 個月期"),
            ("field.tenor_months", "tenor_months"),
            "天期(月)對不起來：參考條件表 6／投資人須知 7",
        ),
        (("summary", f"期初訂價日：{TRADE}", "期初訂價日：2030 年1 月8 日"), ("field.trade_date", "trade_date"), None),
        (
            ("summary", f"期末評價日：{FINAL}", "期末評價日：2030 年7 月20 日"),
            ("field.final_valuation_date", "final_valuation_date"),
            None,
        ),
        (("summary", f"預定為{MATURITY}", "預定為2031 年1 月2 日"), ("field.maturity_date", "maturity_date"), None),
        (
            ("coupon", f"其中C 為{S.monthly}%", "其中C 為0.8334%"),
            ("iis.monthly_coupon", "monthly_coupon_shown_pct"),
            None,
        ),
        (
            ("summary", "自第2 個配息期結束日", "自第3 個配息期結束日"),
            ("field.first_callable_period", "first_callable_period"),
            None,
        ),
        # 同商品說明書
        (("cover", "XS1999900001", "XS1999900002"), ("iis.isin", "isin"), None),
        (("cover", "(不保本)(無擔保)(下稱", "(不保本)(下稱"), ("iis.name_zh", "name_zh"), None),
        (("cover", "Fixed Coupon Early", "Fixed Coupons Early"), ("iis.name_en", "name_en"), None),
        (
            ("summary", "最低申購金額：50,000", "最低申購金額：60,000"),
            ("iis.min_subscription", "min_subscription"),
            "最低申購金額對不起來：說明書",
        ),
        (("summary", f"發行日：{ISSUE}", "發行日：2030 年1 月15 日"), ("iis.issue_date", "issue_date"), None),
        # 審查標準
        (
            ("summary", f"商品開始受理申購日期：{TRADE}至{TRADE}", f"商品開始受理申購日期：{TRADE}至2030 年1 月8 日"),
            ("doc.subscription_start_date", "subscription_end"),
            None,
        ),
        (("org", ADDRESS, "臺北市虛構路2 號"), ("standard.distributor", "distributor_address_org"), None),
        (("rest", PHONE, "02-0000-0000"), ("standard.distributor", "distributor_phone_rights"), None),
        (
            ("org", f"1 發行機構：{ISSUER}", "1 發行機構：BNP Paribas B.V."),
            ("standard.issuer_name", "issuer_name_org"),
            None,
        ),
        (
            ("org", GUARANTOR["cover"], "法商法國巴黎銀行 (BNP Paribas)"),
            ("standard.guarantor_name", "guarantor_name_org"),
            None,
        ),
        (
            ("risk", f"保證機構為{SHORT}。", "保證機構為巴黎銀行。"),
            ("standard.guarantor_name", "guarantor_name_risk"),
            None,
        ),
        (
            ("cover", "商品種類：股權連結結構型商品", "商品種類：股權連結結構型債券"),
            ("standard.product_type", "product_type"),
            None,
        ),
        (
            ("issue", "(等值)300,000 美元", "(等值)500,000 美元"),
            ("standard.minimum_issue_size", "minimum_issue_size"),
            None,
        ),
        (("cover", f"刊印日期：{TRADE}", "刊印日期：2030 年1 月9 日"), ("doc.print_date", "print_date_iis"), None),
        (
            ("warn", "最低收益率。", "最低收益率。受託投資之標的。"),
            ("standard.forbidden_wording", "forbidden_wording"),
            None,
        ),
    ],
)
def test_each_bnp_iis_check_point_reports_a_wrong_value(tmp_path, change, expected, message):
    section, old, new = change
    ts, iis = check_pair(tmp_path, edits=[Edit(old, new, f"iis.{section}")])
    assert iis.report.template == "bnp-zh-iis"
    assert bad(iis) == {(*expected, MISMATCH)}
    if message:
        assert any(message in m for m in iis.problem_messages), iis.problem_messages
    assert ts.report.status == PASS and not ts.fills_sheet, "投資人須知沒通過，說明書不回填"


DIST_PLACES = {  # 受託機構名稱各處：投資人須知原文 → 欄位
    f"雖經{DIST['name']}(受託機構)審查": "distributor_name_w4a",
    f"且{DIST['name']}(受託機構)不負": "distributor_name_w4b",
    f"。{DIST['name']}(受託機構)依法不得": "distributor_name_w4c",
    f"而非由{DIST['name']}(受託機構)所保證": "distributor_name_w5",
    f"受託機構(即{DIST['name']})": "distributor_name_w6",
}


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [(o, o.replace(DIST["name"], "虛構證券股份有限公司"), f) for o, f in DIST_PLACES.items()]
    + [
        (f"係由{ISSUER}(發行機構)", "係由BNP Paribas B.V.(發行機構)", "issuer_name_w5a"),
        (f"{ISSUER}{SPLIT}間有", f"BNP Paribas B.V.{SPLIT}間有", "issuer_name_w5b"),  # 一字一行那句
        (f"與{SHORT}(保證機構)保證", "與巴黎銀行(保證機構)保證", "guarantor_name_w5a"),
    ],
)
def test_every_name_place_in_the_warnings_is_checked(tmp_path, old, new, field):
    rule = {"distributor": "standard.distributor", "issuer": "standard.issuer_name"}.get(
        field.split("_")[0], "standard.guarantor_name"
    )
    _, iis = check_pair(tmp_path, edits=[Edit(old, new, "iis.warn")])
    assert bad(iis) == {(rule, field, MISMATCH)}


def test_distributor_name_in_the_institutions_is_checked(tmp_path):
    _, iis = check_pair(
        tmp_path, edits=[Edit(f"4 受託機構：{DIST['name']}", "4 受託機構：虛構證券股份有限公司", "iis.org")]
    )
    assert bad(iis) == {("standard.distributor", "distributor_name_org", MISMATCH)}


def test_fixed_warning_must_appear_twice(tmp_path):
    """警語 1 與商品簡介「商品風險程度」各一次；商品簡介那段被改字時只剩 1 次。"""
    _, iis = check_pair(tmp_path, edits=[Edit("受託對象僅限", "受託對象只限", "iis.summary")])
    (r,) = [r for r in iis.report.results if r.rule_id == "standard.fixed_warning"]
    assert (r.status, r.expected, r.actual) == (MISMATCH, 2, 1)


def test_risk_level_inside_the_fixed_warning_is_checked(tmp_path):
    _, iis = check_pair(tmp_path, edits=[Edit("歸類為[RR4]", "歸類為[RR3]", "iis.summary")])
    assert bad(iis) == {
        ("standard.risk_level", "risk_level", MISMATCH),
        ("standard.fixed_warning", "fixed_warning", MISMATCH),
    }


def test_all_fee_rates_are_checked(tmp_path):
    _, iis = check_pair(tmp_path, edits=[Edit("0%~5%", "0%~3%", "iis.fees")])
    assert bad(iis) == {("standard.fees", f, MISMATCH) for f in ("申購費用", "提前贖回費用", "分銷費用")}


def test_reference_sheet_ko_and_ki_terms_are_compared(tmp_path):
    """KO 觀察方式、記憶式、Non-Call、KI 型態和參考條件表比（說明書也同時不一致，錯訊各在各的文件）。"""
    _, iis = check_pair(tmp_path, Spec(), **{"KO(Freq)": "P", "KO(memo)": "N", "Non-Call(月)": 3})
    assert {(r, f) for r, f, s in bad(iis) if s == MISMATCH} == {
        ("field.ko_observation", "ko_observation"),
        ("field.ko_memory", "ko_memory"),
        ("field.first_callable_period", "first_callable_period"),
    }


def test_monthly_coupon_is_rounded_to_the_digits_shown(tmp_path):
    """月配息率 = 年利率 ÷ 12，四捨五入到文件顯示位數（日圓 6 位）；和參考條件表年利率比。"""
    spec = Spec(currency_zh="日圓", ko_obs="P", memory=False, first_callable=6)
    _, iis = check_pair(tmp_path, spec, edits=[Edit(f"其中C 為{spec.monthly}%", "其中C 為0.833334%", "iis.coupon")])
    (r,) = [r for r in iis.report.results if r.rule_id == "iis.monthly_coupon"]
    assert (r.status, r.expected, r.actual) == (MISMATCH, Decimal("0.833333"), Decimal("0.833334"))
    (tmp_path / "sheet").mkdir()
    _, iis = check_pair(tmp_path / "sheet", Spec(), **{"Coupon p.a. (%)": 12.6})
    assert {f for r, f, s in bad(iis) if r == "iis.monthly_coupon"} == {"monthly_coupon_shown_pct"}


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        (
            ("summary", "的每一個預定交易日(惟可於", "的每個預定交易日(惟可於"),
            {"ko_observation", "first_callable_period"},
        ),
        (("coupon", "每一連結標的皆曾經於", "每一連結標的皆曾於"), {"ko_memory"}),
        (("fees", "申購價金的 0%~5%", "申購價金的 0%~5% 或 0%~3%"), {"申購費用", "分銷費用"}),
    ],
)
def test_wording_outside_the_template_requires_review(tmp_path, change, fields):
    section, old, new = change
    _, iis = check_pair(tmp_path, edits=[Edit(old, new, f"iis.{section}")])
    found = {f for _, f, s in bad(iis) if s == REVIEW}
    assert fields <= found and not {s for *_, s in bad(iis)} - {REVIEW}


def test_unknown_ki_wording_requires_review(tmp_path):
    _, iis = check_pair(
        tmp_path,
        Spec(ki="AM"),
        edits=[Edit("「觸及生效事件觀察日」：指期末評價日", "「觸及生效事件觀察日」：指每月", "iis.coupon")],
    )
    assert ("field.ki_type", "ki_type", REVIEW) in bad(iis)


def test_vwap_iis_prices_are_compared_with_the_term_sheet(tmp_path):
    spec = Spec()
    blank = {f"UL_{i}_{c}": None for i in range(1, 4) for c in ("進場價", "執行價", "下限價", "KO價")}
    vwap = {"期初定價": "VWAP", **blank}
    ts, iis = check_pair(tmp_path, spec, **vwap)
    assert iis.report.status == PASS, iis.problem_messages
    (tmp_path / "bad").mkdir()
    _, iis = check_pair(tmp_path / "bad", spec, edits=[Edit("140.0000,", "140.0001,", "iis.summary.table")], **vwap)
    assert list(iis.problem_messages) == ["UL_2 執行價對不起來：說明書 140.0000／投資人須知 140.0001"]


def test_term_sheet_values_unavailable_require_review_on_the_iis(tmp_path):
    """同商品說明書讀不到最低申購金額時，投資人須知那項轉人工覆核（說明書本身另有錯訊）。"""
    ts, iis = check_pair(tmp_path, edits=[Edit("最低申購金額：", "最低購買金額：", "ts.ch四")])
    r = next(r for r in iis.report.results if r.rule_id == "iis.min_subscription")
    assert (r.status, r.reason_code) == (REVIEW, "term_sheet_unavailable")
    assert ts.report.status != PASS


def test_guarantor_short_name_missing_from_the_review_standard_requires_review(tmp_path):
    """審查標準沒有簡稱（guarantor_name.bnp.ch1）時，寫簡稱的三處轉人工覆核，寫全名那處照常核對。"""
    standard = tmp_path / "standard.toml"
    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    standard.write_text(text.replace(f'ch1 = "{SHORT}"\n', "", 1), encoding="utf-8")
    assert standard.read_text(encoding="utf-8") != text
    _, iis = check_pair(tmp_path, config=load_config(review_standard=standard))
    assert {(f, s) for r, f, s in bad(iis) if r == "standard.guarantor_name"} == {
        (f, REVIEW) for f in ("guarantor_name_w5a", "guarantor_name_w5b", "guarantor_name_risk")
    }
    assert any("guarantor_name.bnp.ch1" in m for m in iis.problem_messages)


# ---------------------------------------------------------------- 範本辨識


def test_bnp_iis_template_is_not_confused_with_other_templates(tmp_path):
    ts = build_pdf(tmp_path / f"{S.product_code}_TS.pdf", S)
    bnp_iis_lines = lines_of(iis_path(ts))
    issuer, r = detect_iis(bnp_iis_lines)
    assert issuer is BNP and r.actual == "bnp-zh-iis"
    assert not bnp_parser.detect(bnp_iis_lines).matched, "BNP 投資人須知不是 BNP 說明書"
    assert detect_iis(lines_of(ts), (BNP,))[0] is None, "BNP 說明書不是 BNP 投資人須知"
    barc = synth.build_iis_pdf(tmp_path / "029199990001_IIS.pdf", synth.Spec())
    ms_ts = ms_synth.build_pdf(tmp_path / "147199990001_TS.pdf", ms_synth.Spec())
    for other in (barc, iis_path(ms_ts)):
        assert detect_iis(lines_of(other), (BNP,))[0] is None, other.name


def test_a_different_issuer_in_the_institutions_is_not_a_bnp_iis(tmp_path):
    ts, iis = check_pair(
        tmp_path, edits=[Edit(f"1 發行機構：{ISSUER}", "1 發行機構：Fictional Issuer B.V.", "iis.org")]
    )
    r = next(r for r in iis.report.results if r.rule_id == "template.detect")
    assert (r.status, r.reason_code) == (REVIEW, "template_unknown")
    assert "相關機構的發行機構不是BNP" in r.message
    assert ts.report.status == PASS and not ts.fills_sheet


def test_warning_text_is_the_bnp_version(tmp_path):
    """固定警語全文用 BNP 版本（[RR4]、OSU 客戶句）：預設合成文件逐字相符兩次。"""
    _, iis = check_pair(tmp_path)
    (r,) = [r for r in iis.report.results if r.rule_id == "standard.fixed_warning"]
    assert (r.status, r.actual) == (PASS, 2)
    assert "[RR4]" in WARNING


def test_a_wrong_ko_pct_on_one_underlying_requires_review(tmp_path):
    """各檔的自動提前贖回價格百分比不同：讀不出單一 KO %，轉人工覆核（同說明書第 11 項）。"""
    _, iis = check_pair(
        tmp_path, edits=[Edit("200.0000,即期初價格之100.00%", "200.0000,即期初價格之101.00%", "iis.summary.table")]
    )
    assert bad(iis) == {("field.ko_pct", "ko_pct", REVIEW)}
