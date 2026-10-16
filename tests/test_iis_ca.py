"""CA 投資人須知黑箱回歸（Issue #274）：合成說明書＋投資人須知經批量入口；不直接測擷取或解析的內部函式。

真實投資人須知日期的「年」是 CJK 相容字 U+F98E，合成字型畫不出來，最後一個測試直接用擷取後的行測正規化。
"""

from __future__ import annotations

import pytest

from ca_synth import DIST, WARNING, Spec, check_iis
from fcn_checker.issuers import CA
from fcn_checker.parsers import ca_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import Line
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from test_check_ca import VARIANTS


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass(tmp_path, spec):
    r = check_iis(tmp_path, spec).report
    assert r.template == CA.iis.template_id
    assert r.status == S.PASS, problems(r)
    assert rule(r, "iis.page_totals")[0].status == S.PASS
    assert rule(r, "iis.monthly_coupon")[0].status == S.PASS
    assert rule(r, "iis.has_ki")[0].status == S.PASS
    assert [x.status for x in rule(r, "iis.redemption_pct")] == [S.PASS, S.PASS]  # (b)、(c)（Issue #319）
    assert len(rule(r, "field.currency")) == 6  # 計價幣別、剩餘金額、投資本金、利率風險 3 處
    assert rule(r, "standard.fixed_warning")[0].actual == 1  # 警語 1.
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS  # 警語 6.「受託投資或銷售時」是允許片語
    assert [x.status for x in rule(r, "doc.print_date")] == [S.PASS]
    assert bool([x for x in r.results if x.rule_id == "field.first_callable_period"]) == (spec.ko_obs == "D")
    nothing = ("field.ki_type", "field.ki_pct", "field.strike_pct", "field.ko_pct", "iis.underlying_prices")
    assert not [x for x in r.results if x.rule_id in nothing]  # 沒有價格表、%、界限事件定義


def test_five_pages_is_a_mismatch(tmp_path):
    r = check_iis(tmp_path, Spec(iis_pages=5)).report
    assert rule(r, "iis.pages")[0].status == S.MISMATCH


def test_ki_presence_must_match_the_term_sheet(tmp_path):
    """投資人須知到期贖回寫「若未發生界限事件」、說明書卻是無 KI：判不一致。"""
    edit = Edit("(i) 若最差表現之連結標的期末訂價低於執行價：交付", "(i) 若未發生界限事件：交付", "iis.coupon")
    r = check_iis(tmp_path, edits=[edit]).report
    assert rule(r, "iis.has_ki")[0].status == S.MISMATCH


