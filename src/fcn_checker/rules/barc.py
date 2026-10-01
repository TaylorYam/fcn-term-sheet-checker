"""BARC 核對規則（docs/rules/barc-check-rules.md、docs/rules/review-standard.md）。

規則只接收標準化後的說明書欄位、下單欄位與審查標準；不讀檔、不改來源值。
每條規則產生一或多筆 CheckResult；抓不到、歧義、未知值一律轉人工覆核，不猜值。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..config import OrderFormat, ReviewStandard
from ..orders.inquiry import OrderRecord
from ..parsers.barc import BarcTermSheet
from ..parsers.layout import squash
from ..schema import CheckResult, Evidence, FieldStatus, OrderValue, ParsedField
from ..schema import CheckStatus as S

Q4 = Decimal("0.0001")
MONTHLY_TOLERANCE = Decimal("0.0001")
OBS_LABEL = {"D": "期間每日觀察", "P": "期末定日觀察"}
KI_LABEL = {"none": "無 KI", "AM": "到期觀察", "D": "每日觀察", "M": "每月觀察（Monthly KI）"}
PRICE_LABEL = {"strike": "執行價", "ko": "KO 價", "ki": "下限價（觸及生效價）"}
PRICE_PCT_FIELD = {"strike": "strike_pct", "ko": "ko_pct", "ki": "ki_pct"}

# 第二階段或暫不核對的規則：列入報告「未涵蓋」區，不影響也不假裝通過
NOT_COVERED: list[dict[str, str]] = [
    {
        "rule_id": "schedule.coupon_dates",
        "description": "配息評價日／支付日表：期數 = 天期、逐期遞增、評價日 < 支付日（B1、B2）",
    },
    {"rule_id": "schedule.final_period", "description": "配息表末期評價日 = 最終評價日、末期支付日 = 到期日（A3、A4）"},
    {"rule_id": "schedule.autocall_dates", "description": "自動提前出場表日期與配息表的關係（C1–C4）"},
    {
        "rule_id": "field.guaranteed_periods",
        "description": "詢價表 Guaranteed Periods（保證配息期）vs 說明書提前出場表／§13(7) 文字",
    },
    {"rule_id": "field.observation_frequency", "description": "詢價表 Observation Frequency vs 說明書配息表期數"},
    {"rule_id": "doc.autocall_trigger_per_period", "description": "§13(7) 每期觸發百分比 = §15 定義句"},
    {"rule_id": "doc.scenario_price_table", "description": "§16(3) 情境分析重印價格表 = §15 價格表"},
    {"rule_id": "field.monthly_ki", "description": "Monthly KI（MKI）說明書判斷方式（尚無樣本）"},
    {"rule_id": "doc.min_subscription_redemption", "description": "第四章最低申購、最低贖回金額 = 面額"},
    {"rule_id": "doc.underlying_names", "description": "標的中文名稱與交易所（擱置，見核對規則 §6.2）"},
    {"rule_id": "field.isin", "description": "ISIN（詢價表沒有，暫不核對）"},
]


@dataclass
class Context:
    ts: BarcTermSheet
    order: OrderRecord
    std: ReviewStandard
    fmt: OrderFormat


# ---------------------------------------------------------------- 共用


def _result(
    rule_id: str,
    field: str,
    status: S,
    *,
    expected: Any = None,
    actual: Any = None,
    pf: ParsedField | None = None,
    ov: list[OrderValue | None] | None = None,
    reason: str = "",
    message: str = "",
    tolerance: str | None = None,
    evidence: list[Evidence] | None = None,
) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        field=field,
        status=status,
        expected=expected,
        actual=actual,
        tolerance=tolerance,
        reason_code=reason,
        message=message,
        document_evidence=list(evidence if evidence is not None else (pf.evidence if pf else [])),
        order_source=[o.source for o in (ov or []) if o is not None],
    )


_DOC_REASON = {
    FieldStatus.MISSING: ("document_missing", "說明書抓不到此欄位"),
    FieldStatus.AMBIGUOUS: ("document_ambiguous", "說明書出現多個不同的值"),
    FieldStatus.INVALID: ("document_invalid", "說明書的值無法辨識"),
}


def _doc_review(
    rule_id: str, field: str, pf: ParsedField, expected: Any = None, ov: list[OrderValue | None] | None = None
) -> CheckResult:
    reason, msg = _DOC_REASON.get(pf.status, ("document_not_applicable", "說明書判定此欄位不適用"))
    detail = f"：{pf.note}" if pf.note else ""
    actual = pf.candidates or None
    return _result(
        rule_id,
        field,
        S.REVIEW_REQUIRED,
        expected=expected,
        actual=actual,
        pf=pf,
        ov=ov,
        reason=reason,
        message=msg + detail,
    )


def _order_review(
    rule_id: str, field: str, ov: OrderValue | None, pf: ParsedField | None, reason: str, message: str
) -> CheckResult:
    return _result(
        rule_id,
        field,
        S.REVIEW_REQUIRED,
        expected=ov.value if ov else None,
        actual=pf.value if pf and pf.ok else None,
        pf=pf,
        ov=[ov],
        reason=reason,
        message=message,
    )


def _to_decimal(v: Any) -> Decimal | None:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, str):
        try:
            return Decimal(v.strip())
        except InvalidOperation:
            return None
    return None


def _to_int(v: Any) -> int | None:
    d = _to_decimal(v)
    if d is None or d != d.to_integral_value():
        return None
    return int(d)


def _to_date(v: Any) -> dt.date | None:
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else None


def _order_value(
    ctx: Context, key: str, rule_id: str, field: str, pf: ParsedField | None, convert: Callable[[Any], Any], what: str
) -> tuple[Any, OrderValue | None, CheckResult | None]:
    """取得並轉換下單欄位；缺漏或格式錯誤時回傳 REVIEW 結果。"""
    ov = ctx.order.fields.get(key)
    if ov is None or ov.value is None:
        return None, ov, _order_review(rule_id, field, ov, pf, "order_missing", "詢價表沒有此欄位或值為空白")
    v = convert(ov.value)
    if v is None:
        return None, ov, _order_review(rule_id, field, ov, pf, "order_invalid", f"詢價表的值不是{what}")
    return v, ov, None


def _cmp_pct(order_v: Decimal, doc_v: Decimal) -> tuple[bool, Decimal]:
    """百分比：下單值依說明書顯示位數四捨五入（half-up）後比對。"""
    exp = doc_v.as_tuple().exponent
    q = order_v.quantize(Decimal(1).scaleb(exp), ROUND_HALF_UP) if isinstance(exp, int) else order_v
    return q == doc_v, q


# ---------------------------------------------------------------- 範本與格式


def order_format_checks(order: OrderRecord) -> list[CheckResult]:
    out = []
    for col in order.unknown_columns:
        out.append(
            _result(
                "order.unknown_column",
                "詢價表欄位",
                S.REVIEW_REQUIRED,
                expected=None,
                actual=None,
                ov=[col],
                reason="order_unknown_column",
                message=f"詢價表出現格式設定沒有的欄位「{col.value}」，格式可能已改版",
            )
        )
    for col in order.duplicate_columns:
        out.append(
            _result(
                "order.duplicate_column",
                "詢價表欄位",
                S.REVIEW_REQUIRED,
                ov=[col],
                reason="order_duplicate_column",
                message=f"詢價表欄位「{col.value}」重複出現，無法確定以哪一欄為準",
            )
        )
    for name in order.missing_columns:
        out.append(
            _result(
                "order.missing_column",
                "詢價表欄位",
                S.REVIEW_REQUIRED,
                reason="order_missing_column",
                message=f"詢價表缺少格式設定中的欄位「{name}」",
            )
        )
    return out


# ---------------------------------------------------------------- 標準欄位比對


def _simple(
    ctx: Context,
    rule_id: str,
    key: str,
    convert: Callable[[Any], Any],
    what: str,
    compare: Callable[[Any, Any], tuple[bool, Any]] | None = None,
    tolerance: str | None = None,
    pf_key: str | None = None,
) -> CheckResult:
    pf = ctx.ts.f(pf_key or key)
    v, ov, problem = _order_value(ctx, key, rule_id, key, pf, convert, what)
    if problem:
        return problem
    if not pf.ok:
        return _doc_review(rule_id, key, pf, v, [ov])
    ok, shown = compare(v, pf.value) if compare else (v == pf.value, v)
    return _result(
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
    rid, pf, ov = "field.product_code", ctx.ts.f("product_code"), ctx.order.product_code
    if ov.value is None:
        return _order_review(rid, "product_code", ov, pf, "order_missing", "詢價表沒有商品代號")
    if not pf.ok:
        return _doc_review(rid, "product_code", pf, ov.value, [ov])
    ok = ov.value == pf.value
    return _result(
        rid,
        "product_code",
        S.PASS if ok else S.MISMATCH,
        expected=ov.value,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message="" if ok else "說明書與詢價表的商品代號不同，可能拿錯檔案",
    )


def currency(ctx: Context) -> CheckResult:
    rid = "field.currency"
    pf = ctx.ts.f("currency_zh")
    v, ov, problem = _order_value(
        ctx, "currency", rid, "currency", pf, lambda x: x if isinstance(x, str) else None, "文字"
    )
    if problem:
        return problem
    if not pf.ok:
        return _doc_review(rid, "currency", pf, v, [ov])
    iso = ctx.std.currency_zh_to_iso.get(pf.value)
    if iso is None:
        return _result(
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
    return _result(
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
    rid, pf = "field.underlyings", ctx.ts.f("underlyings")
    ovs = [ctx.order.fields.get(f"underlying_{i}") for i in range(1, 6)]
    values = [o.value if o else None for o in ovs]
    filled = [i for i, v in enumerate(values) if v is not None]
    if not filled:
        return _order_review(rid, "underlyings", ovs[0], pf, "order_missing", "詢價表沒有任何 BBG Code")
    if filled != list(range(len(filled))):
        return _result(
            rid,
            "underlyings",
            S.REVIEW_REQUIRED,
            expected=values,
            pf=pf,
            ov=ovs,
            reason="order_invalid",
            message="詢價表 BBG Code 中間有空白欄",
        )
    tickers = [str(values[i]) for i in filled]
    used = [ovs[i] for i in filled]
    if not pf.ok:
        return _doc_review(rid, "underlyings", pf, tickers, used)
    ok = tickers == pf.value
    msg = "" if ok else "彭博代號須依順序逐字相等（含交易所尾碼），數量也須相同"
    return _result(
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


def ko_type(ctx: Context) -> CheckResult:
    rid = "field.ko_type"
    obs, mem = ctx.ts.f("ko_observation"), ctx.ts.f("ko_memory")
    v, ov, problem = _order_value(
        ctx, "ko_type", rid, "ko_type", obs, lambda x: x if isinstance(x, str) else None, "文字"
    )
    if problem:
        return problem
    mapped = ctx.fmt.ko_type_values.get(v)
    if mapped is None:
        return _order_review(rid, "ko_type", ov, obs, "order_unknown_value", f"KO Type「{v}」不在格式設定的允許值內")
    for pf in (obs, mem):
        if not pf.ok:
            return _doc_review(rid, "ko_type", pf, v, [ov])
    exp = (mapped["observation"], bool(mapped["memory"]))
    act = (obs.value, mem.value)

    def label(t: tuple[str, bool]) -> str:
        return f"{OBS_LABEL[t[0]]}／{'記憶式' if t[1] else '非記憶式'}"

    ok = exp == act
    return _result(
        rid,
        "ko_type",
        S.PASS if ok else S.MISMATCH,
        expected=f"{v}（{label(exp)}）",
        actual=label(act),
        evidence=obs.evidence + mem.evidence,
        ov=[ov],
        reason="" if ok else "value_mismatch",
    )


def _order_ki(ctx: Context, rid: str, pf: ParsedField) -> tuple[str | None, OrderValue | None, CheckResult | None]:
    v, ov, problem = _order_value(
        ctx, "ki_type", rid, "ki_type", pf, lambda x: x if isinstance(x, str) else None, "文字"
    )
    if problem:
        return None, ov, problem
    mapped = ctx.fmt.ki_type_values.get(v)
    if mapped is None:
        return (
            None,
            ov,
            _order_review(rid, "ki_type", ov, pf, "order_unknown_value", f"Barrier Type「{v}」不在格式設定的允許值內"),
        )
    return mapped, ov, None


def _doc_ki(pf: ParsedField) -> str | None:
    if pf.status in (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE) and pf.value in KI_LABEL:
        return pf.value
    return None


def ki_type(ctx: Context) -> CheckResult:
    rid, pf = "field.ki_type", ctx.ts.f("ki_type")
    mapped, ov, problem = _order_ki(ctx, rid, pf)
    if problem:
        return problem
    doc = _doc_ki(pf)
    if doc is None:
        return _doc_review(rid, "ki_type", pf, mapped, [ov])
    if "M" in (mapped, doc):
        return _result(
            rid,
            "ki_type",
            S.REVIEW_REQUIRED,
            expected=KI_LABEL[mapped],
            actual=KI_LABEL[doc],
            pf=pf,
            ov=[ov],
            reason="monthly_ki_unsupported",
            message="Monthly KI 尚無樣本，說明書判斷方式未確認（第二階段），請人工覆核",
        )
    ok = mapped == doc
    return _result(
        rid,
        "ki_type",
        S.PASS if ok else S.MISMATCH,
        expected=f"{ov.value}（{KI_LABEL[mapped]}）",
        actual=KI_LABEL[doc],
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
    )


def ki_pct(ctx: Context) -> CheckResult:
    rid, pf, kt = "field.ki_pct", ctx.ts.f("ki_pct"), ctx.ts.f("ki_type")
    ov = ctx.order.fields.get("ki_pct")
    if ov is None:
        return _order_review(rid, "ki_pct", ov, pf, "order_missing", "詢價表沒有 KI Barrier 欄位")
    order_v = Decimal(0) if ov.value is None else _to_decimal(ov.value)
    if order_v is None:
        return _order_review(rid, "ki_pct", ov, pf, "order_invalid", "詢價表的值不是數字")
    doc = _doc_ki(kt)
    if doc is None:
        return _doc_review(rid, "ki_pct", kt, order_v, [ov])
    if doc == "none":
        ok = order_v == 0
        return _result(
            rid,
            "ki_pct",
            S.NOT_APPLICABLE if ok else S.MISMATCH,
            expected=order_v,
            actual=None,
            evidence=kt.evidence,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message="說明書無 KI；詢價表 KI Barrier 應為 0" if not ok else "雙方皆無 KI（由說明書明確判定）",
        )
    if not pf.ok:
        return _doc_review(rid, "ki_pct", pf, order_v, [ov])
    ok, shown = _cmp_pct(order_v, pf.value)
    return _result(
        rid,
        "ki_pct",
        S.PASS if ok else S.MISMATCH,
        expected=shown,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        tolerance="依說明書顯示位數四捨五入後比對",
    )


def issue_date_offset(ctx: Context) -> CheckResult:
    rid, key = "field.issue_date_offset_days", "issue_date_offset_days"
    trade, issue = ctx.ts.f("trade_date"), ctx.ts.f("issue_date")
    v, ov, problem = _order_value(ctx, key, rid, key, issue, _to_int, "整數")
    if problem:
        return problem
    for pf in (trade, issue):
        if not pf.ok:
            return _doc_review(rid, key, pf, v, [ov])
    days = (issue.value - trade.value).days
    ok = v == days
    return _result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=v,
        actual=days,
        evidence=trade.evidence + issue.evidence,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message="說明書發行日 − 交易日（日曆天）",
    )


# ---------------------------------------------------------------- 推算規則


def monthly_coupon(ctx: Context) -> CheckResult:
    """月配息率 = 年利率 × 天期 ÷ 12 ÷ 期數（期數 = 天期 ÷ 觀察頻率）；與說明書差 ≤ 0.0001 視為一致。"""
    rid, field = "derive.monthly_coupon", "monthly_coupon_pct"
    pf = ctx.ts.f(field)
    annual, ov_a, p1 = _order_value(ctx, "coupon_pa_pct", rid, field, pf, _to_decimal, "數字")
    tenor, ov_t, p2 = _order_value(ctx, "tenor_months", rid, field, pf, _to_int, "整數")
    freq, ov_f, p3 = _order_value(ctx, "observation_frequency_months", rid, field, pf, _to_int, "整數")
    for p in (p1, p2, p3):
        if p:
            return p
    if freq <= 0 or tenor % freq:
        return _result(
            rid,
            field,
            S.REVIEW_REQUIRED,
            pf=pf,
            ov=[ov_t, ov_f],
            reason="order_invalid",
            message="天期無法被觀察頻率整除，無法推算期數",
        )
    if not pf.ok:
        return _doc_review(rid, field, pf, None, [ov_a, ov_t, ov_f])
    periods = tenor // freq
    expected = (annual * tenor / 12 / periods).quantize(Q4, ROUND_HALF_UP)
    ok = abs(expected - pf.value) <= MONTHLY_TOLERANCE
    return _result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        pf=pf,
        ov=[ov_a, ov_t, ov_f],
        reason="" if ok else "value_mismatch",
        tolerance="≤ 0.0001",
        message=f"推算：{annual}% × {tenor} ÷ 12 ÷ {periods} 期，四捨五入到 4 位",
    )


def coupon_consistency(ctx: Context) -> list[CheckResult]:
    """月配息率在 §9(1)、§14、§15(2)、§17(1) 相同；年利率在 §9(1)、§14、§17(1) 相同。"""
    rid = "doc.coupon_consistency"
    out = []
    for kind, field in (("monthly", "monthly_coupon_pct"), ("annual", "coupon_pa_pct")):
        mentions = ctx.ts.coupon_mentions[kind]
        evidence = [Evidence.of(ln) for m in mentions for ln in m.lines]
        missing = [m.article for m in mentions if m.value.is_nan()]
        if missing:
            out.append(
                _result(
                    rid,
                    field,
                    S.REVIEW_REQUIRED,
                    evidence=evidence,
                    reason="document_missing",
                    message=f"以下條文找不到{'月配息率' if kind == 'monthly' else '年利率'}：{'、'.join(missing)}",
                )
            )
            continue
        values = sorted({m.value for m in mentions})
        where = {str(v): [m.article for m in mentions if m.value == v] for v in values}
        ok = len(values) == 1
        out.append(
            _result(
                rid,
                field,
                S.PASS if ok else S.MISMATCH,
                expected=None,
                actual=values[0] if ok else where,
                evidence=evidence,
                reason="" if ok else "document_inconsistent",
                message="" if ok else "說明書不同條文的值不一致",
            )
        )
    return out


def prices(ctx: Context) -> list[CheckResult]:
    """各標的執行／KO／下限價 = 最初價格 × 對應百分比，四捨五入（half-up）到 4 位。"""
    rid = "derive.prices"
    table, uls = ctx.ts.f("price_table"), ctx.ts.f("underlyings")
    if not table.ok:
        return [_doc_review(rid, "price_table", table)]
    rows = ctx.ts.price_rows
    if uls.ok and len(uls.value) != len(rows):
        return [
            _result(
                rid,
                "price_table",
                S.REVIEW_REQUIRED,
                expected=len(uls.value),
                actual=len(rows),
                pf=table,
                reason="price_table_row_count",
                message="價格表列數與標的數不同",
            )
        ]
    has_ki_col = all("ki" in r.values for r in rows)
    kt = ctx.ts.f("ki_type")
    doc_ki = _doc_ki(kt)
    if doc_ki is None:
        return [_doc_review(rid, "price_table", kt)]
    if (doc_ki != "none") != has_ki_col:
        return [
            _result(
                rid,
                "price_table",
                S.REVIEW_REQUIRED,
                pf=table,
                reason="price_table_ki_column",
                message="價格表有無觸及生效價格欄與 §15 定義不一致",
            )
        ]
    out = []
    for i, row in enumerate(rows):
        label = uls.value[i] if uls.ok else (row.name or f"第 {i + 1} 檔標的")
        ev = [Evidence.of(ln) for ln in row.lines]
        for col in ("strike", "ko", "ki"):
            if col not in row.values:
                continue
            field = f"{label} {PRICE_LABEL[col]}"
            pct = ctx.ts.f(PRICE_PCT_FIELD[col])
            if not pct.ok:
                out.append(_doc_review(rid, field, pct))
                continue
            expected = (row.values["initial"] * pct.value / 100).quantize(Q4, ROUND_HALF_UP)
            ok = expected == row.values[col]
            out.append(
                _result(
                    rid,
                    field,
                    S.PASS if ok else S.MISMATCH,
                    expected=expected,
                    actual=row.values[col],
                    evidence=ev + pct.evidence,
                    reason="" if ok else "value_mismatch",
                    tolerance="四捨五入（half-up）到 4 位",
                    message=f"最初價格 {row.values['initial']} × {pct.value}%",
                )
            )
    return out


# ---------------------------------------------------------------- 說明書內部規則


def denomination(ctx: Context) -> CheckResult:
    rid, pf, cz = "doc.denomination", ctx.ts.f("denomination"), ctx.ts.f("currency_zh")
    if not pf.ok:
        return _doc_review(rid, "denomination", pf)
    iso = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    default = ctx.std.denomination.get(iso) if iso else None
    if default is None:
        return _result(
            rid,
            "denomination",
            S.REVIEW_REQUIRED,
            actual=pf.value,
            pf=pf,
            reason="currency_unknown",
            message="無法確認幣別，找不到面額預設值",
        )
    ok = pf.value == default
    return _result(
        rid,
        "denomination",
        S.PASS if ok else S.REVIEW_REQUIRED,
        expected=default,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "denomination_non_default",
        message="" if ok else f"面額不是 {iso} 預設值；客戶可能要求特殊面額，請人工確認",
    )


def subscription_start(ctx: Context) -> CheckResult:
    rid, pf, trade = "doc.subscription_start_date", ctx.ts.f("subscription_start_date"), ctx.ts.f("trade_date")
    for f in (pf, trade):
        if not f.ok:
            return _doc_review(rid, "subscription_start_date", f)
    ok = pf.value == trade.value
    return _result(
        rid,
        "subscription_start_date",
        S.PASS if ok else S.MISMATCH,
        expected=trade.value,
        actual=pf.value,
        evidence=pf.evidence + trade.evidence,
        reason="" if ok else "value_mismatch",
        message="第四章商品開始受理申購日期須等於交易日",
    )


def print_date(ctx: Context) -> CheckResult:
    rid, pf, trade = "doc.print_date", ctx.ts.f("print_date"), ctx.ts.f("trade_date")
    for f in (pf, trade):
        if not f.ok:
            return _doc_review(rid, "print_date", f)
    limit = ctx.std.print_date_max_days_after_trade
    gap = (pf.value - trade.value).days
    ok = 0 <= gap <= limit
    return _result(
        rid,
        "print_date",
        S.PASS if ok else S.MISMATCH,
        expected=f"{trade.value} ～ {trade.value + dt.timedelta(days=limit)}",
        actual=pf.value,
        evidence=pf.evidence + trade.evidence,
        reason="" if ok else "value_mismatch",
        tolerance=f"交易日當天至交易日後 {limit} 天",
        message=f"刊印日期為交易日 {gap:+d} 天",
    )


# ---------------------------------------------------------------- 審查標準


def approval_date(ctx: Context) -> CheckResult:
    rid, pf = "standard.approval_date", ctx.ts.f("approval_date")
    if not pf.ok:
        return _doc_review(rid, "approval_date", pf, ctx.std.approval_date)
    ok = pf.value == ctx.std.approval_date
    return _result(
        rid,
        "approval_date",
        S.PASS if ok else S.MISMATCH,
        expected=ctx.std.approval_date,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        message="" if ok else "受託機構審查通過日期與審查標準不同（可能沿用舊審查日期）",
    )


def _codepoints(s: str) -> str:
    return " ".join(f"{c}U+{ord(c):04X}" for c in s)


def chairman(ctx: Context) -> CheckResult:
    rid, pf, exp = "standard.chairman", ctx.ts.f("chairman"), ctx.std.chairman
    if not pf.ok:
        return _doc_review(rid, "chairman", pf, exp)
    ok = pf.value == exp
    msg = "" if ok else f"須逐字（含字碼）相等：預期 {_codepoints(exp)}；說明書 {_codepoints(pf.value)}"
    return _result(
        rid,
        "chairman",
        S.PASS if ok else S.MISMATCH,
        expected=exp,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        message=msg,
    )


def fixed_warning(ctx: Context) -> CheckResult:
    rid = "standard.fixed_warning"
    ti = ctx.ts.full_text
    target = squash(ctx.std.fixed_warning)
    hits = [m for m in re.finditer(re.escape(target), ti.text)]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for m in hits]
    expected = ctx.std.fixed_warning_occurrences
    ok = len(hits) == expected
    return _result(
        rid,
        "fixed_warning",
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=len(hits),
        evidence=evidence,
        reason="" if ok else "occurrence_count",
        tolerance="忽略空白與換行後逐字相等",
        message=f"固定風險警語逐字相符 {len(hits)} 次，應為 {expected} 次"
        + ("" if ok else "（可能被改字、缺漏或多出）"),
    )


def risk_level(ctx: Context) -> CheckResult:
    rid = "standard.risk_level"
    ti = ctx.ts.full_text
    found = [(m.group(1), m) for m in re.finditer(r"【(RR\d)】", ti.text)]
    if not found:
        return _result(
            rid,
            "risk_level",
            S.REVIEW_REQUIRED,
            expected=ctx.std.risk_level,
            reason="document_missing",
            message="說明書找不到【RRn】風險等級",
        )
    bad = [(lv, m) for lv, m in found if lv != ctx.std.risk_level]
    shown = bad or found[:1]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for _, m in shown]
    ok = not bad
    return _result(
        rid,
        "risk_level",
        S.PASS if ok else S.MISMATCH,
        expected=ctx.std.risk_level,
        actual=sorted({lv for lv, _ in found}),
        evidence=evidence,
        reason="" if ok else "value_mismatch",
        message=f"全文共 {len(found)} 處【RRn】",
    )


def forbidden_wording(ctx: Context) -> CheckResult:
    rid = "standard.forbidden_wording"
    ti = ctx.ts.full_text
    text = ti.text
    for phrase in ctx.std.allowed_phrases:
        p = squash(phrase)
        text = text.replace(p, "□" * len(p))  # 遮蔽允許片語，保留字元位置
    hits = [m for w in ctx.std.forbidden for m in re.finditer(re.escape(squash(w)), text)]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for m in hits]
    ok = not hits
    return _result(
        rid,
        "forbidden_wording",
        S.PASS if ok else S.MISMATCH,
        expected=0,
        actual=len(hits),
        evidence=evidence,
        reason="" if ok else "forbidden_wording",
        message=""
        if ok
        else f"允許片語以外出現「{'、'.join(ctx.std.forbidden)}」{len(hits)} 處（SOP：須改為「受託買賣」）",
    )


_BRACKETS = str.maketrans({"(": "（", ")": "）"})


def product_name(ctx: Context) -> list[CheckResult]:
    """用說明書的天期、幣別、是否記憶式組出預期名稱後比對；中文名稱括號全半形不計。"""
    rid = "standard.product_name"
    tenor, cz, mem = ctx.ts.f("tenor_months"), ctx.ts.f("currency_zh"), ctx.ts.f("ko_memory")
    out = []
    for field in ("name_zh", "name_en"):
        pf = ctx.ts.f(field)
        bad = next((p for p in (pf, tenor, cz, mem) if not p.ok), None)
        if bad is not None:
            out.append(_doc_review(rid, field, bad))
            continue
        iso = ctx.std.currency_zh_to_iso.get(cz.value)
        if iso is None:
            out.append(
                _result(
                    rid,
                    field,
                    S.REVIEW_REQUIRED,
                    actual=pf.value,
                    pf=pf,
                    reason="currency_unknown",
                    message="幣別不在審查標準的對照表，無法組出預期名稱",
                )
            )
            continue
        if field == "name_zh":
            expected = ctx.std.name_zh.format(
                tenor=tenor.value, ccy_zh=cz.value, memory_zh=ctx.std.memory_zh if mem.value else ""
            )
            norm = (lambda s: squash(s).translate(_BRACKETS)) if ctx.std.normalize_brackets else squash
            tol = "忽略空白；全形／半形括號不計" if ctx.std.normalize_brackets else "忽略空白"
        else:
            expected = ctx.std.name_en.format(
                tenor=tenor.value, ccy=iso, memory_en=ctx.std.memory_en if mem.value else ""
            )
            norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
            tol = "連續空白視為一個"
        ok = norm(expected) == norm(pf.value)
        out.append(
            _result(
                rid,
                field,
                S.PASS if ok else S.MISMATCH,
                expected=expected,
                actual=pf.value,
                pf=pf,
                reason="" if ok else "value_mismatch",
                tolerance=tol,
                message="依審查標準名稱樣板與說明書天期、幣別、是否記憶式組出",
            )
        )
    return out


# ---------------------------------------------------------------- 入口


def run_all(ctx: Context) -> list[CheckResult]:
    dec, intg, date = _to_decimal, _to_int, _to_date
    pct_tol = "依說明書顯示位數四捨五入後比對"
    out: list[CheckResult] = [
        product_code(ctx),
        currency(ctx),
        underlyings(ctx),
        _simple(ctx, "field.strike_pct", "strike_pct", dec, "數字", _cmp_pct, pct_tol),
        _simple(ctx, "field.ko_pct", "ko_pct", dec, "數字", _cmp_pct, pct_tol),
        ko_type(ctx),
        ki_type(ctx),
        ki_pct(ctx),
        _simple(ctx, "field.coupon_pa_pct", "coupon_pa_pct", dec, "數字", _cmp_pct, pct_tol),
        _simple(ctx, "field.tenor_months", "tenor_months", intg, "整數"),
        _simple(ctx, "field.trade_date", "trade_date", date, "日期"),
        _simple(ctx, "field.issue_date", "issue_date", date, "日期"),
        _simple(ctx, "field.final_valuation_date", "final_valuation_date", date, "日期"),
        _simple(ctx, "field.maturity_date", "maturity_date", date, "日期"),
        issue_date_offset(ctx),
        monthly_coupon(ctx),
        *coupon_consistency(ctx),
        *prices(ctx),
        denomination(ctx),
        subscription_start(ctx),
        print_date(ctx),
        approval_date(ctx),
        chairman(ctx),
        fixed_warning(ctx),
        risk_level(ctx),
        forbidden_wording(ctx),
        *product_name(ctx),
    ]
    return out
