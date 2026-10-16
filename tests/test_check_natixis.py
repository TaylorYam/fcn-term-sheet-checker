"""Natixis 黑箱回歸（Issue #261）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import bnp_synth
import citi_synth
import gs_synth
import hsbc_synth
import jpm_synth
import ms_synth
import nomura_synth
import sg_synth
import synth
from fcn_checker.issuers import NATIXIS
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from harness import field_problems as problems
from harness import results as rule
from natixis_synth import ADDRESS, DIST, PRODUCT_TYPE, WARNING, Spec, article_title, build_pdf, check, check_item, row
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

VARIANTS = {
    "KO 每日記憶式、Non-Call 2、KI 到期、3 檔美股": Spec(),
    "KO 每日非記憶式、單一標的、KI 每日、Non-Call 1": Spec(count=1, memory=False, ki="D", first_callable=1),
    "KO 每期記憶式、日股整數期初價、Non-Call 1": Spec(ko_obs="P", first_callable=1, market="JT", currency_zh="日圓"),
    "KO 每期非記憶式、Non-Call = 天期、無 KI": Spec(
        ko_obs="P", first_callable=6, memory=False, ki="none", ko=Decimal("100.00")
    ),
    "澳幣、12 個月、VWAP、數字拆行": Spec(
        currency_zh="澳幣", tenor=12, first_callable=3, pricing="vwap", split_numbers=True
    ),
}


def _renumbered_from(start: int, stop: int = 27) -> list[Edit]:
    """第一章第 start 條起條號重新從 1 編起（Word 自動編號跑掉，Issue #270）。"""
    edits = []
    for n in range(start, stop):
        edits.append(Edit(f"{n}. {article_title(n)}", f"{n - start + 1}. {article_title(n)}", f"ts.art{n}"))
    return edits


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass_and_are_filled(tmp_path, spec):
    item = check_item(tmp_path, spec)
    r = item.report
    assert r.template == NATIXIS.template_id
    assert r.status == S.PASS, problems(r)
    assert item.fills_sheet
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in NATIXIS.not_covered}
    assert [x.status for x in rule(r, "derive.monthly_coupon")] == [S.PASS]
    assert len(rule(r, "doc.coupon_mentions")) == 2  # 情境假設年利率、各期配息年利率
    assert rule(r, "standard.fixed_warning")[0].actual == 3
    assert len(rule(r, "standard.issuer_name")) == 3  # 封面、第一章第 3 項、第二章
    skipped = ("standard.guarantor_name", "standard.agent_name", "standard.minimum_issue_size")  # 決定不核對
    assert not [x for x in r.results if x.rule_id in skipped]
    assert len(rule(r, "field.min_amounts")) == 4
    assert not [x for x in r.results if x.rule_id == "doc.distributor_product_code"]  # 新版沒有這個標籤
    assert rule(r, "field.initial_pricing")[0].status == S.PASS  # 說明書沒寫：只檢查表上是允許值
    assert rule(r, "standard.product_name", "name_en")[0].status == S.PASS
    assert rule(r, "doc.name_consistency", "name_art1_en")[0].status == S.PASS  # 第一章「Non Principal」視為一致


def test_old_version_labels_are_read_and_blank_distributor_code_needs_review(tmp_path):
    """舊版封面寫「受託機構」：標籤照讀；「受託機構商品代號為」空白轉人工覆核（2026-10-15 確認）。"""
    r = check(tmp_path, Spec(old=True))
    assert {(rid, f) for rid, f, _ in problems(r)} == {("doc.distributor_product_code", "distributor_code")}
    assert rule(r, "doc.distributor_product_code")[0].status == S.REVIEW_REQUIRED
    assert all(x.status == S.PASS for x in rule(r, "standard.distributor"))


def test_2024_document_compares_with_the_2024_approval_date(tmp_path):
    """2024-03-20 是受託機構真實的審查日期（2026-10-15 作業人員確認，Issue #261）。"""
    spec = Spec(trade_date=dt.date(2024, 8, 8), issue_date=dt.date(2024, 8, 15), approval=dt.date(2024, 3, 20))
    r = check(tmp_path, spec)
    assert rule(r, "standard.approval_date")[0].status == S.PASS, problems(r)


