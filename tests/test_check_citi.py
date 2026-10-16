"""Citi 黑箱回歸（Issue #220）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

import bnp_synth
import ms_synth
import sg_synth
import synth
from citi_synth import APPROVAL, GUARANTOR, Spec, check, zh_number
from fcn_checker.backfill import BackfillAction
from fcn_checker.issuers import CITI
from fcn_checker.schema import CheckStatus as S
from harness import CONFIG, check_all
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit, zh_date
from reference_synth import build_reference_sheet, reference_row


@pytest.mark.parametrize(
    "spec",
    [
        Spec(),  # D、記憶式、KI 到期觀察、3 檔、6 個月、Non-Call 1
        Spec(currency_zh="日圓", ki="D", tenor=12, first_callable=6, ko=Decimal("100.00")),  # D Non-Call 6、KI 每日
        Spec(ko_obs="P", ki="none", count=4, tenor=7, ko=Decimal("105.00")),  # P 型、無 KI、4 檔
        Spec(ko_obs="P", ki="P", tenor=9, legacy=True),  # 舊文件版面：P 型、KI 每期觀察、價格 4 位、分銷費用單列
        Spec(autocall=False, ki="none", tenor=3, currency_zh="境外人民幣", strike=Decimal("68.85")),  # 無自動提前出場
        Spec(memory=False, count=1, tenor=12, first_callable=11, ko=Decimal("100.00")),  # 單一標的、非記憶式
        Spec(vwap=True, ki="none", ko=Decimal("91.00")),  # 期初定價 VWAP
    ],
    ids=lambda s: (
        f"{'A' if s.autocall else 'none'}-{s.ko_obs}-{'mem' if s.memory else 'plain'}-KI{s.ki}-{s.count}UL-{s.tenor}m-NC{s.first_callable}-{s.ccy}"
        + ("-legacy" if s.legacy else "")
        + ("-vwap" if s.vwap else "")
    ),
)
def test_supported_types_pass(tmp_path, spec):
    r = check(tmp_path, spec)
    assert r.template == CITI.template_id
    assert r.status == S.PASS, problems(r)
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in CITI.not_covered}
    assert {x.status for x in rule(r, "derive.monthly_coupon")} == {S.PASS}
    assert len(rule(r, "standard.guarantor_name")) == 3
    assert len(rule(r, "standard.agent_name")) == 2
    assert rule(r, "standard.fixed_warning")[0].actual == 3
    assert rule(r, "field.initial_pricing")[0].status == S.PASS
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS  # 「受託投資、受託買賣之投資標的」等允許片語


def test_blank_trustee_code_on_the_old_template_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(legacy=True, trustee_code=""))
    assert problems(r) == [("doc.trustee_product_code", "trustee_product_code", S.MISMATCH)]


# ---------------------------------------------------------------- 標的不看順序（只有 Citi）


def test_underlyings_in_a_different_order_on_the_sheet_pass(tmp_path):
    item = check(tmp_path, Spec(), order=(3, 1, 2))
    assert item.status == S.PASS, problems(item)
    assert "不看順序" in rule(item, "field.underlyings")[0].tolerance
    prices = rule(item, "field.underlying_prices")
    assert len(prices) == 12 and all(x.status == S.PASS for x in prices)
    # 價格欄的項目名稱是表上那一格（說明書第 1 檔 ZCA UW 在表上第 2 檔）
    assert any(x.field == "ZCA UW 進場價" and x.item.name == "UL_2 進場價" for x in prices)


def test_a_wrong_price_is_still_found_after_matching_by_ticker(tmp_path):
    r = check(tmp_path, Spec(), order=(3, 1, 2), **{"UL_2_執行價": 1.0})  # 表上第 2 檔是說明書第 1 檔
    assert problems(r) == [("field.underlying_prices", "ZCA UW 執行價", S.MISMATCH)]


def test_a_different_set_of_tickers_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(), order=(3, 1, 2), UL_1="ZZZ UW")
    assert ("field.underlyings", "underlyings", S.MISMATCH) in problems(r)


def test_other_issuers_still_compare_underlyings_in_order(tmp_path):
    """標的不看順序只給參考條件表格式設定列出的上手：設定拿掉 CITI 時，順序不同就不一致。"""
    fmt = dataclasses.replace(CONFIG.reference_format, underlyings_any_order=())
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


# ---------------------------------------------------------------- 期初定價（說明書表頭）


@pytest.mark.parametrize(("spec", "sheet"), [(Spec(), "VWAP"), (Spec(vwap=True), "收盤價")])
def test_initial_pricing_must_match_the_table_header(tmp_path, spec, sheet):
    r = check(tmp_path, spec, 期初定價=sheet)
    assert rule(r, "field.initial_pricing")[0].status == S.MISMATCH


def test_opening_price_header_matches_the_sheet(tmp_path):
    """表頭「期初評價日之開盤價格」= 表上「開盤價」（Issue #229）。"""
    r = check(tmp_path, Spec(pricing_text="開盤價格"), 期初定價="開盤價")
    assert r.status == S.PASS, problems(r)
    assert rule(r, "field.initial_pricing")[0].status == S.PASS


