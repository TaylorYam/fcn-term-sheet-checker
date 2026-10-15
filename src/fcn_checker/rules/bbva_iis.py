"""BBVA 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B16、B19、B19a、D36）：開始受理贖回日期、KO 每期型的每期
配息率與計息天數慣例、記憶式到期條件的出場值、無 KI 的到期門檻都要等於同商品說明書。

只用投資人須知讀出結果與同商品 BBVA 說明書的讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則
（rules/iis.py、review_standard.iis_review_standard_rules）。範本沒有的欄位（例：KO 每日型沒有每期配息率）不核對。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check
from .ms_iis import redemption_start

# 投資人須知欄位 → (rule_id, 同商品說明書欄位, 項目名稱)
VS_TERM_SHEET = (
    ("coupon_per_period_pct", "iis.coupon_per_period", "monthly_coupon_pct", "每期配息率 %"),
    ("day_count", "iis.coupon_per_period", "day_count", "計息天數慣例"),
    ("ko_at_maturity_pct", "iis.ko_mentions", "ko_at_maturity_pct", "到期條件的出場值 %"),
    ("no_ki_barrier_pct", "iis.no_ki_barrier", "strike_def_pct", "無 KI 的到期門檻 %（= 轉換比例）"),
)


def vs_term_sheet(ctx: IisIssuerContext, field: str, rid: str, ts_field: str, name: str) -> CheckResult:
    """投資人須知某欄 = 同商品說明書某欄；說明書沒有可比對的值時轉人工覆核。"""
    pf = read_iis(ctx.sheet, field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, field, name, NO_TERM_SHEET, pf.value, document=ctx.document)
    expected = ctx.term_sheet.f(ts_field)
    if not expected.ok:
        return ts_unavailable(
            rid, field, name, f"同商品說明書讀不到「{name}」，無法比對", pf.value, document=ctx.document
        )
    return check.compare(expected.value, pf.value)


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    out = [redemption_start(ctx)]
    for field, rid, ts_field, name in VS_TERM_SHEET:
        if ctx.sheet.provides(field):
            out.append(vs_term_sheet(ctx, field, rid, ts_field, name))
    return out
