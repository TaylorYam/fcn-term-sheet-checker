"""BBVA 黑箱回歸（Issue #269）：合成說明書經批量入口（預覽＋核對）；不直接測擷取或解析的內部函式。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import gs_synth
import natixis_synth
import synth
from bbva_synth import ISSUER_EN, WARNING, Spec, build_pdf, check, check_item, row
from fcn_checker.issuers import BBVA
from fcn_checker.schema import CheckStatus as S
from harness import check_all
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import build_reference_sheet


def _dir(tmp_path, name: str):
    out = tmp_path / name
    out.mkdir()
    return out


VARIANTS = {
    "KO 每日記憶式、KI 到期、3 檔美股、Non-Call 1": Spec(),
    "KO 每日非記憶式、單一標的、無 KI、Non-Call 2": Spec(
        count=1, memory=False, ki="none", first_callable=2, strike=Decimal("80.00")
    ),
    "KO 每期記憶式、KI 到期、KO 105": Spec(ko_obs="P", ko=Decimal("105.00")),
    "KO 每期記憶式、Non-Call 3、9 個月": Spec(ko_obs="P", first_callable=3, tenor=9),
    "日圓計價美股、無 KI、記憶式": Spec(currency_zh="日圓", ki="none", first_callable=3, tenor=4),
    "VWAP、沒有受託機構明細、2 檔標的": Spec(pricing="vwap", trustee_detail=False, count=2),
}


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass_and_are_filled(tmp_path, spec):
    item = check_item(tmp_path, spec)
    r = item.report
    assert r.template == BBVA.template_id
    assert r.status == S.PASS, problems(r)
    assert item.fills_sheet
    own = [x for x in r.results if x.rule_id.startswith(("doc.", "derive.", "schedule."))]
    assert own and all(x.document_evidence for x in own if x.status == S.PASS)
    assert {x["rule_id"] for x in r.not_covered} == {x["rule_id"] for x in BBVA.not_covered}
    assert rule(r, "standard.fixed_warning")[0].actual == 3
    # 封面沒有發行機構欄：只核第一章（補「荷蘭商」）與第二章（2026-10-15 確認）
    assert {x.field for x in rule(r, "standard.issuer_name")} == {"issuer_name_ch1", "issuer_name_ch2"}
    skipped = ("standard.guarantor_name", "standard.agent_name", "standard.minimum_issue_size")  # 決定不核對
    assert not [x for x in r.results if x.rule_id in skipped]
    assert len(rule(r, "field.min_amounts")) == 3
    assert rule(r, "field.initial_pricing")[0].status == S.PASS  # 說明書沒寫：只檢查表上是允許值
    assert [x.status for x in rule(r, "doc.underlying_list")] == [S.PASS]
    monthly = [x.status for x in r.results if x.rule_id == "derive.monthly_coupon"]  # 只有 KO 每期型有每期配息率
    assert monthly == ([S.PASS] if spec.ko_obs == "P" else [])
    # Issue #305：重複寫出的值都有核對
    details = {"distributor_name_detail", "distributor_address_detail", "chairman_detail"}
    fields = {x.field for x in r.results}
    assert (details <= fields) == spec.trustee_detail and not (details & fields) == spec.trustee_detail
    assert {"payment_currency_1", "payment_currency_2", "payment_currency_3", "autocall_currency"} <= fields
    assert {"risk_currency", "risk_conversion_currency"} | {f"scenario{n}_currency" for n in range(1, 5)} <= fields
    assert ("best_value_start" in fields) == spec.memory
    assert ("scenario_ko_mentions_3_1" in fields) == spec.memory
    assert ("scenario_coupon_mentions_2_1" in fields) == (spec.ko_obs == "D")
    assert "scenario_barrier_mentions_2_1" in fields and "art18_ko_pct" in fields
    assert rule(r, "doc.scenario_table")[0].actual  # 情境價格表各格放進實際值，看得出逐格比了哪些價格


def test_underlyings_are_paired_by_ticker(tmp_path):
    """BBVA 在 any_order_issuers：表上順序不同、代碼相同時依代碼配對（BV2；2026-10-15 確認）。"""
    spec = Spec()
    out = row(spec)
    for col in ("", "_進場價", "_執行價", "_下限價", "_KO價"):
        out[f"UL_1{col}"], out[f"UL_2{col}"] = out[f"UL_2{col}"], out[f"UL_1{col}"]
    path = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [out])
    item = next(i for i in check_all(sheet, [path]).items if i.term_sheet == path)
    assert item.report.status == S.PASS, problems(item.report)


def test_vwap_prices_are_filled_from_the_term_sheet(tmp_path):
    spec = Spec(pricing="vwap")
    r = check(tmp_path, spec)
    assert r.status == S.PASS, problems(r)
    cells = {d.column: d for d in r.backfill}
    assert cells["UL_1_進場價"].expected == spec.underlyings[0].initial


def test_compare_dates_follow_the_observation_type(tmp_path):
    """比價日：KO 每日填第 Non-Call 期終止日與贖回評價日；KO 每期填每列觀察日，最後一期為贖回評價日。"""
    daily = Spec(first_callable=2)
    cells = {d.column: d.expected for d in check(_dir(tmp_path, "d"), daily).backfill}
    assert cells["比價日_2"] == daily.ends[1] and cells["比價日_6"] == daily.final_date
    assert cells["比價日_3"] == "-"  # KO 每日型只填起訖兩期
    periodic = Spec(ko_obs="P", first_callable=2)
    cells = {d.column: d.expected for d in check(_dir(tmp_path, "p"), periodic).backfill}
    assert [cells[f"比價日_{n}"] for n in range(2, 7)] == periodic.ends[1:]


def test_sheet_memory_ki_and_non_call_are_compared(tmp_path):
    r = check(tmp_path, Spec(), **{"KO(memo)": "N", "Non-Call(月)": 3, "KI(Freq)": "-", "KI(%)": "-"})
    assert rule(r, "field.ko_memory")[0].status == S.MISMATCH
    assert rule(r, "field.first_callable_period")[0].status == S.MISMATCH
    assert rule(r, "field.ki_type")[0].status == S.MISMATCH


def test_wrong_maturity_date_on_the_sheet_is_a_mismatch(tmp_path):
    """BV3：表上到期日差一年是表上錯，判到期日不一致（2026-10-15 確認）。"""
    spec = Spec()
    wrong = dt.datetime.combine(spec.maturity_date.replace(year=spec.maturity_date.year + 1), dt.time())
    r = check(tmp_path, spec, 到期日=wrong)
    assert {(rid, f) for rid, f, _ in problems(r)} == {("field.maturity_date", "maturity_date")}


def test_forbidden_wording_in_chapter_four_follows_the_trade_date_exemption(tmp_path):
    """第四章 7.「受託投資本商品之總金額」不加允許片語：交易日在特例期之後判不一致（Issue #247；2026-10-15 確認）。"""
    edit = Edit(
        "發行機構可全權決定取消本商品之發行。", "若受託投資本商品之總金額未達最低申購總金額，得取消發行。", "ts.ch四"
    )
    r = check(tmp_path, Spec(), edits=[edit])
    assert rule(r, "standard.forbidden_wording")[0].status == S.MISMATCH


