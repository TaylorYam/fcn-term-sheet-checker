"""Natixis 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B19、A4）：KO 價格定義的寫法、空白的受託機構商品代號。

只用投資人須知讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則（rules/iis.py、
review_standard.iis_review_standard_rules）。KO 定義寫了 % 時由共用規則比 KO %（`ko_pct`）；沒寫 % 是參照說明書，不核對。
"""

from __future__ import annotations

import re

from ..investor_sheet import read_iis
from ..parsers.natixis_iis import KO_DEF_INITIAL, KO_DEF_PCT
from ..schema import CheckResult, Item, ParsedField
from .iis import IisIssuerContext
from .kit import Check
from .natixis import distributor_code, ko_final_day


def ko_definition(ctx: IisIssuerContext) -> CheckResult:
    """KO 價格定義只接受兩種寫法：「期初進場價格之Y%」，或「中文產品說明書第一章第14項所列之期初進場價格」（沒有 %）。

    沒寫 % 是參照說明書第一章第 14 項，投資人須知不核對 KO %（2026-10-15 確認，Issue #272；取代導入時 NX7 的
    「判不一致」與 #270 的「讀成 100%」）。其他寫法判不一致。"""
    pf = read_iis(ctx.sheet, "ko_definition")
    check = Check("iis.ko_definition", "ko_definition", Item.expected("KO 價格定義的寫法"), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    return check.compare(
        f"期初進場價格之Y%（或參照說明書的「{KO_DEF_INITIAL}」）",
        pf.value,
        ok=bool(re.fullmatch(KO_DEF_PCT, pf.value)) or pf.value == KO_DEF_INITIAL,
        fail_message="KO 價格定義的寫法不在範本規格內，請上手更正",
    )


def ko_final_day_terms(ctx: IisIssuerContext) -> list[CheckResult]:
    """KO 每日觀察到最終評價日含不含依參考條件表交易日（同說明書，Issue #351）；每期型沒有這句，不檢查。"""
    obs = read_iis(ctx.sheet, "ko_observation")
    if not (obs.ok and obs.value == "D"):
        return []
    trade = ctx.trade_date or ParsedField.missing("trade_date", "沒有參考條件表交易日，無法決定 KO 觀察日的寫法")
    return [ko_final_day(ctx.document, trade, read_iis(ctx.sheet, "ko_final_day"), "貳、商品事項 ")]


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [
        ko_definition(ctx),
        *ko_final_day_terms(ctx),
        *distributor_code(ctx, read_iis(ctx.sheet, "distributor_code")),
    ]
