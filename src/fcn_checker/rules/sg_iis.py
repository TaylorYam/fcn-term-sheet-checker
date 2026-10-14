"""SG 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 D38、D39）：有無自動提前出場與期間每日觀察的觀察起日
都要等於同商品說明書第 8 項。

只用投資人須知讀出結果與同商品 SG 說明書的讀出結果（可讀 SG parser 的專屬欄位 `has_autocall`、`ko_wording`），
不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則（rules/iis.py、review_standard.iis_review_standard_rules）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def _yes_no(v: bool) -> str:
    return "有" if v else "沒有"


def has_autocall(ctx: IisIssuerContext) -> CheckResult:
    """有沒有自動提前出場 = 同商品說明書第 8 項（沒有時 KO 觀察方式、Non-Call、KO % 與 KO 價不在投資人須知核對）。"""
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
    return check.compare(_yes_no(expected.value), _yes_no(pf.value), message="說明書第 8 項有無「自動提前出場事件」")


def ko_observation_start(ctx: IisIssuerContext) -> list[CheckResult]:
    """期間每日觀察（D 型）的觀察起日 = 同商品說明書第 8 項觀察期間起日；其他型態沒有這一項。"""
    rid, key, name = "iis.ko_observation_start", "ko_observation_start", "KO 觀察起日"
    kind = read_iis(ctx.sheet, "ko_observation")
    if not (kind.ok and kind.value == "D"):
        return []
    pf = read_iis(ctx.sheet, key)
    check = Check(rid, key, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return [problem]
    if ctx.term_sheet is None:
        return [ts_unavailable(rid, key, name, NO_TERM_SHEET, pf.value, document=ctx.document)]
    wording = ctx.term_sheet.f("ko_wording")
    if not (wording.ok and wording.value.get("type") == "D"):
        why = "同商品說明書第 8 項讀不到期間每日觀察的觀察期間起日，無法比對"
        return [ts_unavailable(rid, key, name, why, pf.value, document=ctx.document)]
    return [check.compare(wording.value["start"], pf.value, message="須等於說明書第 8 項觀察期間起日")]


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [has_autocall(ctx), *ko_observation_start(ctx)]
