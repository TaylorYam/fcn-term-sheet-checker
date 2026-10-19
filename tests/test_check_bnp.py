"""BNP 黑箱回歸（Issue #191）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import hsbc_synth
import ms_synth
import synth
from bnp_synth import Spec, build_pdf, check
from fcn_checker.issuers import BNP
from fcn_checker.messages import problem_message
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit, zh_date
from reference_synth import build_reference_sheet, reference_row


@pytest.mark.parametrize(
    "spec",
    [
        Spec(),  # D、記憶式、無 KI、3 檔、6 個月、Non-Call 2
        Spec(ko_obs="D", memory=True, ki="D", count=3, annual=Decimal("20.15")),
        Spec(ko_obs="D", memory=False, ki="P", count=1, tenor=12, first_callable=2),
        Spec(ko_obs="D", memory=True, ki="AM", count=4, tenor=7, first_callable=1),
        Spec(ko_obs="P", memory=True, ki="AM", count=3, first_callable=1),
        Spec(
            ko_obs="P",
            memory=False,
            ki="none",
            count=3,
            tenor=6,
            first_callable=6,
            currency_zh="日圓",
            annual=Decimal("8"),
        ),
        Spec(ko_obs="P", memory=True, ki="D", count=2, first_callable=3),
        Spec(currency_zh="英鎊", fx=Decimal("1.36335"), ki="AM", tenor=7, first_callable=1, annual=Decimal("21.5")),
        Spec(count=5, breaks={"date": 3, "price": 1}),
    ],
    ids=lambda s: (
        f"{s.ko_obs}-{'mem' if s.memory else 'plain'}-KI{s.ki}-{s.count}UL-{s.tenor}m-NC{s.first_callable}-{s.ccy}"
    ),
)
def test_supported_types_pass(tmp_path, spec):
    r = check(tmp_path, spec)
    assert r.template == BNP.template_id
    assert r.status == S.PASS, problems(r)
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in BNP.not_covered}
    # 年利率由共用規則核對（說明書第 15 項有年利率）；KO % 與各標的 KO 價一律核對（Non-Call = 天期也有）
    assert rule(r, "field.coupon_pa_pct")[0].status == S.PASS
    assert rule(r, "field.ko_pct")[0].status == S.PASS
    assert {x.status for x in rule(r, "derive.monthly_coupon")} == {S.PASS}
    assert len(rule(r, "standard.guarantor_name")) == 3
    assert rule(r, "standard.product_type")[0].status == S.PASS
    assert rule(r, "standard.minimum_issue_size")[0].status == S.PASS
    assert {x.field for x in rule(r, "doc.subscription_start_date")} == {"subscription_start", "subscription_end"}


def test_monthly_rate_uses_the_digits_shown_in_the_document(tmp_path):
    """USD 4 位、JPY 6 位：月配息率 = 年利率 ÷ 12 四捨五入到說明書顯示的位數，配息金額 JPY 取整數。"""
    for name in ("usd", "jpy"):
        (tmp_path / name).mkdir()
    usd = check(tmp_path / "usd", Spec(annual=Decimal("15.8")))  # 15.8 ÷ 12 = 1.31666… → 1.3167
    assert usd.status == S.PASS, problems(usd)
    assert rule(usd, "derive.monthly_coupon")[0].expected == Decimal("1.3167")
    annualized = [x for x in rule(usd, "scenario.params") if x.field.startswith("scenario_annualized_return")]
    assert annualized and all(x.actual == Decimal("15.800") for x in annualized)
    jpy = check(tmp_path / "jpy", Spec(currency_zh="日圓", annual=Decimal("8")))  # 8 ÷ 12 → 0.666667
    assert jpy.status == S.PASS, problems(jpy)
    assert rule(jpy, "derive.monthly_coupon")[0].expected == Decimal("0.666667")
    assert rule(jpy, "doc.scenario_calculations", "情境一_coupon")[0].expected == Decimal("6667")


def test_min_amounts_use_bnp_multiples(tmp_path):
    """最低申購／最低贖回 = 5 × 單位面額，最低加購／增加贖回單位 = 單位面額（審查標準 min_amount_multiples.bnp）。"""
    r = check(tmp_path)
    amounts = {x.field: x for x in rule(r, "field.min_amounts")}
    assert amounts["minimum_subscription"].expected == 50000 and amounts["minimum_redemption"].expected == 50000
    assert amounts["minimum_additional"].expected == 10000 and amounts["redemption_increment"].expected == 10000
    assert all(x.status == S.PASS for x in amounts.values())


@pytest.mark.parametrize(
    "column,value,rule_id",
    [
        ("Coupon p.a. (%)", 12.12, "field.coupon_pa_pct"),
        ("Coupon p.a. (%)", 12.12, "derive.monthly_coupon"),
        ("Coupon p.a. (%)", 12.12, "doc.scenario_calculations"),
        ("KO(%)", 101, "field.ko_pct"),
        ("K(%)", 71, "field.strike_pct"),
        ("KI(%)", 61, "field.ki_pct"),
        ("KI(Freq)", "AM", "field.ki_type"),
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
        ("UL_1", "ZB9 UW", "field.underlyings"),
        ("UL_1_執行價", 71, "field.underlying_prices"),
        ("UL_2_KO價", 201, "field.underlying_prices"),
        ("ISIN Code", "XS1999900002", "backfill.isin"),
        ("發行日", dt.datetime(2030, 1, 15), "backfill.issue_date"),
        ("比價日_2", dt.datetime(2030, 3, 15), "backfill.compare_dates"),
    ],
)
def test_sheet_difference_is_reported(tmp_path, column, value, rule_id):
    assert flagged(check(tmp_path, **{column: value}), rule_id)


def test_issuer_column_must_be_bnp(tmp_path):
    r = check(tmp_path, **{"發行機構": "MS"})
    assert r.status == S.REVIEW_REQUIRED and not [x for x in r.results if x.rule_id.startswith("doc.")]


def test_split_adjusted_prices_on_the_sheet_require_review_not_mismatch(tmp_path):
    """同一檔標的的進場價、執行價、KO 價都是說明書的 1/5（分割調整後的價格）→ 該檔轉人工覆核並寫出比例；
    只有一欄不同時仍判不一致。"""
    spec = Spec()
    initial = spec.initials[0]
    fifth = {
        "UL_1_進場價": float(initial / 5),
        "UL_1_執行價": float((initial * spec.strike / 100) / 5),
        "UL_1_KO價": float((initial * spec.ko / 100) / 5),
    }
    for name in ("all", "one"):
        (tmp_path / name).mkdir()
    r = check(tmp_path / "all", spec, **fifth)
    ul1 = [
        x for x in rule(r, "field.underlying_prices") if x.item.name.startswith("UL_1 ") and "下限價" not in x.item.name
    ]
    assert len(ul1) == 3 and all(x.status == S.REVIEW_REQUIRED and x.reason_code == "split_suspected" for x in ul1)
    assert all("0.2000" in x.message and "分割" in problem_message(x) for x in ul1)
    ul2 = [
        x for x in rule(r, "field.underlying_prices") if x.item.name.startswith("UL_2 ") and "下限價" not in x.item.name
    ]
    assert ul2 and all(x.status == S.PASS for x in ul2)
    r = check(tmp_path / "one", spec, **{"UL_1_進場價": fifth["UL_1_進場價"]})
    assert rule(r, "field.underlying_prices", "ZB1 UW 進場價")[0].status == S.MISMATCH


S0 = Spec()


@pytest.mark.parametrize(
    "change,rule_id,status",
    [
        # 月配息率與年化報酬率：正式條款正確、重印處錯誤也要抓到（核對規則 §3.8）
        (("art15", "月配息率：0.8333%", "月配息率：0.8334%"), "derive.monthly_coupon", S.MISMATCH),
        (("art15", "每單位面額 × 0.8333%", "每單位面額 × 0.8334%"), "derive.monthly_coupon", S.MISMATCH),
        (("art18", "每月配息率：0.8333%", "每月配息率：0.8334%"), "scenario.params", S.MISMATCH),
        (("scen.二", "× 0.8333%", "× 0.8334%"), "scenario.params", S.MISMATCH),
        (("scen.一", "年化報酬率：10.000%", "年化報酬率：10.100%"), "scenario.params", S.MISMATCH),
        (("scen.二", "(年化報酬率：10.000%)", ""), "scenario.params", S.REVIEW_REQUIRED),
        (("art17", "最低配息率為10%", "最低配息率為11%"), "doc.min_coupon_rate", S.MISMATCH),
        # 封面與第一章交叉驗證
        (
            ("cover", "商品種類：股權連結結構型商品", "商品種類：股權連結結構型債券"),
            "standard.product_type",
            S.MISMATCH,
        ),
        (("art1", "固定配息", "固定收益"), "doc.name_consistency", S.MISMATCH),
        (("art5", "計價幣別：美元", "計價幣別：日圓"), "doc.currency_consistency", S.MISMATCH),
        (("cover", "法國巴黎銀行6個月期", "法國巴黎銀行7個月期"), "doc.coupon_periods", S.MISMATCH),
        (("art14", "商品年期：6 個月期", "商品年期：7 個月期"), "doc.coupon_periods", S.MISMATCH),
        (("art16", "商品面額 × 100%", "商品面額 × 99%"), "doc.redemption_pct", S.MISMATCH),
        (("art17", "商品面額 × 100%；或", "商品面額 × 99%；或"), "doc.redemption_pct", S.MISMATCH),
        (("ch五", "2026 年5 月21 日公告之", "2026 年5 月22 日公告之"), "doc.base_prospectus_date", S.MISMATCH),
        (
            ("ch四", "受託機構聯絡方式：+886 (2) 5556 1313", "受託機構聯絡方式：+886 (2) 5556 1314"),
            "standard.distributor",
            S.MISMATCH,
        ),
        # 日期表與提前出場
        (("art14.table", zh_date(S0.starts[1]), "2030 年2 月21 日"), "schedule.period_start", S.MISMATCH),
        (("art14.table", zh_date(S0.starts[0]), "2030 年1 月15 日"), "schedule.period_start", S.MISMATCH),
        (("art14.table", zh_date(S0.payments[-1]), "2030 年8 月20 日"), "schedule.last_payment", S.MISMATCH),
        (("art14.table", zh_date(S0.payments[1]), "2030 年3 月13 日"), "schedule.observation_order", S.MISMATCH),
        (("art14", "自第2 個配息期結束日", "自第9 個配息期結束日"), "schedule.autocall_dates", S.MISMATCH),
        # 標的表
        (("art11.table", "之70.00%", "之71.00%"), "derive.prices", S.MISMATCH),
        (("art11.table", "70.0000 即期初價格之70.00%", "70.0001 即期初價格之70.00%"), "derive.prices", S.MISMATCH),
        (
            ("art17", "若於期末評價日，表現最差連結標的之收盤價格大於或等於其執行價格", "若於每個觀察日"),
            "field.ki_type",
            S.REVIEW_REQUIRED,
        ),
        # 第 18 項情境（第 11 項正確、第 18 項重印錯）
        (
            ("art18.table", "70.0000，即期初價格之70.00%", "70.0001，即期初價格之70.00%"),
            "scenario.reprint",
            S.MISMATCH,
        ),
        (("art18.table", "之70.00%", "之71.00%"), "scenario.reprint", S.MISMATCH),
        (("scen.三", "小於其執行價格70.0000 美元", "小於其執行價格70.0001 美元"), "scenario.strike", S.MISMATCH),
        (
            ("art18", "商品面額 = 每單位商品為10,000 美元", "商品面額 = 每單位商品為20,000 美元"),
            "scenario.params",
            S.MISMATCH,
        ),
        (("art18", "× 100.00% = 10,000 美元", "× 100.00% = 20,000 美元"), "scenario.params", S.MISMATCH),
        (("art18", "× 100.00% = 10,000 美元", "× 99.00% = 10,000 美元"), "standard.issue_price", S.REVIEW_REQUIRED),
        (("scen.二", "= 83.33 美元(四捨五入", "= 83.34 美元(四捨五入"), "doc.scenario_calculations", S.MISMATCH),
        (
            ("scen.二", "累積總月配息金額 = 499.98 美元", "累積總月配息金額 = 500.00 美元"),
            "doc.scenario_calculations",
            S.MISMATCH,
        ),
        (("scen.一", "第1~2 個月配息金額", "第1~3 個月配息金額"), "doc.scenario_calculations", S.MISMATCH),
        (
            (
                "scen.二",
                "= [(10,000 美元 + 499.98 美元) ÷ 10,000 美元] - 1",
                "= [(10,000 美元 + 499.98 美元) ÷ 10,000 美元] - 2",
            ),
            "doc.scenario_calculations",
            S.REVIEW_REQUIRED,
        ),
        (("scen.二", "= 4.9998%", "= 4.9999%"), "doc.scenario_calculations", S.MISMATCH),
        (
            ("scen.三", "無條件捨去至整數位 = 142 股", "無條件捨去至整數位 = 141 股"),
            "doc.scenario_calculations",
            S.MISMATCH,
        ),
        (("scen.三", "× 0.8571", "× 0.8572"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.四", "現金交割0.86 美元", "現金交割0.87 美元"), "doc.scenario_calculations", S.MISMATCH),
        (("scen.三", "較差情況", "保守情況"), "doc.scenario_parameters", S.REVIEW_REQUIRED),
        # 第四章
        (("ch四", "最低贖回金額為50,000 美元", "最低贖回金額為10,000 美元"), "field.min_amounts", S.MISMATCH),
        (("ch四", "最低加購金額：10,000 美元", "最低加購金額：50,000 美元"), "field.min_amounts", S.MISMATCH),
        (("ch四", "至少達(等值)300,000 美元", "至少達(等值)200,000 美元"), "standard.minimum_issue_size", S.MISMATCH),
        (("ch四", "× 100% + 申購費用", "× 99% + 申購費用"), "standard.issue_price", S.REVIEW_REQUIRED),
        (("ch四", "0%~5%", "0%~6%"), "standard.fees", S.MISMATCH),
        (
            (
                "ch四",
                "商品開始受理申購日期：2030 年1 月7 日至2030 年1 月7 日",
                "商品開始受理申購日期：2030 年1 月7 日至2030 年1 月8 日",
            ),
            "doc.subscription_start_date",
            S.MISMATCH,
        ),
        # 審查標準（BNP 版本）
        (("ch三", "歸類為[RR4]", "歸類為【RR4】"), "standard.fixed_warning", S.MISMATCH),
        (("ch三", "歸類為[RR4]", "歸類為[RR3]"), "standard.risk_level", S.MISMATCH),
        (
            ("art3", "發行機構：BNP Paribas Issuance B.V.", "發行機構：BNP Paribas Issuance N.V."),
            "standard.issuer_name",
            S.MISMATCH,
        ),
        (
            ("ch二", "事業名稱：BNP Paribas Issuance B.V.", "事業名稱：BNP Paribas Issuance N.V."),
            "standard.issuer_name",
            S.MISMATCH,
        ),
        (
            (
                "cover",
                "保證機構之名稱及地址：法商法國巴黎銀行股份有限公司 (BNP Paribas)",
                "保證機構之名稱及地址：法商法國巴黎銀行 (BNP Paribas)",
            ),
            "standard.guarantor_name",
            S.MISMATCH,
        ),
        (("art3", "保證機構：法國巴黎銀行", "保證機構：法國巴黎銀行台北分行"), "standard.guarantor_name", S.MISMATCH),
        (
            ("ch二", "事業名稱：法國巴黎銀行 (BNP Paribas)", "事業名稱：法國巴黎銀行"),
            "standard.guarantor_name",
            S.MISMATCH,
        ),
        (("cover", "電話：+886 (2) 5556 1313", "電話：+886 (2) 5556 1314"), "standard.distributor", S.MISMATCH),
        (
            ("ch二", "◎營業所在地：臺北市105 民生東路三段158 號6 樓", "◎營業所在地：臺北市105 民生東路三段159 號6 樓"),
            "standard.distributor",
            S.MISMATCH,
        ),
        (("cover", "連結一籃子股票", "連結單一股票"), "standard.product_name", S.MISMATCH),
        (("cover", "Early Redeemable Note", "Note"), "standard.product_name", S.MISMATCH),
        (
            ("cover", "審查通過之日期：2026 年06 月11 日", "審查通過之日期：2025 年12 月18 日"),
            "standard.approval_date",
            S.MISMATCH,
        ),
    ],
)
def test_document_difference_is_reported(tmp_path, change, rule_id, status):
    section, old, new = change
    r = check(tmp_path, S0, edits=[Edit(old, new, f"ts.{section}")])
    assert any(x.rule_id == rule_id and x.status == status for x in r.results), problems(r)


@pytest.mark.parametrize(
    "change",
    [
        ("art1", "結構型商品(不保本)(無擔保)", "結構型商品（不保本）（無擔保）"),  # 括號全半形不計
        ("cover", "電話：+886 (2) 5556 1313", "電話：02-5556-1313"),  # 審查標準本身的電話寫法
        ("ch二", "◎營業所在地：臺北市105 民生東路三段158 號6 樓", "◎營業所在地：台北市松山區民生東路三段158號6樓"),
    ],
)
def test_allowed_variants_still_pass(tmp_path, change):
    section, old, new = change
    r = check(tmp_path, S0, edits=[Edit(old, new, f"ts.{section}")])
    assert r.status == S.PASS, problems(r)


def test_fixed_warning_with_square_brackets_counts_three_times(tmp_path):
    r = check(tmp_path)
    warning, level = rule(r, "standard.fixed_warning")[0], rule(r, "standard.risk_level")[0]
    assert (warning.status, warning.actual) == (S.PASS, 3)
    assert level.status == S.PASS and level.actual == ["RR4"]
    names = {x.field: x for x in rule(r, "standard.issuer_name")}
    assert set(names) == {"issuer_name_cover", "issuer_name_ch2", "issuer_name_ch1"}
    assert all(x.status == S.PASS for x in names.values())
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS  # 「受託投資」只在兩個允許片語內


def test_blank_trustee_product_code_is_a_mismatch(tmp_path):
    r = check(tmp_path, Spec(trustee_code=""))
    x = rule(r, "doc.trustee_product_code")[0]
    assert x.status == S.MISMATCH and x.actual == "（空白）"
    assert "受託機構商品代號" in problem_message(x) and x.document_evidence


def test_non_call_equal_to_tenor_still_checks_ko_price(tmp_path):
    """Non-Call = 天期：名稱沒有「自動提前贖回」、P 型日期表只有末列有日期、情境一寫「第1(j=6)個」，KO % 與 KO 價照常核對。"""
    spec = Spec(ko_obs="P", memory=False, ki="none", count=3, tenor=6, first_callable=6)
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    assert rule(r, "field.ko_pct")[0].status == S.PASS
    assert all(x.status == S.PASS for x in rule(r, "field.underlying_prices") if x.item.name.endswith("KO價"))
    name = rule(r, "standard.product_name", "name_zh")[0]
    assert name.status == S.PASS and "自動提前贖回" not in name.expected
    other = tmp_path / "other"
    other.mkdir()
    r = check(other, spec, edits=[Edit("固定配息連結", "固定配息自動提前贖回連結", "ts.cover")])
    assert rule(r, "standard.product_name", "name_zh")[0].status == S.MISMATCH


def test_observation_wording_must_match_the_date_table(tmp_path):
    r = check(
        tmp_path,
        S0,
        edits=[
            Edit(
                "自動提前贖回評價日：自第2 個配息期結束日(含該日)起至期末評價日(含該日)的每一個預定交易日",
                "自動提前贖回評價日：如下表所列之日期",
                "ts.art14",
            )
        ],
    )
    assert rule(r, "doc.ko_observation")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.ko_observation")[0].status == S.REVIEW_REQUIRED


def test_memory_wording_must_match_between_article_16_and_20(tmp_path):
    """第 16 項記憶式、第 20 項(1) 非記憶式：轉人工覆核；參考條件表 KO(memo) 以第 16 項為準。"""
    plain = Edit(
        "每一連結標的皆曾經於該自動提前贖回評價日或之前任一自動提前贖回評價日之收盤價格大於或等於其各自自動提前贖回價格",
        "每一連結標的之收盤價格皆大於或等於其各自自動提前贖回價格",
        "ts.art20",
    )
    r = check(tmp_path, S0, edits=[plain])
    assert rule(r, "doc.ko_memory")[0].status == S.REVIEW_REQUIRED
    assert rule(r, "field.ko_memory")[0].status == S.PASS


def test_periodic_not_applicable_rows_decide_non_call(tmp_path):
    """P 型「不適用」列決定第一個可提前出場期：日期表每期都有日期時 Non-Call = 1，與表上的 3 不符。"""
    spec = Spec(ko_obs="P", count=2, first_callable=3)
    r = check(tmp_path, spec, edits=[Edit("不適用", zh_date(spec.ends[0]), "ts.art14.table")])
    assert rule(r, "field.first_callable_period")[0].status == S.MISMATCH
    assert rule(r, "backfill.compare_dates")[0].status == S.PASS  # 表上空白，依說明書的 Non-Call 填


def test_ki_fields_follow_the_document_ki_type(tmp_path):
    """說明書無 KI、表上 KI(Freq) 為 AM：KI 型態與 KI % 都判不一致。"""
    r = check(tmp_path, Spec(ki="AM"), pdf_spec=Spec(ki="none"))
    assert rule(r, "field.ki_type")[0].status == S.MISMATCH
    assert rule(r, "field.ki_pct")[0].status == S.MISMATCH


def test_page_total_must_equal_page_count(tmp_path):
    r = check(tmp_path, Spec(page_total=99))
    x = rule(r, "doc.page_count")[0]
    assert x.status == S.MISMATCH and x.actual == 99


def test_page_total_counts_attachment_pages_without_text(tmp_path):
    """附件頁沒有文字層（沒有頁首）：頁首「共M頁」仍須等於 PDF 頁數。"""
    (tmp_path / "ok").mkdir()
    (tmp_path / "short").mkdir()
    ok = check(tmp_path / "ok", Spec(blank_pages=2))
    assert rule(ok, "doc.page_count")[0].status == S.PASS, problems(ok)
    r = check(tmp_path / "short", Spec(blank_pages=2, page_total=rule(ok, "doc.page_count")[0].expected - 2))
    x = rule(r, "doc.page_count")[0]
    assert x.status == S.MISMATCH and x.expected - x.actual == 2


def test_worse_scenario_strike_must_be_the_assumed_worst_underlying(tmp_path):
    """假設句「(假設為X)」指出表現最差標的；執行價格要是那一檔的執行價，對不到名稱時轉人工覆核。"""
    (tmp_path / "other").mkdir()
    (tmp_path / "unknown").mkdir()
    first, second = Spec().underlyings[:2]
    worst, other = first.name, second.name
    r = check(tmp_path / "other", Spec(), edits=[Edit(f"假設為{worst}", f"假設為{other}", "ts.scen.三")])
    x = rule(r, "scenario.strike", "scenario_price_1")[0]  # 情境三假設句
    assert x.status == S.MISMATCH and "第 2 檔" in x.item.name
    r = check(
        tmp_path / "unknown",
        Spec(),
        edits=[Edit(f"假設為{worst}", "假設為不存在的公司", "ts.scen.三")],
    )
    assert rule(r, "scenario.strike", "scenario_price_1")[0].status == S.REVIEW_REQUIRED


@pytest.mark.parametrize(
    ("spec", "old", "new"),
    [
        (Spec(), "假設於第1 個自動提前贖回評價日", "假設於第2 個自動提前贖回評價日"),
        (Spec(first_callable=6), "假設於第1(j=6)個自動提前贖回評價日", "假設於第1 個自動提前贖回評價日"),
        (Spec(), "假設於第1 個自動提前贖回評價日", "假設於第1(j=6)個自動提前贖回評價日"),
    ],
)
def test_scenario_one_assumes_the_first_autocall_date(tmp_path, spec, old, new):
    """情境一假設第 1 個自動提前贖回評價日；Non-Call = 天期時加註 (j=天期)，否則不加。"""
    r = check(tmp_path, spec, edits=[Edit(old, new, "ts.scen.一")])
    assert rule(r, "doc.scenario_calculations", "情境一_assumed")[0].status == S.MISMATCH


def test_tables_split_across_pages_are_still_read(tmp_path):
    r = check(tmp_path, Spec(count=4, breaks={"date": 2, "price": 2}))
    assert r.status == S.PASS, problems(r)


def test_bnp_and_other_issuers_are_never_confused(tmp_path):
    """BNP 說明書只符合 BNP 範本；BARC、HSBC、MS 說明書也不符合 BNP 範本（檔名上手編號故意給錯）。"""
    bnp = build_pdf(tmp_path / "029199990004_TS.pdf", Spec(product_code="029199990004"), iis=False)
    barc = synth.build_pdf(tmp_path / "082199990002_TS.pdf", synth.Spec(product_code="082199990002"), iis=False)
    hsbc = hsbc_synth.build_pdf(
        tmp_path / "082199990003_TS.pdf", hsbc_synth.Spec(product_code="082199990003"), iis=False
    )
    ms = ms_synth.build_pdf(tmp_path / "082199990005_TS.pdf", ms_synth.Spec(product_code="082199990005"), iis=False)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(Spec())])
    outcome = check_all(sheet, [bnp, barc, hsbc, ms])
    detected = {
        i.term_sheet.name: next(r for r in i.report.results if r.rule_id == "template.detect")
        for i in outcome.items
        if i.term_sheet.name.endswith("_TS.pdf")
    }
    assert {name: (r.status, r.actual) for name, r in detected.items()} == {
        bnp.name: (S.PASS, "bnp-zh-pd"),
        barc.name: (S.PASS, "barc-zh-pd"),
        hsbc.name: (S.PASS, "hsbc-zh-pd"),
        ms.name: (S.PASS, "ms-zh-pd"),
    }
    assert all(i.report.status == S.REVIEW_REQUIRED for i in outcome.items)  # 內容上手與檔名不符


def test_bnp_term_sheet_is_filled_once_its_investor_sheet_also_passes(tmp_path):
    """BNP 投資人須知有範本（Issue #197）：兩份都通過才回填；投資人須知的核對見 test_iis_bnp.py。"""
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    outcome = check_all(sheet, [pdf])
    ts, iis = sorted(outcome.items, key=lambda i: i.term_sheet.name.endswith("_IIS.pdf"))
    assert ts.report.status == S.PASS and iis.report.status == S.PASS and not iis.unsupported
    assert ts.fills_sheet


