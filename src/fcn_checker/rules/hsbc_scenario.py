"""HSBC 情境明列的簡單算式；不用 eval，不驗證假設股價或複雜實物交割。

情境假設值（天期、面額、配息率、配息期數、發行價格、年化報酬率）與情境寫出的執行價、觸及不保本價格由共用情境規則
（rules/scenario.py，Issue #294）核對；這裡只重算期初投資金額、配息金額與總額、本金給付、損益與期數範圍。
每筆結果的項目名稱由這裡直接給：情境假設寫「情境假設<項目>」，各情境寫「情境 n <項目>」。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from ..parsers.hsbc import SCENARIO_CCY, scenario_formulas, scenario_number, scenario_text, scenario_worst
from ..schema import CheckStatus as S
from ..schema import Evidence, Item
from . import kit
from .kit import Check

N = r"([\d,]+(?:\.\d+)?)"
Q2 = kit.Q2
# Issuers may sum unrounded coupons, so only the printed profit total may differ from its printed items.
PROFIT_TOLERANCE = Decimal("0.01")


SCENARIO = Item.expected("情境試算")
number = scenario_number


def scenario(i, what):
    """第 i 個情境（從 0 起）的項目名稱，例：情境 3 損益金額。"""
    return f"情境 {i + 1} {what}"


def run(ctx):
    ts = ctx.ts
    first = ts.f("first_callable_period")
    deps = [ts.f(k) for k in ["coupon_pa_pct", "tenor_months", "denomination", "issue_price_pct"]]
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return [Check("doc.scenario_calculations", "scenario", SCENARIO, ctx.document).review(bad)]
    text = scenario_text(ts.scenario_index, ts.f("ki_type"))
    if text is None:  # 情境數量或順序不符已知範本：共用情境規則的情境假設值已轉人工覆核
        return []
    rate, tenor, denom, issue_price = [p.value for p in deps]
    monthly = rate / 12
    unit = denom * monthly / 100
    notional = (denom * issue_price / 100).quantize(Q2, ROUND_HALF_UP)
    ccy = SCENARIO_CCY
    out = []
    serial = 0

    def compare(rid, label, name, expected, actual, index, start, end, valid=None, tolerance=None):
        nonlocal serial
        serial += 1
        out.append(
            Check(rid, f"{label}.{serial}", Item.expected(name), ctx.document).compare(
                expected,
                actual,
                ok=valid,
                tolerance=tolerance,
                evidence=[Evidence.of(x) for x in index.lines_for(start, end)],
            )
        )

    def missing(label, name, index):
        out.append(
            Check("doc.scenario_calculations", label, Item.expected(name), ctx.document).result(
                S.REVIEW_REQUIRED,
                reason="scenario_formula_unknown",
                message="必核情境公式缺漏、損壞或寫法未知",
                evidence=[Evidence.of(x) for x in index.lines[:2]],
            )
        )

    def mentions(index, pattern, expected, label, name, required=True):
        ms = list(index.finditer(pattern))
        if required and not ms:
            missing(label, name, index)
        for m in ms:
            compare("doc.scenario_parameters", label, name, expected, number(m[1]), index, m.start(), m.end())

    assumptions = text.assumptions
    initial = list(assumptions.finditer(r"每單位期初投資金額=" + ccy + N + r"\(=" + N + r"×" + N + r"%\)"))
    if not initial:
        missing("initial_investment", "情境試算期初投資金額", assumptions)
    for m in initial:
        compare(
            "doc.scenario_calculations",
            "initial_investment",
            "情境試算期初投資金額",
            notional,
            number(m[1]),
            assumptions,
            m.start(),
            m.end(),
            valid=number(m[1]) == notional and number(m[2]) == denom and number(m[3]) == issue_price,
        )
    expected_count = len(text.segments)
    for i, segment in enumerate(text.segments):
        text_i = segment.text
        worst = scenario_worst(segment)
        expected_period = first.value if i == 0 and first.ok else tenor
        if i == 0 and not first.ok:
            item = Item.expected("第一個可提前出場期")
            out.append(Check("doc.scenario_calculations", "first_callable_period", item, ctx.document).review(first))
        mentions(
            segment,
            r"第1個至第(\d+)個計息期間",
            Decimal(expected_period),
            f"s{i + 1}.period_range",
            scenario(i, "計息期間"),
            required=True,
        )
        coupon_hits = []
        principal_hits = []
        fraction_amounts = []
        for m, principal in scenario_formulas(segment):
            d, r = number(m[1]), number(m[2])
            factor = Decimal(m[3]) if m[3] else Decimal(1)
            divisor = Decimal(m[4]) if m[4] else Decimal(1)
            actual = number(m[5])
            if principal:
                principal_hits.append(m)
                expected = denom
                compare(
                    "doc.scenario_calculations",
                    f"s{i + 1}.principal",
                    scenario(i, "本金給付金額"),
                    expected,
                    actual,
                    segment,
                    m.start(),
                    m.end(),
                    valid=d == denom and r == 100 and actual == expected,
                )
                continue
            coupon_hits.append(m)  # 配息計算面額與配息率由共用情境規則 scenario.params 核對
            if divisor == 0:
                missing(f"s{i + 1}.fraction", scenario(i, "部分期間比例"), segment)
                continue
            partial = m[4] is not None
            if partial:
                # Both numerator and denominator are explicit scenario assumptions, not a market calendar.
                expected = (unit * factor / divisor).quantize(Q2, ROUND_HALF_UP)
                fraction_amounts.append(expected)
            else:
                expected = (unit * factor).quantize(Q2, ROUND_HALF_UP)
                if m[3]:
                    compare(
                        "doc.scenario_parameters",
                        f"s{i + 1}.formula_periods",
                        scenario(i, "配息公式期數"),
                        Decimal(expected_period),
                        factor,
                        segment,
                        m.start(),
                        m.end(),
                    )
            compare(
                "doc.scenario_calculations",
                f"s{i + 1}.coupon_amount",
                scenario(i, "配息金額"),
                expected,
                actual,
                segment,
                m.start(),
                m.end(),
            )
        if not coupon_hits or len(re.findall(r"(?<!總)配息金額=", text_i)) > len(coupon_hits):
            missing(f"s{i + 1}.coupon_formula", scenario(i, "配息公式"), segment)
        if (i == 0 or (expected_count == 4 and i == 2)) and not principal_hits:
            missing(f"s{i + 1}.principal_formula", scenario(i, "本金給付公式"), segment)
        total_pattern = r"(\d+)個計息期間配息金額共為" + ccy + N
        totals = list(segment.finditer(total_pattern))
        total = (unit * Decimal(expected_period)).quantize(Q2, ROUND_HALF_UP)
        if i > 0 and not totals:
            missing(f"s{i + 1}.total_coupon", scenario(i, "配息總額"), segment)
        for m in totals:
            compare(
                "doc.scenario_parameters",
                f"s{i + 1}.total_periods",
                scenario(i, "配息總期數"),
                Decimal(expected_period),
                Decimal(m[1]),
                segment,
                m.start(),
                m.end(),
            )
            compare(
                "doc.scenario_calculations",
                f"s{i + 1}.total_coupon",
                scenario(i, "配息總額"),
                total,
                number(m[2]),
                segment,
                m.start(),
                m.end(),
            )
        if worst:
            # Complex redemption is explicitly excluded; do not feed an unchecked redemption into a PASS profit calculation.
            continue
        term = ccy + r"[\d,]+(?:\.\d+)?"
        sums = list(segment.finditer(r"=" + term + r"(?:[+-]" + term + r"){2,3}=" + ccy + N))
        if not sums:
            missing(f"s{i + 1}.profit", scenario(i, "損益金額"), segment)
        for m in sums:
            expression = m[0][1:].rsplit("=", 1)[0]
            parts = re.findall(r"([+-]?)" + ccy + N, expression)
            values = [(-1 if sign == "-" else 1) * number(v) for sign, v in parts]
            expected_values = [denom, total] + fraction_amounts + [-notional]
            actual = number(m[1])
            expected = sum(expected_values)
            compare(
                "doc.scenario_calculations",
                f"s{i + 1}.profit",
                scenario(i, "損益金額"),
                expected,
                actual,
                segment,
                m.start(),
                m.end(),
                valid=values == expected_values and abs(actual - expected) <= PROFIT_TOLERANCE,
                tolerance=f"損益總額與各項加總相差 ≤ {PROFIT_TOLERANCE}；各項金額須完全相等",
            )
    return out
