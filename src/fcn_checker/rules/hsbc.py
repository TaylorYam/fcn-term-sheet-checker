"""HSBC 專屬規則：說明書內部的條件、表格、日期、文件內重複出處與審查標準。

參考條件表欄位、Non-Call、ISIN 與比價日由各上手共用的 rules/reference.py 核對。
每筆結果的項目名稱寫在規則旁（與 BARC 同一件事用同一個名稱）；預期值來自說明書其他位置或推算。
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal

from ..schema import CheckStatus as S
from ..schema import FieldStatus, Item
from ..text import squash
from . import hsbc_scenario, kit
from .kit import IssuerContext

ISSUER = "HSBC"
Q4 = Decimal("0.0001")
NOT_COVERED = [
    {"rule_id": "doc.underlying_names", "description": "標的中文名稱與外部交易所對照表不核對；以彭博代號為準"},
    {"rule_id": "doc.scenario_complex", "description": "情境股數、零股及最差情境完整贖回重算不核對"},
    {"rule_id": "doc.scenario_other_annualized", "description": "有利與最差情境年化報酬率不核對"},
    {"rule_id": "doc.external_assumptions", "description": "假設股價、匯率、Nt及交易日曆外部正確性不核對"},
    {"rule_id": "field.monthly_ki", "description": "每月KI無樣本，未知寫法轉人工覆核"},
]


# 單份核對以 IssuerContext 呼叫本模組規則；ctx.ts 為 HsbcTermSheet


PRICE_TABLE = Item.expected("價格表")


def check(rid, field, name, deps, expected, actual, ok=None, reason="value_mismatch"):
    """`name` 為項目名稱。"""
    item = Item.expected(name)
    bad = next((p for p in deps if not p.ok), None)
    if bad is not None:
        return kit.doc_review(rid, field, bad, expected, item=item)
    good = expected == actual if ok is None else ok
    return kit.result(
        rid,
        field,
        S.PASS if good else S.MISMATCH,
        expected=expected,
        actual=actual,
        evidence=[e for p in deps for e in p.evidence],
        reason="" if good else reason,
        item=item,
    )


def schedules(ctx):
    c, obs, first = ctx.ts.f("coupon_table"), ctx.ts.f("ko_observation"), ctx.ts.f("first_callable_period")
    deps = [c, obs, first]
    out = []
    if any(not p.ok for p in deps):
        bad = next(p for p in deps if not p.ok)
        return [
            kit.doc_review(rid, "schedule", bad, item=Item.expected("配息表與提前出場表"))
            for rid in [
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
            "配息期數",
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
            "配息支付日",
            [c, maturity],
            maturity.value,
            payments[-1],
            ok=maturity.ok
            and payments[-1] == maturity.value
            and all(a < b for a, b in zip(payments, payments[1:], strict=False)),
        )
    )
    if obs.value == "D":
        k = first.value
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
                "期間每日觀察的提前出場表",
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
            valid &= row["decision"] < row["payment"]
        valid &= mapped == list(range(first.value, len(rows) + 1))
        valid &= all(a["decision"] < b["decision"] for a, b in zip(ko.value, ko.value[1:], strict=False))
        out.append(
            check(
                "schedule.autocall_dates",
                "periodic",
                "定期觀察的提前出場表",
                [c, ko, first, final],
                final.value,
                ko.value[-1]["decision"],
                ok=valid and final.ok and ko.value[-1]["decision"] == final.value,
            )
        )
    return out


def prices(ctx):
    pf = ctx.ts.f("price_table")
    scenario = ctx.ts.f("scenario_table")
    if not pf.ok:
        return [
            kit.doc_review(rid, "prices", pf, item=PRICE_TABLE)
            for rid in ["derive.prices", "doc.price_header_pct", "doc.scenario_table"]
        ]
    rows = pf.value["rows"]
    out = []
    for i, row in enumerate(rows, start=1):
        for col in ["strike", "ko", "ki"]:
            actual = row["prices"].get(col)
            if actual is None:
                continue
            pct = ctx.ts.f(col + "_pct")
            exp = (row["prices"]["initial"] * pct.value / 100).quantize(Q4, ROUND_HALF_UP) if pct.ok else None
            name = kit.price_item(i, col)
            out.append(check("derive.prices", f"underlying_{i}_{col}_price", name, [pf, pct], exp, actual))
    headers = pf.value["headers"]
    for col in ["strike", "ko", "ki"]:
        pct = ctx.ts.f(col + "_pct")
        if pct.status == FieldStatus.NOT_APPLICABLE:
            out.append(
                kit.result(
                    "doc.price_header_pct",
                    col,
                    S.PASS if col not in headers else S.MISMATCH,
                    expected=None,
                    actual=headers.get(col),
                    pf=pf,
                    item=Item.expected(kit.HEADER_PCT_ITEM[col]),
                )
            )
            continue
        out.append(check("doc.price_header_pct", col, kit.HEADER_PCT_ITEM[col], [pf, pct], pct.value, headers.get(col)))
    if not scenario.ok:
        out.append(kit.doc_review("doc.scenario_table", "prices", scenario, item=Item.expected("情境試算價格表")))
    else:
        # Chinese label is deliberately excluded; currency and exchange are internal consistency only.
        def normalized(table):
            return [{k: v for k, v in row.items() if k != "label"} for row in table["rows"]]

        table, header = normalized(pf.value), normalized(scenario.value)
        out.append(check("doc.scenario_table", "prices", "情境試算價格表", [pf, scenario], table, header))
        scenario_headers = scenario.value["headers"]
        out.append(
            check("doc.scenario_header_pct", "headers", "情境試算價格表欄頭", [pf, scenario], headers, scenario_headers)
        )
    return out


def document_info(ctx):
    f = ctx.ts.f
    out = []
    out.append(
        check(
            "doc.currency_consistency",
            "currency",
            "第一章第 5 條計價幣別",
            [f("currency_zh"), f("currency_art5")],
            f("currency_zh").value,
            f("currency_art5").value,
        )
    )

    def norm(s):
        return squash(s).translate(str.maketrans({"(": "（", ")": "）"}))

    zh = f("name_zh")
    en = f("name_en")
    short = re.sub(r"（以下簡稱「本商品」）$", "", norm(zh.value)) if zh.ok else ""
    for key, name, expected in [
        ("name_title", "封面標題商品名稱", short),
        ("name_en_title", "封面標題英文商品名稱", norm(en.value) if en.ok else ""),
        ("name_art1", "第一章第 1 條商品名稱", short + (norm(en.value) if en.ok else "")),
    ]:
        pf = f(key)
        actual = norm(pf.value) if pf.ok else None
        out.append(check("doc.name_consistency", key, name, [zh, en, pf], expected, actual))
    return out


def run_all(ctx: IssuerContext) -> list:
    """說明書內部規則：只用讀出結果與審查標準，不碰參考條件表。"""
    out = prices(ctx)
    out.extend(schedules(ctx))
    out.extend(document_info(ctx))
    out.extend(hsbc_scenario.run(ctx))
    return out
