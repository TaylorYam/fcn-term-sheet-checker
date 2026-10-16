"""UBS 黑箱回歸（Issue #279）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import pytest

import bbva_synth
import bnp_synth
import ca_synth
import citi_synth
import gs_synth
import hsbc_synth
import jpm_synth
import ms_synth
import natixis_synth
import nomura_synth
import sg_synth
import synth
from fcn_checker.issuers import UBS
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row
from ubs_synth import DIST, PHONE, PRODUCT_TYPE, WARNING, Spec, check, check_item

MEMORY = "為避免疑慮，即使各股票在不同提前出場觀察日成為鎖定股票，提前出場事件仍被視為發生。"
VARIANTS = {
    "KO 每日記憶式、Non-Call 1、無 KI、3 檔美股": Spec(),
    "KO 每期記憶式、無 KI": Spec(ko_obs="P"),
    "KO 每期非記憶式、2 檔日股澳幣計價、KO 條款寫 %、發行價格沒有金額、受託機構/銷售機構標籤": Spec(
        ko_obs="P",
        memory=False,
        count=2,
        market="JT",
        currency_zh="澳幣",
        ko_pct_in_clause=True,
        issue_price_amount=False,
        trustee_label="受託機構/銷售機構",
    ),
    "KO 每日非記憶式、單一標的、KI 到期、日圓 3 個月": Spec(
        memory=False, count=1, ki="AM", currency_zh="日圓", tenor=3
    ),
    "KO 每日記憶式、KI 每日、4 檔、Non-Call 3、12 個月、開盤價": Spec(
        ki="D", count=4, first_callable=3, tenor=12, pricing="open"
    ),
    "KO 每期記憶式、KI 每期": Spec(ko_obs="P", ki="P"),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass_and_are_filled(tmp_path, spec):
    item = check_item(tmp_path, spec)
    r = item.report
    assert r.template == UBS.template_id
    assert r.status == S.PASS, problems(r)
    assert item.fills_sheet
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in UBS.not_covered}
    assert [x.status for x in rule(r, "derive.monthly_coupon")] == [S.PASS]
    assert len(rule(r, "doc.issuing_branch")) == 3  # 第 1 頁、商品基本資料三、相關機構事業概況
    assert rule(r, "standard.fixed_warning")[0].actual == 3  # 第 1 頁、商品基本資料二、投資風險警語段
    assert len(rule(r, "standard.issuer_name")) == 3
    assert [x.status for x in rule(r, "standard.product_name")] == [S.PASS, S.PASS]
    skipped = ("standard.guarantor_name", "standard.agent_name", "standard.minimum_issue_size")  # 範本沒有
    assert not [x for x in r.results if x.rule_id in skipped]
    assert len(rule(r, "field.min_amounts")) == 5
    assert rule(r, "field.initial_pricing")[0].status == S.PASS  # 說明書沒寫：只檢查表上是允許值
    assert rule(r, "field.ki_type")[0].status == S.PASS
    assert bool([x for x in r.results if x.rule_id == "doc.autocall_terms"]) == (spec.ko_obs == "D")
    assert bool([x for x in r.results if x.rule_id == "doc.ko_pct_mentions"]) == spec.ko_pct_in_clause


def test_wrapped_underlying_name_stays_in_its_row(tmp_path):
    """價格表的長標的名稱拆兩行（價格置中在兩行之間）：名稱接回一列，照常對到第九項的代碼並通過。"""
    item = check_item(tmp_path, Spec(long_name=True))
    assert item.report.status == S.PASS, problems(item.report)
    assert item.fills_sheet


def test_daily_autocall_dates_are_the_non_call_and_last_period_ends(tmp_path):
    spec = Spec(first_callable=2)
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d.expected for d in r.backfill if d.column.startswith("比價日_") and d.expected != "-"}
    assert cells == {"比價日_2": spec.ends[1], f"比價日_{spec.tenor}": spec.ends[-1]}


def test_periodic_autocall_dates_are_every_observation_from_non_call(tmp_path):
    spec = Spec(ko_obs="P")
    r = check(tmp_path, spec)
    cells = {d.column: d.expected for d in r.backfill if d.column.startswith("比價日_") and d.expected != "-"}
    assert cells == {f"比價日_{n}": spec.ends[n - 1] for n in range(1, spec.tenor + 1)}


def test_sheet_terms_are_compared(tmp_path):
    r = check(tmp_path, Spec(), **{"KO(memo)": "N", "Non-Call(月)": 3, "KO(Freq)": "P", "Coupon p.a. (%)": 12.5})
    for rid in ("field.ko_memory", "field.first_callable_period", "field.ko_observation", "field.coupon_pa_pct"):
        assert rule(r, rid)[0].status == S.MISMATCH, rid


def test_ki_type_is_compared_with_the_sheet(tmp_path):
    r = check(tmp_path, Spec(ki="AM"), **{"KI(Freq)": "D"})
    assert rule(r, "field.ki_type")[0].status == S.MISMATCH


# ---------------------------------------------------------------- 名稱（2026-10-15 確認）


def test_daily_product_named_periodic_is_a_mismatch(tmp_path):
    """KO 每日觀察的商品名稱寫「定期觀察／Periodic」：判不一致（名稱觀察方式要和條文一致）。"""
    edits = [Edit("瑞銀每日觀察", "瑞銀定期觀察", "ts"), Edit("UBS Daily", "UBS Periodic", "ts")]
    r = check(tmp_path, edits=edits)
    assert {(rid, f) for rid, f, _ in problems(r)} == {
        ("standard.product_name", "name_zh"),
        ("standard.product_name", "name_en"),
    }


def test_periodic_product_named_daily_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(ko_obs="P"), edits=[Edit("瑞銀定期觀察", "瑞銀每日觀察", "ts.cover")])
    assert rule(r, "standard.product_name", "name_zh")[0].status == S.MISMATCH


@pytest.mark.parametrize("where", ["ts.cover", "ts.art1", "ts"])
def test_observation_in_brackets_is_the_same_name(tmp_path, where):
    """「瑞銀（每日觀察）」與「瑞銀每日觀察」視為同一個名稱（2026-10-15 確認）；句尾「。」不算。"""
    item = check_item(tmp_path, edits=[Edit("瑞銀每日觀察", "瑞銀（每日觀察）", where)])
    assert item.report.status == S.PASS, problems(item.report)


def test_no_ki_maturity_payoff_compared_with_its_strike(tmp_path):
    """無 KI 的到期日給付寫「收盤價低於其轉換價」（OOS 真實樣本，Issue #295）：和「低於轉換價」一樣是無 KI。"""
    edit = Edit("收盤價低於轉換價，則投資人", "收盤價低於其轉換價，則投資人", "ts")
    item = check_item(tmp_path, edits=[edit])
    assert item.report.status == S.PASS, problems(item.report)