def test_unknown_initial_pricing_header_requires_review(tmp_path):
    r = check(tmp_path, Spec(), pdf_spec=Spec(pricing_text="平均價格"))
    assert rule(r, "field.initial_pricing")[0].status == S.REVIEW_REQUIRED


def test_blank_sheet_pricing_follows_a_vwap_header_and_fills_the_prices(tmp_path):
    """表上期初定價空白、表頭寫 VWAP：當 VWAP 商品，價格欄不比對、通過後依代號以說明書覆寫（Issue #246）。"""
    spec = Spec(vwap=True)
    stale = {f"UL_{i}_進場價": 1.0 for i in range(1, 4)}
    r = check(tmp_path, spec, order=(3, 1, 2), 期初定價=None, **stale)
    assert r.status == S.PASS, problems(r)
    [pricing] = rule(r, "field.initial_pricing")
    assert pricing.status == S.PASS and "VWAP" in pricing.message, pricing.message
    assert not [x for x in r.results if x.rule_id == "field.underlying_prices"]  # 價格欄不比對
    cells = {d.column: d for d in r.backfill}
    assert cells["UL_2_進場價"].expected == spec.underlyings[0].initial  # 表上第 2 檔是說明書第 1 檔
    assert cells["UL_2_進場價"].action == BackfillAction.OVERWRITE


@pytest.mark.parametrize("header", ["收盤價格", "開盤價格"])
def test_blank_sheet_pricing_with_a_closing_or_opening_price_header_compares_the_prices(tmp_path, header):
    r = check(tmp_path, Spec(pricing_text=header), 期初定價=None, UL_1_進場價=1.0)
    assert problems(r) == [("field.underlying_prices", "ZCA UW 進場價", S.MISMATCH)]
    assert rule(r, "field.initial_pricing")[0].status == S.PASS


def test_blank_sheet_pricing_with_an_unknown_header_requires_review(tmp_path):
    r = check(tmp_path, Spec(), pdf_spec=Spec(pricing_text="平均價格"), 期初定價=None)
    assert rule(r, "field.initial_pricing")[0].status == S.REVIEW_REQUIRED


