"""Natixis 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B19、A4）：KO 價格定義的寫法、空白的受託機構商品代號。

只用投資人須知讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則（rules/iis.py、
review_standard.iis_review_standard_rules）。KO % 由共用規則比（`ko_pct`；沒寫 % 的寫法讀成 100%）。
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..investor_sheet import read_iis
from ..parsers.natixis_iis import KO_DEF_INITIAL, KO_DEF_PCT
from ..schema import CheckResult, Item
from .iis import IisIssuerContext
from .kit import Check
from .natixis import distributor_code


def ko_definition(ctx: IisIssuerContext) -> CheckResult:
    """KO 價格定義只接受兩種寫法：「期初進場價格之Y%」，或「中文產品說明書第一章第14項所列之期初進場價格」（沒有 %）。

    沒寫 % 原本一律判不一致（NX7）；2026-10-15 改為讀成 KO 100%（Issue #270）：KO % 由共用規則 `field.ko_pct` 和
    參考條件表比，這裡另外在同商品說明書 KO ≠ 100% 時判不一致，寫出為什麼投資人須知等於 100%。"""
    pf = read_iis(ctx.sheet, "ko_definition")
    check = Check("iis.ko_definition", "ko_definition", Item.expected("KO 價格定義的寫法"), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    expected = f"期初進場價格之Y%（或沒寫 % 的「{KO_DEF_INITIAL}」，即 100%）"
    if re.fullmatch(KO_DEF_PCT, pf.value):
        return check.compare(expected, pf.value, ok=True)
    if pf.value != KO_DEF_INITIAL:
        return check.compare(expected, pf.value, ok=False, fail_message="KO 價格定義的寫法不在範本規格內，請上手更正")
    ts_ko = ctx.term_sheet.f("ko_pct") if ctx.term_sheet is not None else None
    if ts_ko is None or not ts_ko.ok or ts_ko.value == Decimal(100):
        return check.compare(expected, pf.value, ok=True)
    return check.compare(
        expected,
        pf.value,
        ok=False,
        fail_message=(
            f"KO 價格定義沒寫 %，等於期初進場價格（100%），但說明書第一章第 14 項 KO 為 {ts_ko.value}%，請上手更正"
        ),
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [ko_definition(ctx), *distributor_code(ctx, read_iis(ctx.sheet, "distributor_code"))]