# ---------------------------------------------------------------- 金額旁的幣別（Issue #170）


def test_product_currency_need_not_match_the_underlying_currency(tmp_path):
    """英鎊計價、連結美元股票：股數算式含匯率、剩餘金額算式的美元收盤價不算商品幣別的出處，全部通過。"""
    r = check(tmp_path, Spec(currency_zh="英鎊", fx=Decimal("1.36335"), ki="AM", tenor=7, first_callable=1))
    assert r.status == S.PASS, problems(r)
    currency = rule(r, "field.currency")
    assert {x.field for x in currency} >= {
        "currency",
        "denomination_currency",
        "coupon_currency_art15",
        "currency_art16",
        "currency_art17",
        "minimum_subscription_currency",
        "minimum_additional_currency",
        "minimum_redemption_currency",
        "redemption_increment_currency",
        "scenario_denomination_currency",
        "scenario_principal_currency",
        "情境一_currency",
        "情境三_currency",
    }
    assert all(x.expected == "GBP" and x.document_evidence for x in currency)


@pytest.mark.parametrize(
    ("section", "old", "new", "field"),
    [
        ("art6", "商品面額：每單位商品10,000 美元", "商品面額：每單位商品10,000 日圓", "denomination_currency"),
        ("art15", "計算以美元計值的配息款項", "計算以日圓計值的配息款項", "coupon_currency_art15"),
        ("ch四", "最低申購金額：50,000 美元", "最低申購金額：50,000 日圓", "minimum_subscription_currency"),
        (
            "art18",
            "商品面額 = 每單位商品為10,000 美元",
            "商品面額 = 每單位商品為10,000 日圓",
            "scenario_denomination_currency",
        ),
        ("scen.一", "累積總月配息金額 = 166.66 美元", "累積總月配息金額 = 166.66 日圓", "情境一_currency"),
    ],
    ids=lambda x: x if x.endswith("_currency") else "",
)
def test_wrong_currency_next_to_an_amount_is_reported_at_that_place(tmp_path, section, old, new, field):
    r = check(tmp_path, edits=[Edit(old, new, f"ts.{section}")])
    assert (("field.currency", field, S.MISMATCH)) in problems(r), problems(r)
    [x] = rule(r, "field.currency", field)
    assert x.expected == "USD" and x.document_evidence


