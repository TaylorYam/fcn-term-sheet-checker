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
from ..schema import CheckResult, DocKind, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import TermSheet
from ..text import full_brackets, squash
from . import reference
from .kit import (
    Q4,
    Check,
    Context,
    cmp_pct,
    order_review,
    order_value,
    price_item,
    read_standard,
    to_date,
    to_decimal,
    to_int,
)

MONTHLY_TOLERANCE = Decimal("0.0001")  # 同 BARC 說明書月配息率推算（docs/rules/barc-check-rules.md）
NO_TERM_SHEET = "沒有可比對的同商品說明書（這批沒有、讀不到或沒有配對成功），無法比對"
_NAME_TAIL = re.compile(r"（(?:以下簡稱|下稱)「本商品」）.*$")


@dataclass
class IisIssuerContext:
    """上手投資人須知專屬規則的輸入：投資人須知讀出結果與同商品說明書（上手自己 parser 的讀出結果，可讀專屬欄位；
    這批沒有、讀不到或沒有配對成功時為 None）。不含參考條件表（同 ADR 0005 的說明書內部規則）。"""

    sheet: IisSheet
    term_sheet: TermSheet | None
    document: DocKind = DocKind.IIS  # 被核對的文件，訊息與結果都以它稱呼


# ---------------------------------------------------------------- 文件本身


def pages(ctx: Context) -> CheckResult:
    expected, actual = ctx.std.iis_pages, ctx.pages
    return Check("iis.pages", "pages", Item.standard("投資人須知頁數"), ctx.document).compare(
        expected, actual, message=f"預期 {expected} 頁／實際 {actual} 頁"
    )


def page_totals(ctx: Context) -> CheckResult:
    rid, pf, item = "iis.page_totals", read_iis(ctx.iis, "page_totals"), Item.expected("頁首總頁數")
    check = Check(rid, "page_totals", item, ctx.document, expected=ctx.pages).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    bad = sorted({m for m in pf.value if m != ctx.pages})
    return check.compare(
        ctx.pages,
        bad or ctx.pages,
        ok=not bad,
        fail_message=f"頁首寫「共 {'、'.join(map(str, bad))} 頁」，實際 {ctx.pages} 頁",
    )


def product_codes(ctx: Context) -> list[CheckResult]:
    rid, container = "iis.product_code", read_iis(ctx.iis, "product_codes")
    if not container.ok:
        return [Check(rid, "product_codes", Item.expected("封面商品代號"), ctx.document).review(container)]
    return [
        Check(rid, occ.field, Item.expected(occ.name), ctx.document, expected=ctx.file_code)
        .needs(occ.value)
        .compare(ctx.file_code, occ.value.value, fail_message=f"{occ.where}與檔名前 12 碼不同，可能放錯檔案")
        for occ in container.value
    ]


# ---------------------------------------------------------------- 參考條件表


def _prices(ctx: Context) -> list[CheckResult]:
    """各標的期初價格、執行價、KO 價：表上值四捨五入到 4 位後相等；VWAP 時改和說明書價格表同一列比。"""
    rid, rows = "iis.underlying_prices", read_iis(ctx.iis, "underlying_prices")
    table = Check(rid, "price_table", Item.sheet("價格表"), ctx.document)
    if not rows.ok:
        return [table.review(rows)]
    uls = read_iis(ctx.iis, "underlyings")
    if uls.ok and len(uls.value) != len(rows.value):
        return [
            table.needs(rows).result(
                S.REVIEW_REQUIRED,
                expected=len(uls.value),
                actual=len(rows.value),
                reason="document_inconsistent",
                message=f"價格表有 {len(rows.value)} 列，連結標的資產有 {len(uls.value)} 檔",
            )
        ]
    vwap = reference.is_vwap(ctx)
    ts_rows, why = _term_sheet_field(ctx, "underlying_prices", "價格表") if vwap else (None, None)
    order = ctx.order
    out = []
    for i, row in enumerate(rows.value, start=1):
        for col, zh in reference.PRICE_COLUMNS:
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
                ov = order.price(i, col)
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
            out.append(
                Check(rid, field, item, ctx.document).compare(
                    expected, doc_v, evidence=ev, tolerance=None if vwap else "表上值四捨五入（half-up）到 4 位"
                )
            )
    return out


def _monthly_coupon(ctx: Context) -> CheckResult:
    """月配息率 = 參考條件表年利率 ÷ 12（差 ≤ 0.0001 視為一致，同 BARC 說明書規則）。"""
    rid, pf = "iis.monthly_coupon", read_iis(ctx.iis, "monthly_coupon_pct")
    annual, ov, problem = order_value(
        ctx, "coupon_pa_pct", rid, "monthly_coupon_pct", pf, to_decimal, "數字", name="月配息率"
    )
    if problem:
        return problem
    check = Check(rid, "monthly_coupon_pct", Item.derived("月配息率", [ov]), ctx.document, ov=(ov,)).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    expected = (annual / 12).quantize(Q4, ROUND_HALF_UP)
    ok = abs(expected - pf.value) <= MONTHLY_TOLERANCE
    return check.compare(expected, pf.value, ok=ok, tolerance=f"差 ≤ {MONTHLY_TOLERANCE}", message="須等於年利率 ÷ 12")


