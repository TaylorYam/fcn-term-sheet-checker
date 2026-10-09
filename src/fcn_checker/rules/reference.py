"""參考條件表的共用規則：表上事先填好的欄位與 Non-Call(月) 的核對。

各上手只交出說明書標準欄位（standard_fields.py）與提前出場排程；比對在這裡實作一次，所有上手共用。
單欄比對（表上一欄 = 同名標準欄位）宣告在欄位核對表 `FIELD_CHECKS`，只宣告一次：說明書整張執行，投資人須知依範本挑選。
空值寫法一律取自參考條件表格式設定（`empty_value`）。回填欄位（TS、IIS、ISIN、發行日、比價日）見 backfill.py。
說明書明確判定不適用的標準欄位（例：MS 範本沒有年利率、Non-Call = 天期時沒有 KO 價）不核對，結果為不適用並附說明。
項目名稱：讀到參考條件表某一欄時就是該欄的 Excel 欄名（`Item.column`），沒有這欄時用規則旁寫的中文名稱。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP
from typing import Any

from ..orders.reference import UNDERLYING_SLOTS, OrderRecord
from ..schema import CheckResult, FieldStatus, Item, OrderValue, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import AutocallSchedule
from .kit import (
    KI_LABEL,
    Q4,
    Context,
    cmp_pct,
    doc_ki,
    doc_not_applicable,
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

__all__ = [
    "FIELD_CHECKS",
    "AutocallSchedule",
    "FieldCheck",
    "column_checks",
    "field_rules",
    "first_callable_period",
    "is_vwap",
]

PCT_TOLERANCE = "依說明書顯示位數四捨五入後比對"
OBS_LABEL = {"D": "期間每日觀察", "P": "期末定日觀察"}
UNDERLYINGS = "標的"  # UL_1～UL_5 合起來核對，項目用這個名稱
PRICE_COLUMNS = (  # 價格欄（`OrderRecord.price` 的鍵）與核對結果欄位用的中文
    ("initial", "進場價"),
    ("strike", "執行價"),
    ("ki", "下限價"),
    ("ko", "KO 價"),
)
VWAP = "vwap"  # 期初定價為 VWAP：價格欄不比對，核對通過後以說明書覆寫（Issue #122）


# ---------------------------------------------------------------- 表上事先填好的欄位：欄位核對表


@dataclass(frozen=True)
class FieldCheck:
    """表上一欄與同名說明書標準欄位的比對宣告：怎麼轉型、怎麼比、容差多少。

    預設相等；`compare` 回傳（是否一致, 顯示的預期值）。說明書判定不適用時不比對。
    """

    rule_id: str
    key: str  # 標準欄位（參考條件表與說明書同名）
    name: str  # 表上沒有這欄時的項目名稱（有這欄時項目是 Excel 欄名）
    convert: Callable[[Any], Any]
    what: str  # 轉型失敗時錯訊寫的型別（數字／整數／日期）
    compare: Callable[[Any, Any], tuple[bool, Any]] | None = None
    tolerance: str | None = None

    def check(self, ctx: Context) -> CheckResult:
        rule_id, key, name = self.rule_id, self.key, self.name
        pf = standard_field(ctx, key)
        if pf.status == FieldStatus.NOT_APPLICABLE:
            ov = ctx.sheet_field(key)
            return doc_not_applicable(rule_id, key, pf, [ov], item=Item.column(name, [ov]), document=ctx.document)
        v, ov, problem = order_value(ctx, key, rule_id, key, pf, self.convert, self.what, name=name)
        if problem:
            return problem
        item = Item.column(name, [ov])
        if not pf.ok:
            return doc_review(rule_id, key, pf, v, [ov], item=item, document=ctx.document)
        ok, shown = self.compare(v, pf.value) if self.compare else (v == pf.value, v)
        return result(
            rule_id,
            key,
            S.PASS if ok else S.MISMATCH,
            expected=shown,
            actual=pf.value,
            pf=pf,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            tolerance=self.tolerance,
            item=item,
        )


def _pct(rule_id: str, key: str, name: str) -> FieldCheck:
    return FieldCheck(rule_id, key, name, to_decimal, "數字", cmp_pct, PCT_TOLERANCE)


# 單欄比對的欄位核對表，只宣告這一次：說明書（`field_rules`）整張依序執行，投資人須知（rules/iis.py）依範本
# `provides` 挑選。需要看別的欄位或格式設定才能比的欄位（商品代號、幣別、標的、KO／KI 型態、KI %）仍是下方的函式。
FIELD_CHECKS: dict[str, FieldCheck] = {
    c.key: c
    for c in (
        _pct("field.strike_pct", "strike_pct", "執行 %"),
        _pct("field.ko_pct", "ko_pct", "KO %"),
        _pct("field.coupon_pa_pct", "coupon_pa_pct", "年利率 %"),
        FieldCheck("field.tenor_months", "tenor_months", "天期（月）", to_int, "整數"),
        FieldCheck("field.trade_date", "trade_date", "交易日", to_date, "日期"),
        FieldCheck("field.final_valuation_date", "final_valuation_date", "最終評價日", to_date, "日期"),
        FieldCheck("field.maturity_date", "maturity_date", "到期日", to_date, "日期"),
        FieldCheck("field.denomination", "denomination", "面額", to_int, "整數"),
    )
}


# ---------------------------------------------------------------- 表上事先填好的欄位：要看別的欄位或格式設定的規則


def product_code(ctx: Context) -> CheckResult:
    rid, pf, ov, name = "field.product_code", standard_field(ctx, "product_code"), ctx.order.product_code, "商品代號"
    if ov.value is None:
        return order_review(rid, "product_code", ov, pf, "order_missing", f"{ctx.order.source}沒有商品代號", name=name)
    item = Item.column(name, [ov])
    if not pf.ok:
        return doc_review(rid, "product_code", pf, ov.value, [ov], item=item, document=ctx.document)
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
        message="" if ok else f"{ctx.document}與{ctx.order.source}的商品代號不同，可能拿錯檔案",
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
        return doc_review(rid, "currency", pf, v, [ov], item=item, document=ctx.document)
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
            message=f"{ctx.document}幣別「{pf.value}」不在審查標準的幣別對照表",
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
        message=f"{ctx.document}：{pf.value} → {iso}",
        item=item,
    )


def underlyings(ctx: Context) -> CheckResult:
    rid, pf, empty = "field.underlyings", standard_field(ctx, "underlyings"), ctx.fmt.empty_value
    ovs = [ctx.order.underlying(i) for i in range(1, UNDERLYING_SLOTS + 1)]
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
        return doc_review(rid, "underlyings", pf, tickers, used, item=item, document=ctx.document)
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
    ctx: Context, rid: str, key: str, pf: ParsedField | None, values: dict[str, Any], column: str, name: str
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
        return doc_review(rid, key, pf, mapped, [ov], item=item, document=ctx.document)
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
        return doc_review(rid, key, pf, mapped, [ov], item=item, document=ctx.document)
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
        return doc_review(rid, key, pf, mapped, [ov], item=item, document=ctx.document)
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
            message=f"Monthly KI 尚無樣本，{ctx.document}判斷方式未確認，請人工覆核",
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
        return doc_review(rid, key, kt, v, [ov], item=item, document=ctx.document)
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
            message=f"雙方皆無 KI（由{ctx.document}明確判定）" if ok else f"{ctx.document}無 KI；KI(%) 應為 {empty}",
            item=item,
        )
    if not pf.ok:
        return doc_review(rid, key, pf, v, [ov], item=item, document=ctx.document)
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
            message=f"{ctx.document}有 KI，表上 KI(%) 卻是空值",
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


def is_vwap(ctx: Context) -> bool:
    """參考條件表的期初定價是 VWAP（空白或不在允許值內都不算，價格欄照常比對）。"""
    ov = ctx.order.get("initial_pricing")
    return ov is not None and ctx.fmt.initial_pricing_values.get(ov.value) == VWAP


def initial_pricing(ctx: Context) -> CheckResult:
    """期初定價：說明書沒有對應資料，只檢查表上是允許值（開盤價／收盤價／VWAP）。"""
    rid, key, name = "field.initial_pricing", "initial_pricing", "期初定價"
    mapped, ov, problem = _mapped(ctx, rid, key, None, ctx.fmt.initial_pricing_values, name, name)
    if problem:
        return problem
    if mapped == VWAP:
        message = "VWAP：期初價格以上手報的為準，表上各標的價格不比對，核對通過後以說明書覆寫"
    else:
        message = "表上各標的價格與說明書比對"
    return result(rid, key, S.PASS, expected=ov.value, ov=[ov], message=message, item=Item.column(name, [ov]))


def underlying_prices(ctx: Context) -> list[CheckResult]:
    """各標的進場／執行／下限／KO 價：表上值四捨五入（half-up）到 4 位後與說明書價格列相等。

    期初定價為 VWAP 時不比對（改由回填規則 `backfill.underlying_prices` 覆寫）。說明書判定 KO 價不適用
    （`ko_pct` 不適用，例：MS Non-Call = 天期時價格表沒有 KO 欄）時各標的 KO 價不比對。
    """
    if is_vwap(ctx):
        return []
    rid = "field.underlying_prices"
    rows, uls = standard_field(ctx, "underlying_prices"), standard_field(ctx, "underlyings")
    if not rows.ok:
        return [doc_review(rid, "price_table", rows, item=Item.sheet("價格表"), document=ctx.document)]
    empty, ko = ctx.fmt.empty_value, standard_field(ctx, "ko_pct")
    out = []
    for i, row in enumerate(rows.value, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else f"第 {i} 檔標的"
        ev = list(row.evidence)
        for col, zh in PRICE_COLUMNS:
            field, name = f"{label} {zh}", price_item(i, col)
            ov = ctx.order.price(i, col)
            if col == "ko" and "ko" not in row.prices:  # 價格表沒有 KO 價：說明書判定不適用才不比對
                item = Item.column(name, [ov])
                if ko.status == FieldStatus.NOT_APPLICABLE:
                    out.append(doc_not_applicable(rid, field, ko, [ov], item=item, document=ctx.document))
                else:
                    missing = ko if not ko.ok else ParsedField.missing("ko_pct", "價格表沒有 KO 價")
                    out.append(
                        doc_review(
                            rid, field, missing, ov.value if ov else None, [ov], item=item, document=ctx.document
                        )
                    )
                continue
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
                        message=f"{ctx.document}無 KI，沒有下限價" + ("" if ok else f"；表上應為 {empty}"),
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
            out.append(doc_review(rid, occ.field, pf, v, [ov], item=item, document=ctx.document))
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
    return [
        product_code(ctx),
        currency(ctx),
        underlyings(ctx),
        *(check.check(ctx) for check in FIELD_CHECKS.values()),
        *min_amounts(ctx),
        ko_observation(ctx),
        ko_memory(ctx),
        ki_type(ctx),
        ki_pct(ctx),
        initial_pricing(ctx),
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
        return doc_review(rid, key, sched, v, [ov], item=item, document=ctx.document)
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