def test_share_counts_with_thousands_separators_are_read(tmp_path):
    """大面額商品的股數破千，算式寫「=6,250.0000 … = 6,250 股」「(6,250 股 × …」（Issue #240）：照常驗算。"""
    r = check(tmp_path, Spec(denomination=500000, ki="AM"))
    calcs = rule(r, "doc.scenario_calculations")
    assert calcs and {x.status for x in calcs} == {S.PASS}, problems(r)
    assert {x.field for x in calcs} >= {"情境三_shares", "情境四_shares", "情境三_return", "情境四_return"}


def _cumulatives(spec: Spec, periods: int) -> tuple[Decimal, Decimal]:
    """（四捨五入後每期配息 × 期數, 面額 × 月配息率 × 期數再四捨五入）。"""
    step = Decimal(1).scaleb(-spec.decimals)
    return spec.coupon * periods, (spec.denom * spec.monthly / 100 * periods).quantize(step)


def test_cumulative_coupon_rounded_after_multiplying_passes(tmp_path):
    """累積配息寫「面額 × 月配息率 × 期數再四捨五入」（Issue #244；日圓每期 8,333、2 期寫 16,667 而非 16,666）：通過。"""
    spec = Spec(currency_zh="日圓", cumulative="exact")
    rounded, exact = _cumulatives(spec, spec.first_callable)
    assert rounded != exact, "合成條件要讓兩種算法不同"
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)