def test_underlyings_are_compared_in_order(tmp_path):
    """Natixis 連結標的表與參考條件表同順序：表上順序不同判不一致（不在 any_order_issuers）。"""
    spec = Spec()
    out = row(spec)
    out["UL_1"], out["UL_2"] = out["UL_2"], out["UL_1"]
    path = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [out])
    item = next(i for i in check_all(sheet, [path]).items if i.term_sheet == path)
    assert rule(item.report, "field.underlyings")[0].status == S.MISMATCH


def test_vwap_prices_are_filled_from_the_term_sheet(tmp_path):
    spec = Spec(pricing="vwap")
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d for d in r.backfill}
    assert cells["UL_1_進場價"].expected == spec.underlyings[0].initial


def test_sheet_memory_and_non_call_are_compared(tmp_path):
    r = check(tmp_path, Spec(), **{"KO(memo)": "N", "Non-Call(月)": 3})
    assert rule(r, "field.ko_memory")[0].status == S.MISMATCH
    assert rule(r, "field.first_callable_period")[0].status == S.MISMATCH


def test_periodic_ki_is_read_from_the_judgement_day(tmp_path):
    """觸及事件判定日「各配息觀察期間結束日」= 每期觀察（P）。"""
    r = check(tmp_path, Spec(ki="P", first_callable=1))
    assert r.status == S.PASS, problems(r)
    assert rule(r, "field.ki_type")[0].actual == "每期觀察"


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("月配息率：1.5000%", "月配息率：1.5100%", "ts.art13"), "derive.monthly_coupon"),
        (Edit("f. 年利率：每年18.00%", "f. 年利率：每年18.50%", "ts.art16"), "doc.coupon_mentions"),
        (Edit("x (18.00%/12)", "x (18.50%/12)", "ts.art16"), "doc.coupon_mentions"),
        (Edit("自動提前到期事件於第二個", "自動提前到期事件於第三個", "ts.art16"), "doc.autocall_terms"),
        (Edit("e. 自動提前到期觀察日：從第二個", "e. 自動提前到期觀察日：從第三個", "ts.art16"), "doc.autocall_terms"),
        (Edit("c. 天期：6 個月", "c. 天期：7 個月", "ts.art16"), "doc.coupon_periods"),
        (Edit("105.4725", "105.4726", "ts.art16.table"), "doc.scenario_table"),
        (Edit("a. 每單位商品面額 = 10,000 美元", "a. 每單位商品面額 = 1,000 美元", "ts.art16"), "doc.scenario_table"),
        (Edit("之75.00%)", "之76.00%)", "ts.art9.table"), "doc.table_header_pct"),
        (Edit("6個月美元計價", "7個月美元計價", "ts.cover.title"), "doc.name_consistency"),
        (Edit("6個月美元計價", "7個月美元計價", "ts.art1"), "doc.name_consistency"),
        (
            Edit("Linked to Basket of Equities (Non", "Linked to Basket of Shares (Non", "ts.art1"),
            "doc.name_consistency",
        ),
        (Edit("(1) 計價幣別：美元 (USD)", "(1) 計價幣別：美元 (JPY)", "ts.art5"), "doc.currency_consistency"),
        (Edit("美元 (USD)", "美元 (JPY)", "ts.cover"), "doc.currency_consistency"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_internal_rules_find_the_wrong_repeat(tmp_path, edit, broken):
    """正式條款正確、但重複出現處錯誤：只有該規則不通過。"""
    r = check(tmp_path, edits=[edit])
    bad = {rid for rid, _, _ in problems(r)}
    assert broken in bad, problems(r)
    # 連結標的表表頭 % 改了，情境價格表的表頭 % 就和連結標的表不同（同一處錯誤的兩種表現）
    also = {"doc.scenario_table"} if broken == "doc.table_header_pct" else set()
    assert bad <= {broken, "standard.product_name", *also}, problems(r)


@pytest.mark.parametrize(
    ("spec", "edit", "expected"),
    [
        # 情境與第 15 項重複寫出的贖回 %、面額、執行價格、配息列數
        (
            Spec(),
            Edit("自動提前到期贖回金額為原始投資本金乘以100%", "自動提前到期贖回金額為原始投資本金乘以99%", "ts.art16"),
            ("doc.redemption_pct", "scenario_autocall_pct_1"),
        ),
        (
            Spec(),
            Edit(
                "高於其執行價格，到期贖回金額為原始投資本金乘以100%",
                "高於其執行價格，到期贖回金額為原始投資本金乘以99%",
            ),
            ("doc.redemption_pct", "scenario_maturity_pct_2"),
        ),
        (
            Spec(),
            Edit("則本商品將以每單位商品面額之100%贖回", "則本商品將以每單位商品面額之99%贖回", "ts.art15"),
            ("doc.redemption_pct", "art15_case2a_1"),
        ),
        (
            Spec(),
            Edit("每單位商品面額 x 100%", "每單位商品面額 x 99%", "ts.art15"),
            ("doc.redemption_pct", "art15_formula_2"),
        ),
        (
            Spec(),
            Edit("10,000 美元 x (18.00%/12)", "1,000 美元 x (18.00%/12)", "ts.art16.s3.r4"),
            ("doc.scenario_params", "scenario_coupon_denomination_3"),
        ),
        (
            Spec(),
            Edit("自動提前到期日收到10,000 美元", "自動提前到期日收到1,000 美元", "ts.art16"),
            ("doc.scenario_params", "scenario_principal_1"),
        ),
        (
            Spec(),
            Edit("執行價格 = 10,000 美元/", "執行價格 = 1,000 美元/", "ts.art16"),
            ("doc.scenario_params", "scenario_physical_denomination_4"),
        ),
        (Spec(), Edit("/ 75.3375 美元", "/ 75.3376 美元", "ts.art16"), ("doc.scenario_table", "scenario_worst_strike")),
        (
            Spec(),
            Edit("10,000 美元 x (18.00%/12) = 150.00 美元", "", "ts.art16.s2.r6"),
            ("doc.coupon_periods", "scenario2_rows"),
        ),
        # 說明文字與情境的幣別字 = 承作幣別
        (
            Spec(),
            Edit("條款以美元計算及支付配息", "條款以日圓計算及支付配息", "ts.art8"),
            ("field.currency", "payment_currency_1"),
        ),
        (Spec(), Edit("100% 美元投資本金", "100% 日圓投資本金", "ts.art6"), ("field.currency", "principal_currency")),
        (Spec(), Edit("所得之美元金額", "所得之日圓金額", "ts.art15"), ("field.currency", "cash_remainder_currency")),
        (
            VARIANTS["KO 每期記憶式、日股整數期初價、Non-Call 1"],
            Edit("整數位之日圓金額", "整數位之美元金額", "ts.art13"),
            ("field.currency", "coupon_rounding_currency"),
        ),
        (Spec(), Edit("美元之累積配息", "日圓之累積配息", "ts.art16.s2"), ("field.currency", "scenario2_currency")),
        # 受託機構名稱的其他出處
        (
            Spec(),
            Edit("股份有限公司)所保證", "股份有限公司X)所保證", "ts.ch三"),
            ("standard.distributor", "distributor_name_ch3_w5"),
        ),
        (
            Spec(),
            Edit("股份有限公司)負責外", "股份有限公司X)負責外", "ts.warn"),
            ("standard.distributor", "distributor_name_cover_w6b"),
        ),
        (
            Spec(),
            Edit("股份有限公司)提出贖回指示", "股份有限公司X)提出贖回指示", "ts.ch四"),
            ("standard.distributor", "distributor_name_ch4_10a"),
        ),
    ],
    ids=lambda x: x[1] if isinstance(x, tuple) else "",
)
def test_repeated_values_must_equal_the_terms(tmp_path, spec, edit, expected):
    """重複寫出的值（Issue #318）：正式條款正確、只有重複處錯，判不一致，其他規則不受影響。"""
    r = check(tmp_path, spec, edits=[edit])
    bad = {(rid, f): st for rid, f, st in problems(r)}
    assert bad.get(expected) == S.MISMATCH, problems(r)
    assert {rid for rid, _ in bad} <= {expected[0], "standard.product_name"}, problems(r)


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (Edit("100% 美元投資本金", "100%投資本金", "ts.art6"), ("field.currency", "principal_currency")),
        (Edit("執行價格 = 10,000", "執行價格約 10,000", "ts.art16"), ("doc.scenario_table", "scenario_worst_strike")),
        (Edit("10,000 美元 x (18.00%/12) = 150.00 美元", "", "ts.art16.s2"), ("doc.coupon_periods", "scenario2_rows")),
        (
            Edit(f"受託或銷售機構({DIST['name']})提出", "受託或銷售機構提出", "ts.ch四"),
            ("standard.distributor", "distributor_name_ch4_10a"),
        ),
    ],
    ids=lambda x: x[1] if isinstance(x, tuple) else "",
)
def test_unreadable_repeated_values_need_review(tmp_path, edit, expected):
    """重複寫出處讀不到（Issue #318）：轉人工覆核。"""
    r = check(tmp_path, edits=[edit])
    assert {(rid, f): st for rid, f, st in problems(r)}.get(expected) == S.REVIEW_REQUIRED, problems(r)