def _monthly_coupons(ctx: Context) -> list[CheckResult]:
    """月配息率的每一處（MS）= 參考條件表年利率 ÷ 12，四捨五入（half-up）到 4 位後相等（同 MS 說明書 §3.3）。"""
    rid, container = "iis.monthly_coupon", read_iis(ctx.iis, "monthly_coupons")
    if not container.ok:
        return [Check(rid, "monthly_coupons", Item.expected("月配息率"), ctx.document).review(container)]
    annual, ov, problem = order_value(
        ctx, "coupon_pa_pct", rid, "monthly_coupons", None, to_decimal, "數字", name="月配息率"
    )
    if problem:
        return [problem]
    expected = (annual / 12).quantize(Q4, ROUND_HALF_UP)
    return [
        Check(rid, occ.field, Item.derived(occ.name, [ov]), ctx.document, ov=(ov,))
        .needs(occ.value)
        .compare(
            expected,
            occ.value.value,
            tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位",
            message=f"{occ.where}須等於年利率 ÷ 12",
        )
        for occ in container.value
    ]


def _first_callable(ctx: Context) -> CheckResult:
    """「自第 k 個…開始」的 k = 參考條件表 Non-Call(月)（第一個可以提前出場的期別）。"""
    rid, key, name = "field.first_callable_period", "first_callable_period", "第一個可提前出場期"
    pf = read_iis(ctx.iis, key)
    v, ov, problem = order_value(ctx, key, rid, key, pf, to_int, "整數", name=name)
    if problem:
        return problem
    check = Check(rid, key, Item.column(name, [ov]), ctx.document, ov=(ov,), expected=v).needs(pf)
    return check.compare(v, pf.value, message="Non-Call(月) = 第一個可以提前出場的期別（最小為 1）")


def _ko_observation_dates(ctx: Context) -> list[CheckResult]:
    """期間每日觀察的觀察起日 = 同商品說明書提前出場排程第 k 期的比價日（k 取投資人須知）；迄日 = 參考條件表最終比價日。"""
    rid = "iis.ko_observation_dates"
    start, k = read_iis(ctx.iis, "ko_observation_start"), read_iis(ctx.iis, "first_callable_period")
    check = Check(rid, "ko_observation_start", Item.term_sheet("KO 觀察起日"), ctx.document)
    if not (start.ok and k.ok):
        out = [check.review(start if not start.ok else k)]
    else:
        schedule, why = _term_sheet_field(ctx, "autocall_schedule", "提前出場排程")
        expected = None if why else schedule.dates.get(k.value)
        if expected is None:
            why = why or f"同商品說明書提前出場排程沒有第 {k.value} 期，無法比對"
            out = [ts_unavailable(rid, "ko_observation_start", "KO 觀察起日", why, start.value)]
        else:
            out = [
                check.needs(start).compare(
                    expected,
                    start.value,
                    message=f"須等於說明書第 {k.value} 期的比價日（第 {k.value} 個配息週期終止日）",
                )
            ]
    end, name = read_iis(ctx.iis, "ko_observation_end"), "KO 觀察迄日"
    v, ov, problem = order_value(
        ctx, "final_valuation_date", rid, "ko_observation_end", end, to_date, "日期", name=name
    )
    if problem:
        return [*out, problem]
    check = Check(rid, "ko_observation_end", Item.sheet(name, [ov]), ctx.document, ov=(ov,), expected=v).needs(end)
    return [*out, check.compare(v, end.value, message="須等於最終比價日")]


# 投資人須知的 KI %：只比數值（無 KI 時表上的空值寫法由說明書那份的 `field.ki_pct` 判定）
_KI_PCT = reference.FieldCheck("field.ki_pct", "ki_pct", "KI %", to_decimal, "數字", cmp_pct, reference.PCT_TOLERANCE)


def reference_fields(ctx: Context) -> list[CheckResult]:
    """參考條件表有的欄位，只核對範本有的：單欄比對取自欄位核對表（rules/reference.FIELD_CHECKS）。"""
    has, table = ctx.provides, reference.FIELD_CHECKS
    checks: list[tuple[str, Callable[[], CheckResult | list[CheckResult]]]] = [
        ("currency_zh", lambda: reference.currency(ctx)),
        ("denomination", lambda: table["denomination"].check(ctx)),
        ("underlyings", lambda: reference.underlyings(ctx)),
        ("tenor_months", lambda: table["tenor_months"].check(ctx)),
        ("maturity_date", lambda: table["maturity_date"].check(ctx)),
        ("coupon_pa_pct", lambda: table["coupon_pa_pct"].check(ctx)),
        ("monthly_coupon_pct", lambda: _monthly_coupon(ctx)),
        ("strike_pct", lambda: table["strike_pct"].check(ctx)),
        ("ko_pct", lambda: table["ko_pct"].check(ctx)),
        ("ki_pct", lambda: _KI_PCT.check(ctx)),
        ("underlying_prices", lambda: _prices(ctx)),
        # 日期與提前出場、觸及下限條件（docs/rules/iis-check-rules.md §3 D 類，MS）
        ("trade_date", lambda: table["trade_date"].check(ctx)),
        ("final_valuation_date", lambda: table["final_valuation_date"].check(ctx)),
        ("monthly_coupons", lambda: _monthly_coupons(ctx)),
        ("ko_observation", lambda: reference.ko_observation(ctx)),
        ("ko_memory", lambda: reference.ko_memory(ctx)),
        ("first_callable_period", lambda: _first_callable(ctx)),
        ("ko_observation_start", lambda: _ko_observation_dates(ctx)),
        ("ki_type", lambda: reference.ki_type(ctx)),
    ]
    out: list[CheckResult] = []
    for name, check in checks:
        if has(name):
            r = check()
            out.extend(r if isinstance(r, list) else [r])
    return out