# ---------------------------------------------------------------- 說明書內部規則


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("每單位商品面額 × 0.8233% (年化", "每單位商品面額 × 0.8234% (年化", "ts.art14"), "derive.monthly_coupon"),
        (Edit("年化配息率約為9.88%", "年化配息率約為9.89%", "ts.art14"), "derive.coupon_pa_mentions"),
        (
            Edit("d) 固定配息金額為每單位商品面額 × 0.8233%", "d) 固定配息金額為每單位商品面額 × 0.8333%", "ts.art15"),
            "derive.monthly_coupon",
        ),
        (Edit("100.5 美元", "100.4 美元", "ts.art15.table"), "doc.scenario_table"),
        (Edit("期初評價日：", "期初評價日：2029 年1 月1 日，原訂", "ts.art12"), "doc.initial_valuation_date"),
        (Edit("計價幣別：美元(USD)", "計價幣別：美元(JPY)", "ts.art5"), "doc.currency_consistency"),
        (Edit("商品名稱：CGMHI 6 個月", "商品名稱：CGMHI 7 個月", "ts.art1"), "doc.name_consistency"),
        # Issue #314：重複寫出的值（說明文字幣別、情境、其他條、第五章）
        (
            Edit("配息金額 = 每單位商品面額 × 0.8233%", "配息金額 = 每單位商品面額 × 0.8333%", "ts.art15"),
            "derive.monthly_coupon",
        ),
        (Edit("m/M × 0.8233%", "m/M × 0.8333%", "ts.art15"), "derive.monthly_coupon"),
        (Edit("0.8233% (等同年息為9.88%)", "0.8233% (等同年息為9.89%)", "ts.art15"), "derive.coupon_pa_mentions"),
        (
            Edit("界限 (期初股價的95.00%)(假設) 最終", "界限 (期初股價的96.00%)(假設) 最終", "ts.art15"),
            "doc.scenario_header_pct",
        ),
        (
            Edit("下限價 (期初股價的55.00%)(假設) 最終", "下限價 (期初股價的50.00%)(假設) 最終", "ts.art15"),
            "doc.scenario_header_pct",
        ),
        (
            Edit("執行價 (期初股價的65.00%)(假設) 最終", "執行價 (期初股價的60.00%)(假設) 最終", "ts.art15"),
            "doc.scenario_header_pct",
        ),
        (
            Edit("m ÷ M × 0.8233% (等同年息為9.88%)", "m ÷ M × 0.8233% (等同年息為9.89%)", "ts.art13"),
            "derive.coupon_pa_mentions",
        ),
        (Edit("投資單位數 × 100%", "投資單位數 × 101%", "ts.art15"), "doc.redemption_pct"),
        (Edit("每單位商品面額 × 100% =", "每單位商品面額 × 90% =", "ts.art15"), "doc.redemption_pct"),
        (Edit("股數如下：每單位商品面額的100%", "股數如下：每單位商品面額的90%", "ts.art8"), "doc.redemption_pct"),
        (Edit("相當於每單位商品面額100%之金額", "相當於每單位商品面額99%之金額", "ts.art8"), "doc.redemption_pct"),
        (Edit("面額的100%之金額以及", "面額的99%之金額以及", "ts.art13"), "doc.redemption_pct"),
        (Edit("面額的100%之金額（簡稱", "面額的99%之金額（簡稱", "ts.art17"), "doc.redemption_pct"),
        (Edit("關於每10,000 美元", "關於每1,000 美元", "ts.art8"), "doc.denomination_mentions"),
        (Edit("關於每10,000 美元", "關於每10,000 日圓", "ts.art8"), "field.currency"),
        (Edit("投資人之美元實際投資淨額", "投資人之日圓實際投資淨額", "ts.art7"), "field.currency"),
        (Edit("係以美元計算", "係以日圓計算", "ts.art7"), "field.currency"),
        (Edit("以非美元之其他貨幣", "以非日圓之其他貨幣", "ts.art7"), "field.currency"),
        (Edit("本商品係以美元計價", "本商品係以日圓計價", "ts.ch三"), "field.currency"),
        (Edit("轉換成美元進行投資", "轉換成日圓進行投資", "ts.ch三"), "field.currency"),
        (Edit("現金金額406.80美元", "現金金額406.80日圓", "ts.art15"), None),  # 實物交割那一行不列入幣別出處
        (Edit("= 82.33 美元", "= 82.33 日圓", "ts.art15"), "field.currency"),
        (Edit("在本商品發行日2030 年", "在本商品發行日2031 年", "ts.ch五"), "doc.date_mentions"),
        (Edit("本商品在2030 年", "本商品在2029 年", "ts.ch五"), "doc.date_mentions"),
    ],
    ids=lambda x: x if isinstance(x, str) or x is None else x.where,
)
def test_internal_rules_find_the_wrong_repeat(tmp_path, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    if broken is None:
        assert not bad, problems(r)
        return
    assert broken in bad, problems(r)
    assert bad <= {broken, "field.final_valuation_date", "doc.coupon_periods"}, problems(r)
    assert {st for rid, _, st in problems(r) if rid == broken} == {S.MISMATCH}, problems(r)


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("係以美元計算", "係以美金計算", "ts.art7"), {"field.currency"}),
        (Edit("本商品係以美元計價", "本商品係以外幣計價", "ts.ch三"), {"field.currency"}),
        (Edit("關於每10,000 美元", "關於每單位", "ts.art8"), {"doc.denomination_mentions", "field.currency"}),
        (Edit("股數如下：每單位商品面額的100%", "股數如下：每單位商品面額", "ts.art8"), {"doc.redemption_pct"}),
        (Edit("面額的100%之金額（簡稱", "面額之金額（簡稱", "ts.art17"), {"doc.redemption_pct"}),
        (
            Edit("配息金額 = 每單位商品面額 ×", "配息金額 = 每單位商品面額 × 年利率 ÷ 12，即", "ts.art15"),
            {"derive.monthly_coupon"},
        ),
        (Edit("界限 (期初股價的95.00%)(假設) 最終", "界限(假設) 最終", "ts.art15"), {"doc.scenario_header_pct"}),
        (Edit("(一) 配息說明", "配息說明", "ts.art15"), {"field.currency"}),
        (Edit("在本商品發行日2030 年", "在本商品發行日（暫定）2030 年", "ts.ch五"), {"doc.date_mentions"}),
    ],
    ids=lambda x: x.where if isinstance(x, Edit) else "-".join(sorted(x)),
)
def test_unreadable_repeat_requires_review(tmp_path, edit, broken):
    """重複寫出處讀不到：轉人工覆核（Issue #314），不是略過。"""
    r = check(tmp_path, edits=[edit])
    assert {rid for rid, _, _ in problems(r)} == broken, problems(r)
    assert {st for _, _, st in problems(r)} == {S.REVIEW_REQUIRED}, problems(r)


