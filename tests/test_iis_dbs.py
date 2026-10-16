"""DBS 投資人須知黑箱回歸（Issue #280）：合成說明書＋投資人須知經批量入口；不直接測擷取或解析的內部函式。

真實投資人須知寫異體字「視爲」，合成字型畫不出來，合成文件寫「視為」；最後兩個測試直接用擷取後的行測兩種寫法，以及
合成器排不出的價格表版面（表頭在上一頁、前一句換行後以彭博代碼開頭，Issue #295）。
"""

from __future__ import annotations

import pytest

from dbs_synth import DIST_ADDRESS, WARNING, Spec, check_iis
from fcn_checker.issuers import DBS
from fcn_checker.parsers import dbs_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import Line
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from test_check_dbs import VARIANTS


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass(tmp_path, spec):
    r = check_iis(tmp_path, spec).report
    assert r.template == DBS.iis.template_id
    assert r.status == S.PASS, problems(r)
    assert rule(r, "iis.page_totals")[0].status == S.PASS
    assert rule(r, "iis.monthly_coupon")[0].status == S.PASS
    assert [x.status for x in rule(r, "iis.print_dates")] == [S.PASS] * 4  # 每頁頁首 = 說明書刊印日期
    assert [x.status for x in rule(r, "doc.print_date")] == [S.PASS] * 4
    assert rule(r, "standard.fixed_warning")[0].actual == 1  # 警語 1.
    assert rule(r, "standard.forbidden_wording")[0].status == S.PASS
    assert len(rule(r, "iis.product_code")) == 2  # 商品代號、受託或銷售機構商品代號
    for rid in (
        "field.ki_type",
        "field.strike_pct",
        "field.ko_pct",
        "field.first_callable_period",
        "field.denomination",
    ):
        assert rule(r, rid)[0].status == S.PASS, rid
    prices = rule(r, "iis.underlying_prices")  # 價格表各標的價格和參考條件表比
    assert prices and all(x.status == S.PASS for x in prices)
    assert bool([x for x in r.results if x.rule_id == "field.ki_pct"]) == (spec.ki != "none")


def test_price_table_header_across_pages(tmp_path):
    """價格表欄名在第 1 頁頁底、「(期初價格的X%)」在第 2 頁（DB1）：照讀。"""
    r = check_iis(tmp_path, Spec(split_iis_table=True)).report
    assert r.status == S.PASS, problems(r)
    assert rule(r, "field.strike_pct")[0].status == S.PASS


def test_fee_row_across_pages(tmp_path):
    """費用表「提前贖回費用」列跨頁（DB2、DB4）：照讀。"""
    r = check_iis(tmp_path, Spec(split_iis_fee=True)).report
    assert r.status == S.PASS, problems(r)
    assert all(x.status == S.PASS for x in rule(r, "standard.fees"))


def test_five_pages_is_a_mismatch(tmp_path):
    r = check_iis(tmp_path, Spec(iis_pages=5)).report
    assert rule(r, "iis.pages")[0].status == S.MISMATCH


