"""參考條件表的共用規則：表上事先填好的欄位、Non-Call(月)、回填欄位（ISIN、比價日）的核對與回填決策。

各上手只交出說明書標準欄位（standard_fields.py）與提前出場排程；比對與填法在這裡實作一次，所有上手共用。
空值寫法一律取自參考條件表格式設定（`empty_value`）。

比價日填法：

- D（期間每日觀察）：只填 `比價日_{Non-Call}`，其他格填空值寫法。
- P（每期定日觀察）：`比價日_{Non-Call}`～`比價日_{期數}` 每格填該期比價日，其他格填空值寫法。

表上空白的格子在整份核對通過後回填；已有值（含空值寫法）的格子只比對，不覆蓋。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..config import ReferenceFormat
from ..orders.reference import ReferenceRow
from ..schema import CheckResult, OrderValue, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import STANDARD_FIELDS, AutocallSchedule
from .common import (
    KI_LABEL,
    Context,
    cmp_pct,
    doc_ki,
    doc_review,
    order_review,
    order_value,
    result,
    to_date,
    to_decimal,
    to_int,
)

__all__ = ["AutocallSchedule", "CellDecision", "compare_dates", "field_rules", "first_callable_period", "isin"]

SLOTS = 12
Q4 = Decimal("0.0001")
PCT_TOLERANCE = "依說明書顯示位數四捨五入後比對"
OBS_LABEL = {"D": "期間每日觀察", "P": "期末定日觀察"}
PRICE_COLUMNS = (
    ("initial", "initial_price", "進場價"),
    ("strike", "strike_price", "執行價"),
    ("ki", "ki_price", "下限價"),
    ("ko", "ko_price", "KO 價"),
)


def standard_field(ctx: Context, name: str) -> ParsedField:
    """讀說明書標準欄位；上手 adapter 沒交出時視為缺漏，相關規則轉人工覆核。"""
    assert name in STANDARD_FIELDS, f"{name} 不是標準欄位"
    try:
        return ctx.ts.f(name)
    except KeyError:
        return ParsedField.missing(name, f"上手未提供標準欄位「{name}」")


# ---------------------------------------------------------------- 表上事先填好的欄位


def _compare(
    ctx: Context,
    rule_id: str,
    key: str,
    convert: Callable[[Any], Any],
    what: str,
    compare: Callable[[Any, Any], tuple[bool, Any]] | None = None,
    tolerance: str | None = None,
) -> CheckResult:
    """表上欄位與同名標準欄位比對；預設相等，`compare` 回傳（是否一致, 顯示的預期值）。"""
    pf = standard_field(ctx, key)
    v, ov, problem = order_value(ctx, key, rule_id, key, pf, convert, what)
    if problem:
        return problem
    if not pf.ok:
        return doc_review(rule_id, key, pf, v, [ov])
    ok, shown = compare(v, pf.value) if compare else (v == pf.value, v)
    return result(
        rule_id,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=shown,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        tolerance=tolerance,
    )


def product_code(ctx: Context) -> CheckResult:
    rid, pf, ov = "field.product_code", standard_field(ctx, "product_code"), ctx.order.product_code
    if ov.value is None:
        return order_review(rid, "product_code", ov, pf, "order_missing", f"{ctx.order.source}沒有商品代號")
    if not pf.ok:
        return doc_review(rid, "product_code", pf, ov.value, [ov])
    ok = ov.value == pf.value
    return result(
        rid,
        "product_code",
        S.PASS if ok else S.MISMATCH,
        expected=ov.value,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message="" if ok else f"說明書與{ctx.order.source}的商品代號不同，可能拿錯檔案",
    )


def currency(ctx: Context) -> CheckResult:
    rid = "field.currency"
    pf = standard_field(ctx, "currency_zh")
    v, ov, problem = order_value(
        ctx, "currency", rid, "currency", pf, lambda x: x if isinstance(x, str) else None, "文字"
    )
    if problem:
        return problem
    if not pf.ok:
        return doc_review(rid, "currency", pf, v, [ov])
    iso = ctx.std.currency_zh_to_iso.get(pf.value)
    if iso is None:
        return result(
            rid,
            "currency",
            S.REVIEW_REQUIRED,
            expected=v,
            actual=pf.value,
            pf=pf,
            ov=[ov],
            reason="currency_unknown",
            message=f"說明書幣別「{pf.value}」不在審查標準的幣別對照表",
        )
    ok = v == iso
    return result(
        rid,
        "currency",
        S.PASS if ok else S.MISMATCH,
        expected=v,
        actual=iso,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message=f"說明書：{pf.value} → {iso}",
    )


def underlyings(ctx: Context) -> CheckResult:
    rid, pf, empty = "field.underlyings", standard_field(ctx, "underlyings"), ctx.fmt.empty_value
    ovs = [ctx.order.fields.get(f"underlying_{i}") for i in range(1, 6)]
    values = [o.value if o and o.value != empty else None for o in ovs]  # 空值寫法表示沒有這檔標的
    filled = [i for i, v in enumerate(values) if v is not None]
    if not filled:
        return order_review(rid, "underlyings", ovs[0], pf, "order_missing", f"{ctx.order.source}沒有任何標的代號")
    if filled != list(range(len(filled))):
        return result(
            rid,
            "underlyings",
            S.REVIEW_REQUIRED,
            expected=values,
            pf=pf,
            ov=ovs,
            reason="order_invalid",
            message=f"{ctx.order.source}的標的代號中間有空白欄",
        )
    tickers = [str(values[i]) for i in filled]
    used = [ovs[i] for i in filled]
    if not pf.ok:
        return doc_review(rid, "underlyings", pf, tickers, used)
    ok = tickers == pf.value
    msg = "" if ok else "彭博代號須依順序逐字相等（含交易所尾碼），數量也須相同"
    return result(
        rid,
        "underlyings",
        S.PASS if ok else S.MISMATCH,
        expected=tickers,
        actual=pf.value,
        pf=pf,
        ov=used,
        reason="" if ok else "value_mismatch",
        message=msg,
    )


def _mapped(
    ctx: Context, rid: str, key: str, pf: ParsedField, values: dict[str, Any], column: str
) -> tuple[Any, OrderValue | None, CheckResult | None]:
    v, ov, problem = order_value(ctx, key, rid, key, pf, lambda x: x if isinstance(x, str) else None, "文字")
    if problem:
        return None, ov, problem
    if v not in values:
        return None, ov, order_review(rid, key, ov, pf, "order_unknown_value", f"{column}「{v}」不在格式設定的允許值內")
    return values[v], ov, None


def ko_observation(ctx: Context) -> CheckResult:
    rid, key, pf = "field.ko_observation", "ko_observation", standard_field(ctx, "ko_observation")
    mapped, ov, problem = _mapped(ctx, rid, key, pf, ctx.fmt.ko_observation_values, "KO(Freq)")
    if problem:
        return problem
    if not pf.ok:
        return doc_review(rid, key, pf, mapped, [ov])
    ok = mapped == pf.value
    return result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=f"{ov.value}（{OBS_LABEL[mapped]}）",
        actual=OBS_LABEL.get(pf.value, pf.value),
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
    )


def ko_memory(ctx: Context) -> CheckResult:
    rid, key, pf = "field.ko_memory", "ko_memory", standard_field(ctx, "ko_memory")
    mapped, ov, problem = _mapped(ctx, rid, key, pf, ctx.fmt.ko_memory_values, "KO(memo)")
    if problem:
        return problem
    if not pf.ok:
        return doc_review(rid, key, pf, mapped, [ov])
    ok = mapped == pf.value

    def label(m: bool) -> str:
        return "記憶式" if m else "非記憶式"

    return result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=f"{ov.value}（{label(mapped)}）",
        actual=label(pf.value),
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
    )


def ki_type(ctx: Context) -> CheckResult:
    rid, key, pf = "field.ki_type", "ki_type", standard_field(ctx, "ki_type")
    mapped, ov, problem = _mapped(ctx, rid, key, pf, ctx.fmt.ki_type_values, "KI(Freq)")
    if problem:
        return problem
    doc = doc_ki(pf)
    if doc is None:
        return doc_review(rid, key, pf, mapped, [ov])
    if doc == "M":
        return result(
            rid,
            key,
            S.REVIEW_REQUIRED,
            expected=KI_LABEL[mapped],
            actual=KI_LABEL[doc],
            pf=pf,
            ov=[ov],
            reason="monthly_ki_unsupported",
            message="Monthly KI 尚無樣本，說明書判斷方式未確認，請人工覆核",
        )
    ok = mapped == doc
    return result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=f"{ov.value}（{KI_LABEL[mapped]}）",
        actual=KI_LABEL[doc],
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
    )


def ki_pct(ctx: Context) -> CheckResult:
    """KI(%)：說明書無 KI（由 KI 型態判定）時表上必須是空值寫法；有 KI 時數值相等。"""
    rid, key = "field.ki_pct", "ki_pct"
    pf, kt = standard_field(ctx, "ki_pct"), standard_field(ctx, "ki_type")
    empty = ctx.fmt.empty_value
    v, ov, problem = order_value(
        ctx, key, rid, key, pf, lambda x: x if x == empty else to_decimal(x), f"數字或 {empty}"
    )
    if problem:
        return problem
    doc = doc_ki(kt)
    if doc is None:
        return doc_review(rid, key, kt, v, [ov])
    if doc == "none":
        ok = v == empty
        return result(
            rid,
            key,
            S.NOT_APPLICABLE if ok else S.MISMATCH,
            expected=v,
            evidence=kt.evidence,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message="雙方皆無 KI（由說明書明確判定）" if ok else f"說明書無 KI；KI(%) 應為 {empty}",
        )
    if not pf.ok:
        return doc_review(rid, key, pf, v, [ov])
    if v == empty:
        return result(
            rid,
            key,
            S.MISMATCH,
            expected=v,
            actual=pf.value,
            pf=pf,
            ov=[ov],
            reason="value_mismatch",
            message="說明書有 KI，表上 KI(%) 卻是空值",
        )
    ok, shown = cmp_pct(v, pf.value)
    return result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=shown,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        tolerance=PCT_TOLERANCE,
    )


def underlying_prices(ctx: Context) -> list[CheckResult]:
    """各標的進場／執行／下限／KO 價：表上值四捨五入（half-up）到 4 位後與說明書價格列相等。"""
    rid = "field.underlying_prices"
    rows, uls = standard_field(ctx, "underlying_prices"), standard_field(ctx, "underlyings")
    if not rows.ok:
        return [doc_review(rid, "price_table", rows)]
    empty = ctx.fmt.empty_value
    out = []
    for i, row in enumerate(rows.value, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else f"第 {i} 檔標的"
        ev = list(row.evidence)
        for col, std, zh in PRICE_COLUMNS:
            field = f"{label} {zh}"
            ov = ctx.order.fields.get(f"underlying_{i}_{std}")
            if ov is None or ov.value is None:
                out.append(
                    order_review(rid, field, ov, None, "order_missing", f"{ctx.order.source}沒有此欄位或值為空白")
                )
                continue
            doc_v = row.prices.get(col)
            if doc_v is None:  # 無 KI：價格表沒有下限價欄
                ok = ov.value == empty
                out.append(
                    result(
                        rid,
                        field,
                        S.NOT_APPLICABLE if ok else S.MISMATCH,
                        expected=ov.value,
                        evidence=ev,
                        ov=[ov],
                        reason="" if ok else "value_mismatch",
                        message="說明書無 KI，沒有下限價" + ("" if ok else f"；表上應為 {empty}"),
                    )
                )
                continue
            v = to_decimal(ov.value)
            if v is None:
                out.append(order_review(rid, field, ov, None, "order_invalid", f"{ctx.order.source}的值不是數字"))
                continue
            shown = v.quantize(Q4, ROUND_HALF_UP)
            ok = shown == doc_v
            out.append(
                result(
                    rid,
                    field,
                    S.PASS if ok else S.MISMATCH,
                    expected=shown,
                    actual=doc_v,
                    evidence=ev,
                    ov=[ov],
                    reason="" if ok else "value_mismatch",
                    tolerance="表上值四捨五入（half-up）到 4 位",
                )
            )
    return out


def field_rules(ctx: Context) -> list[CheckResult]:
    """表上作業人員事先填好的欄位逐一與說明書標準欄位比對（Non-Call 與回填欄位見下方）。"""
    dec, intg, date = to_decimal, to_int, to_date
    return [
        product_code(ctx),
        currency(ctx),
        underlyings(ctx),
        _compare(ctx, "field.strike_pct", "strike_pct", dec, "數字", cmp_pct, PCT_TOLERANCE),
        _compare(ctx, "field.ko_pct", "ko_pct", dec, "數字", cmp_pct, PCT_TOLERANCE),
        _compare(ctx, "field.coupon_pa_pct", "coupon_pa_pct", dec, "數字", cmp_pct, PCT_TOLERANCE),
        _compare(ctx, "field.tenor_months", "tenor_months", intg, "整數"),
        _compare(ctx, "field.trade_date", "trade_date", date, "日期"),
        _compare(ctx, "field.issue_date", "issue_date", date, "日期"),
        _compare(ctx, "field.final_valuation_date", "final_valuation_date", date, "日期"),
        _compare(ctx, "field.maturity_date", "maturity_date", date, "日期"),
        _compare(ctx, "field.denomination", "denomination", intg, "整數"),
        ko_observation(ctx),
        ko_memory(ctx),
        ki_type(ctx),
        ki_pct(ctx),
        *underlying_prices(ctx),
    ]


# ---------------------------------------------------------------- Non-Call 與回填欄位


@dataclass(frozen=True)
class CellDecision:
    """回填欄位的一格：fill = 空白、核對通過後回填；match = 已有相同值；mismatch = 已有不同值，保留原值。"""

    column: str  # Excel 欄名
    cell: str  # 儲存格位置，例 F4
    sheet_value: Any
    expected: Any  # 日期、空值寫法或 ISIN
    action: str


def _column(fmt: ReferenceFormat, std: str) -> str:
    return next(h for h, s in fmt.columns.items() if s == std)


def _decide(fmt: ReferenceFormat, row: ReferenceRow, std: str, expected: Any) -> CellDecision:
    ov = row.fields.get(std)
    value = ov.value if ov else None
    if value is None:
        action = "fill"
    elif value == expected:
        action = "match"
    else:
        action = "mismatch"
    return CellDecision(_column(fmt, std), row.cells.get(std, "?"), value, expected, action)


def expected_slots(sched: AutocallSchedule, tenor: int | None, empty: str) -> list[Any] | str:
    """12 格比價日的期望值（不需要填的期別為空值寫法 `empty`）；無法對應格子時回傳原因。"""
    if tenor is not None and sched.periods != tenor:
        return f"說明書提前出場表有 {sched.periods} 期，與天期 {tenor} 個月不同，比價日無法對應到格子"
    if sched.periods > SLOTS:
        return f"說明書有 {sched.periods} 期，超過參考條件表的 {SLOTS} 格比價日"
    if not 1 <= sched.first_callable <= sched.periods:
        return f"第一個可提前出場的期別（{sched.first_callable}）不在第 1～{sched.periods} 期之間"
    last = sched.first_callable if sched.observation == "D" else sched.periods
    callable_ = range(sched.first_callable, last + 1)
    missing = [n for n in callable_ if n not in sched.dates]
    if missing:
        return "說明書抓不到以下期別的比價日：" + "、".join(f"第 {n} 期" for n in missing)
    return [sched.dates[n] if n in callable_ else empty for n in range(1, SLOTS + 1)]


def first_callable_period(ctx: Context, sched: ParsedField) -> CheckResult:
    rid, key = "field.first_callable_period", "first_callable_period"
    v, ov, problem = order_value(ctx, key, rid, key, sched, to_int, "整數")
    if problem:
        return problem
    if not sched.ok:
        return doc_review(rid, key, sched, v, [ov])
    s: AutocallSchedule = sched.value
    ok = v == s.first_callable
    return result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=v,
        actual=s.first_callable,
        pf=sched,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message="Non-Call(月) = 第一個可以提前出場的期別（最小為 1）",
    )


def _fill_message(decisions: list[CellDecision]) -> str:
    n = sum(d.action == "fill" for d in decisions)
    return f"表上空白 {n} 格，整份核對通過後回填" if n else ""


def isin(
    ctx: Context, fmt: ReferenceFormat, row: ReferenceRow, pf: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    rid, key = "backfill.isin", "isin"
    ov: OrderValue | None = row.fields.get(key)
    if not pf.ok:
        return doc_review(rid, key, pf, ov.value if ov else None, [ov]), []
    d = _decide(fmt, row, key, pf.value)
    ok = d.action != "mismatch"
    return (
        result(
            rid,
            key,
            S.PASS if ok else S.MISMATCH,
            expected=d.sheet_value,
            actual=pf.value,
            pf=pf,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message=_fill_message([d]) or ("" if ok else "表上 ISIN 與說明書不同，保留原值"),
        ),
        [d],
    )


def compare_dates(
    ctx: Context, fmt: ReferenceFormat, row: ReferenceRow, sched: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    rid, key = "backfill.compare_dates", "compare_dates"
    ovs = [row.fields.get(f"autocall_date_{n}") for n in range(1, SLOTS + 1)]
    if not sched.ok:
        return doc_review(rid, key, sched, None, ovs), []
    tenor = standard_field(ctx, "tenor_months")
    slots = expected_slots(sched.value, tenor.value if tenor.ok else None, fmt.empty_value)
    if isinstance(slots, str):
        return (
            result(rid, key, S.REVIEW_REQUIRED, pf=sched, ov=ovs, reason="compare_dates_unmapped", message=slots),
            [],
        )
    decisions = [_decide(fmt, row, f"autocall_date_{n}", e) for n, e in enumerate(slots, start=1)]
    bad = [d for d in decisions if d.action == "mismatch"]
    rule = "D：只填第一個可提前出場期" if sched.value.observation == "D" else "P：第一個可提前出場期起每期都填"
    return (
        result(
            rid,
            key,
            S.MISMATCH if bad else S.PASS,
            expected={d.column: d.sheet_value for d in bad} or None,
            actual={d.column: d.expected for d in bad} or [d.expected for d in decisions],
            pf=sched,
            ov=ovs,
            reason="value_mismatch" if bad else "",
            message="；".join(x for x in (rule, _fill_message(decisions)) if x)
            + ("；表上值與說明書不同的格子保留原值" if bad else ""),
        ),
        decisions,
    )
