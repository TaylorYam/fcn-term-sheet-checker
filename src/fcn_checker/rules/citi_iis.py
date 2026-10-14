"""Citi 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 D36、D39、D40）：開始受理贖回日期、有無自動提前出場都要
等於同商品說明書；非記憶式商品的商品事項不得出現「記憶式」。

只用投資人須知讀出結果與同商品 Citi 說明書的讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則
（rules/iis.py、review_standard.iis_review_standard_rules）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check
from .ms_iis import redemption_start


def _yes_no(v: bool) -> str:
    return "有" if v else "沒有"


def has_autocall(ctx: IisIssuerContext) -> CheckResult:
    """有沒有自動提前出場 = 同商品說明書（第 13 項有無自動提前出場界限）；沒有時 KO 觀察方式、Non-Call 不核對。"""
    rid, name = "iis.has_autocall", "有無自動提前出場"
    pf = read_iis(ctx.sheet, "has_autocall")
    check = Check(rid, "has_autocall", Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, "has_autocall", name, NO_TERM_SHEET, _yes_no(pf.value), document=ctx.document)
    expected = ctx.term_sheet.f("has_autocall")
    if not expected.ok:
        why = f"同商品說明書讀不到「{name}」，無法比對"
        return ts_unavailable(rid, "has_autocall", name, why, _yes_no(pf.value), document=ctx.document)
    return check.compare(_yes_no(expected.value), _yes_no(pf.value), message="說明書第 13 項有無「自動提前出場界限」")


def memory_wording(ctx: IisIssuerContext) -> CheckResult:
    """非記憶式（商品年期句與到期給付金額句判定）的商品事項段不得出現「記憶式」（2026-10-14 確認）。"""
    rid = "iis.memory_wording"
    mem, words = read_iis(ctx.sheet, "ko_memory"), read_iis(ctx.sheet, "memory_wording")
    check = Check(rid, "memory_wording", Item.expected("非記憶式不得寫「記憶式」"), ctx.document).needs(mem, words)
    if (problem := check.blocked) is not None:
        return problem
    if mem.value:
        return check.compare("記憶式", "記憶式", message="記憶式商品，不檢查「記憶式」用字")
    return check.compare(
        0,
        words.value,
        evidence=list(words.evidence),
        fail_message="非記憶式商品，商品事項卻出現「記憶式」（2026-10-14 確認：非記憶式就應該寫非記憶式）",
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [redemption_start(ctx), has_autocall(ctx), memory_wording(ctx)]