def test_name_without_ki_wording_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(ki="D"), edits=[Edit("（含觸及下限價事件）", "", "ts.cover")])
    assert rule(r, "standard.product_name", "name_zh")[0].status == S.MISMATCH


def test_name_without_memory_wording_is_a_mismatch(tmp_path):
    r = check(tmp_path, edits=[Edit("Memory Callable", "Callable", "ts.cover")])
    assert rule(r, "standard.product_name", "name_en")[0].status == S.MISMATCH


# ---------------------------------------------------------------- 說明書內部規則


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("商品名稱：瑞銀每日觀察記憶式", "商品名稱：瑞銀每日觀察", "ts.art1"), "doc.name_consistency"),
        (
            Edit("受託機構商品代號為092199990001", "受託機構商品代號為092199990002", "ts.cover"),
            "doc.trustee_product_code",
        ),
        (Edit("計價幣別：美元。", "計價幣別：日圓。", "ts.art5"), "doc.currency_consistency"),
        (Edit("每單位面額x 固定配息率", "每單位面額x 變動配息率", "ts.art8.coupon"), "doc.autocall_terms"),
        (Edit("t 係為2 至6", "t 係為2 至5", "ts.art8"), "doc.autocall_terms"),
        (Edit("變動配息率：1.0000%X", "變動配息率：1.0100%X", "ts.art8"), "doc.coupon_mentions"),
        (Edit("（nt/Nt）（年化配息率為12.00%）", "（nt/Nt）（年化配息率為12.50%）", "ts.art8"), "doc.coupon_mentions"),
        (Edit("固定配息率：1.0000%。", "固定配息率：1.0100%。", "ts.art15"), "doc.coupon_mentions"),
        (Edit("天期：6 個月", "天期：7 個月", "ts.art15"), "doc.coupon_periods"),
        (Edit("70.3150 美元", "70.3151 美元", "ts.art15.table"), "doc.scenario_table"),
        (Edit("70.00%）", "75.00%）", "ts.art15.table"), "doc.scenario_table"),
        (Edit("每單位面額：10,000.00 美元 /", "每單位面額：1,000.00 美元 /", "ts.art15"), "doc.scenario_table"),
        (Edit("初始投資金額：10,000.00 美元", "初始投資金額：1,000.00 美元", "ts.art15"), "doc.scenario_table"),
        (Edit("發行價格：100% /", "發行價格：99% /", "ts.art15"), "doc.scenario_table"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:12],
)
def test_internal_rules_find_the_wrong_repeat(tmp_path, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    assert broken in bad, problems(r)
    assert bad <= {broken, "standard.product_name"}, problems(r)


def test_issuing_branch_other_than_london_needs_review(tmp_path):
    """發行分行寫成倫敦以外的分行：轉人工覆核（目前只有瑞士銀行經由倫敦分行，2026-10-15 確認）。"""
    r = check(tmp_path, edits=[Edit("透過其倫敦分行發行", "透過其香港分行發行", "ts.org")])
    assert {(rid, f, st) for rid, f, st in problems(r)} == {
        ("doc.issuing_branch", "issuer_full_ch2", S.REVIEW_REQUIRED)
    }


def test_ko_percentage_in_the_autocall_clause_must_match_the_table(tmp_path):
    spec = VARIANTS["KO 每期非記憶式、2 檔日股澳幣計價、KO 條款寫 %、發行價格沒有金額、受託機構/銷售機構標籤"]
    edit = Edit("（即期初價格的100.00%），則提前出場事件", "（即期初價格的105.00%），則提前出場事件", "ts.art8")
    r = check(tmp_path, spec, edits=[edit])
    assert {rid for rid, _, _ in problems(r)} == {"doc.ko_pct_mentions"}


def test_coupon_rate_must_be_annual_over_twelve(tmp_path):
    r = check(tmp_path, edits=[Edit("固定配息率：1.0000%。（年化", "固定配息率：1.0100%。（年化", "ts.art8")])
    assert rule(r, "derive.monthly_coupon")[0].status == S.MISMATCH


def test_last_payment_date_must_be_the_maturity_date(tmp_path):
    spec = Spec()
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.maturity_date), "2031 年1 月20 日", "ts.art12")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_two_different_maturity_dates_need_review(tmp_path):
    """到期日句的現金交割、證券交割兩個日期不同：到期日歧義，轉人工覆核。"""
    edit = Edit("加三個預定交易日，即現預計為", "加三個預定交易日，即現預計為2031 年1 月20 日；原", "ts.art12")
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.maturity_date")[0].status == S.REVIEW_REQUIRED


