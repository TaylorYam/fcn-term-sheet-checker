"""情境分析的共用規則：重印價格表、情境假設值、較差情境寫出的價格（Issue #294；docs/rules/shared-check-rules.md §7）。

只讀標準欄位，範本沒有（上手交出 absent）的那一塊不產生結果：

- `scenario.reprint`：`scenario_reprint_table` 逐格、表頭百分比、代碼與列數 = `main_price_table`（每張情境價格表一個結果）。
- `scenario.params`：`scenario_params` 每一處 = 出處代號對應的正式值（說明書的標準欄位；範本沒有時改比參考條件表）。
- `scenario.strike`：`scenario_prices` 每一處 = `underlying_prices` 那一檔那一欄（執行價等接受 #259 的兩種進位）。

錯訊用中文欄名，只寫哪一列、哪一欄、兩邊各是多少。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import PriceRow, ScenarioParam, ScenarioPrice, TableAt
from . import kit
from .kit import Check, IssuerContext, read_standard

COLUMNS = {"initial": "期初價格", "strike": "執行價", "ko": "KO 價", "ki": "下限價"}


@dataclass(frozen=True)
class Official:
    """情境假設值要等於的正式值（標準欄位）：錯訊裡的名稱，以及範本沒有它（absent）時怎麼改比參考條件表。
    出處本身的名稱（例：本金、總投資金額）在 `standard_fields.SCENARIO_TARGETS`。"""

    name: str
    sheet_key: str | None  # 改比的參考條件表欄位（上手須在 reference_fields 宣告）；None 為表上沒有可比的欄位
    convert: Callable[[Any], Any] = kit.to_decimal
    shown_digits: bool = False  # 百分比：表上的值依說明書顯示位數四捨五入後比
    of_sheet: Callable[[Decimal], Decimal] | None = None  # 由表上的值推得預期值（月配息率 = 年利率 ÷ 12）


OFFICIAL = {
    "denomination": Official("每單位面額", "denomination"),
    "tenor_months": Official("天期", "tenor_months", kit.to_int),
    "coupon_pa_pct": Official("年利率", "coupon_pa_pct", shown_digits=True),
    "monthly_coupon_pct": Official("月配息率", "coupon_pa_pct", of_sheet=kit.monthly_of_annual),
    "issue_price_pct": Official("發行價格", None),
    "strike_pct": Official("執行價 %", "strike_pct", shown_digits=True),
}


def run(ctx: IssuerContext) -> list[CheckResult]:
    return [*reprint(ctx), *params(ctx), *prices(ctx)]


def _provided(ctx: IssuerContext, rid: str, name: str, what: str) -> tuple[ParsedField | None, list[CheckResult]]:
    """讀一塊的標準欄位：範本沒有時（None, []）；交出的讀不到時（None, [人工覆核]）；否則（欄位, []）。"""
    pf = read_standard(ctx.ts, name)
    if pf.status == FieldStatus.NOT_APPLICABLE:
        return None, []
    if not pf.ok:
        return None, [Check(rid, name, Item.expected(what), ctx.document).review(pf)]
    return pf, []


# ---------------------------------------------------------------- 重印價格表（§7.1）


def reprint(ctx: IssuerContext) -> list[CheckResult]:
    field, problems = _provided(ctx, "scenario.reprint", "scenario_reprint_table", "情境價格表")
    if field is None:
        return problems
    reprinted: TableAt = field.value
    main = read_standard(ctx.ts, "main_price_table")
    check = Check("scenario.reprint", field.name, Item.expected(reprinted.where), ctx.document).needs(field, main)
    if (problem := check.blocked) is not None:
        return [problem]
    source: TableAt = main.value
    scen, own, where = reprinted.table, source.table, source.where
    shape = []
    if set(scen.columns) != set(own.columns):
        shape.append(f"價格欄{_names(scen.columns)}，{where}為{_names(own.columns)}")
    if len(scen.rows) != len(own.rows):
        shape.append(f"列數 {len(scen.rows)}，{where}為 {len(own.rows)}")
    if shape:  # 表格讀法有疑義，不判對錯
        return [check.result(S.REVIEW_REQUIRED, reason="document_inconsistent", message="；".join(shape))]
    issues = []
    if scen.pcts:  # 情境表表頭寫了百分比：每一欄都要和主表相同（情境表表頭整個沒寫百分比時不比）
        issues += [
            f"表頭{COLUMNS[c]} % {_pct(scen.pcts.get(c))}，{where}為 {_pct(own.pcts.get(c))}"
            for c in own.columns
            if scen.pcts.get(c) != own.pcts.get(c)
        ]
    for i, (row, base) in enumerate(zip(scen.rows, own.rows, strict=True), start=1):
        issues += [
            f"第 {i} 列{COLUMNS[c]} {_shown(row.prices.get(c))}，{where}為 {_shown(base.prices.get(c))}"
            for c in own.columns
            if row.prices.get(c) != base.prices.get(c)
        ]
        if row.ticker and base.ticker and row.ticker != base.ticker:
            issues.append(f"第 {i} 列彭博代碼 {row.ticker}，{where}為 {base.ticker}")
    return [
        check.compare(
            kit.price_cells(own.prices),
            kit.price_cells(scen.prices),
            ok=not issues,
            message="；".join(issues) or f"逐格等於{where}",
        )
    ]


def _shown(price: Decimal | None) -> str:
    return "（空白）" if price is None else f"{price:,}"


def _pct(pct: Decimal | None) -> str:
    return "（沒寫）" if pct is None else f"{pct}%"


def _names(columns) -> str:
    return "（" + "、".join(COLUMNS[c] for c in columns) + "）"


# ---------------------------------------------------------------- 情境假設值（§7.2）


def params(ctx: IssuerContext) -> list[CheckResult]:
    field, problems = _provided(ctx, "scenario.params", "scenario_params", "情境假設值")
    if field is None:
        return problems
    seen: Counter[str] = Counter()
    out = []
    for p in field.value:
        seen[p.code] += 1
        out.append(_param(ctx, p, f"{p.code}_{seen[p.code]}"))
    return out


def _param(ctx: IssuerContext, p: ScenarioParam, field: str) -> CheckResult:
    name = f"{p.where}{p.name}"
    times = () if p.times is None else (p.times,)
    official = read_standard(ctx.ts, p.target)
    if official.status == FieldStatus.NOT_APPLICABLE:
        return _against_sheet(ctx, p, field, name, times)
    check = Check("scenario.params", field, Item.expected(name), ctx.document).needs(p.value, *times, official)
    if (problem := check.blocked) is not None:
        return problem
    suffix = f" × {p.times.value}" if p.times is not None else ""
    return check.compare(
        _times(official.value, p.times),
        p.value.value,
        fail_message=f"須等於說明書{OFFICIAL[p.target].name}{suffix}",
    )


def _against_sheet(ctx: IssuerContext, p: ScenarioParam, field: str, name: str, times) -> CheckResult:
    """範本沒有正式值：和參考條件表比（面額、天期、年利率、執行價 % 同名欄位；月配息率 = 年利率 ÷ 12）。"""
    spec = OFFICIAL[p.target]
    if spec.sheet_key is None:
        return Check("scenario.params", field, Item.expected(name), ctx.document).result(
            S.REVIEW_REQUIRED,
            actual=p.value.value if p.value.ok else None,
            evidence=p.value.evidence,
            reason="document_not_applicable",
            message=f"範本沒有{spec.name}，參考條件表也沒有可比的欄位",
        )
    sheet_name = OFFICIAL[spec.sheet_key].name
    v, ov, problem = kit.order_value(
        ctx, spec.sheet_key, "scenario.params", field, p.value, spec.convert, "數字", name=sheet_name
    )
    if problem is not None:
        return problem
    item = Item.derived(name, [ov]) if spec.of_sheet else Item.sheet(name, [ov])
    check = Check("scenario.params", field, item, ctx.document, ov=(ov,)).needs(p.value, *times)
    if (problem := check.blocked) is not None:
        return problem
    actual = p.value.value
    tolerance = None
    if spec.of_sheet is not None:
        expected, tolerance = spec.of_sheet(v), f"{sheet_name} ÷ 12 四捨五入（half-up）到 4 位後比數值"
        ok = expected == actual
    elif spec.shown_digits:
        ok, expected = kit.cmp_pct(v, actual)
        tolerance = "依說明書顯示位數四捨五入後比對"
    else:
        expected = _times(v, p.times)
        ok = expected == actual
    return check.compare(
        expected,
        actual,
        ok=ok,
        tolerance=tolerance,
        message=f"範本沒有{spec.name}，和參考條件表比",
        fail_message=f"；須等於{sheet_name + ' ÷ 12' if spec.of_sheet else spec.name}",
    )


def _times(value: Any, times: ParsedField | None) -> Any:
    return value if times is None else value * times.value


# ---------------------------------------------------------------- 較差情境寫出的價格（§7.3）


def prices(ctx: IssuerContext) -> list[CheckResult]:
    field, problems = _provided(ctx, "scenario.strike", "scenario_prices", "較差情境價格")
    if field is None:
        return problems
    table = read_standard(ctx.ts, "underlying_prices")
    return [_price(ctx, sp, table, f"scenario_price_{n}") for n, sp in enumerate(field.value, start=1)]


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
