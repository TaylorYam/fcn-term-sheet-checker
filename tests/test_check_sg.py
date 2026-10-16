"""SG 黑箱回歸（Issue #194）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import bnp_synth
import hsbc_synth
import ms_synth
import synth
from fcn_checker.issuers import SG
from fcn_checker.messages import problem_message
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row
from sg_synth import Spec, build_pdf, check


@pytest.mark.parametrize(
    "spec",
    [
        Spec(),  # D、記憶式、KI 到期觀察、3 檔、6 個月、Non-Call 2
        Spec(memory=False, ki="none", count=1, tenor=3, first_callable=1),  # 單一標的、非記憶式、無 KI
        Spec(ko_obs="P", memory=True, ki="D", tenor=10, first_callable=8),  # P 型記憶式 + KI 每日觀察
        Spec(ko_obs="P", memory=False, ki="P", tenor=7, first_callable=1, legacy=True, ko=Decimal("110.00")),
        Spec(
            autocall=False,
            ki="none",
            tenor=3,
            currency_zh="境外人民幣",
            strike=Decimal("86.21"),
            units=1,
            fx=Decimal("0.0400"),
            legacy=True,
        ),  # 無自動提前出場、CNH quanto、舊版面
        Spec(currency_zh="日圓", ko=Decimal("105.00"), count=4),
        Spec(ko_obs="D", memory=False, ki="AM", count=2, tenor=12, first_callable=4),
    ],
    ids=lambda s: (
        f"{'A' if s.autocall else 'none'}-{s.ko_obs}-{'mem' if s.memory else 'plain'}-KI{s.ki}-{s.count}UL-{s.tenor}m-NC{s.first_callable}-{s.ccy}"
        + ("-legacy" if s.legacy else "")
    ),
)
def test_supported_types_pass(tmp_path, spec):
    r = check(tmp_path, spec)
    assert r.template == SG.template_id
    assert r.status == S.PASS, problems(r)
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in SG.not_covered}
    assert rule(r, "field.coupon_pa_pct")[0].status == S.PASS
    assert {x.status for x in rule(r, "derive.monthly_coupon")} == {S.PASS}
    assert len(rule(r, "standard.guarantor_name")) == 3
    assert len(rule(r, "standard.agent_name")) == 2
    assert rule(r, "standard.product_type")[0].status == S.PASS
    assert {x.field for x in rule(r, "doc.subscription_start_date")} == {"subscription_start"}
    assert {x.field for x in rule(r, "standard.issuer_name")} == {"issuer_name_cover", "issuer_name_ch2"}


def test_monthly_rate_is_always_four_digits(tmp_path):
    """配息率 = 年利率 ÷ 12 四捨五入 4 位（JPY 也 4 位），配息金額 2 位。"""
    (tmp_path / "jpy").mkdir()
    jpy = check(tmp_path / "jpy", Spec(currency_zh="日圓", annual=Decimal("8")))  # 8 ÷ 12 → 0.6667
    assert jpy.status == S.PASS, problems(jpy)
    assert rule(jpy, "derive.monthly_coupon")[0].expected == Decimal("0.6667")
    assert rule(jpy, "doc.scenario_calculations", "情境一_coupon")[0].expected == Decimal("66670.00")


def test_min_subscription_multiple_depends_on_currency(tmp_path):
    """最低申購金額 = 面額 × 依幣別的倍數（USD 10、JPY 1）；最低加購、最低贖回 = 面額；沒列的幣別轉人工覆核。"""
    for name in ("usd", "jpy", "hkd"):
        (tmp_path / name).mkdir()
    usd = {x.field: x for x in rule(check(tmp_path / "usd"), "field.min_amounts")}
    assert usd["minimum_subscription"].expected == 100000 and usd["minimum_subscription"].status == S.PASS
    assert usd["minimum_additional"].expected == 10000 and usd["minimum_redemption"].expected == 10000
    jpy = {x.field: x for x in rule(check(tmp_path / "jpy", Spec(currency_zh="日圓")), "field.min_amounts")}
    assert jpy["minimum_subscription"].expected == 1000000 and jpy["minimum_subscription"].status == S.PASS
    hkd = rule(check(tmp_path / "hkd", Spec(currency_zh="港幣")), "field.min_amounts", "minimum_subscription")[0]
    assert (hkd.status, hkd.reason_code) == (S.REVIEW_REQUIRED, "multiple_not_configured")
    assert "HKD" in hkd.message


@pytest.mark.parametrize(
    "column,value,rule_id",
    [
        ("Coupon p.a. (%)", 12.12, "field.coupon_pa_pct"),
        ("Coupon p.a. (%)", 12.12, "derive.monthly_coupon"),
        ("Coupon p.a. (%)", 12.12, "derive.annualized_return"),
        ("Coupon p.a. (%)", 12.12, "doc.scenario_calculations"),
        ("KO(%)", 101, "field.ko_pct"),
        ("K(%)", 71, "field.strike_pct"),
        ("KI(%)", 61, "field.ki_pct"),
        ("KI(Freq)", "D", "field.ki_type"),
        ("KO(Freq)", "P", "field.ko_observation"),
        ("KO(memo)", "N", "field.ko_memory"),
        ("天期(月)", 5, "field.tenor_months"),
        ("Non-Call(月)", 1, "field.first_callable_period"),
        ("交易日", dt.datetime(2030, 1, 8), "field.trade_date"),
        ("最終比價日", dt.datetime(2030, 7, 16), "field.final_valuation_date"),
        ("到期日", dt.datetime(2030, 7, 19), "field.maturity_date"),
        ("單位面額", 20000, "field.denomination"),
        ("單位面額", 20000, "field.min_amounts"),
        ("承作幣別", "JPY", "field.currency"),
        ("UL_1", "ZS9 UW", "field.underlyings"),
        ("UL_1_執行價", 71, "field.underlying_prices"),
        ("UL_2_KO價", 201, "field.underlying_prices"),
        ("ISIN Code", "XS1999900002", "backfill.isin"),
        ("發行日", dt.datetime(2030, 1, 15), "backfill.issue_date"),
        ("比價日_2", dt.datetime(2030, 3, 15), "backfill.compare_dates"),
    ],
)
def test_sheet_difference_is_reported(tmp_path, column, value, rule_id):
    assert flagged(check(tmp_path, **{column: value}), rule_id)


def test_issuer_column_must_be_sg(tmp_path):
    r = check(tmp_path, **{"發行機構": "MS"})
    assert r.status == S.REVIEW_REQUIRED and not [x for x in r.results if x.rule_id.startswith("doc.")]


S0 = Spec()


@pytest.mark.parametrize(
    "change,rule_id,status",
    [
        # 配息率與平均年化報酬率：正式條款正確、重印處錯誤也要抓到（核對規則 §3.8）
        (("art8", "配息率 ＝1.1000%", "配息率 ＝1.1001%"), "derive.monthly_coupon", S.MISMATCH),
        (("art15", "配息率 = 1.1000 %", "配息率 = 1.1001 %"), "derive.monthly_coupon", S.MISMATCH),
        (("scen.二", "x1.1000%", "x1.1001%"), "derive.monthly_coupon", S.MISMATCH),
        (("scen.一", "平均年化報酬率：13.20%", "平均年化報酬率：13.21%"), "derive.annualized_return", S.MISMATCH),
        # 封面與第一章交叉驗證
        (("cover", "股權連結結構型債券", "股權連結結構型商品"), "standard.product_type", S.MISMATCH),
        (("art1", "固定配息", "固定收益"), "doc.name_consistency", S.MISMATCH),
        (("art5", "美元", "日圓"), "doc.currency_consistency", S.MISMATCH),
        (("cover", "計價6個月", "計價7個月"), "doc.coupon_periods", S.MISMATCH),
        (("art12", "商品年期：6個月", "商品年期：7個月"), "doc.coupon_periods", S.MISMATCH),
        (
            ("art8", "自動提前出場贖回金額＝每單位面額×100%", "自動提前出場贖回金額＝每單位面額×99%"),
            "doc.redemption_pct",
            S.MISMATCH,
        ),
        (("art8", "到期贖回金額＝每單位面額×100%", "到期贖回金額＝每單位面額×99%"), "doc.redemption_pct", S.MISMATCH),
        (("art26", "XS1999900001", "XS1999900002"), "doc.isin_consistency", S.MISMATCH),
        (
            ("cover", "受託或銷售機構商品代號 037199990001", "受託或銷售機構商品代號 037199990002"),
            "doc.trustee_product_code",
            S.MISMATCH,
        ),
        (("cover", "（記憶式自動提前出場）", "可自動提前出場"), "doc.ko_memory", S.REVIEW_REQUIRED),
        # 日期表與提前出場
        (
            ("art12.table", S0.date(S0.ends[1]), S0.date(S0.ends[1] + dt.timedelta(days=1))),
            "schedule.autocall_dates",  # 第 k+1 期起始日 = 第 8 項觀察期間起始日（第 k 期沒有終止日，共用規則不比）
            S.MISMATCH,
        ),
        (
            ("art12.table", S0.date(S0.payments[-1]), S0.date(S0.payments[-1] + dt.timedelta(days=1))),
            "schedule.last_payment",
            S.MISMATCH,
        ),
        (("art12.table", "（到期日）", ""), "schedule.row_labels", S.MISMATCH),
        (("art8", "第3-6個配息週期", "第4-6個配息週期"), "schedule.autocall_dates", S.MISMATCH),
        (("art12", "事件後第3個營業日", "事件後第5個營業日"), "schedule.autocall_dates", S.MISMATCH),
        # 標的表
        (("art8", "執行價：連結標的期初價格的70.00%", "執行價：連結標的期初價格的71.00%"), "derive.prices", S.MISMATCH),
        (("art8.table", "70.0000美元", "70.0001美元"), "derive.prices", S.MISMATCH),
        (
            ("art8", "(1) 若於最終評價日最差表現之連結標的收盤價大於或等於下限價", "(1) 若於每個觀察日"),
            "field.ki_type",
            S.REVIEW_REQUIRED,
        ),
        # 第 15 項情境（第 8 項正確、第 15 項重印錯）
        (("art15.table", "70.0000美元", "70.0001美元"), "doc.scenario_table", S.MISMATCH),
        (("art15.table", "(期初價格的70.00%)", "(期初價格的71.00%)"), "doc.scenario_header_pct", S.MISMATCH),
        (
            ("art15", "總投資金額 = 10單位面額 = 100,000美元", "總投資金額 = 10單位面額 = 200,000美元"),
            "doc.scenario_parameters",
            S.MISMATCH,
        ),
        (("scen.二", "= 1,100.00美元", "= 1,100.01美元"), "doc.scenario_calculations", S.MISMATCH),
        (
            ("scen.二", "累積總配息金額 = 6,600.00美元", "累積總配息金額 = 6,600.01美元"),
            "doc.scenario_calculations",
            S.MISMATCH,
        ),
        (("scen.一", "第1~2個配息金額", "第1~3個配息金額"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.二", "= 6.6000%", "= 6.6001%"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.三", "= 142單位", "= 141單位"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.三", "x0.8572", "x0.8573"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.三", "= 12.01美元", "= 12.00美元"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.三", "-73.40%", "-73.41%"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.四", "（平均年化報酬率：-", "（平均年化報酬率：-1"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.三", "= 每單位面額 / 於", "= 每單位面額 ÷ 於"), "doc.scenario_calculations", S.REVIEW_REQUIRED),
        (("scen.三", "較差情況", "保守情況"), "doc.scenario_parameters", S.REVIEW_REQUIRED),
        # 第四章
        (("ch四", "最低加購金額：10,000美元", "最低加購金額：50,000美元"), "field.min_amounts", S.MISMATCH),
        (
            ("ch四", "最低贖回金額或單位數：10,000美元", "最低贖回金額或單位數：20,000美元"),
            "field.min_amounts",
            S.MISMATCH,
        ),
        (("ch四", "×申購單位數×100%", "×申購單位數×99%"), "standard.issue_price", S.REVIEW_REQUIRED),
        (("ch四", "0%~5%", "0%~6%"), "standard.fees", S.MISMATCH),
        (
            ("ch四", f"自{S0.date(S0.trade_date)}起", f"自{S0.date(S0.trade_date + dt.timedelta(days=1))}起"),
            "doc.subscription_start_date",
            S.MISMATCH,
        ),
        # 審查標準（SG 版本）
        (("ch三", "歸類為RR4。", "歸類為【RR4】。"), "standard.fixed_warning", S.MISMATCH),
        (("ch三", "歸類為RR4", "歸類為RR3"), "standard.risk_level", S.MISMATCH),
        (("art2", "歸類為[RR4]", "歸類為[RR3]"), "standard.fixed_warning", S.MISMATCH),
        (("ch二", "事業名稱：SG ISSUER", "事業名稱：SG ISSUER B.V."), "standard.issuer_name", S.MISMATCH),
        (
            (
                "cover",
                "法商法國興業銀行股份有限公司(Societe Generale)（下稱",
                "法商法國興業銀行(Societe Generale)（下稱",
            ),
            "standard.guarantor_name",
            S.MISMATCH,
        ),
        (
            ("art3", "：法商法國興業銀行股份有限公司(Societe Generale)，截至", "：法國興業銀行，截至"),
            "standard.guarantor_name",
            S.MISMATCH,
        ),
        (
            (
                "ch二",
                "事業名稱：法商法國興業銀行股份有限公司(Societe Generale)",
                "事業名稱：法國興業銀行(Societe Generale)",
            ),
            "standard.guarantor_name",
            S.MISMATCH,
        ),
        (
            ("ch二", "‧ 事業名稱：法商法國興業銀行股份有限公司台北分行", "‧ 事業名稱：法商法國興業銀行台北分行"),
            "standard.agent_name",
            S.MISMATCH,
        ),
        (("cover", "電話：+886 2 5556 1313", "電話：+886 2 5556 1314"), "standard.distributor", S.MISMATCH),
        (
            ("ch二", "營業所在地：台北市松山區民生東路三段158號6樓", "營業所在地：台北市松山區民生東路三段159號6樓"),
            "standard.distributor",
            S.MISMATCH,
        ),
        (("cover", "Memory Autocallable", "Autocallable"), "standard.product_name", S.MISMATCH),
        (("cover", "2026年06月11日", "2025年12月18日"), "standard.approval_date", S.MISMATCH),
    ],
)
def test_document_difference_is_reported(tmp_path, change, rule_id, status):
    section, old, new = change
    r = check(tmp_path, S0, edits=[Edit(old, new, f"ts.{section}")])
    assert any(x.rule_id == rule_id and x.status == status for x in r.results), problems(r)


@pytest.mark.parametrize(
    "change",
    [
        ("art1", "（記憶式自動提前出場）", "(記憶式自動提前出場)"),  # 括號全半形不計
        ("art1", "（記憶式自動提前出場）", "記憶式自動提前出場"),  # 第一章名稱多或少一對括號不計（2026-10-13 確認）
        ("cover", "電話：+886 2 5556 1313", "電話：02-5556-1313"),  # 審查標準本身的電話寫法
        ("ch二", "‧ 事業名稱：玉山證券", "‧ 事業名稱：玉山綜合證券股份有限公司"),  # 第二章寫全名也可以
        ("ch三", "於中華民國境內受託買賣", "為於中華民國境內受託買賣"),  # 警語七兩種寫法都允許（全文不核對）
    ],
)
def test_allowed_variants_still_pass(tmp_path, change):
    section, old, new = change
    r = check(tmp_path, S0, edits=[Edit(old, new, f"ts.{section}")])
    assert r.status == S.PASS, problems(r)


def test_fixed_warning_counts_the_bracketed_variant_in_chapter_one(tmp_path):
    r = check(tmp_path)
    warning, level = rule(r, "standard.fixed_warning")[0], rule(r, "standard.risk_level")[0]
    assert (warning.status, warning.actual) == (S.PASS, 3)
    assert level.status == S.PASS and level.actual == ["RR4"]
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS
    name = {x.field: x for x in rule(r, "standard.distributor")}
    assert (
        name["distributor_name_ch2"].status == S.PASS and "name_equivalents" in name["distributor_name_ch2"].tolerance
    )
    assert (
        name["distributor_address_cover"].status == S.PASS
        and "address_equivalents" in name["distributor_address_cover"].tolerance
    )


def test_blank_trustee_product_code_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(trustee_code=""))
    x = rule(r, "doc.trustee_product_code")[0]
    assert x.status == S.MISMATCH and x.actual == "（空白）"
    assert "受託或銷售機構商品代號" in problem_message(x) and x.document_evidence


def test_no_autocall_maps_to_p_and_non_call_equals_tenor(tmp_path):
    """無自動提前出場（Non-Call = 天期，機制仍在只是不會觸發）：名稱沒有自動提前出場字樣；
    表上 KO(Freq)=P、KO(%)=100、Non-Call=天期、各標的 KO 價 = 進場價，照常比對。"""
    spec = Spec(autocall=False, ki="none", tenor=3)
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    ko = rule(r, "field.ko_pct")[0]
    assert ko.status == S.PASS and ko.actual == 100
    ko_prices = [x for x in rule(r, "field.underlying_prices") if x.item.name.endswith("KO價")]
    assert ko_prices and all(x.status == S.PASS for x in ko_prices)
    assert rule(r, "field.ko_observation")[0].status == S.PASS
    assert rule(r, "field.first_callable_period")[0].actual == 3
    assert rule(r, "doc.redemption_pct", "autocall_redemption_pct")[0].status == S.NOT_APPLICABLE
    name = rule(r, "standard.product_name", "name_zh")[0]
    assert name.status == S.PASS and "自動提前出場" not in name.expected
    other = tmp_path / "other"
    other.mkdir()
    r = check(other, spec, edits=[Edit("固定配息股權", "固定配息可自動提前出場股權", "ts.cover")])
    assert rule(r, "standard.product_name", "name_zh")[0].status == S.MISMATCH
    assert rule(r, "doc.autocall_wording", "name_zh")[0].status == S.REVIEW_REQUIRED


@pytest.mark.parametrize("column,value", [("KO(%)", 95), ("UL_1_KO價", 1)])
def test_no_autocall_sheet_ko_must_be_100_pct_of_initial(tmp_path, column, value):
    """無自動提前出場時表上 KO(%) 不是 100、或 KO 價不是進場價 → 不一致。"""
    r = check(tmp_path, Spec(autocall=False, ki="none", tenor=3), **{column: value})
    rid = "field.ko_pct" if column == "KO(%)" else "field.underlying_prices"
    assert flagged(r, rid)


def test_tables_split_across_pages_still_read(tmp_path):
    r = check(tmp_path, Spec(table_page_break=True))
    assert r.status == S.PASS, problems(r)


def test_end_labels_on_a_non_final_row_are_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(label_row=5))
    x = rule(r, "schedule.row_labels")[0]
    assert x.status == S.MISMATCH
    assert "末列沒有標「（最終評價日）」" in problem_message(x) and "第 5 期不是末列" in problem_message(x)


def test_p_table_final_row_without_valuation_date_is_reviewed(tmp_path):
    spec = Spec(ko_obs="P", memory=False, ki="AM", tenor=6, first_callable=2)
    r = check(tmp_path, spec, edits=[Edit(spec.date(spec.ends[-1]), "不適用", "ts.art12.table")])
    assert rule(r, "field.final_valuation_date")[0].status == S.REVIEW_REQUIRED


def test_scenario_strike_is_reviewed_when_share_formula_is_unreadable(tmp_path):
    r = check(tmp_path, edits=[Edit("= 每單位面額 / 於", "= 每單位面額 ÷ 於", "ts.scen.三")])
    for field in ("情境三_strike", "情境三_shares"):
        assert rule(r, "doc.scenario_calculations", field)[0].status == S.REVIEW_REQUIRED


def test_price_table_ko_column_without_autocall_event_is_flagged(tmp_path):
    r = check(tmp_path, edits=[Edit("自動提前出場事件：", "提前出場條件：", "ts.art8")])
    assert flagged(r, "doc.autocall_wording")


def test_legacy_layout_with_old_wording_is_a_mismatch(tmp_path):
    spec = Spec(ko_obs="P", memory=False, ki="P", tenor=7, first_callable=1, legacy=True)
    r = check(tmp_path, spec, edits=[Edit("為於中華民國境內受託買賣", "為於中華民國境內受託投資", "ts.cover")])
    assert rule(r, "standard.forbidden_wording")[0].status == S.MISMATCH


def test_annual_report_annex_is_not_scanned(tmp_path):
    r = check(tmp_path, Spec(annex_text="附件內容提到受託投資，不屬於說明書內文。"))
    assert r.status == S.PASS, problems(r)


def test_observation_wording_must_be_known(tmp_path):
    r = check(
        tmp_path,
        S0,
        edits=[Edit("止之每一個預定交易日（應該視為評價日", "止之每一個評價日（應該視為評價日", "ts.art8")],
    )
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "doc.ko_observation")[0].status == S.REVIEW_REQUIRED


def test_periodic_not_applicable_rows_must_match_non_call(tmp_path):
    """P 型「不適用」列 = 第 1～Non-Call−1 期：把它們改成日期就判不一致；Non-Call 以第 8 項的 i=k-n 為準。"""
    spec = Spec(ko_obs="P", count=2, first_callable=3)
    r = check(tmp_path, spec, edits=[Edit("不適用", spec.date(spec.ends[0]), "ts.art12.table")])
    assert rule(r, "schedule.autocall_dates")[0].status == S.MISMATCH
    assert rule(r, "field.first_callable_period")[0].status == S.PASS


def test_ki_fields_follow_the_document_ki_type(tmp_path):
    """說明書無 KI、表上 KI(Freq) 為 AM：KI 型態與 KI % 都判不一致。"""
    r = check(tmp_path, Spec(ki="AM"), pdf_spec=Spec(ki="none"))
    assert rule(r, "field.ki_type")[0].status == S.MISMATCH
    assert rule(r, "field.ki_pct")[0].status == S.MISMATCH


def test_worse_scenario_strike_must_be_the_assumed_underlying(tmp_path):
    """假設句「(k=1)」指出表現最差標的；數額算式的執行價要是那一檔的執行價。"""
    r = check(tmp_path, S0, edits=[Edit("(k=1)收盤價", "(k=2)收盤價", "ts.scen.三")])
    x = rule(r, "doc.scenario_calculations", "情境三_strike")[0]
    assert x.status == S.MISMATCH and "第 2 檔" in x.message


def test_sg_and_other_issuers_are_never_confused(tmp_path):
    """SG 說明書只符合 SG 範本；BARC、HSBC、MS、BNP 說明書也不符合 SG 範本（檔名上手編號故意給錯）。"""
    sg = build_pdf(tmp_path / "029199990004_TS.pdf", Spec(product_code="029199990004"), iis=False)
    barc = synth.build_pdf(tmp_path / "037199990002_TS.pdf", synth.Spec(product_code="037199990002"), iis=False)
    hsbc = hsbc_synth.build_pdf(
        tmp_path / "037199990003_TS.pdf", hsbc_synth.Spec(product_code="037199990003"), iis=False
    )
    ms = ms_synth.build_pdf(tmp_path / "037199990005_TS.pdf", ms_synth.Spec(product_code="037199990005"), iis=False)
    bnp = bnp_synth.build_pdf(tmp_path / "037199990006_TS.pdf", bnp_synth.Spec(product_code="037199990006"), iis=False)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    outcome = check_all(sheet, [sg, barc, hsbc, ms, bnp])
    detected = {
        i.term_sheet.name: next(r for r in i.report.results if r.rule_id == "template.detect")
        for i in outcome.items
        if i.term_sheet.name.endswith("_TS.pdf")
    }
    assert {name: (r.status, r.actual) for name, r in detected.items()} == {
        sg.name: (S.PASS, "sg-zh-pd"),
        barc.name: (S.PASS, "barc-zh-pd"),
        hsbc.name: (S.PASS, "hsbc-zh-pd"),
        ms.name: (S.PASS, "ms-zh-pd"),
        bnp.name: (S.PASS, "bnp-zh-pd"),
    }
    assert all(i.report.status == S.REVIEW_REQUIRED for i in outcome.items)  # 內容上手與檔名不符


def test_sg_term_sheet_is_filled_when_its_investor_sheet_also_passes(tmp_path):
    """SG 投資人須知已有範本（Issue #213）：兩份都通過才打 V、回填（ADR 0007）。"""
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    outcome = check_all(sheet, [pdf])
    ts, iis = sorted(outcome.items, key=lambda i: i.term_sheet.name.endswith("_IIS.pdf"))
    assert ts.report.status == S.PASS and not iis.unsupported and iis.report.status == S.PASS
    assert ts.fills_sheet