def test_schedule_rows_must_equal_the_tenor(tmp_path):
    r = check(tmp_path, edits=[Edit("天期/年期：6 個月", "天期/年期：7 個月", "ts.art12")])
    assert rule(r, "doc.coupon_periods", "coupon_table")[0].status == S.MISMATCH


@pytest.mark.parametrize("ko_obs", ["D", "P"])
def test_last_period_must_end_on_the_final_valuation_date(tmp_path, ko_obs):
    spec = Spec(ko_obs=ko_obs)
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[-1]), "2030 年7 月10 日", "ts.art8.coupon")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_ko_observation_must_agree_between_items_eight_and_twelve(tmp_path):
    """商品基本資料十二的提前出場觀察日和八不同：KO 觀察方式、Non-Call、比價日轉人工覆核。"""
    edit = Edit("為自第1 個配息觀察期間結束日", "為自第2 個配息觀察期間結束日", "ts.art12")
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_ki_definition_must_match_the_maturity_wording(tmp_path):
    """到期日給付寫「若觸及下限事件」卻沒有觸及下限事件定義：KI 型態轉人工覆核。"""
    edit = Edit("若在期末訂價日，連結標的股票的收盤價低於下限價，則觸及下限事件視同發生。", "", "ts.art8")
    r = check(tmp_path, Spec(ki="AM", count=1, memory=False), edits=[edit])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_only_one_memory_sentence_needs_review(tmp_path):
    r = check(tmp_path, edits=[Edit(MEMORY, "", "ts.art8")])
    assert rule(r, "field.ko_memory")[0].status == S.REVIEW_REQUIRED


