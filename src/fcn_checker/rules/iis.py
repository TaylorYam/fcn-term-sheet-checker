"""投資人須知規則（docs/rules/iis-check-rules.md §3 A、B 類，ADR 0007）：各上手共用，只核對範本有的項目。

- 文件本身：頁數、頁首總頁數、封面商品代號（= 檔名前 12 碼）。
- 參考條件表有的欄位：沿用參考條件表共用規則（rules/reference.py），文件那一邊是投資人須知。
  期初定價為 VWAP 時參考條件表價格欄不比對（Issue #122），價格改和說明書比。
- 參考條件表沒有的欄位（ISIN、商品名稱、最低申購金額、標的中文名稱、D 型觀察起日）：和同商品說明書讀出的值比。
  發行日是回填欄位：表上有值就和參考條件表比，空白時才和說明書比（將回填的值）。
  說明書錯時錯訊只在說明書那份，這裡不重複報。
審查標準類（C 類）在 review_standard.iis_review_standard_rules；範本專屬的規則（例：MS 商品種類）由上手的投資人須知範本
提供（`IisTemplate.rules`，輸入 `IisIssuerContext`，不含參考條件表）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..investor_sheet import IisSheet, read_iis
from ..schema import CheckResult, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import TermSheet
from ..text import full_brackets, squash
from . import reference
from .kit import (
    Q4,
    Context,
    cmp_pct,
    doc_review,
    order_review,
    order_value,
    price_item,
    read_standard,
    result,
    to_date,
    to_decimal,
    to_int,
)

MONTHLY_TOLERANCE = Decimal("0.0001")  # 同 BARC 說明書月配息率推算（docs/rules/barc-check-rules.md）
NO_TERM_SHEET = "沒有可比對的同商品說明書（這批沒有、讀不到或沒有配對成功），無法比對"
_NAME_TAIL = re.compile(r"（(?:以下簡稱|下稱)「本商品」）.*$")


@dataclass
class IisContext:
    """投資人須知規則的輸入：`base.ts` 是投資人須知讀出結果，`term_sheet` 是同商品說明書（這批沒有或讀不到時為 None）。"""

    base: Context
    sheet: IisSheet
    term_sheet: TermSheet | None
    pages: int
    file_code: str  # 檔名前 12 碼


@dataclass
class IisIssuerContext:
    """上手投資人須知專屬規則的輸入：投資人須知讀出結果與同商品說明書（上手自己 parser 的讀出結果，可讀專屬欄位；
    這批沒有、讀不到或沒有配對成功時為 None）。不含參考條件表（同 ADR 0005 的說明書內部規則）。"""

    sheet: IisSheet
    term_sheet: TermSheet | None


def as_iis(results: list[CheckResult]) -> list[CheckResult]:
    """沿用說明書規則產生的結果，說明開頭（或分句開頭）指文件那一邊的「說明書」改寫成投資人須知；
    引號內的文件原文不動。"""
    for r in results:
        r.message = re.sub(r"(^|；)說明書", lambda m: m[1] + "投資人須知", r.message)
    return results


# ---------------------------------------------------------------- 文件本身


def pages(ctx: IisContext) -> CheckResult:
    expected, actual = ctx.base.std.iis_pages, ctx.pages
    ok = actual == expected
    return result(
        "iis.pages",
        "pages",
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=actual,
        reason="" if ok else "value_mismatch",
        message=f"預期 {expected} 頁／實際 {actual} 頁",
        item=Item.standard("投資人須知頁數"),
    )


def page_totals(ctx: IisContext) -> CheckResult:
    rid, pf, item = "iis.page_totals", read_iis(ctx.sheet, "page_totals"), Item.expected("頁首總頁數")
    if not pf.ok:
        return doc_review(rid, "page_totals", pf, ctx.pages, item=item)
    bad = sorted({m for m in pf.value if m != ctx.pages})
    return result(
        rid,
        "page_totals",
        S.MISMATCH if bad else S.PASS,
        expected=ctx.pages,
        actual=bad or ctx.pages,
        pf=pf,
        reason="value_mismatch" if bad else "",
        message=f"頁首寫「共 {'、'.join(map(str, bad))} 頁」，實際 {ctx.pages} 頁" if bad else "",
        item=item,
    )


def product_codes(ctx: IisContext) -> list[CheckResult]:
    rid, container = "iis.product_code", read_iis(ctx.sheet, "product_codes")
    if not container.ok:
        return [doc_review(rid, "product_codes", container, item=Item.expected("封面商品代號"))]
    out = []
    for occ in container.value:
        pf, item = occ.value, Item.expected(occ.name)
        if not pf.ok:
            out.append(doc_review(rid, occ.field, pf, ctx.file_code, item=item))
            continue
        ok = pf.value == ctx.file_code
        out.append(
            result(
                rid,
                occ.field,
                S.PASS if ok else S.MISMATCH,
                expected=ctx.file_code,
                actual=pf.value,
                pf=pf,
                reason="" if ok else "value_mismatch",
                message="" if ok else f"{occ.where}與檔名前 12 碼不同，可能放錯檔案",
                item=item,
            )
        )
    return out


# ---------------------------------------------------------------- 參考條件表


def _prices(ctx: IisContext) -> list[CheckResult]:
    """各標的期初價格、執行價、KO 價：表上值四捨五入到 4 位後相等；VWAP 時改和說明書價格表同一列比。"""
    rid, rows = "iis.underlying_prices", read_iis(ctx.sheet, "underlying_prices")
    if not rows.ok:
        return [doc_review(rid, "price_table", rows, item=Item.sheet("價格表"))]
    uls = read_iis(ctx.sheet, "underlyings")
    if uls.ok and len(uls.value) != len(rows.value):
        return [
            result(
                rid,
                "price_table",
                S.REVIEW_REQUIRED,
                expected=len(uls.value),
                actual=len(rows.value),
                pf=rows,
                reason="document_inconsistent",
                message=f"價格表有 {len(rows.value)} 列，連結標的資產有 {len(uls.value)} 檔",
                item=Item.sheet("價格表"),
            )
        ]
    vwap = reference.is_vwap(ctx.base)
    ts_rows, why = _term_sheet_field(ctx, "underlying_prices", "價格表") if vwap else (None, None)
    order = ctx.base.order
    out = []
    for i, row in enumerate(rows.value, start=1):
        for col, std, zh in reference.PRICE_COLUMNS:
            doc_v = row.prices.get(col)
            if doc_v is None:  # 價格表沒有這一欄（例：無 KI）
                continue
            field, name, ev = f"UL_{i} {zh}", price_item(i, col), list(row.evidence)
            if vwap:
                item = Item.term_sheet(name)
                if why or i > len(ts_rows) or col not in ts_rows[i - 1].prices:
                    out.append(
                        ts_unavailable(rid, field, name, why or "同商品說明書價格表沒有對應的價格，無法比對", doc_v)
                    )
                    continue
                expected = ts_rows[i - 1].prices[col]
            else:
                ov = order.fields.get(f"underlying_{i}_{std}")
                if ov is None or ov.value is None or to_decimal(ov.value) is None:
                    reason = "order_missing" if ov is None or ov.value is None else "order_invalid"
                    message = (
                        f"{order.source}沒有此欄位或值為空白"
                        if reason == "order_missing"
                        else f"{order.source}的值不是數字"
                    )
                    out.append(order_review(rid, field, ov, None, reason, message, name=name))
                    continue
                item = Item.column(name, [ov])
                expected = to_decimal(ov.value).quantize(Q4, ROUND_HALF_UP)
            ok = expected == doc_v
            out.append(
                result(
                    rid,
                    field,
                    S.PASS if ok else S.MISMATCH,
                    expected=expected,
                    actual=doc_v,
                    evidence=ev,
                    reason="" if ok else "value_mismatch",
                    tolerance=None if vwap else "表上值四捨五入（half-up）到 4 位",
                    item=item,
                )
            )
    return out


def _monthly_coupon(ctx: IisContext) -> CheckResult:
    """月配息率 = 參考條件表年利率 ÷ 12（差 ≤ 0.0001 視為一致，同 BARC 說明書規則）。"""
    rid, pf = "iis.monthly_coupon", read_iis(ctx.sheet, "monthly_coupon_pct")
    annual, ov, problem = order_value(
        ctx.base, "coupon_pa_pct", rid, "monthly_coupon_pct", pf, to_decimal, "數字", name="月配息率"
    )
    if problem:
        return problem
    item = Item.derived("月配息率", [ov])
    if not pf.ok:
        return doc_review(rid, "monthly_coupon_pct", pf, item=item)
    expected = (annual / 12).quantize(Q4, ROUND_HALF_UP)
    ok = abs(expected - pf.value) <= MONTHLY_TOLERANCE
    return result(
        rid,
        "monthly_coupon_pct",
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        tolerance=f"差 ≤ {MONTHLY_TOLERANCE}",
        message="須等於年利率 ÷ 12",
        item=item,
    )


def _monthly_coupons(ctx: IisContext) -> list[CheckResult]:
    """月配息率的每一處（MS）= 參考條件表年利率 ÷ 12，四捨五入（half-up）到 4 位後相等（同 MS 說明書 §3.3）。"""
    rid, container = "iis.monthly_coupon", read_iis(ctx.sheet, "monthly_coupons")
    if not container.ok:
        return [doc_review(rid, "monthly_coupons", container, item=Item.expected("月配息率"))]
    annual, ov, problem = order_value(
        ctx.base, "coupon_pa_pct", rid, "monthly_coupons", None, to_decimal, "數字", name="月配息率"
    )
    if problem:
        return [problem]
    expected = (annual / 12).quantize(Q4, ROUND_HALF_UP)
    out = []
    for occ in container.value:
        pf, item = occ.value, Item.derived(occ.name, [ov])
        if not pf.ok:
            out.append(doc_review(rid, occ.field, pf, item=item))
            continue
        ok = expected == pf.value
        out.append(
            result(
                rid,
                occ.field,
                S.PASS if ok else S.MISMATCH,
                expected=expected,
                actual=pf.value,
                pf=pf,
                ov=[ov],
                reason="" if ok else "value_mismatch",
                tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位",
                message=f"{occ.where}須等於年利率 ÷ 12",
                item=item,
            )
        )
    return out


def _first_callable(ctx: IisContext) -> CheckResult:
    """「自第 k 個…開始」的 k = 參考條件表 Non-Call(月)（第一個可以提前出場的期別）。"""
    rid, key, name = "field.first_callable_period", "first_callable_period", "第一個可提前出場期"
    pf = read_iis(ctx.sheet, key)
    v, ov, problem = order_value(ctx.base, key, rid, key, pf, to_int, "整數", name=name)
    if problem:
        return problem
    item = Item.column(name, [ov])
    if not pf.ok:
        return doc_review(rid, key, pf, v, [ov], item=item)
    ok = v == pf.value
    return result(
        rid,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=v,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message="Non-Call(月) = 第一個可以提前出場的期別（最小為 1）",
        item=item,
    )


def _ko_observation_dates(ctx: IisContext) -> list[CheckResult]:
    """期間每日觀察的觀察起日 = 同商品說明書提前出場排程第 k 期的比價日（k 取投資人須知）；迄日 = 參考條件表最終比價日。"""
    rid = "iis.ko_observation_dates"
    start, k = read_iis(ctx.sheet, "ko_observation_start"), read_iis(ctx.sheet, "first_callable_period")
    item = Item.term_sheet("KO 觀察起日")
    if not (start.ok and k.ok):
        out = [doc_review(rid, "ko_observation_start", start if not start.ok else k, item=item)]
    else:
        schedule, why = _term_sheet_field(ctx, "autocall_schedule", "提前出場排程")
        expected = None if why else schedule.dates.get(k.value)
        if expected is None:
            why = why or f"同商品說明書提前出場排程沒有第 {k.value} 期，無法比對"
            out = [ts_unavailable(rid, "ko_observation_start", "KO 觀察起日", why, start.value)]
        else:
            ok = expected == start.value
            out = [
                result(
                    rid,
                    "ko_observation_start",
                    S.PASS if ok else S.MISMATCH,
                    expected=expected,
                    actual=start.value,
                    pf=start,
                    reason="" if ok else "value_mismatch",
                    message=f"須等於說明書第 {k.value} 期的比價日（第 {k.value} 個配息週期終止日）",
                    item=item,
                )
            ]
    end, name = read_iis(ctx.sheet, "ko_observation_end"), "KO 觀察迄日"
    v, ov, problem = order_value(
        ctx.base, "final_valuation_date", rid, "ko_observation_end", end, to_date, "日期", name=name
    )
    if problem:
        return [*out, problem]
    item = Item.sheet(name, [ov])
    if not end.ok:
        return [*out, doc_review(rid, "ko_observation_end", end, v, [ov], item=item)]
    ok = v == end.value
    return [
        *out,
        result(
            rid,
            "ko_observation_end",
            S.PASS if ok else S.MISMATCH,
            expected=v,
            actual=end.value,
            pf=end,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message="須等於最終比價日",
            item=item,
        ),
    ]


def reference_fields(ctx: IisContext) -> list[CheckResult]:
    base, has = ctx.base, ctx.sheet.provides
    dec, pct = to_decimal, reference.PCT_TOLERANCE
    checks: list[tuple[str, Callable[[], CheckResult | list[CheckResult]]]] = [
        ("currency_zh", lambda: reference.currency(base)),
        (
            "denomination",
            lambda: reference.compare_field(base, "field.denomination", "denomination", "面額", to_int, "整數"),
        ),
        ("underlyings", lambda: reference.underlyings(base)),
        (
            "tenor_months",
            lambda: reference.compare_field(base, "field.tenor_months", "tenor_months", "天期（月）", to_int, "整數"),
        ),
        (
            "maturity_date",
            lambda: reference.compare_field(base, "field.maturity_date", "maturity_date", "到期日", to_date, "日期"),
        ),
        (
            "coupon_pa_pct",
            lambda: reference.compare_field(
                base, "field.coupon_pa_pct", "coupon_pa_pct", "年利率 %", dec, "數字", cmp_pct, pct
            ),
        ),
        ("monthly_coupon_pct", lambda: _monthly_coupon(ctx)),
        (
            "strike_pct",
            lambda: reference.compare_field(
                base, "field.strike_pct", "strike_pct", "執行 %", dec, "數字", cmp_pct, pct
            ),
        ),
        ("ko_pct", lambda: reference.compare_field(base, "field.ko_pct", "ko_pct", "KO %", dec, "數字", cmp_pct, pct)),
        ("ki_pct", lambda: reference.compare_field(base, "field.ki_pct", "ki_pct", "KI %", dec, "數字", cmp_pct, pct)),
        ("underlying_prices", lambda: _prices(ctx)),
        # 日期與提前出場、觸及下限條件（docs/rules/iis-check-rules.md §3 D 類，MS）
        (
            "trade_date",
            lambda: reference.compare_field(base, "field.trade_date", "trade_date", "交易日", to_date, "日期"),
        ),
        (
            "final_valuation_date",
            lambda: reference.compare_field(
                base, "field.final_valuation_date", "final_valuation_date", "最終評價日", to_date, "日期"
            ),
        ),
        ("monthly_coupons", lambda: _monthly_coupons(ctx)),
        ("ko_observation", lambda: reference.ko_observation(base)),
        ("ko_memory", lambda: reference.ko_memory(base)),
        ("first_callable_period", lambda: _first_callable(ctx)),
        ("ko_observation_start", lambda: _ko_observation_dates(ctx)),
        ("ki_type", lambda: reference.ki_type(base)),
    ]
    out: list[CheckResult] = []
    for name, check in checks:
        if has(name):
            r = check()
            out.extend(r if isinstance(r, list) else [r])
    return out


# ---------------------------------------------------------------- 同商品說明書


Found = tuple[Any, str | None]  # （說明書的值, 無法比對的原因）


def _term_sheet_field(ctx: IisContext, name: str, label: str) -> Found:
    """同商品說明書讀出的標準欄位值；沒有可比對的說明書或讀不到時回傳原因（`label` 是給作業人員看的名稱）。"""
    if ctx.term_sheet is None:
        return None, NO_TERM_SHEET
    pf = read_standard(ctx.term_sheet, name)
    if not pf.ok:
        return None, f"同商品說明書讀不到「{label}」，無法比對"
    return pf.value, None


def ts_unavailable(rid: str, field: str, name: str, why: str, actual: Any) -> CheckResult:
    """同商品說明書沒有可比對的值：轉人工覆核，說明原因（上手投資人須知專屬規則也用）。"""
    return result(
        rid,
        field,
        S.REVIEW_REQUIRED,
        actual=actual,
        reason="term_sheet_unavailable",
        message=why,
        item=Item.term_sheet(name),
    )


def _vs_term_sheet(
    ctx: IisContext,
    rid: str,
    field: str,
    name: str,
    found: Found,
    normalize: Callable[[Any], Any],
    tolerance: str | None,
) -> CheckResult:
    pf, item = read_iis(ctx.sheet, field), Item.term_sheet(name)
    if not pf.ok:
        return as_iis([doc_review(rid, field, pf, item=item)])[0]
    expected, why = found
    if why:
        return ts_unavailable(rid, field, name, why, pf.value)
    ok = normalize(expected) == normalize(pf.value)
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        tolerance=tolerance,
        item=item,
    )


def _name(text: str) -> str:
    """商品名稱比對：去空白、括號統一全形，去掉「（下稱／以下簡稱「本商品」）」及其後文字。"""
    return _NAME_TAIL.sub("", full_brackets(squash(text)))


def _min_subscription(ctx: IisContext) -> Found:
    occs, why = _term_sheet_field(ctx, "min_amounts", "最低申購金額")
    if why:
        return None, why
    occ = next((o for o in occs if o.name == "最低申購金額"), None)
    if occ is None or not occ.value.ok:
        return None, "同商品說明書讀不到「最低申購金額」，無法比對"
    return occ.value.value, None


def _underlying_names(ctx: IisContext) -> Found:
    rows, why = _term_sheet_field(ctx, "underlying_prices", "價格表")
    if why:
        return None, why
    if not all(r.name for r in rows):
        return None, "同商品說明書價格表有標的沒有名稱，無法比對"
    return [r.name for r in rows], None


def term_sheet_fields(ctx: IisContext) -> list[CheckResult]:
    names = lambda v: [squash(x) for x in v] if isinstance(v, list) else v  # noqa: E731
    checks = (
        ("isin", "ISIN", lambda: _term_sheet_field(ctx, "isin", "ISIN"), lambda v: v, None),
        (
            "name_zh",
            "中文商品名稱",
            lambda: _term_sheet_field(ctx, "name_zh", "中文商品名稱"),
            _name,
            "忽略空白；全形／半形括號不計；不看「下稱「本商品」」",
        ),
        (
            "name_en",
            "英文商品名稱",
            lambda: _term_sheet_field(ctx, "name_en", "英文商品名稱"),
            _name,
            "忽略所有空白；全形／半形括號不計",
        ),
        ("min_subscription", "最低申購金額", lambda: _min_subscription(ctx), lambda v: v, None),
        ("underlying_names", "標的中文名稱", lambda: _underlying_names(ctx), names, "忽略空白，依標的順序"),
    )
    out = [
        _vs_term_sheet(ctx, f"iis.{field}", field, name, expected(), normalize, tolerance)
        for field, name, expected, normalize, tolerance in checks
        if ctx.sheet.provides(field)
    ]
    if ctx.sheet.provides("issue_date"):
        out.append(_issue_date(ctx))
    return out


def _issue_date(ctx: IisContext) -> CheckResult:
    """發行日是回填欄位：表上有值就和參考條件表比（說明書錯時不在這裡重複報），空白時和說明書（將回填的值）比。"""
    ov = ctx.base.order.fields.get("issue_date")
    if ov is not None and ov.value is not None:
        return as_iis([reference.compare_field(ctx.base, "iis.issue_date", "issue_date", "發行日", to_date, "日期")])[0]
    found = _term_sheet_field(ctx, "issue_date", "發行日")
    return _vs_term_sheet(ctx, "iis.issue_date", "issue_date", "發行日", found, lambda v: v, None)


# ---------------------------------------------------------------- 入口


def run_all(ctx: IisContext) -> list[CheckResult]:
    """文件本身 → 參考條件表 → 同商品說明書；審查標準另由 review_standard.iis_review_standard_rules 執行。"""
    out = [pages(ctx)]
    if ctx.sheet.provides("page_totals"):
        out.append(page_totals(ctx))
    if ctx.sheet.provides("product_codes"):
        out.extend(product_codes(ctx))
    out.extend(as_iis(reference_fields(ctx)))
    out.extend(term_sheet_fields(ctx))
    return out


def trade_date(ctx: IisContext) -> ParsedField:
    """刊印日期規則用的交易日：取參考條件表（投資人須知上沒有交易日）。"""
    ov = ctx.base.order.fields.get("trade_date")
    value = to_date(ov.value) if ov is not None else None
    if value is None:
        return ParsedField.missing("trade_date", f"{ctx.base.order.source}沒有交易日，無法決定刊印日期的範圍")
    return ParsedField.present("trade_date", value, [])