def test_page_head_print_date_must_match_the_term_sheet(tmp_path):
    """頁首刊印日期和說明書不同（仍在交易日與發行日之間）：4 頁都判不一致。"""
    edit = Edit("刊印日期：2030 年01 月08 日", "刊印日期：2030 年01 月09 日", "iis.head")
    r = check_iis(tmp_path, edits=[edit]).report
    assert [x.status for x in rule(r, "iis.print_dates")] == [S.MISMATCH] * 4


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("為0.8334% (10.00%/12)", "為0.8333% (10.00%/12)", "iis.terms"), "iis.monthly_coupon"),
        (Edit("為0.8334% (10.00%/12)", "為0.8334% (10.50%/12)", "iis.terms"), "field.coupon_pa_pct"),
        (Edit("對於第1 個觀察期間", "對於第1~2 個觀察期間", "iis.terms"), "field.first_callable_period"),
        (Edit("記憶事件：", "觀察事件：", "iis.terms"), "field.ko_memory"),
        (Edit("商品年期：6 個月", "商品年期：7 個月", "iis.summary"), "field.tenor_months"),
        (Edit("彭博代碼為ZDB UN", "彭博代碼為ZDX UN", "iis.summary"), "iis.underlying_prices"),
        (Edit("70.3150", "70.3151", "iis.summary.table"), "iis.underlying_prices"),
        (Edit("(期初價格的70.00%)", "(期初價格的75.00%)", "iis.summary.table"), "field.strike_pct"),
        (Edit("每單位美元10,000", "每單位美元1,000", "iis.summary"), "field.denomination"),
        (Edit("商品面額的100.00%", "商品面額的99.00%", "iis.summary"), "standard.issue_price"),
        (
            Edit("新加坡商星展銀行股份有限公司(DBS Bank Ltd)；", "新加坡商星展銀行(DBS Bank Ltd)；", "iis.org"),
            "standard.issuer_name",
        ),
        (Edit(f"營業所在地：{DIST_ADDRESS}", "營業所在地：台北市大安區虛構路2號", "iis.org"), "standard.distributor"),
        (Edit("的0%~5%", "的0%~3%", "iis.fees"), "standard.fees"),
        (Edit(WARNING[:20], WARNING[:19] + "X", "iis.warn"), "standard.fixed_warning"),
        (Edit("刊印日期：2030 年01 月08 日", "刊印日期：2030 年01 月20 日", "iis.head"), "doc.print_date"),
        (Edit("計價幣別：美元(USD)", "計價幣別：日圓(JPY)", "iis.summary"), "field.currency"),
        (Edit("(商品代號：078199990001 )", "(商品代號：078199990002 )", "iis.cover"), "iis.product_code"),
        (Edit("(non-principal protected)", "(principal protected)", "iis.cover"), "iis.name_en"),
        (Edit("本商品風險程度：RR4", "本商品風險程度：RR5", "iis.summary"), "standard.risk_level"),
        (Edit("受託投資或受託買賣本商品", "受託投資本商品", "iis.rest"), "standard.forbidden_wording"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_items_are_checked(tmp_path, edit, broken):
    r = check_iis(tmp_path, edits=[edit]).report
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_ki_is_compared_with_the_sheet(tmp_path):
    """投資人須知有下限觸發事件、參考條件表卻是無 KI：判不一致。"""
    r = check_iis(tmp_path, Spec(ki="AM"), **{"KI(Freq)": "-", "KI(%)": "-"}).report
    assert rule(r, "field.ki_type")[0].status == S.MISMATCH


@pytest.mark.parametrize("word", ["為", "爲"])
def test_memory_wording_accepts_the_variant_character(word):
    """真實投資人須知寫「皆被視爲記憶標的」（異體字），說明書寫「視為」：兩種都是記憶式。"""
    texts = [
        "收益分配事項",
        "自動提前出場事件：",
        f"若所有連結標的皆被視{word}記憶標的，則視為發生自動提前出場事件。",
        "記憶事件：",
        "就每一連結標的而言，如果該連結標的在任一自動提前出場決定日的參考價格大於或等於其自動提前出場價格，則視為發生記憶事件。",
        "發行或受託不成立之處理",
    ]
    lines = [Line(1, 30.0, 100.0 + 15 * i, 560.0, 112.0 + 15 * i, t) for i, t in enumerate(texts)]
    sheet = dbs_iis.read(lines)
    assert sheet.f("ko_memory").ok and sheet.f("ko_memory").value is True


def test_price_table_after_the_underlying_sentence_wrapping_onto_a_ticker():
    """「連結標的資產：…，彭博代碼為」換行後下一行以彭博代碼開頭（`ZBBB UN。`），表頭整個在上一頁、各列在下一頁
    （OOS 真實樣本版面，Issue #295）：那一行不是價格表第 1 列。"""
    texts = [
        (1, 16.5, 750.0, 560.0, "連結標的資產：甲公司 (Alpha Inc)，彭博代碼為ZAAA UW、乙公司 (Beta Inc)，彭博代碼為"),
        (1, 16.5, 762.7, 80.0, "ZBBB UN。"),
        (1, 46.8, 776.0, 94.8, "彭博代碼"),
        (1, 155.1, 779.0, 203.1, "期初價格"),
        (1, 290.4, 779.0, 338.4, "執行價格"),
        (1, 258.6, 793.4, 370.2, "(期初價格的80.00%)"),
        (1, 428.8, 779.0, 524.8, "自動提前出場價格"),
        (1, 417.6, 793.4, 536.0, "(期初價格的100.00%)"),
        (2, 41.8, 26.4, 99.8, "ZAAA UW"),
        (2, 140.4, 26.4, 217.8, "美元 100.0000"),
        (2, 275.7, 26.4, 353.1, "美元 80.0000"),
        (2, 438.1, 26.4, 515.5, "美元 100.0000"),
        (2, 43.8, 38.9, 97.8, "ZBBB UN"),
        (2, 140.4, 38.9, 217.8, "美元 50.0000"),
        (2, 275.7, 38.9, 353.1, "美元 40.0000"),
        (2, 438.1, 38.9, 515.5, "美元 50.0000"),
        (2, 16.5, 52.0, 400.0, "商品面額：每單位美元10,000, 發行價格：商品面額的100.00%。"),
    ]
    lines = [Line(p, x0, y0, x1, y0 + 12, t) for p, x0, y0, x1, t in texts]
    sheet = dbs_iis.read(lines)
    assert sheet.f("price_table").ok, sheet.f("price_table").note
    assert sheet.f("price_table").value.tickers == ["ZAAA UW", "ZBBB UN"]
    assert sheet.f("strike_pct").value == 80