# ---------------------------------------------------------------- 金額旁的幣別（Issue #170）


def test_product_currency_need_not_match_the_underlying_currency(tmp_path):
    """境外人民幣計價、連結日圓股票（quanto）：標的價格、數額與現金交割算式的日圓不算商品幣別的出處，全部通過。"""
    r = check(
        tmp_path,
        Spec(
            autocall=False,
            ki="none",
            tenor=3,
            currency_zh="境外人民幣",
            strike=Decimal("86.21"),
            units=1,
            fx=Decimal("0.0400"),
        ),
    )
    assert r.status == S.PASS, problems(r)
    currency = rule(r, "field.currency")
    assert {x.field for x in currency} >= {
        "currency",
        "denomination_currency",
        "currency_art8",
        "minimum_subscription_currency",
        "minimum_additional_currency",
        "minimum_redemption_currency",
        "scenario_total_currency",
        "情境一_currency",
        "情境二_currency",
    }
    assert all(x.expected == "CNH" and x.document_evidence for x in currency)


@pytest.mark.parametrize(
    ("section", "old", "new", "field"),
    [
        ("art5", "每單位面額：10,000美元", "每單位面額：10,000日圓", "denomination_currency"),
        ("ch四", "最低申購金額：100,000美元", "最低申購金額：100,000日圓", "minimum_subscription_currency"),
        (
            "art15",
            "總投資金額 = 10單位面額 = 100,000美元",
            "總投資金額 = 10單位面額 = 100,000日圓",
            "scenario_total_currency",
        ),
        ("scen.一", "累積總配息金額 = 2,200.00美元", "累積總配息金額 = 2,200.00日圓", "情境一_currency"),
    ],
    ids=lambda x: x if x.endswith("_currency") else "",
)
def test_wrong_currency_next_to_an_amount_is_reported_at_that_place(tmp_path, section, old, new, field):
    r = check(tmp_path, edits=[Edit(old, new, f"ts.{section}")])
    assert ("field.currency", field, S.MISMATCH) in problems(r), problems(r)
    [x] = rule(r, "field.currency", field)
    assert x.expected == "USD" and x.document_evidence