def test_scenario_results_show_each_cell(tmp_path):
    """情境價格表、贖回 %、面額的結果帶出每一格／每一處（Issue #318）。"""
    r = check(tmp_path)
    table = rule(r, "doc.scenario_table")[0]
    assert table.status == S.PASS and len(table.actual) == 12 and table.actual == table.expected  # 3 檔 x 4 欄
    assert {x.field for x in rule(r, "doc.scenario_params")} >= {
        "scenario_coupon_denomination_1",
        "scenario_principal_1",
        "scenario_physical_denomination_4",
    }


def test_ko_start_outside_the_schedule_needs_review(tmp_path):
    r = check(tmp_path, edits=[Edit("觀察日：從第二個配息觀察期間", "觀察日：從第九個配息觀察期間", "ts.art12")])
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_ki_period_must_match_the_judgement_day(tmp_path):
    """觸及事件判定日寫最終評價日（AM）、判定期間卻寫整段期間：轉人工覆核。"""
    edit = Edit(
        "「觸及事件判定期間」係指最終評價日。",
        "「觸及事件判定期間」係指期初進場日(含)起至最終評價日(含)止之期間",
        "ts.art15",
    )
    r = check(tmp_path, edits=[edit])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_ki_type_without_the_no_ki_redemption_wording_needs_review(tmp_path):
    """沒有觸及事件與界限價格定義、到期贖回情況二也不是只有實物交割：KI 型態轉人工覆核（Issue #297）。"""
    spec = VARIANTS["KO 每期非記憶式、Non-Call = 天期、無 KI"]
    edit = Edit("則於到期日發行機構將依下列所述情況", "則於到期日發行機構將依下列方式", "ts.art15")
    r = check(tmp_path, spec, edits=[edit])
    assert rule(r, "field.ki_type")[0].status == S.REVIEW_REQUIRED


