"""JPM 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B16 JPM 欄）：年利率每處都要相同。

收益分配事項配息金額的年利率（主來源）由共用規則和參考條件表比；D 型贖回價金段的自動提前出場配息金額
「x Y% x 1/12 x n/N」的 Y 每處 = 主來源。只用投資人須知讀出結果，不碰參考條件表；其他檢查點都是各上手共用的
投資人須知規則（rules/iis.py、review_standard.iis_review_standard_rules）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .iis import IisIssuerContext
from .kit import Check


def coupon_pa_mentions(ctx: IisIssuerContext) -> list[CheckResult]:
    """自動提前出場配息金額的年利率每處 = 收益分配事項配息金額的年利率（P 型沒有這處，不產生結果）。"""
    rid = "iis.coupon_pa_mentions"
    main, container = read_iis(ctx.sheet, "coupon_pa_pct"), read_iis(ctx.sheet, "coupon_pa_mentions")
    if not container.ok:
        return [Check(rid, "coupon_pa_mentions", Item.expected("年利率"), ctx.document).review(container)]
    return [
        Check(rid, occ.field, Item.expected(occ.name), ctx.document)
        .needs(main, occ.value)
        .compare(main.value, occ.value.value, fail_message=f"{occ.where}的年利率須等於收益分配事項配息金額的年利率")
        for occ in container.value
    ]


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return coupon_pa_mentions(ctx)