@pytest.mark.parametrize(
    ("spec", "edit", "broken"),
    [
        pytest.param(
            Spec(),
            Edit("商品名稱：BBVA GLOBAL MARKETS, B.V. 6", "商品名稱：BBVA GLOBAL MARKETS, B.V. 9", "ts.art1"),
            ("doc.name_consistency", "name_art1_zh"),
            id="第一章名稱",
        ),
        pytest.param(
            Spec(),
            Edit("計價幣別：美元", "計價幣別：日圓", "ts.art5"),
            ("doc.currency_consistency", "currency_art5"),
            id="第一章幣別",
        ),
        pytest.param(
            Spec(),
            Edit("受託機構商品代號：326199990001", "受託機構商品代號：326199990009", "ts.cover"),
            ("doc.distributor_product_code", "distributor_code"),
            id="受託機構商品代號",
        ),
        pytest.param(
            Spec(),
            Edit("的75.00%）", "的70.00%）", "ts.art14.table"),
            ("doc.table_header_pct", "header_strike"),
            id="表頭轉換比例",
        ),
        pytest.param(
            Spec(),
            Edit("100.4500 美元", "100.4600 美元", "ts.art16.table"),
            ("doc.scenario_table", "scenario_table"),
            id="情境價格表",
        ),
        pytest.param(
            Spec(),
            Edit("年利率：18.00%", "年利率：17.00%", "ts.art16"),
            ("doc.coupon_mentions", "scenario_coupon_pct"),
            id="情境年利率",
        ),
        pytest.param(
            Spec(),
            Edit("X 1.5000%", "X 1.6000%", "ts.art16"),
            ("doc.coupon_mentions", "scenario_first_coupon_pct"),
            id="情境一配息率",
        ),
        pytest.param(
            Spec(ko_obs="P"),
            Edit("配息率（Rate of Interest）：1.5000%", "配息率（Rate of Interest）：1.6000%", "ts.art13"),
            ("derive.monthly_coupon", "monthly_coupon_pct"),
            id="每期配息率",
        ),
        pytest.param(
            Spec(),
            Edit("最佳表現值大於或等於100.00%（出場值）", "最佳表現值大於或等於105.00%（出場值）", "ts.art14"),
            ("doc.memory_consistency", "ko_at_maturity_pct"),
            id="到期條件出場值",
        ),
        pytest.param(
            Spec(ki="none"),
            Edit("最差表現值大於或等於75.00%：100%", "最差表現值大於或等於70.00%：100%", "ts.art14"),
            ("doc.ki_consistency", "no_ki_barrier_pct"),
            id="無 KI 到期門檻",
        ),
        pytest.param(
            Spec(),
            Edit("ZBB UN Equity", "ZBX UN Equity", "ts.art9.table"),
            ("doc.underlying_list", "underlying_list"),
            id="第 9 項標的",
        ),
        pytest.param(
            Spec(),
            Edit("2030 年2 月11 日（含）起至贖回評價日", "2030 年2 月12 日（含）起至贖回評價日", "ts.art12"),
            ("field.first_callable_period", "first_callable_period"),
            id="觀察期間起日",
        ),
        pytest.param(
            Spec(ko_obs="P"),
            Edit(
                "2030 年2 月11 日（含）起至2030 年6 月10 日", "2030 年2 月11 日（含）起至2030 年6 月11 日", "ts.art12"
            ),
            ("schedule.autocall_dates", "ko_rows"),
            id="每期觀察期間迄日",
        ),
        # Issue #305：重複寫出的值，只有這一處錯
        pytest.param(
            Spec(),
            Edit("(i) 事業名稱:玉山綜合證券股份有限公司", "(i) 事業名稱:玉山證券股份有限公司", "ts.cover"),
            ("standard.distributor", "distributor_name_detail"),
            id="第 1 頁明細名稱",
        ),
        pytest.param(
            Spec(),
            Edit("(iii) 營業所在地:台北市", "(iii) 營業所在地:新北市", "ts.cover"),
            ("standard.distributor", "distributor_address_detail"),
            id="第 1 頁明細地址",
        ),
        pytest.param(
            Spec(),
            Edit("(iv) 負責人:", "(iv) 負責人:王", "ts.cover"),
            ("standard.chairman", "chairman_detail"),
            id="第 1 頁明細負責人",
        ),
        pytest.param(
            Spec(),
            Edit("以美元為計價單位的配息金額", "以日圓為計價單位的配息金額", "ts.art8"),
            ("field.currency", "payment_currency_1"),
            id="第 8 項幣別",
        ),
        pytest.param(
            Spec(),
            Edit("以美元為計價單位的自動提前贖回金額", "以日圓為計價單位的自動提前贖回金額", "ts.art15"),
            ("field.currency", "autocall_currency"),
            id="第 15 項幣別",
        ),
        pytest.param(
            Spec(),
            Edit("本商品屬美元計價之產品", "本商品屬日圓計價之產品", "ts.ch三"),
            ("field.currency", "risk_currency"),
            id="第三章幣別",
        ),
        pytest.param(
            Spec(),
            Edit(
                "到期贖回金額＝10 單位面額X 100%＝10 x 10,000 美元",
                "到期贖回金額＝10 單位面額X 100%＝10 x 10,000 日圓",
                "ts.art16",
            ),
            ("field.currency", "scenario2_currency"),
            id="情境金額幣別",
        ),
        pytest.param(
            Spec(),
            Edit("為自2030 年2 月11 日（含）", "為自2030 年2 月12 日（含）", "ts.art14"),
            ("doc.ko_period_mentions", "best_value_start"),
            id="第 14 項最佳表現值起算日",
        ),
        pytest.param(
            Spec(),
            Edit("於2030 年2 月11 日（含）至贖回評價日", "於2030 年2 月12 日（含）至贖回評價日", "ts.art16"),
            ("doc.ko_period_mentions", "scenario_start_mentions_3_1"),
            id="情境觀察起算日",
        ),
        pytest.param(
            Spec(),
            Edit(
                "第1～6 個配息觀察期間之配息金額＝10 單位面額X 配息率＝10 X 10,000 美元 X 1.5000%",
                "第1～6 個配息觀察期間之配息金額＝10 單位面額X 配息率＝10 X 10,000 美元 X 1.6000%",
                "ts.art16",
            ),
            ("doc.coupon_mentions", "scenario_coupon_mentions_2_1"),
            id="情境二～四配息率",
        ),
        pytest.param(
            Spec(),
            Edit("期初價格的60.00%（下限值）。", "期初價格的65.00%（下限值）。", "ts.art16"),
            ("doc.ki_consistency", "scenario_barrier_mentions_2_1"),
            id="情境下限值",
        ),
        pytest.param(
            Spec(ki="none"),
            Edit("期初價格的75.00%。", "期初價格的70.00%。", "ts.art16"),
            ("doc.ki_consistency", "scenario_barrier_mentions_2_1"),
            id="情境無 KI 門檻",
        ),
        pytest.param(
            Spec(),
            Edit("小於100.00%（出場值）", "小於105.00%（出場值）", "ts.art16"),
            ("doc.ko_mentions", "scenario_ko_mentions_3_1"),
            id="情境出場值",
        ),
        pytest.param(
            Spec(),
            Edit("大於或等於100.00%，則視為", "大於或等於105.00%，則視為", "ts.art18"),
            ("doc.ko_mentions", "art18_ko_pct"),
            id="第 18 項 KO",
        ),
    ],
)
def test_internal_rules_catch_errors(tmp_path, spec, edit, broken):
    r = check(tmp_path, spec, edits=[edit])
    assert broken in {(rid, f) for rid, f, _ in problems(r)}, problems(r)


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        pytest.param(
            Edit("(i) 事業名稱:", "(i) 名稱:", "ts.cover"),
            ("standard.distributor", "distributor_name_detail"),
            id="明細只有一個標籤讀不到",
        ),
        pytest.param(
            Edit("期初價格的60.00%（下限值）", "下限值", "ts.art16"),
            ("doc.ki_consistency", "scenario_barrier_mentions"),
            id="情境下限值句子讀不到",
        ),
    ],
)
def test_unreadable_repeated_terms_need_review(tmp_path, edit, broken):
    """Issue #305：每種型態都會寫的重複出處讀不到時轉人工覆核，不當作沒有。"""
    r = check(tmp_path, Spec(), edits=[edit])
    assert (*broken, S.REVIEW_REQUIRED) in problems(r), problems(r)