def test_cumulative_coupon_rounded_after_multiplying_passes(tmp_path):
    """累積配息寫「總投資金額 × 配息率 × 期數再四捨五入」（Issue #244）：通過。面額與年利率選成兩種算法會差幾分錢。"""
    spec = Spec(denomination=10050, annual=Decimal("13.21"), cumulative="exact")
    rounded = spec.coupon * spec.tenor
    exact = (spec.total * spec.monthly / 100 * spec.tenor).quantize(Decimal("0.01"))
    assert rounded != exact, "合成條件要讓兩種算法不同"
    r = check(tmp_path, spec)
    calcs = [x for x in rule(r, "doc.scenario_calculations") if x.field.endswith("_cumulative")]
    assert calcs and {x.status for x in calcs} == {S.PASS}, problems(r)


@pytest.mark.parametrize(
    ("trade", "status"), [(dt.datetime(2026, 10, 15), S.PASS), (dt.datetime(2026, 10, 16), S.MISMATCH)]
)
def test_forbidden_wording_on_or_before_the_exemption_date_passes(tmp_path, trade, status):
    """參考條件表交易日在 2026-10-15（含）以前：「受託投資」特例通過並註明（已通知上手改版，Issue #247）；之後照規則判錯。"""
    r = check(tmp_path, edits=[Edit("為於中華民國境內受託買賣", "為於中華民國境內受託投資", "ts.cover")], 交易日=trade)
    [res] = rule(r, "standard.forbidden_wording")
    assert res.status == status and res.actual == 1
    assert ("特例通過" in res.message) == (status == S.PASS)
