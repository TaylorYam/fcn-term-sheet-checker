"""DBS 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B16、C28）：每頁頁首刊印日期彼此相同且 = 同商品說明書刊印日期；
最大利率值 = 投資人須知自己括號內年利率 ÷ 12 無條件進位 4 位（年利率另由共用規則和參考條件表比）。

只用投資人須知與同商品 DBS 說明書的讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則
（rules/iis.py、review_standard.iis_review_standard_rules；刊印日期在交易日與發行日之間也是共用規則）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .dbs import ceiling_monthly
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def print_dates(ctx: IisIssuerContext) -> list[CheckResult]:
    """每頁頁首的刊印日期 = 同商品說明書第 2 頁的刊印日期；沒有可比的說明書時，各頁 = 第 1 頁頁首（2026-10-15 確認）。"""
    rid = "iis.print_dates"
    container = read_iis(ctx.sheet, "print_dates")
    if not container.ok:
        return [Check(rid, "print_dates", Item.term_sheet("頁首刊印日期"), ctx.document).review(container)]
    occs = container.value
    ts = None
    if ctx.term_sheet is not None:
        ts = next((o.value for o in ctx.term_sheet.f("print_dates").value or () if o.value.ok), None)
    first = occs[0].value
    out = []
    for occ in occs:
        if ts is not None:
            check = Check(rid, occ.field, Item.term_sheet(occ.name), ctx.document).needs(occ.value)
            out.append(
                check.blocked or check.compare(ts.value, occ.value.value, message=f"{occ.where}須等於說明書刊印日期")
            )
            continue
        why = NO_TERM_SHEET if ctx.term_sheet is None else "同商品說明書讀不到刊印日期，無法比對"
        if occ is occs[0] or not first.ok:
            check = Check(rid, occ.field, Item.term_sheet(occ.name), ctx.document).needs(occ.value)
            out.append(
                check.blocked or ts_unavailable(rid, occ.field, occ.name, why, occ.value.value, document=ctx.document)
            )
            continue
        check = Check(rid, occ.field, Item.expected(occ.name), ctx.document).needs(occ.value)
        out.append(
            check.blocked
            or check.compare(first.value, occ.value.value, message=f"{occ.where}須等於第 1 頁頁首的刊印日期（{why}）")
        )
    return out


def monthly_coupon(ctx: IisIssuerContext) -> CheckResult:
    """收益分配事項的最大利率值 = 括號內年利率 ÷ 12，無條件進位到 4 位（同 DBS 說明書 §3.3）。"""
    name = "最大利率值（年利率 ÷ 12）"
    annual, pf = read_iis(ctx.sheet, "coupon_pa_pct"), read_iis(ctx.sheet, "monthly_coupon_ceiling_pct")
    check = Check("iis.monthly_coupon", "monthly_coupon_ceiling_pct", Item.expected(name), ctx.document).needs(
        annual, pf
    )
    return check.blocked or check.compare(
        ceiling_monthly(annual.value),
        pf.value,
        tolerance="年利率 ÷ 12 無條件進位到 4 位後比數值",
        message="最大利率值須等於括號內年利率 ÷ 12、無條件進位到 4 位",
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [*print_dates(ctx), monthly_coupon(ctx)]