def test_non_memory_with_an_exit_condition_needs_review(tmp_path):
    """非記憶式（最差表現值）的到期條件卻有「最佳表現值…（出場值）」：轉人工覆核。"""
    spec = Spec(memory=False)
    edit = Edit(
        "否則，發行機構將不須支付",
        "若所有連結標的之最佳表現值大於或等於100.00%（出場值）：100%；否則，發行機構將不須支付",
        "ts.art14",
    )
    r = check(tmp_path, spec, edits=[edit])
    assert rule(r, "doc.memory_consistency")[0].status == S.REVIEW_REQUIRED


def test_issuer_name_in_chapter_two_must_have_the_prefix(tmp_path):
    """第二章寫成沒有「荷蘭商」判不一致（只有第一章 3.(1) 沒有前綴也算一致）。"""
    edit = Edit(f"(a) 事業名稱：荷蘭商{ISSUER_EN}", f"(a) 事業名稱：{ISSUER_EN}", "ts.ch二")
    r = check(tmp_path, Spec(), edits=[edit])
    assert rule(r, "standard.issuer_name", "issuer_name_ch2")[0].status == S.MISMATCH
    assert rule(r, "standard.issuer_name", "issuer_name_ch1")[0].status == S.PASS


def test_fixed_warning_needs_three_occurrences(tmp_path):
    edit = Edit(WARNING, "本商品風險程度等級為【RR4】。", "ts.warn")
    r = check(tmp_path, Spec(), edits=[edit])
    assert rule(r, "standard.fixed_warning")[0].status == S.MISMATCH


