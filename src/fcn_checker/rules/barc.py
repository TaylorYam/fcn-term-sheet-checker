"""BARC 專屬核對規則（docs/rules/barc-check-rules.md）；共用規則見 rules/common.py。

規則只接收標準化後的說明書欄位、參考條件表欄位與審查標準；不讀檔、不改來源值。
每條規則產生一或多筆 CheckResult；抓不到、歧義、未知值一律轉人工覆核，不猜值。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..parsers.barc import BarcTermSheet
from ..parsers.barc_schedule import NA, ScheduleRow, Table
from ..parsers.layout import squash
from ..schema import CheckResult, Evidence, FieldStatus, OrderValue, ParsedField
from ..schema import CheckStatus as S
from . import common
from .common import (
    approval_date,
    chairman,
    cmp_pct,
    distributor_info,
    doc_review,
    fees,
    fixed_warning,
    forbidden_wording,
    issue_price,
    issuer_name,
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
from .reference import AutocallSchedule

Q4 = Decimal("0.0001")
MONTHLY_TOLERANCE = Decimal("0.0001")
OBS_LABEL = {"D": "期間每日觀察", "P": "期末定日觀察"}
KI_LABEL = {"none": "無 KI", "AM": "到期觀察", "D": "每日觀察", "M": "每月觀察（Monthly KI）"}
PRICE_LABEL = {"strike": "執行價", "ko": "KO 價", "ki": "下限價（觸及生效價）"}
PRICE_PCT_FIELD = {"strike": "strike_pct", "ko": "ko_pct", "ki": "ki_pct"}

# 第二階段或暫不核對的規則：列入報告「未涵蓋」區，不影響也不假裝通過
NOT_COVERED: list[dict[str, str]] = [
    {"rule_id": "field.monthly_ki", "description": "Monthly KI（MKI）說明書判斷方式（尚無樣本，見 Issue #10）"},
    {
        "rule_id": "doc.underlying_names",
        "description": "標的名稱：不核對，上手依彭博代號帶入名稱，以代號為準（核對規則 §6.2）",
    },
    {"rule_id": "doc.initial_prices", "description": "最初價格本身的外部正確性（無權威來源，Issue #38 排除）"},
    {
        "rule_id": "doc.scenario_other_returns",
        "description": "情境分析有利情況的年化報酬率與最差情況的報酬率（取決於假設的持有期間與價格，無固定基準）",
    },
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
    values = [o.value if o and o.value != "-" else None for o in ovs]  # 參考條件表以 - 表示沒有這檔標的
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


def _doc_ki(pf: ParsedField) -> str | None:
    if pf.status in (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE) and pf.value in KI_LABEL:
        return pf.value
    return None


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


# ---------------------------------------------------------------- 文件內重複出現處（Issue #38）

_NAME_SUFFIX = "（下稱「本商品」）"
_BRACKETS = str.maketrans({"(": "（", ")": "）"})


def _same_text(a: str, b: str) -> bool:
    """去空白、括號全半形不計（同審查標準商品名稱的寬鬆度）。"""
    return squash(a).translate(_BRACKETS) == squash(b).translate(_BRACKETS)


def _equal(rid: str, field: str, pf: ParsedField, ref: ParsedField, expected: object, message: str) -> CheckResult:
    """說明書某處（pf）的值須等於另一處（ref）推得的值（expected）。"""
    for x in (pf, ref):
        if not x.ok:
            return doc_review(rid, field, x)
    ok = _same_text(str(pf.value), str(expected)) if isinstance(expected, str) else pf.value == expected
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        evidence=pf.evidence + ref.evidence,
        reason="" if ok else "document_inconsistent",
        message=message,
    )


def name_consistency(ctx: Context) -> list[CheckResult]:
    """封面標題與第一章第 1 條商品名稱 = 封面「商品中文名稱」（標題不含「（下稱「本商品」）」）。"""
    rid, cover = "doc.name_consistency", ctx.ts.f("name_zh")
    title = _equal(
        rid,
        "title_name",
        ctx.ts.f("title_name"),
        cover,
        # 括號先統一為全形，半形的「(下稱「本商品」)」也要去掉
        squash(cover.value).translate(_BRACKETS).replace(_NAME_SUFFIX, "") if cover.ok else None,
        "封面標題須等於封面「商品中文名稱」（去掉「（下稱「本商品」）」）",
    )
    art1 = _equal(
        rid, "art1_name", ctx.ts.f("art1_name"), cover, cover.value, "第一章第 1 條商品名稱須等於封面「商品中文名稱」"
    )
    return [title, art1]


def distributor_product_code(ctx: Context) -> CheckResult:
    code = ctx.ts.f("product_code")
    return _equal(
        "doc.distributor_product_code",
        "distributor_product_code",
        ctx.ts.f("distributor_product_code"),
        code,
        code.value,
        "封面「受託或銷售機構商品代號」須等於「商品代號」",
    )


def currency_consistency(ctx: Context) -> CheckResult:
    cz = ctx.ts.f("currency_zh")
    return _equal(
        "doc.currency_consistency",
        "art5_currency",
        ctx.ts.f("art5_currency"),
        cz,
        cz.value,
        "第一章第 5 條計價幣別須等於封面「計價幣別」",
    )


def scenario_notional(ctx: Context) -> CheckResult:
    denom = ctx.ts.f("denomination")
    return _equal(
        "doc.scenario_notional",
        "scenario_notional",
        ctx.ts.f("scenario_notional"),
        denom,
        denom.value,
        "第 16 條情境假設的每單位商品面額須等於第 6 條面額",
    )


def price_header_pct(ctx: Context) -> list[CheckResult]:
    """§15 價格表與 §16 情境表欄頭「X（為最初價格的N%）」= §15 定義句的百分比（執行價格另與參考條件表 K(%) 比對）。"""
    rid = "doc.price_header_pct"
    out = []
    for field in ("strike_pct", "ko_pct", "ki_pct"):
        mentions = [h.mention for h in ctx.ts.header_pcts if h.field == field]
        if field == "strike_pct" and not any(m.article == "第15條" for m in mentions):
            out.append(
                result(
                    rid,
                    field,
                    S.REVIEW_REQUIRED,
                    reason="document_missing",
                    message="第 15 條價格表找不到「執行價格（為最初價格的N%）」欄頭",
                )
            )
        if not mentions:
            continue
        pf = ctx.ts.f(field)
        if not pf.ok:
            out.append(doc_review(rid, field, pf))
            continue
        bad = [m for m in mentions if m.value != pf.value]
        out.append(
            result(
                rid,
                field,
                S.MISMATCH if bad else S.PASS,
                expected=pf.value,
                actual=sorted({f"{m.article} {m.value}%" for m in bad}) if bad else pf.value,
                evidence=pf.evidence + [Evidence.of(ln) for m in (bad or mentions) for ln in m.lines],
                reason="document_inconsistent" if bad else "",
                message=f"價格表欄頭共 {len(mentions)} 處，須等於第 15 條定義句",
            )
        )
    return out


def coupon_repeats(ctx: Context) -> list[CheckResult]:
    """§9(3) 相關配息率與 §16 情境試算中每次出現的月配息率 = §14 正式月配息率。"""
    rid, pf = "doc.coupon_repeats", ctx.ts.f("monthly_coupon_pct")
    mentions = ctx.ts.coupon_mentions["repeat"]
    out = []
    obs = ctx.ts.f("ko_observation")
    if obs.ok and obs.value == "D" and not any(m.article == "第9條(3)" for m in mentions):
        # 期間每日觀察型態的 §9(3) 一定列出相關配息率；抓不到代表寫法不同，不能略過
        out.append(
            result(
                rid,
                "第9條(3)",
                S.REVIEW_REQUIRED,
                evidence=obs.evidence,
                reason="document_missing",
                message="期間每日觀察型態的第9條(3)找不到「相關配息率為…%」",
            )
        )
    for article in dict.fromkeys(m.article for m in mentions):
        group = [m for m in mentions if m.article == article]
        if any(m.value.is_nan() for m in group):
            out.append(
                result(
                    rid,
                    article,
                    S.REVIEW_REQUIRED,
                    reason="document_missing",
                    evidence=[Evidence.of(ln) for m in group if m.value.is_nan() for ln in m.lines],
                    message=f"{article}有月配息率讀不到數值（寫法與範本不同），請人工確認",
                )
            )
            continue
        if not pf.ok:
            out.append(doc_review(rid, article, pf))
            continue
        bad = [m for m in group if m.value != pf.value]
        out.append(
            result(
                rid,
                article,
                S.MISMATCH if bad else S.PASS,
                expected=pf.value,
                actual=sorted({str(m.value) for m in bad}) if bad else pf.value,
                evidence=pf.evidence + [Evidence.of(ln) for m in (bad or group) for ln in m.lines],
                reason="document_inconsistent" if bad else "",
                message=f"{article}共 {len(group)} 處月配息率，須等於第 14 條「配息率」",
            )
        )
    return out


def _shown(value: Decimal, like: Decimal) -> Decimal:
    """依說明書顯示位數四捨五入（half-up）。"""
    exp = like.as_tuple().exponent
    return value.quantize(Decimal(1).scaleb(exp), ROUND_HALF_UP) if isinstance(exp, int) else value


def scenario_returns(ctx: Context) -> list[CheckResult]:
    """§16(3) 有利情況：總報酬率 = 月配息率 × 配息期數；一般情況：總報酬率 = 月配息率 × 總期數、
    平均年化報酬率 = 正式年利率。有利情況的年化率取決於持有期間，不核對（列未涵蓋）。"""
    rid = "doc.scenario_returns"
    monthly, annual, table = ctx.ts.f("monthly_coupon_pct"), ctx.ts.f("coupon_pa_pct"), ctx.ts.f("coupon_table")
    out = []
    for name, label in (("scenario_favourable", "有利情況"), ("scenario_general", "一般情況")):
        pf = ctx.ts.f(name)
        if not pf.ok:
            out.append(doc_review(rid, f"{name}_total", pf))
            continue
        if name == "scenario_general" and not table.ok:
            out.append(doc_review(rid, f"{name}_total", table))
            continue
        if not monthly.ok:
            out.append(doc_review(rid, f"{name}_total", monthly))
            continue
        periods = pf.value["periods"] if name == "scenario_favourable" else len(table.value.rows)
        total = pf.value["total"]
        expected = _shown(monthly.value * periods, total)
        ok = expected == total
        out.append(
            result(
                rid,
                f"{name}_total",
                S.PASS if ok else S.MISMATCH,
                expected=expected,
                actual=total,
                evidence=pf.evidence + monthly.evidence,
                reason="" if ok else "value_mismatch",
                tolerance="依說明書顯示位數四捨五入",
                message=f"{label}總報酬率 = 月配息率 {monthly.value}% × {periods} 期",
            )
        )
        if name == "scenario_general":
            if not annual.ok:
                out.append(doc_review(rid, f"{name}_annualized", annual))
                continue
            ann = pf.value["annualized"]
            ok = _shown(annual.value, ann) == ann
            out.append(
                result(
                    rid,
                    f"{name}_annualized",
                    S.PASS if ok else S.MISMATCH,
                    expected=annual.value,
                    actual=ann,
                    evidence=pf.evidence + annual.evidence,
                    reason="" if ok else "value_mismatch",
                    message="一般情況（持有至到期、全部配息）平均年化報酬率須等於第 14 條年利率",
                )
            )
    return out


def observation_t_range(ctx: Context) -> CheckResult:
    """§13(7) 自動提前出場觀察期定義句（Daily Memory）各段 t 的起訖：
    保證配息期 G ≥ 1 → (G, G) 與 (G+1, 總期數)；G = 0 → (1, 總期數)。總期數 = 配息表列數。"""
    rid, field = "doc.observation_t_range", "observation_t_ranges"
    obs, mem = ctx.ts.f("ko_observation"), ctx.ts.f("ko_memory")
    for x in (obs, mem):
        if not x.ok:
            return doc_review(rid, field, x)
    if not (obs.value == "D" and mem.value):
        return result(
            rid,
            field,
            S.NOT_APPLICABLE,
            evidence=obs.evidence,
            message="只有期間每日觀察的記憶式商品有「自動提前出場觀察期」t 範圍定義句",
        )
    pf, g, table = ctx.ts.f(field), ctx.ts.f("guaranteed_periods"), ctx.ts.f("coupon_table")
    for x in (pf, g, table):
        if not x.ok:
            return doc_review(rid, field, x)
    n = len(table.value.rows)
    expected = [(g.value, g.value), (g.value + 1, n)] if g.value >= 1 else [(1, n)]
    ok = pf.value == expected

    def show(rs: list[tuple[int, int]]) -> str:
        return "；".join(f"t={a}" if a == b else f"t={a}～{b}" for a, b in rs)

    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=show(expected),
        actual=show(pf.value),
        evidence=pf.evidence + g.evidence,
        reason="" if ok else "value_mismatch",
        message=f"依提前出場表推得保證配息期 {g.value}、配息表 {n} 期",
    )


# ---------------------------------------------------------------- 參考條件表欄位


def autocall_schedule(ts: BarcTermSheet) -> ParsedField:
    """由 §13 提前出場表推得提前出場排程（第一個可提前出場期、各期比價日）。

    D：Non-Call = 保證配息期 G（第 G 期期始日 N/A、期末日起可提前出場），比價日 = 各期期末日。
    P：Non-Call = G + 1，比價日 = 各期自動提前出場評價日（或評價日表的評價日）。
    D 型第 1 期期始日就有日期（G = 0，從第一天開始比價）尚無已確認的填法，轉人工覆核。
    """
    name = "autocall_schedule"
    obs, ko, g, text = (
        ts.f(k) for k in ("ko_observation", "ko_table", "guaranteed_periods", "guaranteed_periods_text")
    )
    for pf in (obs, ko, g):
        if not pf.ok:
            return ParsedField(name, pf.status, None, list(pf.evidence), note=pf.note)
    if text.ok and text.value != g.value:
        return ParsedField(
            name,
            FieldStatus.INVALID,
            None,
            g.evidence + text.evidence,
            note="提前出場表與 §13(7) 定義句推得的保證配息期不同",
        )
    table: Table = ko.value
    if obs.value == "D":
        if g.value == 0:
            return ParsedField(
                name,
                FieldStatus.INVALID,
                None,
                list(g.evidence),
                note="第 1 期期始日就有日期（從第一天開始比價），比價日填法尚未確認",
            )
        first, key = g.value, "end"
    else:
        first = g.value + 1
        key = "ko_valuation" if table.kind == "ko_fixed" else "valuation"
    dates = {r.t: r.get(key) for r in table.rows if r.t >= first}
    bad = [t for t, d in dates.items() if not isinstance(d, dt.date)]
    if bad:
        return ParsedField(
            name,
            FieldStatus.INVALID,
            None,
            _header_ev(table),
            note="以下期別的比價日無法辨識：" + "、".join(f"第 {t} 期" for t in bad),
        )
    sched = AutocallSchedule(obs.value, first, len(table.rows), dates)
    return ParsedField(name, FieldStatus.PRESENT, sched, list(g.evidence))


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
    rid, key, pf = "field.ko_observation", "ko_observation", ctx.ts.f("ko_observation")
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
    rid, key, pf = "field.ko_memory", "ko_memory", ctx.ts.f("ko_memory")
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
    rid, key, pf = "field.ki_type", "ki_type", ctx.ts.f("ki_type")
    mapped, ov, problem = _mapped(ctx, rid, key, pf, ctx.fmt.ki_type_values, "KI(Freq)")
    if problem:
        return problem
    doc = _doc_ki(pf)
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
    """KI(%)：說明書無 KI 時表上必須是空值寫法 `-`；有 KI 時數值相等。"""
    rid, key, pf, kt = "field.ki_pct", "ki_pct", ctx.ts.f("ki_pct"), ctx.ts.f("ki_type")
    dash = ctx.fmt.empty_value
    v, ov, problem = order_value(ctx, key, rid, key, pf, lambda x: x if x == dash else to_decimal(x), f"數字或 {dash}")
    if problem:
        return problem
    doc = _doc_ki(kt)
    if doc is None:
        return doc_review(rid, key, kt, v, [ov])
    if doc == "none":
        ok = v == dash
        return result(
            rid,
            key,
            S.NOT_APPLICABLE if ok else S.MISMATCH,
            expected=v,
            evidence=kt.evidence,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message="雙方皆無 KI（由說明書明確判定）" if ok else f"說明書無 KI；KI(%) 應為 {dash}",
        )
    if not pf.ok:
        return doc_review(rid, key, pf, v, [ov])
    if v == dash:
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
        tolerance="依說明書顯示位數四捨五入後比對",
    )


PRICE_COLUMNS = (
    ("initial", "initial_price", "進場價"),
    ("strike", "strike_price", "執行價"),
    ("ki", "ki_price", "下限價"),
    ("ko", "ko_price", "KO 價"),
)


def underlying_prices(ctx: Context) -> list[CheckResult]:
    """各標的進場／執行／下限／KO 價：表上值四捨五入（half-up）到 4 位後與 §15 價格表相等。"""
    rid, table, uls = "field.underlying_prices", ctx.ts.f("price_table"), ctx.ts.f("underlyings")
    if not table.ok:
        return [doc_review(rid, "price_table", table)]
    dash = ctx.fmt.empty_value
    out = []
    for i, row in enumerate(ctx.ts.price_rows, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else f"第 {i} 檔標的"
        ev = [Evidence.of(ln) for ln in row.lines]
        for col, std, zh in PRICE_COLUMNS:
            field = f"{label} {zh}"
            ov = ctx.order.fields.get(f"underlying_{i}_{std}")
            if ov is None or ov.value is None:
                out.append(
                    order_review(rid, field, ov, None, "order_missing", f"{ctx.order.source}沒有此欄位或值為空白")
                )
                continue
            doc_v = row.values.get(col)
            if doc_v is None:  # 無 KI：價格表沒有下限價欄
                ok = ov.value == dash
                out.append(
                    result(
                        rid,
                        field,
                        S.NOT_APPLICABLE if ok else S.MISMATCH,
                        expected=ov.value,
                        evidence=ev,
                        ov=[ov],
                        reason="" if ok else "value_mismatch",
                        message="說明書無 KI，沒有下限價" + ("" if ok else f"；表上應為 {dash}"),
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


# ---------------------------------------------------------------- 入口


def run_all(ctx: Context) -> list[CheckResult]:
    """表上事先填好的欄位逐一核對，再加上說明書內部規則與審查標準。回填欄位與 Non-Call 由 rules/reference.py 處理。"""
    dec, intg, date = to_decimal, to_int, to_date
    pct_tol = "依說明書顯示位數四捨五入後比對"
    return [
        product_code(ctx),
        currency(ctx),
        underlyings(ctx),
        simple(ctx, "field.strike_pct", "strike_pct", dec, "數字", cmp_pct, pct_tol),
        simple(ctx, "field.ko_pct", "ko_pct", dec, "數字", cmp_pct, pct_tol),
        simple(ctx, "field.coupon_pa_pct", "coupon_pa_pct", dec, "數字", cmp_pct, pct_tol),
        simple(ctx, "field.tenor_months", "tenor_months", intg, "整數"),
        simple(ctx, "field.trade_date", "trade_date", date, "日期"),
        simple(ctx, "field.issue_date", "issue_date", date, "日期"),
        simple(ctx, "field.final_valuation_date", "final_valuation_date", date, "日期"),
        simple(ctx, "field.maturity_date", "maturity_date", date, "日期"),
        simple(ctx, "field.denomination", "denomination", intg, "整數"),
        ko_observation(ctx),
        ko_memory(ctx),
        ki_type(ctx),
        ki_pct(ctx),
        *underlying_prices(ctx),
        monthly_coupon(ctx),
        *document_rules(ctx),
    ]


def document_rules(ctx: Context) -> list[CheckResult]:
    """說明書內部規則與審查標準：不使用參考條件表的值。"""
    return [
        *coupon_consistency(ctx),
        *prices(ctx),
        *coupon_dates(ctx),
        *final_period(ctx),
        *autocall_dates(ctx),
        trigger_per_period(ctx),
        scenario_price_table(ctx),
        *min_amounts(ctx),
        *name_consistency(ctx),
        distributor_product_code(ctx),
        currency_consistency(ctx),
        scenario_notional(ctx),
        *price_header_pct(ctx),
        *coupon_repeats(ctx),
        *scenario_returns(ctx),
        observation_t_range(ctx),
        denomination(ctx),
        subscription_start(ctx),
        print_date(ctx),
        approval_date(ctx),
        chairman(ctx),
        fixed_warning(ctx),
        risk_level(ctx),
        forbidden_wording(ctx),
        *product_name(ctx, ISSUER),
        *issuer_name(ctx, ISSUER),
        *distributor_info(ctx),
        *fees(ctx),
        issue_price(ctx),
    ]
