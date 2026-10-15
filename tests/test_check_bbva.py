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
            ("schedule.coupon_dates", "coupon_dates"),
            id="每期觀察期間迄日",
        ),
    ],
)
def test_internal_rules_catch_errors(tmp_path, spec, edit, broken):
    r = check(tmp_path, spec, edits=[edit])
    assert broken in {(rid, f) for rid, f, _ in problems(r)}, problems(r)


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
