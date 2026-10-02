"""HSBC 情境參數及明列簡單算式；不用 eval，不驗證假設股價或複雜實物交割。"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from ..parsers.hsbc import ScenarioIndex
from ..schema import CheckStatus as S
from ..schema import Evidence
from . import common

N = r"([\d,]+(?:\.\d+)?)"
Q2, Q4 = Decimal("0.01"), Decimal("0.0001")


def number(s):
    return Decimal(s.replace(",", ""))


def run(ctx):
    ts = ctx.ts
    first = ts.f("first_callable_period")
    deps = [
        ts.f(k)
        for k in ["coupon_pa_pct", "tenor_months", "denomination", "currency_zh", "issue_price_pct", "price_table"]
    ]
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return [
            common.doc_review("doc.scenario_parameters", "scenario", bad),
            common.doc_review("doc.scenario_calculations", "scenario", bad),
        ]
    ti = ts.scenario_index
    headings = list(ti.finditer(r"情境分析([一二三四五六])\)"))
    expected_count = 3 if ts.f("ki_type").ok and ts.f("ki_type").value == "none" else 4
    if len(headings) != expected_count or [m[1] for m in headings] != list("一二三四")[:expected_count]:
        return [
            common.result(
                "doc.scenario_parameters",
                "scenario",
                S.REVIEW_REQUIRED,
                reason="scenario_unknown",
                message="情境數量或順序不符已知範本",
                evidence=[Evidence.of(x) for x in ts.scenarios[:2]],
            )
        ]
    rate, tenor, denom, currency, issue_price, _ = [p.value for p in deps]
    monthly = rate / 12
    unit = denom * monthly / 100
    notional = (denom * issue_price / 100).quantize(Q2, ROUND_HALF_UP)
    money = re.escape(currency)
    out = []
    serial = 0

    def compare(rid, label, expected, actual, index, start, end, valid=None):
        nonlocal serial
        serial += 1
        ok = expected == actual if valid is None else valid
        out.append(
            common.result(
                rid,
                f"{label}.{serial}",
                S.PASS if ok else S.MISMATCH,
                expected=expected,
                actual=actual,
                reason="" if ok else "value_mismatch",
                evidence=[Evidence.of(x) for x in index.lines_for(start, end)],
            )
        )

    def missing(label, index):
        out.append(
            common.result(
                "doc.scenario_calculations",
                label,
                S.REVIEW_REQUIRED,
                reason="scenario_formula_unknown",
                message="必核情境公式缺漏、損壞或寫法未知",
                evidence=[Evidence.of(x) for x in index.lines[:2]],
            )
        )

    def mentions(index, pattern, expected, label, required=True):
        ms = list(index.finditer(pattern))
        if required and not ms:
            missing(label, index)
        for m in ms:
            compare("doc.scenario_parameters", label, expected, number(m[1]), index, m.start(), m.end())

    a = ti.lines_for(0, headings[0].start())
    assumptions = ScenarioIndex(a)
    mentions(assumptions, r"商品天期為(\d+)個月期", Decimal(tenor), "assumption.tenor")
    mentions(assumptions, r"每單位面額為" + money + N + "元", denom, "assumption.denomination")
    mentions(assumptions, r"固定配息率為" + N + "%", monthly.quantize(Q4, ROUND_HALF_UP), "assumption.monthly")
    mentions(assumptions, r"配息期數=(\d+)", Decimal(tenor), "assumption.periods")
    mentions(assumptions, r"發行價格為" + N + "%", issue_price, "assumption.issue_price")
    initial = list(assumptions.finditer(r"每單位期初投資金額=" + money + N + r"\(=" + N + r"×" + N + r"%\)"))
    if not initial:
        missing("initial_investment", assumptions)
    for m in initial:
        compare(
            "doc.scenario_calculations",
            "initial_investment",
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
            out.append(common.doc_review("doc.scenario_calculations", "first_callable_period", first))
        mentions(segment, r"(?:存續期間|本商品於)(\d+)個月", Decimal(tenor), f"s{i + 1}.tenor", required=False)
        mentions(segment, r"於(\d+)個月存續期間", Decimal(tenor), f"s{i + 1}.tenor", required=i > 0)
        mentions(segment, r"共(\d+)次配息", Decimal(tenor), f"s{i + 1}.coupon_count", required=i > 0)
        mentions(
            segment, r"第1個至第(\d+)個計息期間", Decimal(expected_period), f"s{i + 1}.period_range", required=True
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
                    expected,
                    actual,
                    segment,
                    m.start(),
                    m.end(),
                    valid=d == denom and r == 100 and actual == expected,
                )
                continue
            coupon_hits.append(m)
            compare("doc.scenario_parameters", f"s{i + 1}.coupon_notional", denom, d, segment, m.start(), m.end())
            compare(
                "doc.scenario_parameters",
                f"s{i + 1}.coupon_monthly",
                monthly.quantize(Q4, ROUND_HALF_UP),
                r,
                segment,
                m.start(),
                m.end(),
            )
            if divisor == 0:
                missing(f"s{i + 1}.fraction", segment)
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
                        Decimal(expected_period),
                        factor,
                        segment,
                        m.start(),
                        m.end(),
                    )
            compare(
                "doc.scenario_calculations", f"s{i + 1}.coupon_amount", expected, actual, segment, m.start(), m.end()
            )
        if not coupon_hits or len(re.findall(r"(?<!總)配息金額=", text)) > len(coupon_hits):
            missing(f"s{i + 1}.coupon_formula", segment)
        if (i == 0 or (expected_count == 4 and i == 2)) and not principal_hits:
            missing(f"s{i + 1}.principal_formula", segment)
        total_pattern = r"(\d+)個計息期間配息金額共為" + money + N
        totals = list(segment.finditer(total_pattern))
        total = (unit * Decimal(expected_period)).quantize(Q2, ROUND_HALF_UP)
        if i > 0 and not totals:
            missing(f"s{i + 1}.total_coupon", segment)
        for m in totals:
            compare(
                "doc.scenario_parameters",
                f"s{i + 1}.total_periods",
                Decimal(tenor),
                Decimal(m[1]),
                segment,
                m.start(),
                m.end(),
            )
            compare(
                "doc.scenario_calculations",
                f"s{i + 1}.total_coupon",
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
            if (required and not refs) or mentions_count > len(refs):
                missing(f"s{i + 1}.reference_{key}", segment)
            for m in refs:
                if row is None or key not in row["prices"]:
                    missing(f"s{i + 1}.reference_{key}", segment)
                else:
                    compare(
                        "doc.scenario_parameters",
                        f"s{i + 1}.reference_{key}",
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
            missing(f"s{i + 1}.profit", segment)
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
                expected,
                actual,
                segment,
                m.start(),
                m.end(),
                valid=values == expected_values and actual == expected,
            )
        if i > 0:
            annual = list(segment.finditer(r"平均年化報酬率\(以簡單平均年化報酬率之方式計算\)為" + N + "%"))
            if not annual:
                missing(f"s{i + 1}.annualized", segment)
            for m in annual:
                compare(
                    "doc.scenario_general_annualized",
                    f"s{i + 1}.annualized",
                    rate.quantize(Q2, ROUND_HALF_UP),
                    number(m[1]),
                    segment,
                    m.start(),
                    m.end(),
                )
    return out