def test_unknown_scenario_header_requires_review(tmp_path):
    """情境小表表頭「(期初股價的X%)」前面認不出欄名：轉人工覆核（Issue #314）。"""
    edit = Edit("期初股價 自動提前出場界限 (期初股價的", "期初股價 出場價 (期初股價的", "ts.art15")
    r = check(tmp_path, edits=[edit])
    assert {(rid, st) for rid, _, st in problems(r)} == {("doc.scenario_header_pct", S.REVIEW_REQUIRED)}, problems(r)


def test_repeated_values_are_read(tmp_path):
    """重複寫出處都有結果（Issue #314）：情境價格表帶出各格、情境幣別每個情境一筆。"""
    r = check(tmp_path, Spec())
    scen = rule(r, "doc.scenario_table")[0]
    assert scen.status == S.PASS and scen.expected and scen.expected == scen.actual
    assert len(rule(r, "doc.table_header_pct")) == 3  # §9 三欄
    assert len(rule(r, "doc.scenario_header_pct")) == 3  # 三個情境小表
    assert len(rule(r, "doc.redemption_pct")) == 1 + 2 + 1 + 3 + 1  # §8(a)；§8(b)(4)；§13；情境；§17(C)
    assert len(rule(r, "doc.date_mentions")) == 2
    currencies = {x.field for x in rule(r, "field.currency")}
    assert {"note7_net_currency", "risk_currency", "delivery_unit_currency", "scenario_currency_2"} <= currencies


def test_ko_observation_start_must_agree_between_articles_12_and_13(tmp_path):
    r = check(
        tmp_path, edits=[Edit(f"於第{zh_number(1)}個配息觀察期間結束日", "於第二個配息觀察期間結束日", "ts.art12")]
    )
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_last_payment_date_must_be_the_maturity_date(tmp_path):
    spec = Spec()
    r = check(
        tmp_path, spec, edits=[Edit(f"到期日：{zh_date(spec.maturity_date)}", "到期日：2031 年1 月20 日", "ts.art12")]
    )
    assert rule(r, "schedule.last_payment")[0].status == S.MISMATCH


def test_ki_periods_must_equal_the_tenor(tmp_path):
    r = check(tmp_path, Spec(ko_obs="P", ki="P", tenor=9), edits=[Edit("(t=1-9)", "(t=1-8)", "ts.art8")])
    assert rule(r, "doc.ki_consistency", "ki_periods")[0].status == S.MISMATCH


