"""各上手共用的說明書推算規則：只讀標準欄位，所有上手沿用（ADR 0005；語意以 BARC 為準，Issue #94）。

目前只有價格推算：各標的執行價／KO 價／下限價 = 最初價格 × 對應百分比，四捨五入（half-up）到 4 位。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP

from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from .kit import PRICE_LABEL, Q4, RuleContext, doc_ki, doc_review, price_item, result, standard_field

PRICE_PCT_FIELD = {"strike": "strike_pct", "ko": "ko_pct", "ki": "ki_pct"}
PRICE_TABLE = Item.expected("價格表")


def prices(ctx: RuleContext) -> list[CheckResult]:
    """價格表列數須等於標的數、有無下限價欄須與 KI 型態一致；各標的價格 = 最初價格 × 對應百分比。"""
    rid = "derive.prices"
    table, uls = standard_field(ctx, "underlying_prices"), standard_field(ctx, "underlyings")
    if not table.ok:
        return [doc_review(rid, "price_table", table, item=PRICE_TABLE)]
    rows = table.value
    if uls.ok and len(uls.value) != len(rows):
        return [
            result(
                rid,
                "price_table",
                S.REVIEW_REQUIRED,
                expected=len(uls.value),
                actual=len(rows),
                pf=table,
                reason="price_table_row_count",
                message="價格表列數與標的數不同",
                item=PRICE_TABLE,
            )
        ]
    kt = standard_field(ctx, "ki_type")
    ki = doc_ki(kt)
    if ki is None:
        return [doc_review(rid, "price_table", kt, item=PRICE_TABLE)]
    if (ki != "none") != all("ki" in r.prices for r in rows):
        return [
            result(
                rid,
                "price_table",
                S.REVIEW_REQUIRED,
                pf=table,
                reason="price_table_ki_column",
                message="價格表有無觸及生效價格欄與說明書 KI 型態定義不一致",
                item=PRICE_TABLE,
            )
        ]
    out = []
    for i, row in enumerate(rows):
        label = uls.value[i] if uls.ok else (row.ticker or row.name or f"第 {i + 1} 檔標的")
        for col in ("strike", "ko", "ki"):
            if col not in row.prices:
                continue
            field, item = f"{label} {PRICE_LABEL[col]}", Item.expected(price_item(i + 1, col))
            pct = standard_field(ctx, PRICE_PCT_FIELD[col])
            if not pct.ok:
                out.append(doc_review(rid, field, pct, item=item))
                continue
            initial, actual = row.prices["initial"], row.prices[col]
            expected = (initial * pct.value / 100).quantize(Q4, ROUND_HALF_UP)
            ok = expected == actual
            out.append(
                result(
                    rid,
                    field,
                    S.PASS if ok else S.MISMATCH,
                    expected=expected,
                    actual=actual,
                    evidence=[*row.evidence, *pct.evidence],
                    reason="" if ok else "value_mismatch",
                    tolerance="四捨五入（half-up）到 4 位",
                    message=f"最初價格 {initial} × {pct.value}%",
                    item=item,
                )
            )
    return out
