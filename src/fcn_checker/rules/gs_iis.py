"""GS 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 GS 欄；Issue #317）：重複寫出的天期 = 商品年期、到期買回
「每單位面額 × X%」的 X = 100。

只用投資人須知本身的讀出結果（商品年期另由共用規則和參考條件表比），不碰參考條件表與說明書；說明文字的幣別字併入
`currency_others`，由共用規則核對。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item, ParsedField
from .iis import IisIssuerContext
from .kit import Check


def _mentions(ctx: IisIssuerContext, rid: str, name: str, source: ParsedField, fail_message: str) -> list[CheckResult]:
    container = read_iis(ctx.sheet, name)
    if not container.ok:
        return [Check(rid, name, Item.expected(name), ctx.document).review(container)]
    return [
        Check(rid, occ.field, Item.expected(occ.where), ctx.document)
        .needs(source, occ.value)
        .compare(source.value, occ.value.value, fail_message=fail_message)
        for occ in container.value
    ]


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    hundred = ParsedField.present("redemption_pct", 100, [])
    return [
        *_mentions(
            ctx, "iis.tenor_mentions", "tenor_mentions", read_iis(ctx.sheet, "tenor_months"), "須等於二.1.(11) 商品年期"
        ),
        *_mentions(ctx, "iis.redemption_pct", "redemption_pct_mentions", hundred, "到期買回金額須為每單位面額 × 100%"),
    ]