def test_ki_definition_must_match_the_ki_wording(tmp_path):
    edit = Edit(
        "「界限價格」係指就任一連結標的而言，各該連結標的之期初進場價格之60.00%，惟可能因第一章第11 項之規定而調整。",
        "",
        "ts.art15",
    )
    r = check(tmp_path, edits=[edit])
    assert rule(r, "doc.ki_consistency", "ki_def_pct")[0].status == S.REVIEW_REQUIRED


def test_last_payment_date_must_be_the_maturity_date(tmp_path):
    spec = Spec()
    old = f"即{spec.date(spec.maturity_date)}"
    r = check(tmp_path, spec, edits=[Edit(old, "即2031 年01 月20 日", "ts.art12")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_schedule_rows_must_equal_the_tenor(tmp_path):
    r = check(tmp_path, edits=[Edit("商品年期：6 個月", "商品年期：7 個月", "ts.art12")])
    assert rule(r, "doc.coupon_periods", "date_table")[0].status == S.MISMATCH


def test_periodic_last_observation_must_be_the_final_valuation_date(tmp_path):
    spec = VARIANTS["KO 每期記憶式、日股整數期初價、Non-Call 1"]
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[-1]), "2030 年07 月10 日", "ts.art12.table")])
    assert rule(r, "schedule.coupon_dates")[0].status == S.MISMATCH


