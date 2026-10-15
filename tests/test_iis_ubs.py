"""UBS 投資人須知黑箱回歸（Issue #279）：合成說明書＋投資人須知經批量入口；不直接測擷取或解析的內部函式。

真實文件每頁的私用區字元 U+F020 合成字型畫不出來，最後一個測試直接用擷取後的行測刪除。
"""

from __future__ import annotations

import pytest

from fcn_checker.issuers import UBS
from fcn_checker.parsers import ubs_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import Line
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from test_check_ubs import VARIANTS
from ubs_synth import DIST, PHONE, PRODUCT_TYPE, WARNING, Spec, check_iis


@pytest.mark.parametrize("spec", VARIANTS.values(), ids=VARIANTS.keys())
def test_supported_types_pass(tmp_path, spec):
    r = check_iis(tmp_path, spec).report
    assert r.template == UBS.iis.template_id
    assert r.status == S.PASS, problems(r)
    assert rule(r, "iis.page_totals")[0].status == S.PASS
    assert rule(r, "iis.issuing_branch")[0].status == S.PASS
    assert rule(r, "standard.fixed_warning")[0].actual == 1  # 警語 1.
    assert len(rule(r, "standard.distributor")) == 3  # 名稱、地址（相關機構）、電話（末頁聯絡方式）
    assert [x.status for x in rule(r, "doc.print_date")] == [S.PASS]
    nothing = (  # 範本沒有：商品代號、ISIN、交易日、期末訂價日、KO／KI、配息率、價格與 %
        "iis.product_code",
        "field.isin",
        "field.trade_date",
        "field.final_valuation_date",
        "field.ko_observation",
        "field.ko_memory",
        "field.ki_type",
        "field.coupon_pa_pct",
        "field.strike_pct",
        "iis.underlying_prices",
    )
    assert not [x for x in r.results if x.rule_id in nothing]


def test_five_pages_is_a_mismatch(tmp_path):
    r = check_iis(tmp_path, Spec(iis_pages=5)).report
    assert rule(r, "iis.pages")[0].status == S.MISMATCH


def test_observation_in_brackets_is_the_same_name(tmp_path):
    """投資人須知寫「瑞銀（每日觀察）」：和說明書「瑞銀每日觀察」視為相同（2026-10-15 確認）。"""
    r = check_iis(tmp_path, edits=[Edit("瑞銀每日觀察", "瑞銀（每日觀察）", "iis.cover")]).report
    assert r.status == S.PASS, problems(r)


def test_old_template_is_not_supported(tmp_path):
    edit = Edit(f"商品種類：{PRODUCT_TYPE}。", "商品種類：股權結構型債券。", "iis.cover")
    r = check_iis(tmp_path, edits=[edit]).report
    assert r.template != UBS.iis.template_id
    assert "舊版範本" in rule(r, "template.detect")[0].message


def test_issuing_branch_other_than_london_needs_review(tmp_path):
    r = check_iis(tmp_path, edits=[Edit("經由倫敦分行發行。", "經由香港分行發行。", "iis.org")]).report
    assert rule(r, "iis.issuing_branch")[0].status == S.REVIEW_REQUIRED


@pytest.mark.parametrize(
    ("edit", "broken"),
    [
        (Edit("瑞銀每日觀察記憶式", "瑞銀每日觀察", "iis.cover"), "iis.name_zh"),
        (Edit("UBS Daily Memory", "UBS Daily", "iis.cover"), "iis.name_en"),
        (Edit("10. 天期/年期：6 個月", "10. 天期/年期：7 個月", "iis.summary"), "field.tenor_months"),
        (Edit("ZUB UN", "ZUX UN", "iis.summary"), "field.underlyings"),
        (Edit("6. 計價幣別：美元", "6. 計價幣別：日圓", "iis.summary"), "field.currency"),
        (
            Edit("11. 發行日/交割日：2030 年1 月14 日", "11. 發行日/交割日：2030 年1 月15 日", "iis.summary"),
            "iis.issue_date",
        ),
        (Edit("即現預計2030", "即現預計2031", "iis.summary"), "field.maturity_date"),
        (Edit("發行機構：瑞士銀行，", "發行機構：瑞士銀行集團，", "iis.org"), "standard.issuer_name"),
        (Edit(f"地址為{DIST['address']}", "地址為台北市大安區虛構路2號", "iis.org"), "standard.distributor"),
        (Edit(f"電話：{PHONE}。", "電話：+886-2-5556-1314。", "iis.rest"), "standard.distributor"),
        (Edit("申購價金的 0%~5%", "申購價金的 0%~3%", "iis.fees"), "standard.fees"),
        (Edit(WARNING[:20], WARNING[:19] + "X", "iis.warn"), "standard.fixed_warning"),
        (Edit("刋印日期：2030 年1 月8 日", "刋印日期：2030 年1 月20 日", "iis.cover"), "doc.print_date"),
        (Edit("【RR4】", "【RR5】", "iis.warn"), "standard.risk_level"),
    ],
    ids=lambda x: x if isinstance(x, str) else x.where + " " + x.old[:10],
)
def test_items_are_checked(tmp_path, edit, broken):
    r = check_iis(tmp_path, edits=[edit]).report
    assert broken in {rid for rid, _, _ in problems(r)}, problems(r)


def test_private_use_space_is_removed():
    """真實文件換頁處夾在句子中間的私用區字元 U+F020（Symbol 字型的空白）先刪除，名稱與日期照讀。"""
    texts = [
        "中文投資人須知",
        "商品中文名稱：瑞銀每日觀察提前出場固定配息連結股票（不保本）（無擔保）（無保證機構）結構型商品（下稱“本商品”）；",
        "",
        "11. 發行日/交割日：2030 年1 月14 日。",
    ]
    lines = [Line(1, 36.0, 100.0 + 15 * i, 560.0, 112.0 + 15 * i, t) for i, t in enumerate(texts)]
    sheet = ubs_iis.read(lines)
    assert (
        sheet.f("name_zh").value == "瑞銀每日觀察提前出場固定配息連結股票（不保本）（無擔保）（無保證機構）結構型商品"
    )
    assert str(sheet.f("issue_date").value) == "2030-01-14"
