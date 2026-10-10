"""HSBC 情境參數及明列簡單算式；不用 eval，不驗證假設股價或複雜實物交割。

每筆結果的項目名稱由這裡直接給：情境假設寫「情境假設<項目>」，各情境寫「情境 n <項目>」。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from ..parsers.hsbc import SCENARIO_HEADING, ScenarioIndex
from ..parsers.money import CURRENCY
from ..schema import CheckStatus as S
from ..schema import Evidence, Item
from . import kit
from .kit import Check

N = r"([\d,]+(?:\.\d+)?)"
Q2, Q4 = kit.Q2, kit.Q4
# Issuers may sum unrounded coupons, so only the printed profit total may differ from its printed items.
PROFIT_TOLERANCE = Decimal("0.01")


SCENARIO = Item.expected("情境試算")


def number(s):
    return Decimal(s.replace(",", ""))


def assumption(what):
    """情境假設段落的項目名稱。"""
    return f"情境假設{what}"


def scenario(i, what):
    """第 i 個情境（從 0 起）的項目名稱，例：情境 3 損益金額。"""
    return f"情境 {i + 1} {what}"


def run(ctx):
    ts = ctx.ts
    first = ts.f("first_callable_period")
    deps = [ts.f(k) for k in ["coupon_pa_pct", "tenor_months", "denomination", "issue_price_pct", "price_table"]]
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return [
            Check("doc.scenario_parameters", "scenario", SCENARIO, ctx.document).review(bad),
            Check("doc.scenario_calculations", "scenario", SCENARIO, ctx.document).review(bad),
        ]
    ti = ts.scenario_index
    headings = list(ti.finditer(SCENARIO_HEADING))
    expected_count = 3 if ts.f("ki_type").ok and ts.f("ki_type").value == "none" else 4
    if len(headings) != expected_count or [m[1] for m in headings] != list("一二三四")[:expected_count]:
        return [
            Check("doc.scenario_parameters", "scenario", SCENARIO, ctx.document).result(
                S.REVIEW_REQUIRED,
                reason="scenario_unknown",
                message="情境數量或順序不符已知範本",
                evidence=[Evidence.of(x) for x in ts.scenarios[:2]],
            )
        ]
    rate, tenor, denom, issue_price, _ = [p.value for p in deps]
    monthly = rate / 12
    unit = denom * monthly / 100
    notional = (denom * issue_price / 100).quantize(Q2, ROUND_HALF_UP)
    money = CURRENCY  # 金額前的幣別字（任一已知寫法）：這裡只核對數字，幣別由共用規則 field.currency 逐處核對
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

    a = ti.lines_for(0, headings[0].start())
    assumptions = ScenarioIndex(a)
    monthly_shown = monthly.quantize(Q4, ROUND_HALF_UP)
    mentions(assumptions, r"商品天期為(\d+)個月期", Decimal(tenor), "assumption.tenor", assumption("天期"))
    mentions(
        assumptions, r"每單位面額為" + money + N + "元", denom, "assumption.denomination", assumption("每單位面額")
    )
    mentions(assumptions, r"固定配息率為" + N + "%", monthly_shown, "assumption.monthly", assumption("固定配息率"))
    mentions(assumptions, r"配息期數=(\d+)", Decimal(tenor), "assumption.periods", assumption("配息期數"))
    mentions(assumptions, r"發行價格為" + N + "%", issue_price, "assumption.issue_price", assumption("發行價格"))
    initial = list(assumptions.finditer(r"每單位期初投資金額=" + money + N + r"\(=" + N + r"×" + N + r"%\)"))
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
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(ti.text)
        segment = ScenarioIndex(ti.lines_for(heading.start(), end))
        # Remove overlap caused by a heading sharing its original line with preceding text.
        text = segment.text
        worst = "交割股數" in text or "零股數" in text or "實物給付" in text
        expected_period = first.value if i == 0 and first.ok else tenor
        if i == 0 and not first.ok:
            item = Item.expected("第一個可提前出場期")
            out.append(Check("doc.scenario_calculations", "first_callable_period", item, ctx.document).review(first))
        tenor_name = scenario(i, "天期")
        mentions(
            segment, r"(?:存續期間|本商品於)(\d+)個月", Decimal(tenor), f"s{i + 1}.tenor", tenor_name, required=False
        )
        mentions(segment, r"於(\d+)個月存續期間", Decimal(tenor), f"s{i + 1}.tenor", tenor_name, required=i > 0)
        mentions(
            segment, r"共(\d+)次配息", Decimal(tenor), f"s{i + 1}.coupon_count", scenario(i, "配息次數"), required=i > 0
        )
        mentions(
            segment,
            r"第1個至第(\d+)個計息期間",
            Decimal(expected_period),
            f"s{i + 1}.period_range",
            scenario(i, "計息期間"),
            required=True,
        )
        # Denomination, monthly percentage, optional full periods or partial-period fraction.
        formula = money + N + r"[×xX]" + N + r"%(?:[×xX](\d+)(?:/(\d+))?)?=" + money + N
        formulas = list(segment.finditer(formula))
        coupon_hits = []
        principal_hits = []
        fraction_amounts = []
        for m in formulas:
            before = text[max(0, m.start() - 120) : m.start()]
            principal_pos = max(before.rfind("提前到期給付金額"), before.rfind("到期贖回金額"))
            coupon_pos = before.rfind("配息金額")
            principal = principal_pos >= 0 and principal_pos > coupon_pos
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
            coupon_hits.append(m)
            compare(
                "doc.scenario_parameters",
                f"s{i + 1}.coupon_notional",
                scenario(i, "配息計算面額"),
                denom,
                d,
                segment,
                m.start(),
                m.end(),
            )
            compare(
                "doc.scenario_parameters",
                f"s{i + 1}.coupon_monthly",
                scenario(i, "配息率"),
                monthly_shown,
                r,
                segment,
                m.start(),
                m.end(),
            )
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
        if not coupon_hits or len(re.findall(r"(?<!總)配息金額=", text)) > len(coupon_hits):
            missing(f"s{i + 1}.coupon_formula", scenario(i, "配息公式"), segment)
        if (i == 0 or (expected_count == 4 and i == 2)) and not principal_hits:
            missing(f"s{i + 1}.principal_formula", scenario(i, "本金給付公式"), segment)
        total_pattern = r"(\d+)個計息期間配息金額共為" + money + N
        totals = list(segment.finditer(total_pattern))
        total = (unit * Decimal(expected_period)).quantize(Q2, ROUND_HALF_UP)
        if i > 0 and not totals:
            missing(f"s{i + 1}.total_coupon", scenario(i, "配息總額"), segment)
        for m in totals:
            compare(
                "doc.scenario_parameters",
                f"s{i + 1}.total_periods",
                scenario(i, "配息總期數"),
                Decimal(tenor),
                Decimal(m[1]),
                segment,
                m.start(),
                m.end(),
            )
            compare(
                "doc.scenario_calculations",
                f"s{i + 1}.total_coupon",
                scenario(i, "配息總額"),
                (unit * tenor).quantize(Q2, ROUND_HALF_UP),
                number(m[2]),
                segment,
                m.start(),
                m.end(),
            )
        # Stock-specific price references must identify the same row, not merely any equal numeric value.
        rows = ts.f("price_table").value["rows"]
        named = [row for row in rows if row["label"] and "（" + row["label"] + "）" in text]
        row = rows[0] if len(rows) == 1 else named[0] if len(named) == 1 else None
        for label, key in [("執行價", "strike"), ("觸及不保本價格", "ki")]:
            refs = list(segment.finditer(re.escape(label) + money + N))
            required = i >= 2 and (key == "strike" or expected_count == 4)
            mentions_count = len(re.findall(re.escape(label) + money, text))
            name = scenario(i, label)
            if (required and not refs) or mentions_count > len(refs):
                missing(f"s{i + 1}.reference_{key}", name, segment)
            for m in refs:
                if row is None or key not in row["prices"]:
                    missing(f"s{i + 1}.reference_{key}", name, segment)
                else:
                    compare(
                        "doc.scenario_parameters",
                        f"s{i + 1}.reference_{key}",
                        name,
                        row["prices"][key],
                        number(m[1]),
                        segment,
                        m.start(),
                        m.end(),
                    )
        if worst:
            # Complex redemption is explicitly excluded; do not feed an unchecked redemption into a PASS profit calculation.
            continue
        term = money + r"[\d,]+(?:\.\d+)?"
        sums = list(segment.finditer(r"=" + term + r"(?:[+-]" + term + r"){2,3}=" + money + N))
        if not sums:
            missing(f"s{i + 1}.profit", scenario(i, "損益金額"), segment)
        for m in sums:
            expression = m[0][1:].rsplit("=", 1)[0]
            parts = re.findall(r"([+-]?)" + money + N, expression)
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
        if i > 0:
            annual = list(segment.finditer(r"平均年化報酬率\(以簡單平均年化報酬率之方式計算\)為" + N + "%"))
            if not annual:
                missing(f"s{i + 1}.annualized", scenario(i, "平均年化報酬率"), segment)
            for m in annual:
                compare(
                    "doc.scenario_general_annualized",
                    f"s{i + 1}.annualized",
                    scenario(i, "平均年化報酬率"),
                    rate.quantize(Q2, ROUND_HALF_UP),
                    number(m[1]),
                    segment,
                    m.start(),
                    m.end(),
                )
    return out
