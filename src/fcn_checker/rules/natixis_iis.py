"""Natixis 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B19、A4）：KO 價格定義的寫法、空白的受託機構商品代號。

只用投資人須知讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則（rules/iis.py、
review_standard.iis_review_standard_rules）。KO % 由共用規則比（`ko_pct`；沒寫 % 的寫法讀成 100%）。
"""

from __future__ import annotations

import re

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .iis import IisIssuerContext
from .kit import Check
from .natixis import distributor_code


def ko_definition(ctx: IisIssuerContext) -> CheckResult:
    """KO 價格定義只接受兩種寫法：「…期初進場價格之Y%」，或「…所列之期初進場價格」（沒有 %，即 100%）。

    沒寫 % 原本一律判不一致（NX7）；2026-10-15 改為讀成 100%，由共用規則 `ko_pct` 和參考條件表比：KO 100% 通過、
    其他判不一致（Issue #270）。"""
    pf = read_iis(ctx.sheet, "ko_definition")
    check = Check("iis.ko_definition", "ko_definition", Item.expected("KO 價格定義的百分比"), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    known = bool(re.fullmatch(r"期初進場價格之\d+(?:\.\d+)?%|.*所列之期初進場價格", pf.value))
    return check.compare(
        "期初進場價格之X%（或沒寫 % 的期初進場價格）",
        pf.value,
        ok=known,
        fail_message="KO 價格定義的寫法不在範本規格內，請上手更正",
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [ko_definition(ctx), *distributor_code(ctx, read_iis(ctx.sheet, "distributor_code"))]
