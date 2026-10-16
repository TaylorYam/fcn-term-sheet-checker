"""CA 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 D35）：有無 KI、贖回 % 要等於同商品說明書。

投資人須知只寫到期贖回「若未發生界限事件」，沒有界限事件定義，分不出 AM／D：只核有無 KI（2026-10-15 確認）。
同商品說明書的 KI 型態已和參考條件表比過，這裡和說明書比即可（三方一致）。只用投資人須知與同商品 CA 說明書的讀出結果，
不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則（rules/iis.py、review_standard.iis_review_standard_rules）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def _yes_no(v: bool) -> str:
    return "有 KI" if v else "無 KI"


def has_ki(ctx: IisIssuerContext) -> CheckResult:
    """二、2.(c) 有無「若未發生界限事件」= 同商品說明書有無 KI（說明書 KI 型態 none 以外都算有）。"""
    rid, name = "iis.has_ki", "有無 KI"
    pf = read_iis(ctx.sheet, "has_ki")
    check = Check(rid, "has_ki", Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, "has_ki", name, NO_TERM_SHEET, _yes_no(pf.value), document=ctx.document)
    ki = ctx.term_sheet.f("ki_type")
    if not ki.ok:
        why = "同商品說明書讀不到 KI 型態，無法比對"
        return ts_unavailable(rid, "has_ki", name, why, _yes_no(pf.value), document=ctx.document)
    return check.compare(
        _yes_no(ki.value != "none"), _yes_no(pf.value), message="說明書第一章第 9 項有無界限事件（KI 型態不核對）"
    )


def redemption(ctx: IisIssuerContext) -> list[CheckResult]:
    """二、2.(b)、(c) 贖回金額「商品面額x N%」的 N = 同商品說明書第 9 項 (b)、(c)（Issue #319）。"""
    rid = "iis.redemption_pct"
    pf = read_iis(ctx.sheet, "redemption_pcts")
    out = []
    for occ in pf.value if pf.ok else ():
        check = Check(rid, occ.field, Item.term_sheet(occ.name), ctx.document).needs(occ.value)
        if (problem := check.blocked) is not None:
            out.append(problem)
            continue
        ts = ctx.term_sheet.f(occ.field) if ctx.term_sheet is not None else None
        if ts is None or not ts.ok:
            why = NO_TERM_SHEET if ts is None else "同商品說明書讀不到第 9 項贖回 %，無法比對"
            out.append(ts_unavailable(rid, occ.field, occ.name, why, f"{occ.value.value}%", document=ctx.document))
            continue
        out.append(
            check.compare(
                f"{ts.value}%",
                f"{occ.value.value}%",
                ok=ts.value == occ.value.value,
                message=f"說明書第一章第 9 項（{occ.where}）",
            )
        )
    return out


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [has_ki(ctx), *redemption(ctx)]
