"""情境分析的共用規則：重印價格表、情境假設值、較差情境寫出的價格（Issue #294；docs/rules/shared-check-rules.md §7）。

只讀標準欄位，範本沒有（上手交出 absent）的那一塊不產生結果：

- `scenario.reprint`：`scenario_reprint_table` 逐格、表頭百分比、代碼與列數 = `main_price_table`（每張情境價格表一個結果）。
- `scenario.params`：`scenario_params` 每一處 = 出處代號對應的正式值（說明書的標準欄位；範本沒有時改比參考條件表）。
- `scenario.strike`：`scenario_prices` 每一處 = `underlying_prices` 那一檔那一欄（執行價等接受 #259 的兩種進位）。

錯訊用中文欄名，只寫哪一列、哪一欄、兩邊各是多少。
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import PriceRow, ScenarioParam, ScenarioPrice, TableAt
from . import kit
from .kit import Check, IssuerContext, read_standard

COLUMNS = {"initial": "期初價格", "strike": "執行價", "ko": "KO 價", "ki": "下限價"}
OFFICIAL = {  # 正式值的標準欄位 → 錯訊裡的名稱
    "denomination": "每單位面額",
    "tenor_months": "天期",
    "coupon_pa_pct": "年利率",
    "monthly_coupon_pct": "月配息率",
    "issue_price_pct": "發行價格",
    "strike_pct": "執行價 %",
}
# 範本沒有正式值（absent）時改比的參考條件表欄位；月配息率由年利率 ÷ 12 推（上手須在 reference_fields 宣告）
SHEET_KEY = {
    "denomination": "denomination",
    "tenor_months": "tenor_months",
    "coupon_pa_pct": "coupon_pa_pct",
    "monthly_coupon_pct": "coupon_pa_pct",
    "strike_pct": "strike_pct",
}
PCT_TARGETS = ("coupon_pa_pct", "strike_pct")  # 和參考條件表比時依說明書顯示位數四捨五入


def run(ctx: IssuerContext) -> list[CheckResult]:
    return [*reprint(ctx), *params(ctx), *prices(ctx)]


# ---------------------------------------------------------------- 重印價格表（§7.1）


def reprint(ctx: IssuerContext) -> list[CheckResult]:
    scen = read_standard(ctx.ts, "scenario_reprint_table")
    if scen.status == FieldStatus.NOT_APPLICABLE:
        return []
    if not scen.ok:
        return [
            Check("scenario.reprint", "scenario_reprint_table", Item.expected("情境價格表"), ctx.document).review(scen)
        ]
    s: TableAt = scen.value
    main = read_standard(ctx.ts, "main_price_table")
    check = Check("scenario.reprint", "scenario_reprint_table", Item.expected(s.where), ctx.document).needs(scen, main)
    if (problem := check.blocked) is not None:
        return [problem]
    m: TableAt = main.value
    a, b = s.table, m.table
    shape = []
    if set(a.columns) != set(b.columns):
        shape.append(f"價格欄{_names(a.columns)}，{m.where}為{_names(b.columns)}")
    if len(a.rows) != len(b.rows):
        shape.append(f"列數 {len(a.rows)}，{m.where}為 {len(b.rows)}")
    if shape:  # 表格讀法有疑義，不判對錯
        return [check.result(S.REVIEW_REQUIRED, reason="document_inconsistent", message="；".join(shape))]
    issues = [
        f"表頭{COLUMNS[c]} % {a.pcts[c]}%，{m.where}為 {b.pcts[c]}%"
        for c in b.columns
        if c in a.pcts and c in b.pcts and a.pcts[c] != b.pcts[c]
    ]
    for i, (x, y) in enumerate(zip(a.rows, b.rows, strict=True), start=1):
        issues += [
            f"第 {i} 列{COLUMNS[c]} {_shown(x.prices.get(c))}，{m.where}為 {_shown(y.prices.get(c))}"
            for c in b.columns
            if x.prices.get(c) != y.prices.get(c)
        ]
        if x.ticker and y.ticker and x.ticker != y.ticker:
            issues.append(f"第 {i} 列彭博代碼 {x.ticker}，{m.where}為 {y.ticker}")
    return [
        check.compare(
            kit.price_cells(b.prices),
            kit.price_cells(a.prices),
            ok=not issues,
            message="；".join(issues) or f"逐格等於{m.where}",
        )
    ]


def _shown(price: Decimal | None) -> str:
    return "（空白）" if price is None else f"{price:,}"


def _names(columns) -> str:
    return "（" + "、".join(COLUMNS[c] for c in columns) + "）"


# ---------------------------------------------------------------- 情境假設值（§7.2）


def params(ctx: IssuerContext) -> list[CheckResult]:
    container = read_standard(ctx.ts, "scenario_params")
    if container.status == FieldStatus.NOT_APPLICABLE:
        return []
    if not container.ok:
        return [
            Check("scenario.params", "scenario_params", Item.expected("情境假設值"), ctx.document).review(container)
        ]
    seen: Counter[str] = Counter()
    out = []
    for p in container.value:
        seen[p.code] += 1
        out.append(_param(ctx, p, f"{p.code}_{seen[p.code]}"))
    return out


def _param(ctx: IssuerContext, p: ScenarioParam, field: str) -> CheckResult:
    name = f"{p.where}{p.name}"
    times = () if p.times is None else (p.times,)
    official = read_standard(ctx.ts, p.target)
    what = OFFICIAL[p.target]
    if official.status == FieldStatus.NOT_APPLICABLE:
        return _against_sheet(ctx, p, field, name, times)
    check = Check("scenario.params", field, Item.expected(name), ctx.document).needs(p.value, *times, official)
    if (problem := check.blocked) is not None:
        return problem
    expected = _times(official.value, p.times)
    suffix = f" × {p.times.value}" if p.times is not None else ""
    return check.compare(expected, p.value.value, fail_message=f"須等於說明書{what}{suffix}")


def _against_sheet(ctx: IssuerContext, p: ScenarioParam, field: str, name: str, times) -> CheckResult:
    """範本沒有正式值：和參考條件表比（面額、天期、年利率、執行價 % 同名欄位；月配息率 = 年利率 ÷ 12）。"""
    key = SHEET_KEY.get(p.target)
    what = OFFICIAL[p.target]
    if key is None:
        return Check("scenario.params", field, Item.expected(name), ctx.document).result(
            S.REVIEW_REQUIRED,
            actual=p.value.value if p.value.ok else None,
            evidence=p.value.evidence,
            reason="document_not_applicable",
            message=f"範本沒有{what}，參考條件表也沒有可比的欄位",
        )
    convert = kit.to_int if p.target == "tenor_months" else kit.to_decimal
    v, ov, problem = kit.order_value(
        ctx, key, "scenario.params", field, p.value, convert, "數字", name=OFFICIAL.get(key, what)
    )
    if problem is not None:
        return problem
    monthly = p.target == "monthly_coupon_pct"
    item = Item.derived(name, [ov]) if monthly else Item.sheet(name, [ov])
    check = Check("scenario.params", field, item, ctx.document, ov=(ov,)).needs(p.value, *times)
    if (problem := check.blocked) is not None:
        return problem
    actual = p.value.value
    if monthly:
        expected, tolerance = kit.monthly_of_annual(v), "年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值"
        ok = expected == actual
    elif p.target in PCT_TARGETS:
        ok, expected = kit.cmp_pct(v, actual)
        tolerance = "依說明書顯示位數四捨五入後比對"
    else:
        expected, tolerance = _times(v, p.times), None
        ok = expected == actual
    return check.compare(
        expected,
        actual,
        ok=ok,
        tolerance=tolerance,
        message=f"範本沒有{what}，和參考條件表比",
        fail_message=f"；須等於{'年利率 ÷ 12' if monthly else what}",
    )


def _times(value: Any, times: ParsedField | None) -> Any:
    return value if times is None else value * times.value


# ---------------------------------------------------------------- 較差情境寫出的價格（§7.3）


def prices(ctx: IssuerContext) -> list[CheckResult]:
    container = read_standard(ctx.ts, "scenario_prices")
    if container.status == FieldStatus.NOT_APPLICABLE:
        return []
    if not container.ok:
        return [
            Check("scenario.strike", "scenario_prices", Item.expected("較差情境價格"), ctx.document).review(container)
        ]
    table = read_standard(ctx.ts, "underlying_prices")
    return [_price(ctx, sp, table, f"scenario_price_{n}") for n, sp in enumerate(container.value, start=1)]


def _price(ctx: IssuerContext, sp: ScenarioPrice, table: ParsedField, field: str) -> CheckResult:
    col = COLUMNS[sp.column]
    which = f"第 {sp.row} 檔" if sp.row is not None else ""
    check = Check("scenario.strike", field, Item.expected(f"{sp.where}{which}{col}"), ctx.document)
    check = check.needs(sp.value, table)
    if (problem := check.blocked) is not None:
        return problem
    rows: tuple[PriceRow, ...] = table.value
    if sp.row is None or not 1 <= sp.row <= len(rows) or sp.column not in rows[sp.row - 1].prices:
        what = "認不出是價格表的哪一檔" if sp.row is None else f"價格表沒有第 {sp.row} 檔的{col}"
        return check.result(
            S.REVIEW_REQUIRED,
            actual=f"{sp.value.value:,}",
            evidence=sp.value.evidence,
            reason="document_inconsistent",
            message=f"情境寫的{col}{what}",
        )
    row = rows[sp.row - 1].prices
    actual: Decimal = sp.value.value
    expected = row[sp.column]
    if sp.column == "initial":
        ok, tolerance = actual == expected, "數值相等"
    else:
        pct = kit.field_value(ctx.ts, f"{sp.column}_pct")
        ok, tolerance = kit.scenario_price_ok(actual, expected, row.get("initial"), pct), kit.SCENARIO_PRICE_ROUNDING
    return check.compare(
        f"{expected:,}",
        f"{actual:,}",
        ok=ok,
        tolerance=tolerance,
        evidence=sp.value.evidence,
        fail_message=f"第 {sp.row} 檔{col} {actual:,}，價格表為 {expected:,}",
    )