def test_template_is_not_recognised_without_the_guarantor_label(tmp_path):
    r = check(tmp_path, Spec(), edits=[Edit("保證機構之名稱及地址：", "其他機構之名稱及地址：", "ts.cover")])
    assert rule(r, "template.detect")[0].status == S.REVIEW_REQUIRED


@pytest.mark.parametrize(
    "report",
    [
        lambda p: synth.check(p, synth.Spec()),
        lambda p: gs_synth.check(p, gs_synth.Spec()),
        lambda p: natixis_synth.check(p, natixis_synth.Spec()),
    ],
    ids=["BARC", "GS", "Natixis"],
)
def test_other_issuers_are_not_mistaken_for_bbva(tmp_path, report):
    """其他上手的說明書照常辨識為自己的範本（BBVA 範本不會同時符合而變成 template_ambiguous）。"""
    r = report(tmp_path)
    assert r.template not in (BBVA.template_id, None)


def test_periodic_autocall_table_missing_a_row_needs_review(tmp_path):
    """KO 每期型自動提前贖回表少一列（期別不連續到天期 − 1）：KO 觀察方式與 Non-Call 轉人工覆核。"""
    spec = Spec(ko_obs="P")
    last = spec.tenor - 1
    # 表上第 tenor − 1 列整列拿掉（期別、觀察日、自動提前贖回日、j、觀察日每格都換成空字串）
    cells = (str(last), spec.date(spec.ends[last - 1]), spec.date(spec.payments[last - 1]))
    r = check(tmp_path, spec, edits=[Edit(x, "", "ts.art12.table.ko") for x in cells])
    assert rule(r, "field.first_callable_period")[0].status == S.REVIEW_REQUIRED


def test_issuer_name_in_chapter_one_must_match_besides_the_prefix(tmp_path):
    """第一章 3.(1) 沒有「荷蘭商」算一致，但名稱本身不同仍判不一致。"""
    edit = Edit(f"(1) 發行機構：{ISSUER_EN}", "(1) 發行機構：BBVA GLOBAL MARKET, B.V.", "ts.art3")
    r = check(tmp_path, Spec(), edits=[edit])
    assert rule(r, "standard.issuer_name", "issuer_name_ch1")[0].status == S.MISMATCH