def test_price_table_names_must_match_the_ticker_table(tmp_path):
    r = check(tmp_path, edits=[Edit("虛構控股三公司", "虛構控股參公司", "ts.art9.table")])
    assert rule(r, "field.underlyings")[0].status == S.REVIEW_REQUIRED


# ---------------------------------------------------------------- 審查標準（UBS 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit(WARNING[:20], WARNING[:19] + "X", "ts.warnings"), "standard.fixed_warning"),
        (Edit("事業名稱：瑞士銀行（UBS AG）", "事業名稱：瑞士商銀行（UBS AG）", "ts.org"), "standard.issuer_name"),
        (Edit("申購價金的 0%~5%", "申購價金的 0%~3%", "ts.general"), "standard.fees"),
        (Edit(f"地址：{DIST['address']}", "地址：台北市虛構路2號", "ts.cover"), "standard.distributor"),
        (Edit(f"電話：{PHONE}", "電話：+886-2-5556-1314", "ts.general"), "standard.distributor"),
        (Edit(f"負責人姓名：{DIST['chairman']}", "負責人姓名：林晉輝", "ts.org"), "standard.chairman"),
        (Edit("2026 年6 月11 日", "2025 年12 月18 日", "ts.cover"), "standard.approval_date"),
        (Edit("最低申購金額：50,000.00 美元", "最低申購金額：10,000.00 美元", "ts.general"), "field.min_amounts"),
        (Edit("最低交易金額：50,000.00 美元", "最低交易金額：10,000.00 美元", "ts.art8"), "field.min_amounts"),
        (Edit("發行價格：100%，即", "發行價格：99%，即", "ts.art8"), "standard.issue_price"),
        (Edit("於中華民國境內受託買賣", "於中華民國境內受託投資", "ts.warn"), "standard.forbidden_wording"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:12],
)
def test_review_standard_items(tmp_path, edit, broken):
    r = check(tmp_path, edits=[edit])
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


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
        lambda p: gs_synth.build_pdf(p, gs_synth.Spec()),
        lambda p: natixis_synth.build_pdf(p, natixis_synth.Spec()),
        lambda p: ca_synth.build_pdf(p, ca_synth.Spec()),
        lambda p: bbva_synth.build_pdf(p, bbva_synth.Spec()),
    ],
    ids=["BARC", "HSBC", "MS", "BNP", "SG", "Citi", "JPM", "Nomura", "GS", "Natixis", "CA", "BBVA"],
)
def test_other_issuers_are_not_ubs(tmp_path, build):
    pdf = build(tmp_path / "092199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    for item in check_all(sheet, [pdf]).items:
        assert item.report.template not in (UBS.template_id, UBS.iis.template_id)


def test_old_template_is_not_supported(tmp_path):
    """舊版範本（商品種類「股權結構型債券」）辨識失敗、轉人工覆核並寫出原因（2026-10-15 確認只支援新版）。"""
    item = check_item(tmp_path, edits=[Edit(f"商品種類：{PRODUCT_TYPE}。", "商品種類：股權結構型債券。", "ts.cover")])
    detect = rule(item.report, "template.detect")[0]
    assert item.report.template != UBS.template_id
    assert detect.status == S.REVIEW_REQUIRED and "舊版範本" in detect.message
