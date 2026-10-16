"""HSBC 專屬規則：說明書內部的條件、表格、日期、文件內重複出處與審查標準。

參考條件表欄位、Non-Call、ISIN 與比價日由各上手共用的 rules/reference.py 核對。
每筆結果的項目名稱寫在規則旁（與 BARC 同一件事用同一個名稱）；預期值來自說明書其他位置或推算。
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..schema import CheckStatus as S
from ..schema import FieldStatus, Item
from ..text import full_brackets, squash
from . import hsbc_scenario, kit, schedule
from .kit import Check, IssuerContext

ISSUER = "HSBC"
NOT_COVERED = [
    {"rule_id": "doc.underlying_names", "description": "標的中文名稱與外部交易所對照表不核對；以彭博代號為準"},
    {"rule_id": "doc.scenario_complex", "description": "情境股數、零股及最差情境完整贖回重算不核對"},
    {"rule_id": "doc.scenario_other_annualized", "description": "有利與最差情境年化報酬率不核對"},
    {"rule_id": "doc.external_assumptions", "description": "假設股價、匯率、Nt及交易日曆外部正確性不核對"},
    {"rule_id": "field.monthly_ki", "description": "每月KI無樣本，未知寫法轉人工覆核"},
]


# 單份核對以 IssuerContext 呼叫本模組規則；ctx.ts 為 HsbcTermSheet


PRICE_TABLE = Item.expected("價格表")


def schedules(ctx):
    """HSBC 獨有的日期表交叉驗證；期數、配息日與觀察日順序、末期日期、期始日由共用規則（rules/schedule.py）檢查。

    - 第 11 條(1)「配息期數=n」= 天期。
    - D：第 1～k 期 Nt 為「-」，之後每期都有 Nt。
    - P：定期 KO 表每列的付款日依序對到第 k～n 期的配息日（各對到唯一一期）。
    """
    f = ctx.ts.f
    c, obs, first, tenor, n = (
        f(x) for x in ("coupon_table", "ko_observation", "first_callable_period", "tenor_months", "coupon_periods")
    )
    out = [
        Check("doc.coupon_periods", "coupon_periods", Item.expected("配息期數"), ctx.document, expected=tenor.value)
        .needs(tenor, n)
        .compare(tenor.value, n.value, message="第 11 條(1)「配息期數」須等於天期")
    ]
    if not obs.ok:
        out.append(Check("schedule.autocall_dates", "schedule", Item.expected("提前出場表"), ctx.document).review(obs))
        return out
    if obs.value == "D":
        nt = Check("schedule.autocall_dates", "nt", Item.expected("第一章第 11 條日期表 Nt 欄"), ctx.document).needs(
            c, first
        )
        if (problem := nt.blocked) is not None:
            out.append(problem)
            return out
        bad = [r["period"] for r in c.value if (r["nt"] is None) != (r["period"] <= first.value)]
        out.append(
            nt.compare(
                None,
                "、".join(f"第 {p} 期" for p in bad) or None,
                ok=not bad,
                message=f"第 1～{first.value} 期 Nt 為「-」，之後每期都有 Nt",
            )
        )
        return out
    ko = f("ko_table")
    autocall = Check("schedule.autocall_dates", "periodic", Item.expected("定期觀察的提前出場表"), ctx.document).needs(
        c, ko, first
    )
    if (problem := autocall.blocked) is not None:
        out.append(problem)
        return out
    mapped = []
    for row in ko.value:
        hits = [r["period"] for r in c.value if r["payment"] == row["payment"]]
        mapped.append(hits[0] if len(hits) == 1 else None)
    expected = list(range(first.value, len(c.value) + 1))
    out.append(
        autocall.compare(
            expected,
            mapped,
            message="定期 KO 表每列的付款日須依序對到第 k～n 期的配息日",
        )
    )
    return out


def price_headers(ctx):
    """價格表欄頭百分比與情境試算價格表（價格推算是各上手共用規則，見 rules/derivation.py）。"""
    pf = ctx.ts.f("price_table")
    scenario = ctx.ts.f("scenario_table")
    if not pf.ok:
        return [
            Check(rid, "prices", PRICE_TABLE, ctx.document).review(pf)
            for rid in ["doc.price_header_pct", "doc.scenario_table"]
        ]
    out = []
    headers = pf.value.pcts
    for col in ["strike", "ko", "ki"]:
        pct = ctx.ts.f(col + "_pct")
        header = Check("doc.price_header_pct", col, Item.expected(kit.HEADER_PCT_ITEM[col]), ctx.document)
        if pct.status == FieldStatus.NOT_APPLICABLE:
            out.append(
                header.needs(pf).result(
                    S.PASS if col not in headers else S.MISMATCH, expected=None, actual=headers.get(col)
                )
            )
            continue
        header = replace(header, expected=pct.value).needs(pf, pct)
        out.append(header.compare(pct.value, headers.get(col)))
    if not scenario.ok:
        out.append(
            Check("doc.scenario_table", "prices", Item.expected("情境試算價格表"), ctx.document).review(scenario)
        )
    else:
        # Chinese label is deliberately excluded; currency and exchange are internal consistency only.
        def normalized(table):
            return [
                {
                    "ticker": r.ticker,
                    "currency": r.texts["currency"],
                    "exchange": r.texts["exchange"],
                    "prices": r.prices,
                }
                for r in table.rows
            ]

        table, header = normalized(pf.value), normalized(scenario.value)
        out.append(
            Check("doc.scenario_table", "prices", Item.expected("情境試算價格表"), ctx.document, expected=table)
            .needs(pf, scenario)
            .compare(table, header)
        )
        scenario_headers = scenario.value.pcts
        out.append(
            Check(
                "doc.scenario_header_pct",
                "headers",
                Item.expected("情境試算價格表欄頭"),
                ctx.document,
                expected=headers,
            )
            .needs(pf, scenario)
            .compare(headers, scenario_headers)
        )
    return out


def document_info(ctx):
    f = ctx.ts.f
    out = []
    cz, c5 = f("currency_zh"), f("currency_art5")
    out.append(
        Check(
            "doc.currency_consistency",
            "currency",
            Item.expected("第一章第 5 條計價幣別"),
            ctx.document,
            expected=cz.value,
        )
        .needs(cz, c5)
        .compare(cz.value, c5.value)
    )

    def norm(s):
        return full_brackets(squash(s))

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
        out.append(
            Check("doc.name_consistency", key, Item.expected(name), ctx.document, expected=expected)
            .needs(zh, en, pf)
            .compare(expected, actual)
        )
    return out


def run_all(ctx: IssuerContext) -> list:
    """說明書內部規則：只用讀出結果與審查標準，不碰參考條件表。"""
    out = price_headers(ctx)
    out.extend(schedule.run(ctx))  # 日期表共用檢查（Issue #289）；其餘上手切換後改由單份核對呼叫
    out.extend(schedules(ctx))
    out.extend(document_info(ctx))
    out.extend(hsbc_scenario.run(ctx))
    return out