def test_periodic_table_must_cover_non_call_to_the_last_period(tmp_path):
    """自動提前到期觀察日表第 2 列的自動提前到期日不在配息日表：KO 觀察、Non-Call、比價日轉人工覆核。"""
    spec = VARIANTS["KO 每期記憶式、日股整數期初價、Non-Call 1"]
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.payments[1]), "2030 年03 月20 日", "ts.art12.table.ko")])
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


# ---------------------------------------------------------------- 審查標準（Natixis 分節）


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit(WARNING[:20], WARNING[:19] + "X", "ts.ch三"), "standard.fixed_warning"),
        (Edit(PRODUCT_TYPE, "股權連結結構型債券", "ts.cover"), "standard.product_type"),
        (Edit("(1) 事業名稱：法國外貿銀行", "(1) 事業名稱：法國外貿銀行台北分行", "ts.ch二"), "standard.issuer_name"),
        (Edit("申購價金的 0%~5%", "申購價金的 0%~3%", "ts.ch四"), "standard.fees"),
        (Edit(f"地址：{ADDRESS}", "地址：台北市虛構路2號", "ts.cover"), "standard.distributor"),
        (Edit(f"負責人姓名：{DIST['chairman']}", "負責人姓名：林晉輝", "ts.ch二"), "standard.chairman"),
        (Edit("2026 年06 月11 日", "2025 年12 月18 日", "ts.cover"), "standard.approval_date"),
        (
            Edit(
                "最低申購金額為1 單位商品面額，即10,000 美元", "最低申購金額為1 單位商品面額，即50,000 美元", "ts.ch四"
            ),
            "field.min_amounts",
        ),
        (Edit("(3) 發行價格：100%", "(3) 發行價格：99%", "ts.art5"), "standard.issue_price"),
        (Edit("(Non-Principal Protected", "(Non Principal Protected", "ts.cover"), "standard.product_name"),
        (
            Edit("於中華民國境內受託買賣之投資標的", "於中華民國境內受託投資之投資標的", "ts.warn"),
            "standard.forbidden_wording",
        ),
    ],
    ids=lambda x: x if isinstance(x, str) else x.old[:12],
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
    ],
    ids=["BARC", "HSBC", "MS", "BNP", "SG", "Citi", "JPM", "Nomura", "GS"],
)
def test_other_issuers_are_not_natixis(tmp_path, build):
    pdf = build(tmp_path / "328199990001_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    for item in check_all(sheet, [pdf]).items:
        assert item.report.template not in (NATIXIS.template_id, NATIXIS.iis.template_id)


@pytest.mark.parametrize("start", [9, 13, 25])
def test_chapter_one_numbering_restarting_from_one_is_read_in_order(tmp_path, start):
    """第一章條號中途重新從 1 編起（例：第 12 條後接「1. 配息資料」…「14.」；共 26 條）：依先後當成第 1～26 條照常核對，
    不另報條號錯誤（2026-10-15 確認，Issue #270）。"""
    item = check_item(tmp_path, edits=_renumbered_from(start))
    assert item.report.template == NATIXIS.template_id
    assert item.report.status == S.PASS, problems(item.report)
    assert item.fills_sheet


def test_numbering_restarting_with_articles_missing_is_still_not_natixis(tmp_path):
    """重編後合計不是 26 條（第 26 條不見）：仍認不出範本。"""
    edits = [*_renumbered_from(13), Edit("14. 第26項說明（不核對）", "", "ts.art26")]
    item = check_item(tmp_path, edits=edits)
    assert item.report.template != NATIXIS.template_id


def test_only_one_restart_is_accepted(tmp_path):
    """重編只容許一次（Issue #270）：第 13 條起從 1 重編、第 20 條起又從 1 重編 → 不當成 26 條，仍認不出範本。"""
    item = check_item(tmp_path, edits=[*_renumbered_from(13, 20), *_renumbered_from(20)])
    assert item.report.template != NATIXIS.template_id