def test_cumulative_coupon_matching_neither_method_lists_both(tmp_path):
    spec = Spec(currency_zh="日圓")
    rounded, exact = _cumulatives(spec, spec.first_callable)
    edit = Edit(
        f"累積總月配息金額 = {spec.money(rounded)} 日圓",
        f"累積總月配息金額 = {spec.money(rounded + 2)} 日圓",
        "ts.scen.一",
    )
    r = check(tmp_path, spec, edits=[edit])
    [res] = rule(r, "doc.scenario_calculations", "情境一_cumulative")
    assert res.status == S.MISMATCH
    assert f"= {rounded}，或 " in res.message and res.message.endswith(f"= {exact}")


RMB = Spec(currency_zh="境外人民幣", ki="AM")


def test_offshore_rmb_version_is_read_by_item_names_and_passes(tmp_path):
    """境外人民幣版（Issue #345）：第 10 項後多一項「人民幣付款中斷事件」（不核對）、之後條號 +1，各項依項目名稱讀到；
    「境外人民幣」= CNH（計價幣別後的人民幣定義句不核對）；英文名稱的 CNY 對參考條件表的 CNH。"""
    r = check(tmp_path, RMB)
    assert r.status == S.PASS, problems(r)
    assert {x.expected for x in rule(r, "field.currency")} == {"CNH"}
    assert rule(r, "doc.currency_consistency")[0].actual == "境外人民幣"
    [name_en] = rule(r, "standard.product_name", "name_en")
    assert "6 Months CNY Fixed Coupon" in name_en.actual
    # 美元版第 11、14～18、20 項在人民幣版是第 12、15～19、21 項：錯訊與項目名稱寫文件上的條號
    assert rule(r, "doc.min_coupon_rate")[0].item.name == "第 18 項最低保證配息率"
    assert rule(r, "doc.scenario_parameters")[0].item.name == "第 19 項情境標題"
    assert {x.item.name for x in rule(r, "doc.redemption_pct")} == {"第 17 項自動提前贖回金額", "第 18 項到期贖回金額"}


@pytest.mark.parametrize(
    ("section", "old", "new", "rule_id", "where"),
    [
        ("art15", "月配息率：0.8333%", "月配息率：0.8334%", "derive.monthly_coupon", "第 16 項月配息率"),
        (
            "art20",
            "每一連結標的皆曾經於該自動提前贖回評價日或之前任一自動提前贖回評價日之收盤價格大於或等於其各自自動提前贖回價格",
            "每一連結標的之收盤價格皆大於或等於其各自自動提前贖回價格",
            "doc.ko_memory",
            "第 17 項與第 21 項(1)",
        ),
        ("cover", "6 Months CNY", "6 Months CNH", "standard.product_name", ""),
    ],
    ids=["coupon", "art21", "name_en"],
)
def test_offshore_rmb_problems_name_the_document_article(tmp_path, section, old, new, rule_id, where):
    r = check(tmp_path, RMB, edits=[Edit(old, new, f"ts.{section}")])
    found = [x for x in r.results if x.rule_id == rule_id and x.status != S.PASS]
    assert found, problems(r)
    assert all(where in problem_message(x) for x in found), [problem_message(x) for x in found]
