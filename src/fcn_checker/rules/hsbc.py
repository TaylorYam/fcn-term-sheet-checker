"""HSBC 專屬規則：條件、表格、日期及文件內重複出處。"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..parsers.hsbc import HsbcTermSheet
from ..parsers.layout import squash
from ..schema import CheckStatus as S
from ..schema import FieldStatus, ParsedField
from . import common, hsbc_scenario

ISSUER = "HSBC"
Q4 = Decimal("0.0001")
NOT_COVERED = [
    {"rule_id": "doc.underlying_names", "description": "標的中文名稱與外部交易所對照表不核對；以彭博代號為準"},
    {"rule_id": "doc.scenario_complex", "description": "情境股數、零股及最差情境完整贖回重算不核對"},
    {"rule_id": "doc.scenario_other_annualized", "description": "有利與最差情境年化報酬率不核對"},
    {"rule_id": "doc.external_assumptions", "description": "假設股價、匯率、Nt及交易日曆外部正確性不核對"},
    {"rule_id": "field.monthly_ki", "description": "每月KI無樣本，未知寫法轉人工覆核"},
]


@dataclass
class Context(common.Context):
    ts: HsbcTermSheet


def check(rid, field, deps, expected, actual, ok=None, reason="value_mismatch"):
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return common.doc_review(rid, field, bad, expected)
    good = expected == actual if ok is None else ok
    return common.result(
        rid,
        field,
        S.PASS if good else S.MISMATCH,
        expected=expected,
        actual=actual,
        evidence=[e for p in deps for e in p.evidence],
        reason="" if good else reason,
    )


def enum_field(ctx, name, mapping):
    pf, ov = ctx.ts.f(name), ctx.order.fields.get(name)
    if ov is None or ov.value not in mapping:
        return common.order_review("field." + name, name, ov, pf, "order_unknown_value", "整理表值缺漏或不在允許值中")
    r = check("field." + name, name, [pf], mapping[ov.value], pf.value)
    r.order_source = [ov.source]
    return r


def first_callable(ctx) -> ParsedField:
    coupons, obs = ctx.ts.f("coupon_table"), ctx.ts.f("ko_observation")
    deps = [coupons, obs]
    if obs.ok and obs.value == "D":
        deps.append(ctx.ts.f("ko_start"))
    else:
        deps.append(ctx.ts.f("ko_table"))
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return ParsedField("first_callable_period", bad.status, evidence=bad.evidence, note=bad.note)
    if obs.value == "D":
        periods = [r["period"] for r in coupons.value if r["end"] == ctx.ts.f("ko_start").value]
    else:
        first = ctx.ts.f("ko_table").value[0]
        periods = [r["period"] for r in coupons.value if r["payment"] == first["payment"]]
    ev = [e for p in deps for e in p.evidence]
    if len(periods) != 1:
        return ParsedField(
            "first_callable_period",
            FieldStatus.AMBIGUOUS,
            evidence=ev,
            candidates=periods,
            note="首個KO日期無法唯一對應配息期別",
        )
    return ParsedField("first_callable_period", FieldStatus.PRESENT, periods[0], ev)


def schedules(ctx):
    c, obs, first = ctx.ts.f("coupon_table"), ctx.ts.f("ko_observation"), first_callable(ctx)
    deps = [c, obs, first]
    out = []
    if any(not p.ok for p in deps):
        bad = next(p for p in deps if not p.ok)
        return [
            common.doc_review(rid, "schedule", bad)
            for rid in [
                "field.first_callable_period",
                "field.autocall_dates",
                "schedule.coupon_dates",
                "schedule.autocall_dates",
                "doc.coupon_periods",
            ]
        ]
    rows = c.value
    periods = [r["period"] for r in rows]
    payments = [r["payment"] for r in rows]
    tenor = ctx.ts.f("tenor_months")
    n = ctx.ts.f("coupon_periods")
    maturity = ctx.ts.f("maturity_date")
    final = ctx.ts.f("final_valuation_date")
    out.append(
        check(
            "doc.coupon_periods",
            "coupon_periods",
            [c, tenor, n],
            tenor.value,
            [n.value, len(rows)],
            ok=n.ok and tenor.ok and n.value == tenor.value == len(rows) and periods == list(range(1, len(rows) + 1)),
        )
    )
    out.append(
        check(
            "schedule.coupon_dates",
            "payment",
            [c, maturity],
            maturity.value,
            payments[-1],
            ok=maturity.ok
            and payments[-1] == maturity.value
            and all(a < b for a, b in zip(payments, payments[1:], strict=False)),
        )
    )
    ov = ctx.order.fields.get("first_callable_period")
    if ov is None or common.to_int(ov.value) is None:
        out.append(
            common.order_review(
                "field.first_callable_period",
                "first_callable_period",
                ov,
                first,
                "order_invalid",
                "首可KO期缺漏或不是整數",
            )
        )
    else:
        r = check("field.first_callable_period", "first_callable_period", [first], common.to_int(ov.value), first.value)
        r.order_source = [ov.source]
        out.append(r)
    expected_dates = {}
    if obs.value == "D":
        k = first.value
        expected_dates = {k: ctx.ts.f("ko_start").value, len(rows): rows[-1]["end"]}
        valid = all(a["end"] < b["end"] for a, b in zip(rows, rows[1:], strict=False))
        for i, row in enumerate(rows):
            valid &= row["end"] < row["payment"]
            if row["period"] <= k:
                valid &= row["start"] is None and row["nt"] is None
            else:
                prev = rows[i - 1]["end"] + dt.timedelta(days=1)
                while prev.weekday() >= 5:
                    prev += dt.timedelta(days=1)
                valid &= row["start"] == prev and row["nt"] is not None
                valid &= row["start"] is not None and row["start"] <= row["end"]
        out.append(
            check(
                "schedule.autocall_dates",
                "daily",
                [c, first, final],
                final.value,
                rows[-1]["end"],
                ok=valid and final.ok and rows[-1]["end"] == final.value,
            )
        )
    else:
        ko = ctx.ts.f("ko_table")
        mapped = []
        valid = True
        for row in ko.value:
            hits = [r["period"] for r in rows if r["payment"] == row["payment"]]
            if len(hits) != 1:
                valid = False
            else:
                mapped.append(hits[0])
                expected_dates[hits[0]] = row["decision"]
            valid &= row["decision"] < row["payment"]
        valid &= mapped == list(range(first.value, len(rows) + 1))
        valid &= all(a["decision"] < b["decision"] for a, b in zip(ko.value, ko.value[1:], strict=False))
        out.append(
            check(
                "schedule.autocall_dates",
                "periodic",
                [c, ko, first, final],
                final.value,
                ko.value[-1]["decision"],
                ok=valid and final.ok and ko.value[-1]["decision"] == final.value,
            )
        )
    for i in range(1, 13):
        key = f"autocall_date_{i}"
        ov = ctx.order.fields.get(key)
        expected = expected_dates.get(i)
        actual = ov.value if ov else None
        if actual is not None and common.to_date(actual) is None:
            out.append(
                common.order_review(
                    "field.autocall_dates", key, ctx.order.fields.get(key), c, "order_invalid", "比價日不是日期"
                )
            )
            continue
        r = check("field.autocall_dates", key, [c, first], actual, expected)
        r.order_source = [ov.source] if ov else []
        out.append(r)
    return out


def prices(ctx):
    pf = ctx.ts.f("price_table")
    scenario = ctx.ts.f("scenario_table")
    if not pf.ok:
        return [
            common.doc_review(rid, "prices", pf)
            for rid in [
                "field.underlyings",
                "field.prices",
                "derive.prices",
                "doc.price_header_pct",
                "doc.scenario_table",
            ]
        ]
    rows = pf.value["rows"]
    out = []
    ovs = [ctx.order.fields.get(f"underlying_{i}") for i in range(1, 6)]
    values = [ov.value if ov else None for ov in ovs]
    filled = [i for i, v in enumerate(values) if v is not None]
    expected = [values[i] for i in filled]
    if not filled or filled != list(range(len(filled))):
        out.append(
            common.order_review(
                "field.underlyings", "underlyings", ovs[0], pf, "order_invalid", "彭博代號缺漏或中間有空白"
            )
        )
    else:
        r = check("field.underlyings", "underlyings", [pf], expected, [r["ticker"] for r in rows])
        r.order_source = [ov.source for ov in ovs if ov]
        out.append(r)
    for i in range(1, 6):
        row = rows[i - 1] if i <= len(rows) else None
        for col in ["initial", "strike", "ko", "ki"]:
            key = f"underlying_{i}_{col}_price"
            ov = ctx.order.fields.get(key)
            actual = row["prices"].get(col) if row else None
            expected = ov.value if ov else None
            if actual is None and expected is None:
                out.append(common.result("field.prices", key, S.NOT_APPLICABLE, pf=pf, ov=[ov]))
                continue
            value = common.to_decimal(expected)
            if value is None:
                out.append(common.order_review("field.prices", key, ov, pf, "order_missing", "整理表價格缺漏或不合法"))
                continue
            rounded = value.quantize(Q4, ROUND_HALF_UP)
            r = check("field.prices", key, [pf], rounded, actual)
            r.order_source = [ov.source]
            out.append(r)
            if row and col != "initial" and actual is not None:
                pct = ctx.ts.f({"strike": "strike_pct", "ko": "ko_pct", "ki": "ki_pct"}[col])
                exp = (row["prices"]["initial"] * pct.value / 100).quantize(Q4, ROUND_HALF_UP) if pct.ok else None
                out.append(check("derive.prices", key, [pf, pct], exp, actual))
    headers = pf.value["headers"]
    for col in ["strike", "ko", "ki"]:
        pct = ctx.ts.f(col + "_pct")
        if pct.status == FieldStatus.NOT_APPLICABLE:
            out.append(
                common.result(
                    "doc.price_header_pct",
                    col,
                    S.PASS if col not in headers else S.MISMATCH,
                    expected=None,
                    actual=headers.get(col),
                    pf=pf,
                )
            )
            continue
        out.append(check("doc.price_header_pct", col, [pf, pct], pct.value, headers.get(col)))
    if not scenario.ok:
        out.append(common.doc_review("doc.scenario_table", "prices", scenario))
    else:
        # Chinese label is deliberately excluded; currency and exchange are internal consistency only.
        def normalized(table):
            return [{k: v for k, v in row.items() if k != "label"} for row in table["rows"]]

        out.append(
            check("doc.scenario_table", "prices", [pf, scenario], normalized(pf.value), normalized(scenario.value))
        )
        out.append(check("doc.scenario_header_pct", "headers", [pf, scenario], headers, scenario.value["headers"]))
    return out


def document_info(ctx):
    f = ctx.ts.f
    out = []
    iso = ctx.std.currency_zh_to_iso.get(f("currency_zh").value)
    ov = ctx.order.fields.get("currency")
    if ov is None or iso is None:
        out.append(
            common.order_review(
                "field.currency", "currency", ov, f("currency_zh"), "currency_unknown", "幣別缺漏或不在審查標準"
            )
        )
    else:
        r = check("field.currency", "currency", [f("currency_zh")], ov.value, iso)
        r.order_source = [ov.source]
        out.append(r)
    out.append(
        check(
            "doc.currency_consistency",
            "currency",
            [f("currency_zh"), f("currency_art5")],
            f("currency_zh").value,
            f("currency_art5").value,
        )
    )
    denom = f("denomination")
    standard = ctx.std.denomination.get(iso)
    if not denom.ok:
        out.append(common.doc_review("doc.denomination", "denomination", denom))
    else:
        out.append(
            common.result(
                "doc.denomination",
                "denomination",
                S.PASS if denom.value == standard else S.REVIEW_REQUIRED,
                expected=standard,
                actual=denom.value,
                pf=denom,
                reason="" if denom.value == standard else "denomination_non_standard",
            )
        )
    for key in ["minimum_trade", "minimum_subscription", "minimum_additional"]:
        out.append(check("doc.minimum_amounts", key, [denom, f(key)], denom.value, f(key).value))
    for key in ["subscription_start", "subscription_end"]:
        out.append(check("doc.subscription_dates", key, [f("trade_date"), f(key)], f("trade_date").value, f(key).value))
    for key in ["print_date_review", "print_date_final"]:
        trade, pf = f("trade_date"), f(key)
        ok = trade.ok and pf.ok and 0 <= (pf.value - trade.value).days <= ctx.std.print_date_max_days_after_trade
        out.append(check("doc.print_date", key, [trade, pf], "交易日至允許天數", pf.value, ok=ok))

    def norm(s):
        return squash(s).translate(str.maketrans({"(": "（", ")": "）"}))

    zh = f("name_zh")
    en = f("name_en")
    short = re.sub(r"（以下簡稱「本商品」）$", "", norm(zh.value)) if zh.ok else ""
    for key, expected in [
        ("name_title", short),
        ("name_en_title", norm(en.value) if en.ok else ""),
        ("name_art1", short + (norm(en.value) if en.ok else "")),
    ]:
        pf = f(key)
        out.append(check("doc.name_consistency", key, [zh, en, pf], expected, norm(pf.value) if pf.ok else None))
    return out


def run_all(ctx):
    out = [common.product_code(ctx)]
    for key, convert, what in [
        ("isin", str, "文字"),
        ("denomination", common.to_decimal, "數值"),
        ("trade_date", common.to_date, "日期"),
        ("issue_date", common.to_date, "日期"),
        ("final_valuation_date", common.to_date, "日期"),
        ("maturity_date", common.to_date, "日期"),
        ("ko_pct", common.to_decimal, "百分比"),
        ("strike_pct", common.to_decimal, "百分比"),
        ("coupon_pa_pct", common.to_decimal, "百分比"),
        ("tenor_months", common.to_int, "整數"),
    ]:
        compare = (
            (
                lambda a, b: (
                    a.quantize(Decimal("0.01"), ROUND_HALF_UP) == b.quantize(Decimal("0.01"), ROUND_HALF_UP),
                    a.quantize(Decimal("0.01"), ROUND_HALF_UP),
                )
            )
            if key == "coupon_pa_pct"
            else None
        )
        out.append(common.simple(ctx, "field." + key, key, convert, what, compare=compare))
    out.extend(
        [
            enum_field(ctx, "ko_observation", ctx.fmt.ko_observation_values),
            enum_field(ctx, "ko_memory", ctx.fmt.ko_memory_values),
            enum_field(ctx, "ki_type", ctx.fmt.ki_type_values),
        ]
    )
    ki = ctx.ts.f("ki_pct")
    ov = ctx.order.fields.get("ki_pct")
    if ki.status == FieldStatus.NOT_APPLICABLE:
        out.append(
            common.result(
                "field.ki_pct",
                "ki_pct",
                S.NOT_APPLICABLE if ov and ov.value is None else S.MISMATCH,
                expected=ov.value if ov else None,
                actual=None,
                pf=ki,
                ov=[ov],
            )
        )
    else:
        out.append(common.simple(ctx, "field.ki_pct", "ki_pct", common.to_decimal, "百分比", compare=common.cmp_pct))
    out.extend(prices(ctx))
    out.extend(schedules(ctx))
    out.extend(document_info(ctx))
    out.extend(
        [
            common.approval_date(ctx),
            common.chairman(ctx),
            common.fixed_warning(ctx),
            common.risk_level(ctx),
            common.forbidden_wording(ctx),
            common.issue_price(ctx),
        ]
    )
    out.extend(common.issuer_name(ctx, ISSUER))
    out.extend(common.distributor_info(ctx, allow_international_phone=True))
    out.extend(common.fees(ctx))
    out.extend(common.product_name(ctx, ISSUER))
    out.extend(hsbc_scenario.run(ctx, first_callable(ctx)))
    return out
