"""JPM 黑箱回歸（Issue #233）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

import bnp_synth
import citi_synth
import ms_synth
import sg_synth
import synth
from fcn_checker.backfill import BackfillAction
from fcn_checker.issuers import JPM
from fcn_checker.schema import CheckStatus as S
from harness import CONFIG, check_all
from harness import field_problems as problems
from harness import results as rule
from jpm_synth import APPROVAL, GUARANTOR, PHONE, Spec, check, jdate
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

TYPES = [
    Spec(),  # D（4 欄日期表）、記憶式、無 KI、3 檔、6 個月、Non-Call 1
    Spec(d4=False, ki="D", count=1, tenor=12, first_callable=2, ko=Decimal("120.00")),  # D 3 欄、單一標的、KI 每日
    Spec(ko_obs="P", ki="AM", currency_zh="日圓", tenor=8, first_callable=2),  # P 記憶式、KI 到期
    Spec(ko_obs="P", memory=False, tenor=3, first_callable=3, strike=Decimal("75.92")),  # P 非記憶式、Non-Call = 天期
    Spec(d4=False, vwap=True),  # D 3 欄、期初定價 VWAP
    Spec(currency_zh="澳幣"),  # 澳幣計價
]


@pytest.mark.parametrize(
    "spec",
    TYPES,
    ids=lambda s: (
        f"{s.ko_obs}{'4' if s.d4 and s.ko_obs == 'D' else ''}-{'mem' if s.memory else 'plain'}-KI{s.ki}-{s.count}UL-"
        f"{s.tenor}m-NC{s.first_callable}-{s.ccy}" + ("-vwap" if s.vwap else "")
    ),
)
def test_supported_types_pass(tmp_path, spec):
    r = check(tmp_path, spec)
    assert r.template == JPM.template_id
    assert r.status == S.PASS, problems(r)
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in JPM.not_covered}
    assert len(rule(r, "standard.guarantor_name")) == 3
    assert len(rule(r, "standard.agent_name")) == 2
    assert len(rule(r, "standard.issuer_name")) == 2  # 封面、第 II 部分（第 I 部分沒有出處）
    assert rule(r, "standard.fixed_warning")[0].actual == 3
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS  # 「受託投資/受託買賣」為允許片語
    assert rule(r, "field.initial_pricing")[0].status == S.PASS  # 說明書沒有期初定價（absent），只看表上允許值
    assert not [x for x in r.results if x.rule_id == "standard.minimum_issue_size"]  # 範本沒有發行不成立門檻
    mentions = [x for x in r.results if x.rule_id == "derive.coupon_pa_mentions"]
    assert bool(mentions) == (spec.ko_obs == "D")  # D 型才有自動提前出場配息金額


# ---------------------------------------------------------------- 標的不看順序（Citi、JPM）


def test_underlyings_in_a_different_order_on_the_sheet_pass(tmp_path):
    item = check(tmp_path, Spec(), order=(3, 1, 2))
    assert item.status == S.PASS, problems(item)
    assert "不看順序" in rule(item, "field.underlyings")[0].tolerance
    prices = rule(item, "field.underlying_prices")
    assert len(prices) == 12 and all(x.status in (S.PASS, S.NOT_APPLICABLE) for x in prices)
    # 價格欄的項目名稱是表上那一格（說明書第 1 檔 ZJA UW 在表上第 2 檔）
    assert any(x.field == "ZJA UW 進場價" and x.item.name == "UL_2 進場價" for x in prices)


def test_a_wrong_price_is_still_found_after_matching_by_ticker(tmp_path):
    r = check(tmp_path, Spec(), order=(3, 1, 2), **{"UL_2_執行價": 1.0})  # 表上第 2 檔是說明書第 1 檔
    assert problems(r) == [("field.underlying_prices", "ZJA UW 執行價", S.MISMATCH)]


def test_jpm_is_listed_as_an_any_order_issuer(tmp_path):
    """設定拿掉 JPM 時，順序不同就不一致（標的不看順序只給格式設定列出的上手）。"""
    fmt = dataclasses.replace(CONFIG.reference_format, underlyings_any_order=("CITI",))
    r = check(tmp_path, Spec(), order=(2, 1, 3), config=dataclasses.replace(CONFIG, reference_format=fmt))
    assert rule(r, "field.underlyings")[0].status == S.MISMATCH


def test_vwap_backfill_writes_each_price_to_the_cell_of_the_same_ticker(tmp_path):
    spec = Spec(vwap=True)
    r = check(tmp_path, spec, order=(3, 1, 2), **{f"UL_{i}_進場價": None for i in range(1, 4)})
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d for d in r.backfill}
    first, third = spec.underlyings[0], spec.underlyings[2]
    assert cells["UL_2_進場價"].expected == first.initial  # 表上第 2 檔是說明書第 1 檔
    assert cells["UL_1_進場價"].expected == third.initial
    assert cells["UL_1_進場價"].action == BackfillAction.FILL


@pytest.mark.parametrize(
    ("spec", "dates"),
    [
        (Spec(), {1: 0, 6: 5}),  # D 4 欄：第 Non-Call 期評價日、最後一期
        (Spec(d4=False, tenor=7, first_callable=2), {2: 1, 7: 6}),  # D 3 欄：第 Non-Call 期觀察期間終止日
        (Spec(ko_obs="P", tenor=4, first_callable=2), {2: 1, 3: 2, 4: 3}),  # P：Non-Call 期起每期評價日
        (Spec(ko_obs="P", tenor=3, first_callable=3), {3: 2}),  # P、Non-Call = 天期：只填最後一格
    ],
    ids=["D4", "D3", "P", "P-NC=tenor"],
)
def test_compare_dates_are_backfilled_from_the_date_table(tmp_path, spec, dates):
    r = check(tmp_path, spec)
    cells = {d.column: d.expected for d in r.backfill if d.column.startswith("比價日_")}
    filled = {int(c.removeprefix("比價日_")): v for c, v in cells.items() if v != "-"}
    assert filled == {n: spec.valuations[j] for n, j in dates.items()}


# ---------------------------------------------------------------- 說明書內部規則


@pytest.mark.parametrize(
    ("spec", "edit", "broken"),
    [
        (Spec(), Edit("100.5000美元", "100.4000美元", "ts.art15.table"), "doc.scenario_table"),
        (Spec(), Edit("假設原計價幣別本金為10,000美元", "假設原計價幣別本金為20,000美元", "ts.art15"), "doc.scenario_table"),
        (Spec(), Edit("x 12.34% x 1/12 x n/N", "x 12.43% x 1/12 x n/N", "ts.art8"), "derive.coupon_pa_mentions"),
        (Spec(), Edit("商品名稱：摩根大通6個月", "商品名稱：摩根大通7個月", "ts.art1"), "doc.name_consistency"),
        (Spec(), Edit("計價幣別：美元", "計價幣別：日圓", "ts.art5"), "doc.currency_consistency"),
        (Spec(), Edit("受託機構商品代號為076199990001", "受託機構商品代號為076199990002", "ts.cover"), "doc.trustee_product_code"),
        (Spec(), Edit("最差表現連結標的：", "表現最差之標的說明：", "ts.art8"), "doc.underlying_count"),
        (Spec(), Edit("(1) 若於最後評價日時最差表現連結標的之股票收盤價大於或等於其執行價：每單位面額 x 100%", "(1) 若於最後評價日時最差表現連結標的之股票收盤價大於或等於其執行價：每單位面額 x 99%", "ts.art8"), "doc.redemption_pct"),
    ],
    ids=["scenario-price", "scenario-principal", "pro-rata-rate", "name", "currency", "trustee-code", "worst-of", "redemption"],
)  # fmt: skip
def test_internal_rules_find_the_wrong_repeat(tmp_path, spec, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, spec, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    assert broken in bad, problems(r)
    assert bad <= {broken, "doc.coupon_periods"}, problems(r)


def test_last_payment_date_must_be_the_maturity_date(tmp_path):
    spec = Spec()
    r = check(
        tmp_path, spec, edits=[Edit(f"到期日：{jdate(spec.maturity_date)}", "到期日：2031年01月20日", "ts.art12")]
    )
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_d3_blank_start_dates_must_end_at_the_first_callable_period(tmp_path):
    """3 欄日期表：觀察期間起始日為 N.A 的期別 = 第 1～Non-Call 期。"""
    spec = Spec(d4=False, tenor=7, first_callable=2)
    start = jdate(spec.valuations[1] + dt.timedelta(days=1))  # 第 3 期起始日
    r = check(tmp_path, spec, edits=[Edit(start, "N.A", "ts.art12.table")])
    assert rule(r, "schedule.autocall_dates")[0].status == S.MISMATCH


def test_ko_start_period_is_read_from_article_8(tmp_path):
    r = check(tmp_path, edits=[Edit("(t=1)(含)起至最終評價日", "(t=2)(含)起至最終評價日", "ts.art8")])
    assert problems(r) == [("field.first_callable_period", "first_callable_period", S.MISMATCH)] or {
        rid for rid, _, _ in problems(r)
    } <= {"field.first_callable_period", "backfill.compare_dates"}


def test_daily_wording_with_a_periodic_date_table_requires_review(tmp_path):
    """第 8 項寫每日觀察、日期表卻是每期觀察的排法：KO 觀察方式與 Non-Call 轉人工覆核。"""
    spec = Spec(ko_obs="P", tenor=4, first_callable=2)
    r = check(
        tmp_path,
        spec,
        edits=[Edit("於任一評價日(t)(t=2-4)", "於評價日(t)(t=2)(含)起至最終評價日(含)止之每一個預定交易日", "ts.art8")],
    )
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_daily_product_without_pro_rata_coupon_requires_review(tmp_path):
    r = check(tmp_path, edits=[Edit("自動提前出場配息金額：每單位面額", "自動提前出場之配息：每單位面額", "ts.art8")])
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED


def test_ki_line_without_a_touch_event_requires_review(tmp_path):
    r = check(tmp_path, Spec(ki="AM"), edits=[Edit("觸碰事件：若計算代理機構判定，", "觸碰條件：", "ts.art8")])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_sheet_memory_flag_is_compared_with_article_8(tmp_path):
    r = check(tmp_path, Spec(), pdf_spec=Spec(memory=False))  # 表上記憶式、說明書非記憶式
    assert rule(r, "field.ko_memory")[0].status == S.MISMATCH


def test_single_underlying_is_not_memory(tmp_path):
    r = check(tmp_path, Spec(count=1))
    assert r.status == S.PASS, problems(r)
    assert rule(r, "field.ko_memory")[0].status == S.PASS


# ---------------------------------------------------------------- 審查標準（JPM 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("(包括OSU客戶)以下稱", "(包括OSU客戶)(以下稱", "ts.cover"), "standard.fixed_warning"),
        (Edit("受託投資/受託買賣", "受託投資", "ts.cover"), "standard.forbidden_wording"),
        (Edit("股權連結結構型商品", "連結股權結構型商品", "ts.cover"), "standard.product_type"),
        (
            Edit(f"(2) 事業名稱：{GUARANTOR['ch2']}", "(2) 事業名稱：美商摩根大通銀行", "ts.chII"),
            "standard.guarantor_name",
        ),
        (Edit(f"信用評等：{GUARANTOR['ch1']}，", "信用評等：摩根大通銀行，", "ts.art3"), "standard.guarantor_name"),
        (
            Edit("事業名稱：美商摩根大通銀行股份有限公司台北分公司", "事業名稱：摩根大通台北分公司", "ts.chII"),
            "standard.agent_name",
        ),
        (Edit("價金的0%~5%", "價金的0%~3%", "ts.chIV"), "standard.fees"),
        (Edit(PHONE, "+886 2 5556 1314", "ts.cover"), "standard.distributor"),
        (Edit(jdate(APPROVAL), "2025年12月18日", "ts.cover"), "standard.approval_date"),
        (Edit("固定配息可自動提前出場", "固定配息自動提前出場", "ts.cover"), "standard.product_name"),
        (Edit("(1) 事業名稱：荷蘭商J.P. Morgan", "(1) 事業名稱：荷蘭商J.P. Morgen", "ts.chII"), "standard.issuer_name"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:12],
)
def test_review_standard_items(tmp_path, edit, broken):
    r = check(tmp_path, edits=[edit])
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_risk_level_is_written_without_brackets(tmp_path):
    r = check(tmp_path, edits=[Edit("風險等級中歸類為RR4", "風險等級中歸類為RR5", "ts.art2")])
    assert rule(r, "standard.risk_level")[0].status == S.MISMATCH


# ---------------------------------------------------------------- 範本辨識


@pytest.mark.parametrize(
    "build",
    [
        lambda p: synth.build_pdf(p, synth.Spec()),
        lambda p: ms_synth.build_pdf(p, ms_synth.Spec()),
        lambda p: bnp_synth.build_pdf(p, bnp_synth.Spec()),
        lambda p: sg_synth.build_pdf(p, sg_synth.Spec()),
        lambda p: citi_synth.build_pdf(p, citi_synth.Spec()),
    ],
    ids=["BARC", "MS", "BNP", "SG", "Citi"],
)
def test_other_issuers_are_not_jpm(tmp_path, build):
    pdf = build(tmp_path / "076199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    r = next(i.report for i in check_all(sheet, [pdf]).items if i.term_sheet == pdf)
    assert r.template != JPM.template_id
