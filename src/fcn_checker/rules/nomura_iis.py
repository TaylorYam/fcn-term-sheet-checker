"""Nomura 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 B12、B16、D34、D36 的 Nomura 欄）：每期配息率、D 型 KO 起日、
標的英文名稱、開始／後續受理贖回日期文字都要等於同商品說明書。

只用投資人須知讀出結果與同商品 Nomura 說明書的讀出結果，不碰參考條件表（每期配息率 = 年利率 ÷ 12 是共用規則
`rules/iis.py`）；其他檢查點都是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from ..text import squash
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def _vs_term_sheet(
    ctx: IisIssuerContext,
    rid: str,
    field: str,
    name: str,
    expected_of: Callable[[Any], Any],
    ts_field: str,
    *,
    normalize: Callable[[Any], Any] = lambda v: v,
    tolerance: str | None = None,
    message: str = "",
) -> CheckResult:
    """投資人須知欄位 `field` = 同商品說明書 `ts_field` 讀出值經 `expected_of` 取出的值；說明書讀不到時轉人工覆核。"""
    pf = read_iis(ctx.sheet, field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, field, name, NO_TERM_SHEET, pf.value, document=ctx.document)
    src = ctx.term_sheet.f(ts_field)
    expected = expected_of(src.value) if src.ok else None
    if expected is None:
        why = f"同商品說明書讀不到「{name}」，無法比對"
        return ts_unavailable(rid, field, name, why, pf.value, document=ctx.document)
    return check.compare(
        expected, pf.value, ok=normalize(expected) == normalize(pf.value), tolerance=tolerance, message=message
    )


def coupon(ctx: IisIssuerContext) -> CheckResult:
    """收益分配事項每期配息率 = 說明書第 7 項（B16；= 年利率 ÷ 12 另由共用規則核對）。"""
    return _vs_term_sheet(
        ctx, "iis.coupon_per_period", "coupon_per_period_pct", "每期配息率 %", lambda v: v, "coupon_period_pct"
    )


def ko_start(ctx: IisIssuerContext) -> list[CheckResult]:
    """D 型「自動提前出場決定日」：自…起（含）的起日 = 說明書第 7 項 KO 起日（D34）；P 型只寫參照，不核對。"""
    obs = read_iis(ctx.sheet, "ko_observation")
    if not (obs.ok and obs.value == "D"):
        return []
    return [
        _vs_term_sheet(
            ctx,
            "iis.ko_observation_dates",
            "ko_observation_start",
            "KO 觀察起日",
            lambda v: v[1] if v[0] == "D" else None,
            "ko_wording",
            message="須等於說明書第 7 項自動提前出場決定日的起日",
        )
    ]


def underlying_names(ctx: IisIssuerContext) -> CheckResult:
    """商品簡介標的英文名稱（依順序、檔數相同）= 說明書第一章第 8 項（B12；名稱本身是否正確不核對）。"""
    return _vs_term_sheet(
        ctx,
        "iis.underlying_names",
        "underlying_names_en",
        "標的英文名稱",
        lambda v: list(v[1]),
        "art8_names",
        normalize=lambda v: [squash(x) for x in v],
        tolerance="忽略空白，依標的順序",
    )


def redemption_dates(ctx: IisIssuerContext) -> list[CheckResult]:
    """開始、後續受理贖回日期的文字 = 說明書第四章 1.(3)、(4)（D36；忽略空白）。"""
    return [
        _vs_term_sheet(
            ctx,
            "iis.redemption_start",
            field,
            name,
            lambda v: v,
            field,
            normalize=lambda v: squash(v) if isinstance(v, str) else v,
            tolerance="忽略空白後逐字相等",
        )
        for field, name in (
            ("redemption_start_text", "開始受理投資人贖回日期"),
            ("redemption_later_text", "後續受理投資人贖回日期"),
        )
    ]


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [coupon(ctx), *ko_start(ctx), underlying_names(ctx), *redemption_dates(ctx)]
