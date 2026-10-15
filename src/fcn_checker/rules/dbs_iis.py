"""DBS 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B16、C28）：每頁頁首刊印日期 = 同商品說明書刊印日期；
最大利率值 = 同商品說明書的最大利率值（說明書那份已核對 = 年利率 ÷ 12 無條件進位 4 位）。

只用投資人須知與同商品 DBS 說明書的讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則
（rules/iis.py、review_standard.iis_review_standard_rules；刊印日期在交易日與發行日之間也是共用規則）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def print_dates(ctx: IisIssuerContext) -> list[CheckResult]:
    """每頁頁首的刊印日期 = 同商品說明書第 2 頁的刊印日期（2026-10-15 確認）。"""
    rid, name = "iis.print_dates", "頁首刊印日期"
    container = read_iis(ctx.sheet, "print_dates")
    if not container.ok:
        return [Check(rid, "print_dates", Item.term_sheet(name), ctx.document).review(container)]
    ts = None
    if ctx.term_sheet is not None:
        ts = next((o.value for o in ctx.term_sheet.f("print_dates").value or () if o.value.ok), None)
    out = []
    for occ in container.value:
        check = Check(rid, occ.field, Item.term_sheet(occ.name), ctx.document).needs(occ.value)
        if (problem := check.blocked) is not None:
            out.append(problem)
        elif ctx.term_sheet is None or ts is None:
            why = NO_TERM_SHEET if ctx.term_sheet is None else "同商品說明書讀不到刊印日期，無法比對"
            out.append(ts_unavailable(rid, occ.field, occ.name, why, occ.value.value, document=ctx.document))
        else:
            out.append(check.compare(ts.value, occ.value.value, message=f"{occ.where}須等於說明書刊印日期"))
    return out


def monthly_coupon(ctx: IisIssuerContext) -> CheckResult:
    """收益分配事項的最大利率值 = 同商品說明書配息資料的最大利率值。"""
    rid, name = "iis.monthly_coupon", "最大利率值"
    pf = read_iis(ctx.sheet, "monthly_coupon_ceiling_pct")
    check = Check(rid, "monthly_coupon_ceiling_pct", Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, "monthly_coupon_ceiling_pct", name, NO_TERM_SHEET, pf.value, document=ctx.document)
    ts = ctx.term_sheet.f("monthly_coupon_pct")
    if not ts.ok:
        why = "同商品說明書讀不到最大利率值，無法比對"
        return ts_unavailable(rid, "monthly_coupon_ceiling_pct", name, why, pf.value, document=ctx.document)
    return check.compare(ts.value, pf.value, message="須等於說明書配息資料的最大利率值（年利率 ÷ 12 無條件進位 4 位）")


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [*print_dates(ctx), monthly_coupon(ctx)]