def test_ki_on_the_term_sheet_must_appear_on_the_investor_sheet(tmp_path):
    """說明書有界限事件、投資人須知到期贖回卻直接比執行價：判不一致。"""
    edit = Edit(
        "(i) 若未發生界限事件，則到期贖回金額為",
        "(i) 若最差表現之連結標的期末訂價低於執行價：到期贖回金額為",
        "iis.coupon",
    )
    r = check_iis(tmp_path, Spec(ki="AM", count=1, memory=False), edits=[edit]).report
    assert rule(r, "iis.has_ki")[0].status == S.MISMATCH


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("配息率0.7500%，換算", "配息率0.7600%，換算", "iis.coupon"), "iis.monthly_coupon"),
        (Edit("換算年利率為9.00%", "換算年利率為9.50%", "iis.coupon"), "field.coupon_pa_pct"),
        (Edit("或任何先前的自動提前出場觀察日", "", "iis.coupon"), "field.ko_memory"),
        (Edit("自第1 個配息期結束日", "自第2 個配息期結束日", "iis.coupon"), "field.first_callable_period"),
        (Edit("(10) 商品年期：6 個月", "(10) 商品年期：7 個月", "iis.summary"), "field.tenor_months"),
        (Edit("ZCB UN", "ZCX UN", "iis.summary"), "field.underlyings"),
        (
            Edit(
                "Financial Solutions (營業所在地",
                "Financial Solutions S.A. (營業所在地",
                "iis.org",
            ),
            "standard.issuer_name",
        ),
        (Edit(f"地址：{DIST['address']}", "地址：台北市大安區虛構路2號", "iis.org"), "standard.distributor"),
        (Edit("申購價金的0%~5%", "申購價金的0%~3%", "iis.fees"), "standard.fees"),
        (Edit(WARNING[:20], WARNING[:19] + "X", "iis.warn"), "standard.fixed_warning"),
        (Edit("刊印日期：2030 年01 月08 日", "刊印日期：2030 年01 月20 日", "iis.cover"), "doc.print_date"),
        (Edit("(6) 計價幣別：美元(USD)", "(6) 計價幣別：日圓(JPY)", "iis.summary"), "field.currency"),
        (Edit("以美元支付剩餘金額", "以日圓支付剩餘金額", "iis.coupon"), "field.currency"),
        # Issue #319：說明文字的幣別字、贖回 %
        (Edit("低於100%美元投資本金", "低於100%日圓投資本金", "iis.coupon"), "field.currency"),
        (Edit("當美元利率下跌", "當日圓利率下跌", "iis.rest"), "field.currency"),
        (Edit("商品面額x100%", "商品面額x99%", "iis.coupon"), "iis.redemption_pct"),
        (Edit("商品面額x 100%", "商品面額x 99%", "iis.coupon"), "iis.redemption_pct"),
        (Edit("(TDCC 商品代號：086199990001)", "(TDCC 商品代號：086199990002)", "iis.cover"), "iis.product_code"),
        (
            Edit("(Unsecured)(Non-principal protected)", "(Secured)(Non-principal protected)", "iis.cover"),
            "iis.name_en",
        ),
        (Edit("本商品風險等級為RR4", "本商品風險等級為RR5", "iis.summary"), "standard.risk_level"),
        (Edit("受託投資或銷售時", "受託投資時", "iis.warn"), "standard.forbidden_wording"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_items_are_checked(tmp_path, edit, broken):
    r = check_iis(tmp_path, edits=[edit]).report
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_compatibility_ideograph_year_is_normalised():
    """真實投資人須知日期的「年」是 U+F98E（外觀和「年」相同）：正規化後日期照讀（同 GS 投資人須知的「利」）。"""
    texts = [
        "(10) 商品年期：6 個月。",
        "(11) 期初訂價日：2030 年01 月07 日。",
        "(12) 發行日： 2030 年01 月14 日。",
        "中文投資人須知刊印日期：2030 年01 月08 日",
    ]
    lines = [Line(1, 30.0, 100.0 + 15 * i, 560.0, 112.0 + 15 * i, t) for i, t in enumerate(texts)]
    sheet = ca_iis.read(lines)
    assert sheet.f("trade_date").ok and str(sheet.f("trade_date").value) == "2030-01-07"
    assert str(sheet.f("issue_date").value) == "2030-01-14"
    assert str(sheet.f("print_dates").value[0].value.value) == "2030-01-08"


@pytest.mark.parametrize(
    ("edit", "field"),
    [
        (
            Edit("4. 本商品雖經玉山綜合證券股份有限公司審查", "4. 本商品雖經玉山綜合證券有限公司審查", "iis.warn"),
            "distributor_name_w4a",
        ),
        (
            Edit("價值，玉山綜合證券股份有限公司不負", "價值，玉山綜合證券有限公司不負", "iis.warn"),
            "distributor_name_w4b",
        ),
        (Edit("責。玉山綜合證券股份有限公司依法", "責。玉山綜合證券有限公司依法", "iis.warn"), "distributor_name_w4c"),
        (
            Edit("而非由玉山綜合證券股份有限公司所保證", "而非由玉山綜合證券有限公司所保證", "iis.warn"),
            "distributor_name_w5",
        ),
        (
            Edit("(即玉山綜合證券股份有限公司)負責外", "(即玉山綜合證券有限公司)負責外", "iis.warn"),
            "distributor_name_w6",
        ),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:14],
)
def test_warning_distributor_name_is_checked(tmp_path, edit, field):
    """警語裡的受託機構名稱每處都算名稱出處（Issue #306）：只有這一處錯，相關機構名稱正確。"""
    r = check_iis(tmp_path, edits=[edit]).report
    assert ("standard.distributor", field) in {(rid, f) for rid, f, _ in problems(r)}, problems(r)


def test_warning_distributor_names_are_read(tmp_path):
    r = check_iis(tmp_path).report
    assert {x.field for x in rule(r, "standard.distributor")} >= {
        "distributor_name_org",
        "distributor_name_w4a",
        "distributor_name_w4b",
        "distributor_name_w4c",
        "distributor_name_w5",
        "distributor_name_w6",
    }
