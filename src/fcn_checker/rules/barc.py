"""BARC 專屬核對規則（docs/rules/barc-check-rules.md）；共用規則見 rules/common.py。

規則只接收標準化後的說明書欄位、下單欄位與審查標準；不讀檔、不改來源值。
每條規則產生一或多筆 CheckResult；抓不到、歧義、未知值一律轉人工覆核，不猜值。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..parsers.barc import BarcTermSheet
from ..parsers.barc_schedule import NA, ScheduleRow, Table
from ..schema import CheckResult, Evidence, FieldStatus, OrderValue, ParsedField
from ..schema import CheckStatus as S
from . import common
from .common import (
    approval_date,
    chairman,
    cmp_pct,
    doc_review,
    fixed_warning,
    forbidden_wording,
    order_review,
    order_value,
    product_code,
    product_name,
    result,
    risk_level,
    simple,
    to_date,
    to_decimal,
    to_int,
)

Q4 = Decimal("0.0001")
MONTHLY_TOLERANCE = Decimal("0.0001")
OBS_LABEL = {"D": "期間每日觀察", "P": "期末定日觀察"}
KI_LABEL = {"none": "無 KI", "AM": "到期觀察", "D": "每日觀察", "M": "每月觀察（Monthly KI）"}
PRICE_LABEL = {"strike": "執行價", "ko": "KO 價", "ki": "下限價（觸及生效價）"}
PRICE_PCT_FIELD = {"strike": "strike_pct", "ko": "ko_pct", "ki": "ki_pct"}

# 第二階段或暫不核對的規則：列入報告「未涵蓋」區，不影響也不假裝通過
NOT_COVERED: list[dict[str, str]] = [
    {"rule_id": "field.monthly_ki", "description": "Monthly KI（MKI）說明書判斷方式（尚無樣本，見 Issue #10）"},
    {"rule_id": "doc.underlying_names", "description": "標的中文名稱與交易所（擱置，見核對規則 §6.2）"},
    {"rule_id": "field.isin", "description": "ISIN（詢價表沒有，暫不核對）"},
]


ISSUER = "BARC"


@dataclass
class Context(common.Context):
    ts: BarcTermSheet


def currency(ctx: Context) -> CheckResult:
    rid = "field.currency"
    pf = ctx.ts.f("currency_zh")
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
    rid, pf = "field.underlyings", ctx.ts.f("underlyings")
    ovs = [ctx.order.fields.get(f"underlying_{i}") for i in range(1, 6)]
    values = [o.value if o else None for o in ovs]
    filled = [i for i, v in enumerate(values) if v is not None]
    if not filled:
        return order_review(rid, "underlyings", ovs[0], pf, "order_missing", "詢價表沒有任何 BBG Code")
    if filled != list(range(len(filled))):
        return result(
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


def ko_type(ctx: Context) -> CheckResult:
    rid = "field.ko_type"
    obs, mem = ctx.ts.f("ko_observation"), ctx.ts.f("ko_memory")
    v, ov, problem = order_value(
        ctx, "ko_type", rid, "ko_type", obs, lambda x: x if isinstance(x, str) else None, "文字"
    )
    if problem:
        return problem
    mapped = ctx.fmt.ko_type_values.get(v)
    if mapped is None:
        return order_review(rid, "ko_type", ov, obs, "order_unknown_value", f"KO Type「{v}」不在格式設定的允許值內")
    for pf in (obs, mem):
        if not pf.ok:
            return doc_review(rid, "ko_type", pf, v, [ov])
    exp = (mapped["observation"], bool(mapped["memory"]))
    act = (obs.value, mem.value)

    def label(t: tuple[str, bool]) -> str:
        return f"{OBS_LABEL[t[0]]}／{'記憶式' if t[1] else '非記憶式'}"

    ok = exp == act
    return result(
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
    v, ov, problem = order_value(
        ctx, "ki_type", rid, "ki_type", pf, lambda x: x if isinstance(x, str) else None, "文字"
    )
    if problem:
        return None, ov, problem
    mapped = ctx.fmt.ki_type_values.get(v)
    if mapped is None:
        return (
            None,
            ov,
            order_review(rid, "ki_type", ov, pf, "order_unknown_value", f"Barrier Type「{v}」不在格式設定的允許值內"),
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
        return doc_review(rid, "ki_type", pf, mapped, [ov])
    if "M" in (mapped, doc):
        return result(
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
    return result(
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
        return order_review(rid, "ki_pct", ov, pf, "order_missing", "詢價表沒有 KI Barrier 欄位")
    order_v = Decimal(0) if ov.value is None else to_decimal(ov.value)
    if order_v is None:
        return order_review(rid, "ki_pct", ov, pf, "order_invalid", "詢價表的值不是數字")
    doc = _doc_ki(kt)
    if doc is None:
        return doc_review(rid, "ki_pct", kt, order_v, [ov])
    if doc == "none":
        ok = order_v == 0
        return result(
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
        return doc_review(rid, "ki_pct", pf, order_v, [ov])
    ok, shown = cmp_pct(order_v, pf.value)
    return result(
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
    v, ov, problem = order_value(ctx, key, rid, key, issue, to_int, "整數")
    if problem:
        return problem
    for pf in (trade, issue):
        if not pf.ok:
            return doc_review(rid, key, pf, v, [ov])
    days = (issue.value - trade.value).days
    ok = v == days
    return result(
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
    """月配息率 = 年利率 × 天期 ÷ 12 ÷ 期數（期數 = 說明書配息表列數）；與說明書差 ≤ 0.0001 視為一致。"""
    rid, field = "derive.monthly_coupon", "monthly_coupon_pct"
    pf, table = ctx.ts.f(field), ctx.ts.f("coupon_table")
    annual, ov_a, p1 = order_value(ctx, "coupon_pa_pct", rid, field, pf, to_decimal, "數字")
    tenor, ov_t, p2 = order_value(ctx, "tenor_months", rid, field, pf, to_int, "整數")
    for p in (p1, p2):
        if p:
            return p
    if not pf.ok:
        return doc_review(rid, field, pf, None, [ov_a, ov_t])
    if not table.ok:
        return doc_review(rid, field, table, None, [ov_a, ov_t])
    periods = len(table.value.rows)
    expected = (annual * tenor / 12 / periods).quantize(Q4, ROUND_HALF_UP)
    ok = abs(expected - pf.value) <= MONTHLY_TOLERANCE
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        pf=pf,
        ov=[ov_a, ov_t],
        reason="" if ok else "value_mismatch",
        tolerance="≤ 0.0001",
        message=f"推算：{annual}% × {tenor} ÷ 12 ÷ {periods} 期（說明書配息表列數），四捨五入到 4 位",
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
                result(
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
            result(
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
        return [doc_review(rid, "price_table", table)]
    rows = ctx.ts.price_rows
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
            )
        ]
    has_ki_col = all("ki" in r.values for r in rows)
    kt = ctx.ts.f("ki_type")
    doc_ki = _doc_ki(kt)
    if doc_ki is None:
        return [doc_review(rid, "price_table", kt)]
    if (doc_ki != "none") != has_ki_col:
        return [
            result(
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
                out.append(doc_review(rid, field, pct))
                continue
            expected = (row.values["initial"] * pct.value / 100).quantize(Q4, ROUND_HALF_UP)
            ok = expected == row.values[col]
            out.append(
                result(
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
        return doc_review(rid, "denomination", pf)
    iso = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    default = ctx.std.denomination.get(iso) if iso else None
    if default is None:
        return result(
            rid,
            "denomination",
            S.REVIEW_REQUIRED,
            actual=pf.value,
            pf=pf,
            reason="currency_unknown",
            message="無法確認幣別，找不到面額預設值",
        )
    ok = pf.value == default
    return result(
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
            return doc_review(rid, "subscription_start_date", f)
    ok = pf.value == trade.value
    return result(
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
            return doc_review(rid, "print_date", f)
    limit = ctx.std.print_date_max_days_after_trade
    gap = (pf.value - trade.value).days
    ok = 0 <= gap <= limit
    return result(
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


# ---------------------------------------------------------------- 配息表與提前出場表（第二階段）


def _next_weekday(d: dt.date) -> dt.date:
    """後 1 個平日（只排除週末；沒有假日曆，核對規則 §3.8）。"""
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def _row_ev(rows: list[ScheduleRow]) -> list[Evidence]:
    return [Evidence.of(ln) for r in rows for ln in r.lines]


def _header_ev(table: Table) -> list[Evidence]:
    return [Evidence.of(h) for h in table.header]


def _periods(rows: list[ScheduleRow]) -> str:
    return "、".join(f"第 {r.t} 期" for r in rows)


def observation_frequency(ctx: Context) -> CheckResult:
    """配息期數：詢價表 天期 ÷ Observation Frequency = 說明書配息表列數。"""
    rid, field = "field.observation_frequency", "observation_frequency_months"
    table = ctx.ts.f("coupon_table")
    tenor, ov_t, p1 = order_value(ctx, "tenor_months", rid, field, table, to_int, "整數")
    freq, ov_f, p2 = order_value(ctx, field, rid, field, table, to_int, "整數")
    for p in (p1, p2):
        if p:
            return p
    if freq <= 0 or tenor % freq:
        return result(
            rid,
            field,
            S.REVIEW_REQUIRED,
            ov=[ov_t, ov_f],
            reason="order_invalid",
            message="天期無法被觀察頻率整除，無法推算期數",
        )
    if not table.ok:
        return doc_review(rid, field, table, tenor // freq, [ov_t, ov_f])
    n = len(table.value.rows)
    ok = n == tenor // freq
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=tenor // freq,
        actual=n,
        evidence=_header_ev(table.value),
        ov=[ov_t, ov_f],
        reason="" if ok else "value_mismatch",
        message=f"期數：詢價表 {tenor} ÷ {freq}；說明書配息表 {n} 列",
    )


def guaranteed_periods(ctx: Context) -> CheckResult:
    """保證配息期：詢價表 vs 說明書提前出場表；Daily Memory 另以 §13(7) 定義句交叉驗證。"""
    rid, field = "field.guaranteed_periods", "guaranteed_periods"
    pf, text = ctx.ts.f(field), ctx.ts.f("guaranteed_periods_text")
    v, ov, problem = order_value(ctx, field, rid, field, pf, to_int, "整數")
    if problem:
        return problem
    if not pf.ok:
        return doc_review(rid, field, pf, v, [ov])
    if text.status not in (FieldStatus.PRESENT, FieldStatus.MISSING):
        return doc_review(rid, field, text, v, [ov])
    if text.ok and text.value != pf.value:
        return result(
            rid,
            field,
            S.REVIEW_REQUIRED,
            expected=v,
            actual={"表格": pf.value, "定義句": text.value},
            evidence=pf.evidence + text.evidence,
            ov=[ov],
            reason="document_inconsistent",
            message="提前出場表與 §13(7)「自動提前出場觀察期」定義句推得的保證配息期不同",
        )
    ok = v == pf.value
    source = "提前出場表與 §13(7) 定義句" if text.ok else "提前出場表"
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=v,
        actual=pf.value,
        evidence=pf.evidence + (text.evidence if text.ok else []),
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message=f"說明書值由{source}推得",
    )


def coupon_dates(ctx: Context) -> list[CheckResult]:
    """B1、B2：期數 = 天期；評價日、支付日逐期遞增；每期評價日 < 支付日。"""
    rid = "schedule.coupon_dates"
    table, tenor = ctx.ts.f("coupon_table"), ctx.ts.f("tenor_months")
    if not table.ok:
        return [doc_review(rid, "coupon_table", table)]
    rows = table.value.rows
    out = []
    if not tenor.ok:
        out.append(doc_review(rid, "coupon_periods", tenor))
    else:
        ok = len(rows) == tenor.value
        out.append(
            result(
                rid,
                "coupon_periods",
                S.PASS if ok else S.MISMATCH,
                expected=tenor.value,
                actual=len(rows),
                evidence=tenor.evidence + _header_ev(table.value),
                reason="" if ok else "value_mismatch",
                message="配息表列數須等於天期（每月一期）",
            )
        )
    bad = []
    for k, r in enumerate(rows):
        v, p = r.get("valuation"), r.get("payment")
        if not (isinstance(v, dt.date) and isinstance(p, dt.date)) or v >= p:
            bad.append(r)
            continue
        prev_v, prev_p = (rows[k - 1].get("valuation"), rows[k - 1].get("payment")) if k else (None, None)
        if k and not (isinstance(prev_v, dt.date) and isinstance(prev_p, dt.date) and prev_v < v and prev_p < p):
            bad.append(r)
    out.append(
        result(
            rid,
            "coupon_date_order",
            S.MISMATCH if bad else S.PASS,
            actual=_periods(bad) or None,
            evidence=_row_ev(bad) or _header_ev(table.value),
            reason="date_order" if bad else "",
            message="每期評價日 < 支付日，且評價日、支付日逐期遞增",
        )
    )
    return out


def final_period(ctx: Context) -> list[CheckResult]:
    """A3、A4：末期評價日 = 最終評價日；末期支付日 = 到期日。"""
    rid = "schedule.final_period"
    table = ctx.ts.f("coupon_table")
    out = []
    for field, key, label in (
        ("final_valuation_date", "valuation", "末期評價日須等於最終評價日"),
        ("maturity_date", "payment", "末期支付日須等於到期日"),
    ):
        pf = ctx.ts.f(field)
        bad = next((x for x in (table, pf) if not x.ok), None)
        if bad is not None:
            out.append(doc_review(rid, f"last_{key}", bad))
            continue
        last = table.value.rows[-1]
        ok = last.get(key) == pf.value
        out.append(
            result(
                rid,
                f"last_{key}",
                S.PASS if ok else S.MISMATCH,
                expected=pf.value,
                actual=last.get(key),
                evidence=pf.evidence + _row_ev([last]),
                reason="" if ok else "value_mismatch",
                message=label,
            )
        )
    return out


def autocall_dates(ctx: Context) -> list[CheckResult]:
    """C1–C4：自動提前出場表與配息表的日期關係。"""
    rid = "schedule.autocall_dates"
    coupon, ko = ctx.ts.f("coupon_table"), ctx.ts.f("ko_table")
    for x in (coupon, ko):
        if not x.ok:
            return [doc_review(rid, "ko_table", x)]
    ct, kt = coupon.value, ko.value
    if kt.kind == "ko_fixed":
        pairs = (
            ("ko_valuation", "valuation", "C1 自動提前出場評價日 = 同期配息評價日"),
            ("early_redemption", "payment", "C2 指定提前現金贖回日 = 同期配息支付日"),
        )
    elif kt.kind == "ko_period":
        pairs = (("end", "valuation", "C3 期末日 = 同期配息評價日"),)
    elif kt.kind == "coupon":
        return [
            result(
                rid,
                "ko_table",
                S.NOT_APPLICABLE,
                evidence=_header_ev(kt),
                message="評價日表兼作自動提前出場評價日，沒有另一張提前出場表可比對",
            )
        ]
    else:
        pairs = ()  # 觀察期合併表：期末日即配息評價日，只需檢查期始日（C4）
    out = []
    for ko_key, c_key, label in pairs:
        if len(kt.rows) != len(ct.rows):
            out.append(
                result(
                    rid,
                    ko_key,
                    S.MISMATCH,
                    expected=len(ct.rows),
                    actual=len(kt.rows),
                    evidence=_header_ev(kt),
                    reason="period_count",
                    message=f"{label}：提前出場表與配息表期數不同",
                )
            )
            continue
        bad = [k for k, c in zip(kt.rows, ct.rows, strict=True) if k.get(ko_key) not in (NA, c.get(c_key))]
        out.append(
            result(
                rid,
                ko_key,
                S.MISMATCH if bad else S.PASS,
                actual=_periods(bad) or None,
                evidence=_row_ev(bad) or _header_ev(kt),
                reason="value_mismatch" if bad else "",
                message=label,
            )
        )
    if kt.kind in ("ko_period", "combined"):
        out.append(_period_starts(ctx, rid, kt))
    return out


def _period_starts(ctx: Context, rid: str, kt: Table) -> CheckResult:
    """C4：第 1 期期始日為 N/A 或發行日後 1 個平日；之後各期 = 前一期期末日後 1 個平日。

    前一期期末日為 N/A（不可提前出場）時，本期期始日也應為 N/A。
    """
    issue = ctx.ts.f("issue_date")
    if not issue.ok:
        return doc_review(rid, "start", issue)
    bad = []
    for k, r in enumerate(kt.rows):
        start = r.get("start")
        prev_end = issue.value if k == 0 else kt.rows[k - 1].get("end")
        if start == NA:
            if k and prev_end != NA:
                bad.append(r)
        elif not isinstance(prev_end, dt.date) or start != _next_weekday(prev_end):
            bad.append(r)
    return result(
        rid,
        "start",
        S.MISMATCH if bad else S.PASS,
        actual=_periods(bad) or None,
        evidence=_row_ev(bad) or _header_ev(kt),
        reason="value_mismatch" if bad else "",
        tolerance="平日只排除週末（無假日曆）",
        message="C4 期始日 = 前一期期末日後 1 個平日；第 1 期為 N/A 或發行日後 1 個平日",
    )


def trigger_per_period(ctx: Context) -> CheckResult:
    """§13(7) 每期觸發百分比 = §15 觸發百分比定義句。"""
    rid, ko, pct = "doc.autocall_trigger_per_period", ctx.ts.f("ko_table"), ctx.ts.f("ko_pct")
    if not ko.ok:
        return doc_review(rid, "trigger", ko)
    if "trigger" not in ko.value.columns:
        return result(
            rid,
            "trigger",
            S.NOT_APPLICABLE,
            evidence=_header_ev(ko.value),
            message="此型態的提前出場表沒有每期觸發百分比欄",
        )
    if not pct.ok:
        return doc_review(rid, "trigger", pct)

    def callable_(r: ScheduleRow) -> bool:
        return any(isinstance(r.get(k), dt.date) for k in ("end", "ko_valuation"))

    # 不可提前出場的期別為 N/A；可提前出場的期別必須有百分比且等於定義句
    bad = [r for r in ko.value.rows if r.get("trigger") != pct.value and (r.get("trigger") != NA or callable_(r))]
    return result(
        rid,
        "trigger",
        S.MISMATCH if bad else S.PASS,
        expected=pct.value,
        actual=sorted({str(r.get("trigger")) for r in bad}) if bad else pct.value,
        evidence=pct.evidence + _row_ev(bad),
        reason="value_mismatch" if bad else "",
        message="每期觸發百分比須等於 §15 定義句" + (f"；不符：{_periods(bad)}" if bad else ""),
    )


def scenario_price_table(ctx: Context) -> CheckResult:
    """§16(3) 情境分析重印價格表逐格 = §15 價格表。"""
    rid, s15, s16 = "doc.scenario_price_table", ctx.ts.f("price_table"), ctx.ts.f("scenario_price_table")
    for x in (s15, s16):
        if not x.ok:
            return doc_review(rid, "scenario_price_table", x)
    a, b = ctx.ts.price_rows, ctx.ts.scenario_rows
    diffs = [] if len(a) == len(b) else [f"列數 {len(a)} ≠ {len(b)}"]
    for k, (r15, r16) in enumerate(zip(a, b, strict=False), 1):
        for col in sorted(set(r15.values) | set(r16.values)):
            if r15.values.get(col) != r16.values.get(col):
                diffs.append(
                    f"第 {k} 檔 {PRICE_LABEL.get(col, '最初價格')}：§15 {r15.values.get(col)}／§16 {r16.values.get(col)}"
                )
    return result(
        rid,
        "scenario_price_table",
        S.MISMATCH if diffs else S.PASS,
        actual=diffs or None,
        evidence=s16.evidence[:6],
        reason="value_mismatch" if diffs else "",
        message="§16(3) 重印價格表須與 §15 價格表逐格相同",
    )


def min_amounts(ctx: Context) -> list[CheckResult]:
    """第四章最低申購金額、最低贖回商品面額 = §6 面額。"""
    rid, denom = "doc.min_subscription_redemption", ctx.ts.f("denomination")
    out = []
    for field in ("min_subscription", "min_redemption"):
        pf = ctx.ts.f(field)
        bad = next((x for x in (pf, denom) if not x.ok), None)
        if bad is not None:
            out.append(doc_review(rid, field, bad))
            continue
        ok = pf.value == denom.value
        out.append(
            result(
                rid,
                field,
                S.PASS if ok else S.MISMATCH,
                expected=denom.value,
                actual=pf.value,
                evidence=pf.evidence + denom.evidence,
                reason="" if ok else "value_mismatch",
                message="須等於 §6 每單位商品面額",
            )
        )
    return out


# ---------------------------------------------------------------- 入口


def run_all(ctx: Context) -> list[CheckResult]:
    dec, intg, date = to_decimal, to_int, to_date
    pct_tol = "依說明書顯示位數四捨五入後比對"
    out: list[CheckResult] = [
        product_code(ctx),
        currency(ctx),
        underlyings(ctx),
        simple(ctx, "field.strike_pct", "strike_pct", dec, "數字", cmp_pct, pct_tol),
        simple(ctx, "field.ko_pct", "ko_pct", dec, "數字", cmp_pct, pct_tol),
        ko_type(ctx),
        ki_type(ctx),
        ki_pct(ctx),
        simple(ctx, "field.coupon_pa_pct", "coupon_pa_pct", dec, "數字", cmp_pct, pct_tol),
        simple(ctx, "field.tenor_months", "tenor_months", intg, "整數"),
        simple(ctx, "field.trade_date", "trade_date", date, "日期"),
        simple(ctx, "field.issue_date", "issue_date", date, "日期"),
        simple(ctx, "field.final_valuation_date", "final_valuation_date", date, "日期"),
        simple(ctx, "field.maturity_date", "maturity_date", date, "日期"),
        issue_date_offset(ctx),
        monthly_coupon(ctx),
        observation_frequency(ctx),
        guaranteed_periods(ctx),
        *coupon_consistency(ctx),
        *prices(ctx),
        *coupon_dates(ctx),
        *final_period(ctx),
        *autocall_dates(ctx),
        trigger_per_period(ctx),
        scenario_price_table(ctx),
        *min_amounts(ctx),
        denomination(ctx),
        subscription_start(ctx),
        print_date(ctx),
        approval_date(ctx),
        chairman(ctx),
        fixed_warning(ctx),
        risk_level(ctx),
        forbidden_wording(ctx),
        *product_name(ctx, ISSUER),
    ]
    return out