# ---------------------------------------------------------------- 同商品說明書


Found = tuple[Any, str | None]  # （說明書的值, 無法比對的原因）


def _term_sheet_field(ctx: Context, name: str, label: str) -> Found:
    """同商品說明書讀出的標準欄位值；沒有可比對的說明書或讀不到時回傳原因（`label` 是給作業人員看的名稱）。"""
    if ctx.term_sheet is None:
        return None, NO_TERM_SHEET
    pf = read_standard(ctx.term_sheet, name)
    if not pf.ok:
        return None, f"同商品說明書讀不到「{label}」，無法比對"
    return pf.value, None


def ts_unavailable(rid: str, field: str, name: str, why: str, actual: Any) -> CheckResult:
    """同商品說明書沒有可比對的值：轉人工覆核，說明原因（上手投資人須知專屬規則也用）。"""
    return Check(rid, field, Item.term_sheet(name)).result(
        S.REVIEW_REQUIRED, actual=actual, reason="term_sheet_unavailable", message=why
    )


def _vs_term_sheet(
    ctx: Context,
    rid: str,
    field: str,
    name: str,
    found: Found,
    normalize: Callable[[Any], Any],
    tolerance: str | None,
) -> CheckResult:
    pf = read_iis(ctx.iis, field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    expected, why = found
    if why:
        return ts_unavailable(rid, field, name, why, pf.value)
    return check.compare(expected, pf.value, ok=normalize(expected) == normalize(pf.value), tolerance=tolerance)


def _name(text: str) -> str:
    """商品名稱比對：去空白、括號統一全形，去掉「（下稱／以下簡稱「本商品」）」及其後文字。"""
    return _NAME_TAIL.sub("", full_brackets(squash(text)))


def _min_subscription(ctx: Context) -> Found:
    occs, why = _term_sheet_field(ctx, "min_amounts", "最低申購金額")
    if why:
        return None, why
    occ = next((o for o in occs if o.name == "最低申購金額"), None)
    if occ is None or not occ.value.ok:
        return None, "同商品說明書讀不到「最低申購金額」，無法比對"
    return occ.value.value, None


def _underlying_names(ctx: Context) -> Found:
    rows, why = _term_sheet_field(ctx, "underlying_prices", "價格表")
    if why:
        return None, why
    if not all(r.name for r in rows):
        return None, "同商品說明書價格表有標的沒有名稱，無法比對"
    return [r.name for r in rows], None


def term_sheet_fields(ctx: Context) -> list[CheckResult]:
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
        if ctx.provides(field)
    ]
    if ctx.provides("issue_date"):
        out.append(_issue_date(ctx))
    return out


_ISSUE_DATE = reference.FieldCheck("iis.issue_date", "issue_date", "發行日", to_date, "日期")


def _issue_date(ctx: Context) -> CheckResult:
    """發行日是回填欄位：表上有值就和參考條件表比（說明書錯時不在這裡重複報），空白時和說明書（將回填的值）比。"""
    ov = ctx.order.get("issue_date")
    if ov is not None and ov.value is not None:
        return _ISSUE_DATE.check(ctx)
    found = _term_sheet_field(ctx, "issue_date", "發行日")
    return _vs_term_sheet(ctx, "iis.issue_date", "issue_date", "發行日", found, lambda v: v, None)


# ---------------------------------------------------------------- 入口


def run_all(ctx: Context) -> list[CheckResult]:
    """文件本身 → 參考條件表 → 同商品說明書；審查標準另由 review_standard.iis_review_standard_rules 執行。"""
    out = [pages(ctx)]
    if ctx.provides("page_totals"):
        out.append(page_totals(ctx))
    if ctx.provides("product_codes"):
        out.extend(product_codes(ctx))
    out.extend(reference_fields(ctx))
    out.extend(term_sheet_fields(ctx))
    return out


def trade_date(ctx: Context) -> ParsedField:
    """刊印日期規則用的交易日：取參考條件表（投資人須知上沒有交易日）。"""
    ov = ctx.order.get("trade_date")
    value = to_date(ov.value) if ov is not None else None
    if value is None:
        return ParsedField.missing("trade_date", f"{ctx.order.source}沒有交易日，無法決定刊印日期的範圍")
    return ParsedField.present("trade_date", value, [])