def test_non_memory_product_must_not_mention_memory(tmp_path):
    """非記憶式就應該寫非記憶式（2026-10-14 確認）：第一章出現「記憶式」判不一致。"""
    spec = Spec(memory=False, count=1, tenor=12, first_callable=11, ko=Decimal("100.00"))
    r = check(tmp_path, spec, edits=[Edit("最終收盤價：", "最終收盤價（記憶式自動提前出場事件除外）：", "ts.art13")])
    assert problems(r) == [("doc.memory_wording", "memory_wording_ch1", S.MISMATCH)]


def test_memory_wording_in_the_name_must_match_article_13(tmp_path):
    """名稱寫記憶式、第 13 項卻是非記憶式：寫法超出範本規格，轉人工覆核。"""
    spec = Spec(memory=False)
    r = check(tmp_path, spec, edits=[Edit("(自動提前出場)", "(記憶式自動提前出場)", "ts.cover")])
    assert rule(r, "doc.ko_memory")[0].status == S.REVIEW_REQUIRED


def test_article_8_item_4_must_agree_with_article_13(tmp_path):
    r = check(tmp_path, edits=[Edit("(4) 發生記憶式自動提前出場", "(4) 其他給付", "ts.art8")])
    assert rule(r, "doc.autocall_wording", "art8_autocall")[0].status == S.REVIEW_REQUIRED


def test_a_blank_column_between_underlyings_is_not_matched_by_ticker(tmp_path):
    """表上 UL 中間有空白欄：不依代號配對（價格不會對到錯的那一格），標的轉人工覆核。"""
    r = check(tmp_path, Spec(count=2), order=(2, 1), UL_2="-", UL_3="ZCA UW")
    assert rule(r, "field.underlyings")[0].status == S.REVIEW_REQUIRED


def test_sheet_memory_flag_is_compared_with_article_13(tmp_path):
    r = check(tmp_path, Spec(), pdf_spec=Spec(memory=False))  # 表上記憶式、說明書非記憶式
    assert rule(r, "field.ko_memory")[0].status == S.MISMATCH


# ---------------------------------------------------------------- 審查標準（Citi 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (
            Edit("證券交易所交易證券，再綜合評量", "證券交易所交易證券，在綜合評量", "ts.cover"),
            "standard.fixed_warning",
        ),
        (Edit("受託投資、受託買賣之投資標的", "受託投資之投資標的", "ts.cover"), "standard.forbidden_wording"),
        (Edit("連結股權結構型商品", "股權連結結構型商品", "ts.cover"), "standard.product_type"),
        (Edit("等值30 萬美元", "等值300,000 美元", "ts.ch四"), "standard.minimum_issue_size"),
        (Edit(f"公司名稱：{GUARANTOR['ch2']}", "公司名稱：Citigroup Inc.", "ts.ch二"), "standard.guarantor_name"),
        (Edit("公司名稱：花旗環球證券股份有限公司", "公司名稱：花旗環球證券", "ts.ch二"), "standard.agent_name"),
        (Edit("申購價金的0%~5%", "申購價金的0%~3%", "ts.ch四"), "standard.fees"),
        (Edit("(+886)2-5556-1313", "(886)2-5556-1313", "ts.cover"), "standard.distributor"),
        (Edit(zh_date(APPROVAL), "2025 年12 月18 日", "ts.cover"), "standard.approval_date"),
        (Edit("由花旗集團保證之固定配息", "由花旗集團保證之配息", "ts.cover"), "standard.product_name"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:12],
)
def test_review_standard_items(tmp_path, edit, broken):
    r = check(tmp_path, edits=[edit])
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_issuer_name_cover_ignores_the_cgmhi_abbreviation(tmp_path):
    r = check(tmp_path)
    found = rule(r, "standard.issuer_name")
    assert len(found) == 3 and all(x.status == S.PASS for x in found)  # 封面、第一章第 3 項、第二章


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
def test_other_issuers_are_not_citi(tmp_path, build):
    pdf = build(tmp_path / "159199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    r = next(i.report for i in check_all(sheet, [pdf]).items if i.term_sheet == pdf)
    assert r.template != CITI.template_id
