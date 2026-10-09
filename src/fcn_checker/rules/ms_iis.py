"""MS 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 D36、D37）：開始受理贖回日期 = 說明書、商品種類依標的數。

只用投資人須知讀出結果與同商品 MS 說明書的讀出結果（可讀 MS parser 的專屬欄位），不碰參考條件表；
其他檢查點都是各上手共用的投資人須知規則（rules/iis.py、review_standard.iis_review_standard_rules）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from ..text import squash
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import doc_review, result
from .ms import PRODUCT_TYPES, check


def redemption_start(ctx: IisIssuerContext) -> CheckResult:
    """開始受理贖回日期 = 同商品說明書第四章開始受理贖回日期（說明書另核對 = 發行日下一個平日）。"""
    rid, name = "iis.redemption_start", "開始受理贖回日期"
    pf, item = read_iis(ctx.sheet, "redemption_start"), Item.term_sheet(name)
    if not pf.ok:
        return doc_review(rid, "redemption_start", pf, item=item)
    if ctx.term_sheet is None:
        return ts_unavailable(rid, "redemption_start", name, NO_TERM_SHEET, pf.value)
    expected = ctx.term_sheet.f("redemption_start")
    if not expected.ok:
        return ts_unavailable(rid, "redemption_start", name, f"同商品說明書讀不到「{name}」，無法比對", pf.value)
    ok = expected.value == pf.value
    return result(
        rid,
        "redemption_start",
        S.PASS if ok else S.MISMATCH,
        expected=expected.value,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        item=item,
    )


def product_type(ctx: IisIssuerContext) -> CheckResult:
    """商品種類依投資人須知的標的數：1 檔與 2 檔以上寫法不同（同 MS 說明書封面 6，rules/ms.py `PRODUCT_TYPES`）。"""
    pf, names = read_iis(ctx.sheet, "product_type"), read_iis(ctx.sheet, "underlying_names")
    return check(
        "iis.product_type",
        "product_type",
        "商品種類",
        [pf, names],
        PRODUCT_TYPES[len(names.value) >= 2] if names.ok else None,
        squash(pf.value) if pf.ok else None,
        message="商品種類依連結標的資產的檔數：1 檔與 2 檔以上寫法不同",
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [redemption_start(ctx), product_type(ctx)]
