"""參考條件表的共用規則：表上事先填好的欄位與 Non-Call(月) 的核對。

各上手只交出說明書標準欄位（standard_fields.py）與提前出場排程；比對在這裡實作一次，所有上手共用。
空值寫法一律取自參考條件表格式設定（`empty_value`）。回填欄位（ISIN、發行日、比價日）見 backfill.py。
項目名稱：讀到參考條件表某一欄時就是該欄的 Excel 欄名（`Item.column`），沒有這欄時用規則旁寫的中文名稱。
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_UP
from typing import Any

from ..orders.reference import OrderRecord
from ..schema import CheckResult, Item, OrderValue, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import AutocallSchedule
from .kit import (
    KI_LABEL,
    Q4,
    Context,
    cmp_pct,
    doc_ki,
    doc_review,
    occurrences_of,
    order_review,
    order_value,
    price_item,
    result,
    standard_field,
    to_date,
    to_decimal,
    to_int,
)

__all__ = ["AutocallSchedule", "column_checks", "field_rules", "first_callable_period"]

PCT_TOLERANCE = "依說明書顯示位數四捨五入後比對"
OBS_LABEL = {"D": "期間每日觀察", "P": "期末定日觀察"}
UNDERLYINGS = "標的"  # UL_1～UL_5 合起來核對，項目用這個名稱
PRICE_COLUMNS = (  # 價格列的鍵、標準欄位、核對結果欄位用的中文
    ("initial", "initial_price", "進場價"),
    ("strike", "strike_price", "執行價"),
    ("ki", "ki_price", "下限價"),
    ("ko", "ko_price", "KO 價"),
)


# ---------------------------------------------------------------- 表上事先填好的欄位


def _compare(
    ctx: Context,
    rule_id: str,
    key: str,
    name: str,
    convert: Callable[[Any], Any],
    what: str,
    compare: Callable[[Any, Any], tuple[bool, Any]] | None = None,
    tolerance: str | None = None,
) -> CheckResult:
    """表上欄位與同名標準欄位比對；預設相等，`compare` 回傳（是否一致, 顯示的預期值）。"""
    pf = standard_field(ctx, key)
    v, ov, problem = order_value(ctx, key, rule_id, key, pf, convert, what, name=name)
    if problem:
        return problem
    item = Item.column(name, [ov])
    if not pf.ok:
        return doc_review(rule_id, key, pf, v, [ov], item=item)
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
        item=item,
    )


def product_code(ctx: Context) -> CheckResult:
    rid, pf, ov, name = "field.product_code", standard_field(ctx, "product_code"), ctx.order.product_code, "商品代號"
    if ov.value is None:
        return order_review(rid, "product_code", ov, pf, "order_missing", f"{ctx.order.source}沒有商品代號", name=name)
    item = Item.column(name, [ov])
    if not pf.ok:
        return doc_review(rid, "product_code", pf, ov.value, [ov], item=item)
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
        item=item,
    )


def currency(ctx: Context) -> CheckResult:
    rid, name = "field.currency", "幣別"
    pf = standard_field(ctx, "currency_zh")
    v, ov, problem = order_value(
        ctx, "currency", rid, "currency", pf, lambda x: x if isinstance(x, str) else None, "文字", name=name
    )
    if problem:
        return problem
    item = Item.column(name, [ov])
    if not pf.ok:
        return doc_review(rid, "currency", pf, v, [ov], item=item)
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
            item=item,
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
        item=item,
    )


def underlyings(ctx: Context) -> CheckResult:
    rid, pf, empty = "field.underlyings", standard_field(ctx, "underlyings"), ctx.fmt.empty_value
    ovs = [ctx.order.fields.get(f"underlying_{i}") for i in range(1, 6)]
    values = [o.value if o and o.value != empty else None for o in ovs]  # 空值寫法表示沒有這檔標的
    filled = [i for i, v in enumerate(values) if v is not None]
    if not filled:
        return order_review(
            rid, "underlyings", ovs[0], pf, "order_missing", f"{ctx.order.source}沒有任何標的代號", name=UNDERLYINGS
        )
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
            item=Item.group(UNDERLYINGS, ovs),
        )
    tickers = [str(values[i]) for i in filled]
    used = [ovs[i] for i in filled]
    item = Item.group(UNDERLYINGS, used)
    if not pf.ok:
        return doc_review(rid, "underlyings", pf, tickers, used, item=item)
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
        item=item,
    )


def _mapped(
    ctx: Context, rid: str, key: str, pf: ParsedField, values: dict[str, Any], column: str, name: str
) -> tuple[Any, OrderValue | None, CheckResult | None]:
    text = lambda x: x if isinstance(x, str) else None  # noqa: E731
    v, ov, problem = order_value(ctx, key, rid, key, pf, text, "文字", name=name)
    if problem:
        return None, ov, problem
    if v not in values:
        message = f"{column}「{v}」不在格式設定的允許值內"
        return None, ov, order_review(rid, key, ov, pf, "order_unknown_value", message, name=name)
    return values[v], ov, None


def ko_observation(ctx: Context) -> CheckResult:
    rid, key, pf, name = "field.ko_observation", "ko_observation", standard_field(ctx, "ko_observation"), "KO 觀察方式"
    mapped, ov, problem = _mapped(ctx, rid, key, pf, ctx.fmt.ko_observation_values, "KO(Freq)", name)
    if problem:
        return problem
    item = Item.column(name, [ov])
    if not pf.ok:
        return doc_review(rid, key, pf, mapped, [ov], item=item)
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
        item=item,
    )


def ko_memory(ctx: Context) -> CheckResult:
    rid, key, pf, name = "field.ko_memory", "ko_memory", standard_field(ctx, "ko_memory"), "記憶式"
    mapped, ov, problem = _mapped(ctx, rid, key, pf, ctx.fmt.ko_memory_values, "KO(memo)", name)
    if problem:
        return problem
    item = Item.column(name, [ov])
    if not pf.ok:
        return doc_review(rid, key, pf, mapped, [ov], item=item)
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
        item=item,
    )


def ki_type(ctx: Context) -> CheckResult:
    rid, key, pf, name = "field.ki_type", "ki_type", standard_field(ctx, "ki_type"), "KI 型態"
    mapped, ov, problem = _mapped(ctx, rid, key, pf, ctx.fmt.ki_type_values, "KI(Freq)", name)
    if problem:
        return problem
    item = Item.column(name, [ov])
    doc = doc_ki(pf)
    if doc is None:
        return doc_review(rid, key, pf, mapped, [ov], item=item)
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
            item=item,
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
        item=item,
    )


def ki_pct(ctx: Context) -> CheckResult:
    """KI(%)：說明書無 KI（由 KI 型態判定）時表上必須是空值寫法；有 KI 時數值相等。"""
    rid, key, name = "field.ki_pct", "ki_pct", "KI %"
    pf, kt = standard_field(ctx, "ki_pct"), standard_field(ctx, "ki_type")
    empty = ctx.fmt.empty_value
    v, ov, problem = order_value(
        ctx, key, rid, key, pf, lambda x: x if x == empty else to_decimal(x), f"數字或 {empty}", name=name
    )
    if problem:
        return problem
    item = Item.column(name, [ov])
    doc = doc_ki(kt)
    if doc is None:
        return doc_review(rid, key, kt, v, [ov], item=item)
    if doc == "none":
        ok = v == empty
        return result(
            rid,
            key,
            S.NOT_APPLICABLE if ok else S.MISMATCH,
            expected=v,
            actual=None if ok else KI_LABEL["none"],
            evidence=kt.evidence,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message="雙方皆無 KI（由說明書明確判定）" if ok else f"說明書無 KI；KI(%) 應為 {empty}",
            item=item,
        )
    if not pf.ok:
        return doc_review(rid, key, pf, v, [ov], item=item)
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
            item=item,
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
        item=item,
    )


def underlying_prices(ctx: Context) -> list[CheckResult]:
    """各標的進場／執行／下限／KO 價：表上值四捨五入（half-up）到 4 位後與說明書價格列相等。"""
    rid = "field.underlying_prices"
    rows, uls = standard_field(ctx, "underlying_prices"), standard_field(ctx, "underlyings")
    if not rows.ok:
        return [doc_review(rid, "price_table", rows, item=Item.sheet("價格表"))]
    empty = ctx.fmt.empty_value
    out = []
    for i, row in enumerate(rows.value, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else f"第 {i} 檔標的"
        ev = list(row.evidence)
        for col, std, zh in PRICE_COLUMNS:
            field, name = f"{label} {zh}", price_item(i, col)
            ov = ctx.order.fields.get(f"underlying_{i}_{std}")
            if ov is None or ov.value is None:
                message = f"{ctx.order.source}沒有此欄位或值為空白"
                out.append(order_review(rid, field, ov, None, "order_missing", message, name=name))
                continue
            item = Item.column(name, [ov])
            doc_v = row.prices.get(col)
            if doc_v is None:  # 無 KI：價格表沒有下限價欄
                ok = ov.value == empty
                out.append(
                    result(
                        rid,
                        field,
                        S.NOT_APPLICABLE if ok else S.MISMATCH,
                        expected=ov.value,
                        actual=None if ok else "無下限價（無 KI）",
                        evidence=ev,
                        ov=[ov],
                        reason="" if ok else "value_mismatch",
                        message="說明書無 KI，沒有下限價" + ("" if ok else f"；表上應為 {empty}"),
                        item=item,
                    )
                )
                continue
            v = to_decimal(ov.value)
            if v is None:
                message = f"{ctx.order.source}的值不是數字"
                out.append(order_review(rid, field, ov, None, "order_invalid", message, name=name))
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
                    item=item,
                )
            )
    return out


def min_amounts(ctx: Context) -> list[CheckResult]:
    """說明書各最低金額出處（最低交易／申購／加購／贖回金額）= 參考條件表「單位面額」。"""
    rid = "field.min_amounts"
    items, problem = occurrences_of(ctx, rid, "min_amounts", Item.sheet("最低金額"))
    if problem:
        return [problem]
    out = []
    for occ in items:
        pf = occ.value
        v, ov, problem = order_value(ctx, "denomination", rid, occ.field, pf, to_int, "整數", name=occ.name)
        if problem:
            out.append(problem)
            continue
        item = Item.sheet(occ.name, [ov])  # 各出處分開寫（例：最低申購金額），不寫成「單位面額」
        if not pf.ok:
            out.append(doc_review(rid, occ.field, pf, v, [ov], item=item))
            continue
        ok = pf.value == v
        out.append(
            result(
                rid,
                occ.field,
                S.PASS if ok else S.MISMATCH,
                expected=v,
                actual=pf.value,
                pf=pf,
                ov=[ov],
                reason="" if ok else "value_mismatch",
                message=f"{occ.where}須等於參考條件表「單位面額」",
                item=item,
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
        _compare(ctx, "field.strike_pct", "strike_pct", "執行 %", dec, "數字", cmp_pct, PCT_TOLERANCE),
        _compare(ctx, "field.ko_pct", "ko_pct", "KO %", dec, "數字", cmp_pct, PCT_TOLERANCE),
        _compare(ctx, "field.coupon_pa_pct", "coupon_pa_pct", "年利率 %", dec, "數字", cmp_pct, PCT_TOLERANCE),
        _compare(ctx, "field.tenor_months", "tenor_months", "天期（月）", intg, "整數"),
        _compare(ctx, "field.trade_date", "trade_date", "交易日", date, "日期"),
        _compare(ctx, "field.final_valuation_date", "final_valuation_date", "最終評價日", date, "日期"),
        _compare(ctx, "field.maturity_date", "maturity_date", "到期日", date, "日期"),
        _compare(ctx, "field.denomination", "denomination", "面額", intg, "整數"),
        *min_amounts(ctx),
        ko_observation(ctx),
        ko_memory(ctx),
        ki_type(ctx),
        ki_pct(ctx),
        *underlying_prices(ctx),
    ]


# ---------------------------------------------------------------- Non-Call


def first_callable_period(ctx: Context) -> CheckResult:
    rid, key, sched = "field.first_callable_period", "first_callable_period", standard_field(ctx, "autocall_schedule")
    name = "第一個可提前出場期"
    v, ov, problem = order_value(ctx, key, rid, key, sched, to_int, "整數", name=name)
    if problem:
        return problem
    item = Item.column(name, [ov])
    if not sched.ok:
        return doc_review(rid, key, sched, v, [ov], item=item)
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
        item=item,
    )


# ---------------------------------------------------------------- 表頭欄位


def column_checks(order: OrderRecord) -> list[CheckResult]:
    """表頭欄名的問題：只寫說明，不附雙方值。"""
    src = order.source
    item = Item.note(f"{src}表頭")
    out = []
    for col in order.unknown_columns:
        out.append(
            result(
                "order.unknown_column",
                f"{src}欄位",
                S.REVIEW_REQUIRED,
                expected=None,
                actual=None,
                ov=[col],
                reason="order_unknown_column",
                message=f"{src}出現格式設定沒有的欄位「{col.value}」，格式可能已改版",
                item=item,
            )
        )
    for col in order.duplicate_columns:
        out.append(
            result(
                "order.duplicate_column",
                f"{src}欄位",
                S.REVIEW_REQUIRED,
                ov=[col],
                reason="order_duplicate_column",
                message=f"{src}欄位「{col.value}」重複出現，無法確定以哪一欄為準",
                item=item,
            )
        )
    for name in order.missing_columns:
        out.append(
            result(
                "order.missing_column",
                f"{src}欄位",
                S.REVIEW_REQUIRED,
                reason="order_missing_column",
                message=f"{src}缺少格式設定中的欄位「{name}」",
                item=item,
            )
        )
    return out
