"""Nomura 專屬規則（docs/rules/nomura-check-rules.md）：每期配息率與年利率、說明書內部交叉驗證、日期表結構、情境設定參數。

參考條件表欄位、價格推算、審查標準（含 Nomura 分節：保證機構與總代理人名稱、商品種類、發行不成立門檻、上手別的固定警語
次數）、年利率（第 14 項情境一、二的年化報酬率，`field.coupon_pa_pct`）與回填是各上手共用的規則。抓不到、歧義或寫法
不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..parsers.nomura import ISSUER_EN
from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import squash
from . import kit
from .kit import Check, IssuerContext

ISSUER = "NOMURA"
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_12",
        "description": "固定警語(二)～(十二) 的全文不核對（只保留禁用語檢查；2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的英文名稱、交易所、關連交易所不核對；以第 7 項價格表彭博代碼為準（第 8 項名稱只和投資人須知比）",
    },
    {"rule_id": "doc.initial_prices", "description": "期初價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.initial_pricing",
        "description": "期初定價方式：說明書沒有寫（價格表表頭只寫「期初價格」），參考條件表「期初定價」欄不核對（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 14 項情境三、四（假設價格、股數、剩餘金額、損失率）與情境二的算式不核對；只核對設定參數、百分比表、"
        "情境一／二年化報酬率（2026-10-15 確認）；情境三金額旁的幣別仍要核對（Issue #316）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構、總代理人、保證機構的地址電話、總代理人與代理機構負責人、信用評等、最終匯率條款、準據法不核對"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：P 型記憶式＋KI、D 型多標的非記憶式、單一標的記憶式、多標的 KI 每期觀察、"
        "單一標的 KI 每日觀察、無自動提前出場、4～5 檔標的、天期 12 個月以上、HKD／CNH／EUR／GBP／ZAR 計價、非每月配息、"
        "Nomura International Funding Pte. Ltd. 以外的發行主體；範本以外的寫法會轉人工覆核",
    },
]
MEMORY_ZH, MEMORY_EN = "(記憶式自動提前出場)", "with Memory Knock-Out"
ISSUED_BY = f"issued by {ISSUER_EN}"


# ---------------------------------------------------------------- 每期配息率（核對規則 §3.3）


def coupon(ctx: IssuerContext) -> list[CheckResult]:
    """第 7 項每期配息率 = 年利率（第 14 項情境一、二年化報酬率）÷ 12，四捨五入（half-up）到 4 位；日期表列數 = 天期
    （每月配息）才換算，否則轉人工覆核。"""
    f = ctx.ts.f
    annual, period, table, tenor = f("coupon_pa_pct"), f("coupon_period_pct"), f("date_table"), f("tenor_months")
    check = kit.doc_check(
        ctx,
        "derive.monthly_coupon",
        "coupon_period_pct",
        "第 7 項每期配息率 %（年利率 ÷ 12）",
        annual,
        period,
        table,
        tenor,
    )
    if (blocked := check.blocked) is not None:
        return [blocked]
    expected = kit.monthly_of_annual(annual.value)
    if len(table.value["rows"]) != tenor.value:
        return [
            check.result(
                S.REVIEW_REQUIRED,
                expected=expected,
                actual=period.value,
                reason="coupon_frequency_unknown",
                message=f"日期表有 {len(table.value['rows'])} 期、天期 {tenor.value} 個月，不是每月配息，無法由年利率換算每期配息率",
            )
        ]
    return [
        check.compare(
            expected,
            period.value,
            evidence=list(period.evidence),
            tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="第 7 項每期配息率須等於第 14 項情境一、二年化報酬率（年利率）÷ 12 四捨五入到 4 位",
        )
    ]


def coupon_mentions(ctx: IssuerContext) -> list[CheckResult]:
    """第 14 項設定參數、第 15 項(1) 的每期配息率 = 第 7 項（核對規則 §3.3）。"""
    main = ctx.ts.f("coupon_period_pct")
    out = []
    for m in ctx.ts.coupon_mentions:
        pf = ParsedField(
            "coupon_mention",
            FieldStatus.PRESENT if m.value is not None else FieldStatus.MISSING,
            m.value,
            list(m.evidence),
            note="" if m.value is not None else f"找不到{m.where}或出現多個",
        )
        out.append(
            kit.doc_check(ctx, "doc.coupon_mentions", "coupon_period_pct", m.where, main, pf).compare(
                main.value, pf.value, evidence=list(m.evidence), fail_message="每期配息率須等於第 7 項固定配息率"
            )
        )
    return out


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    """商品代號第 2 處、ISIN 三處、名稱（封面 = 第一章第 1 項）、幣別（中文與 ISO 代碼）。"""
    f = ctx.ts.f
    code, isin = f("product_code"), f("isin")
    out = [
        kit.doc_check(
            ctx, "doc.product_code", "product_code_en", "封面英文名稱後 TDCC No.", code, f("product_code_en")
        ).compare(
            code.value, f("product_code_en").value, fail_message="封面英文名稱後的 TDCC No. 須等於中文名稱後的商品代號"
        )
    ]
    for field, name in (("isin_en", "封面英文名稱後 ISIN"), ("isin_art1", "第一章第 1 項 ISIN")):
        out.append(
            kit.doc_check(ctx, "doc.isin_consistency", field, name, isin, f(field)).compare(
                isin.value, f(field).value, fail_message="ISIN 須與封面中文名稱後的 ISIN 相同"
            )
        )
    zh, art1_zh = f("name_zh"), f("name_art1_zh")
    out.append(
        kit.doc_check(ctx, "doc.name_consistency", "name_art1_zh", "第一章第 1 項商品中文名稱", zh, art1_zh).compare(
            zh.value,
            art1_zh.value,
            ok=zh.ok and art1_zh.ok and squash(zh.value) == squash(art1_zh.value),
            tolerance="忽略空白",
            fail_message="第一章第 1 項商品中文名稱須等於封面名稱",
        )
    )
    en, art1_en = f("name_en"), f("name_art1_en")
    expected_en = _art1_name_en(en.value) if en.ok else None
    out.append(
        kit.doc_check(ctx, "doc.name_consistency", "name_art1_en", "第一章第 1 項商品英文名稱", en, art1_en).compare(
            expected_en,
            art1_en.value,
            ok=expected_en is not None and art1_en.ok and squash(expected_en) == squash(art1_en.value),
            tolerance="發行機構寫在最前面、沒有「issued by …」（範本寫法，2026-10-15 確認）；忽略空白",
            fail_message="第一章第 1 項商品英文名稱須等於封面英文名稱（發行機構移到最前面）",
        )
    )
    cz = f("currency_zh")
    expected_iso = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    for field, name in (("currency_cover_iso", "封面計價幣別代碼"), ("currency_art5_iso", "第一章第 5 項計價幣別代碼")):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, "doc.currency_consistency", field, name, cz, pf).compare(
                expected_iso,
                pf.value,
                ok=expected_iso is not None and pf.value == expected_iso,
                fail_message="括號內的幣別代碼須對應封面中文計價幣別（審查標準幣別對照）",
            )
        )
    art5 = f("currency_art5")
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, art5).compare(
            cz.value, art5.value
        )
    )
    return out


def _art1_name_en(cover: str) -> str | None:
    """封面英文名稱 → 第一章第 1 項寫法：發行機構移到最前面、去掉「issued by …」。"""
    text = re.sub(r"\s+", " ", cover).strip()
    if ISSUED_BY not in text:
        return None
    return f"{ISSUER_EN} " + text.replace(" " + ISSUED_BY, "")


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 日期表列數 = 中英文名稱月數 = 第 14 項設定參數年期。"""
    f = ctx.ts.f
    tenor, table, zh, en = f("tenor_months"), f("date_table"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months = months(zh, r"發行(\d+)個月", "商品中文名稱")
    en_months = months(en, r"^(\d+)-Month", "商品英文名稱")
    scen = f("scenario_tenor")
    return [
        kit.doc_check(ctx, rid, "date_table", "第 11 項(6) 日期表期數", tenor, table).compare(
            tenor.value, len(table.value["rows"]) if table.ok else None, fail_message="日期表列數須等於天期"
        ),
        kit.doc_check(ctx, rid, "name_months_zh", "商品中文名稱月數", tenor, zh_months).compare(
            tenor.value, zh_months.value
        ),
        kit.doc_check(ctx, rid, "name_months_en", "商品英文名稱月數", tenor, en_months).compare(
            tenor.value, en_months.value
        ),
        kit.doc_check(ctx, rid, "scenario_tenor", "第 14 項設定參數年期", tenor, scen).compare(tenor.value, scen.value),
    ]


def _has(pf: ParsedField, word: str) -> bool | None:
    return (squash(word) in squash(pf.value)) if pf.ok else None


def ko_terms(ctx: IssuerContext) -> list[CheckResult]:
    """記憶式：第 7 項句型 ⇔ 名稱「(記憶式自動提前出場)」／「with Memory Knock-Out」（不一致轉人工覆核）；
    日期表「自動提前出場價」欄（如有）每列 = 價格表表頭 KO %。KO 觀察方式與日期表排法、D 型起日的一致由 parser 判定。"""
    f = ctx.ts.f
    mem, zh, en = f("ko_memory"), f("name_zh"), f("name_en")
    label = {True: "記憶式", False: "非記憶式"}
    out = []
    for field, pf, word, name in (
        ("name_zh", zh, MEMORY_ZH, "商品中文名稱的記憶式字樣"),
        ("name_en", en, MEMORY_EN, "商品英文名稱的記憶式字樣"),
    ):
        out.append(
            kit.doc_check(ctx, "doc.ko_memory", field, name, mem, pf).compare(
                label.get(mem.value) if mem.ok else None,
                label.get(_has(pf, word)) if pf.ok else None,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="名稱有無記憶式字樣須與第 7 項自動提前出場事件、記憶事件的句型一致",
            )
        )
    table, ko = f("date_table"), f("ko_pct")
    if table.ok and table.value["rows"] and "ko_pct" in table.value["rows"][0]:
        rows = table.value["rows"]
        bad = [r["period"] for r in rows if ko.ok and r["ko_pct"] != ko.value]
        out.append(
            kit.doc_check(ctx, "doc.date_table_ko", "date_table_ko", "日期表自動提前出場價欄", ko, table).compare(
                ko.value,
                sorted({r["ko_pct"] for r in rows}),
                ok=not bad,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message=f"第 {'、'.join(map(str, bad))} 期的自動提前出場價不等於價格表表頭 KO %（樣本沒有逐期不同的 KO）",
            )
        )
    return out


def underlying_terms(ctx: IssuerContext) -> list[CheckResult]:
    """標的數：第 7 項單一／多標的句型、第 8 項「連結標的：」／「連結標的n：」檔數、第 9 項彭博代碼 = 價格表。"""
    f = ctx.ts.f
    uls, style, art8, art9 = f("underlyings"), f("memory_style"), f("art8_names"), f("art9_tickers")
    shape = {True: "多標的", False: "單一標的"}
    count = len(uls.value) if uls.ok else None
    out = [
        kit.doc_check(ctx, "doc.underlying_count", "memory_style", "第 7 項單一／多標的句型", uls, style).compare(
            shape.get(count is not None and count > 1) if uls.ok else None,
            shape.get(style.value != "single") if style.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 7 項自動提前出場事件的單一／多標的句型須與價格表檔數一致",
        )
    ]
    art8_shape = (shape[art8.value[0] == "basket"], len(art8.value[1])) if art8.ok else None
    out.append(
        kit.doc_check(ctx, "doc.underlying_count", "art8_names", "第 8 項連結標的檔數", uls, art8).compare(
            (shape[count > 1], count) if uls.ok else None,
            art8_shape,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 8 項連結標的的寫法（單一「連結標的：」／多標的「連結標的n：」）與檔數須與價格表一致",
        )
    )
    out.append(
        kit.doc_check(ctx, "doc.underlying_tickers", "art9_tickers", "第 9 項標的彭博代碼", uls, art9).compare(
            uls.value, art9.value, fail_message="第 9 項各檔「彭博代碼：」須依序等於第 7 項價格表"
        )
    )
    return out


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KI：第 7 項下限觸發事件條款 ⇔ 價格表下限觸發價欄 ⇔ 到期贖回款數（無 KI 兩款、有 KI 三款）；
    KI 每期觀察的下限觸發決定日表每列 = 同列自動提前出場決定日（不一致轉人工覆核）。"""
    f = ctx.ts.f
    ki, table, cases = f("ki_type"), f("underlying_table"), f("maturity_cases")
    doc_ki = kit.doc_ki(ki)
    label = {True: "有 KI", False: "無 KI"}
    if doc_ki is None:
        return [Check("doc.ki_consistency", "ki_type", Item.expected("第 7 項下限觸發"), ctx.document).review(ki)]
    has = doc_ki != "none"
    out = [
        kit.doc_check(ctx, "doc.ki_consistency", "underlying_table", "價格表下限觸發價欄", ki, table).compare(
            label[has],
            label["ki" in table.value.columns] if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="價格表有無下限觸發價欄須與第 7 項下限觸發事件條款一致",
        ),
        kit.doc_check(ctx, "doc.ki_consistency", "maturity_cases", "第 7 項到期贖回款數", ki, cases).compare(
            3 if has else 2,
            cases.value,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="到期贖回款數須與下限觸發事件條款一致（無 KI 兩款、有 KI 三款）",
        ),
    ]
    ki_table, dates = f("ki_table"), f("date_table")
    if doc_ki == "P":
        check = kit.doc_check(ctx, "doc.ki_consistency", "ki_table", "第 11 項(6) 下限觸發決定日表", ki_table, dates)
        if ki_table.ok and dates.ok and dates.value["type"] == "P":
            ko_days = [r["observation"] for r in dates.value["rows"]]
            ki_days = [r["ki_date"] for r in ki_table.value["rows"]]
            bad = [n for n, (a, b) in enumerate(zip(ko_days, ki_days, strict=False), 1) if a is not None and a != b]
            ok = len(ko_days) == len(ki_days) and not bad
            out.append(
                check.compare(
                    None,
                    None,
                    ok=ok,
                    fail=S.REVIEW_REQUIRED,
                    reason="document_inconsistent",
                    message=""
                    if ok
                    else "下限觸發決定日表的期數或第 "
                    + "、".join(map(str, bad))
                    + " 期日期不等於同列自動提前出場決定日",
                )
            )
        else:
            out.append(
                check.compare(
                    None,
                    None,
                    ok=False,
                    fail=S.REVIEW_REQUIRED,
                    reason="document_inconsistent",
                    message="KI 每期觀察要有下限觸發決定日表，且日期表是每期觀察排法",
                )
            )
    elif ki_table.ok:
        out.append(
            kit.doc_check(ctx, "doc.ki_consistency", "ki_table", "第 11 項(6) 下限觸發決定日表", ki).compare(
                "沒有",
                "有",
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="只有 KI 每期觀察才有下限觸發決定日表",
            )
        )
    return out


def redemption(ctx: IssuerContext) -> list[CheckResult]:
    """第 7 項到期贖回「每單位商品面額x 100.00%」固定 100%（無 KI 1 處、有 KI 2 處）。"""
    pf, ki = ctx.ts.f("maturity_redemption_pcts"), kit.doc_ki(ctx.ts.f("ki_type"))
    check = Check("doc.redemption_pct", "maturity_redemption_pct", Item.expected("第 7 項到期贖回金額"), ctx.document)
    expected = [Decimal(100)] * (1 if ki == "none" else 2)
    return [
        check.needs(pf, ctx.ts.f("ki_type")).compare(
            expected,
            pf.value,
            ok=pf.ok and ki is not None and len(pf.value) == len(expected) and all(v == 100 for v in pf.value),
            fail_message="到期贖回須為每單位商品面額 x 100.00%（無 KI 第 (1) 款；有 KI 第 (1)、(2) 款）",
        )
    ]


def scenario(ctx: IssuerContext) -> list[CheckResult]:
    """第 14 項設定參數面額 = 第 7 項面額；百分比表每列 = 價格表表頭（期初 100%、K／KO／KI %），列數 = 標的數。
    設定參數年期、固定配息率、發行價格另由 `periods`、`coupon_mentions` 與共用發行價格規則核對。"""
    f = ctx.ts.f
    denom, scen = f("denomination"), f("scenario_denomination")
    out = [
        kit.doc_check(
            ctx, "doc.scenario_params", "scenario_denomination", "第 14 項設定參數每單位商品面額", denom, scen
        ).compare(denom.value, scen.value)
    ]
    # 投資金額那一行 `a x b% x n = c` 的 a、b、n 各自等於設定參數的面額、發行價格、投資單位數，c = a × b% × n
    line = [f(x) for x in ("scenario_investment", "scenario_investment_pct", "scenario_investment_units")]
    rate, units, amount = f("scenario_issue_price"), f("scenario_units"), f("scenario_investment_amount")
    check = kit.doc_check(
        ctx, "doc.scenario_params", "scenario_investment", "第 14 項設定參數投資金額", denom, rate, units, *line, amount
    )
    if (problem := check.blocked) is not None:
        out.append(problem)
    else:
        principal, pct, count = (pf.value for pf in line)
        total = denom.value * rate.value / 100 * units.value
        out.append(
            check.compare(
                f"{denom.value:,} x {rate.value}% x {units.value} = {total:,}",
                f"{principal:,} x {pct}% x {count} = {amount.value:,}",
                ok=(principal, pct, count) == (denom.value, rate.value, units.value)
                and amount.value == principal * pct / 100 * count,
                fail_message="投資金額須為每單位商品面額 x 發行價格 x 投資單位數",
            )
        )
    table, pcts = f("underlying_table"), f("scenario_table")
    check = kit.doc_check(ctx, "doc.scenario_table", "scenario_table", "第 14 項百分比表", table, pcts)
    if table.ok and pcts.ok:
        expected_row = [Decimal(100) if c == "initial" else table.value.pcts[c] for c in table.value.columns]
        issues = []
        if pcts.value["columns"] != list(table.value.columns):
            issues.append("欄位與價格表表頭不同")
        if len(pcts.value["rows"]) != len(table.value.rows):
            issues.append(f"有 {len(pcts.value['rows'])} 列，價格表有 {len(table.value.rows)} 檔")
        bad = [n for n, row in enumerate(pcts.value["rows"], 1) if row != expected_row]
        if bad:
            issues.append(f"第 {'、'.join(map(str, bad))} 列百分比不等於價格表表頭 {[str(x) for x in expected_row]}")
        out.append(
            check.compare(  # 預期值／實際值帶出每一格，看得出比了哪些格（Issue #316，比照 BBVA #305）
                [str(x) for _row in pcts.value["rows"] for x in expected_row],
                [str(x) for row in pcts.value["rows"] for x in row],
                ok=not issues,
                reason="value_mismatch",
                message="；".join(issues) or "每列等於價格表表頭百分比",
            )
        )
    else:
        out.append(check.compare(None, None))
    return out


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    """末列配息日 = 預定到期日；末列決定日（P）／觀察期間終止日（D）= 最終評價日；配息日遞增；決定日／終止日早於同列配息日；
    P 型第 1～k−1 期決定日為「不適用」、第 k 期起有日期。"""
    f = ctx.ts.f
    table, final, maturity, k = (
        f(x) for x in ("date_table", "final_valuation_date", "maturity_date", "first_callable_period")
    )
    names = {"schedule.coupon_dates": "日期表配息日與末列", "schedule.autocall_dates": "日期表提前出場期別"}
    if not table.ok:
        return [Check(rid, "date_table", Item.expected(n), ctx.document).review(table) for rid, n in names.items()]
    kind, rows = table.value["type"], table.value["rows"]
    observed = {"D": "end", "P": "observation"}[kind]
    issues = []
    payments = [r["payment"] for r in rows]
    if any(a >= b for a, b in zip(payments, payments[1:], strict=False)):
        issues.append("配息日沒有逐期遞增")
    late = [r["period"] for r in rows if r[observed] is not None and r[observed] >= r["payment"]]
    if late:
        issues.append(f"第 {'、'.join(map(str, late))} 期{'觀察期間終止日' if kind == 'D' else '決定日'}沒有早於配息日")
    if final.ok and rows[-1][observed] != final.value:
        issues.append(
            f"末列{'觀察期間終止日' if kind == 'D' else '決定日'} {rows[-1][observed]} 不等於最終評價日 {final.value}"
        )
    if maturity.ok and payments[-1] != maturity.value:
        issues.append(f"末列配息日 {payments[-1]} 不等於預定到期日 {maturity.value}")
    out = [
        kit.structure_check(
            ctx,
            "schedule.coupon_dates",
            "coupon_dates",
            names["schedule.coupon_dates"],
            [table, final, maturity],
            issues,
        )
    ]
    if kind == "P" and k.ok:
        blank = [r["period"] for r in rows if r["observation"] is None]
        issues = (
            []
            if blank == list(range(1, k.value))
            else [f"決定日是「不適用」的期別應為第 1～{k.value - 1} 期，實際為 {blank or '無'}"]
        )
        out.append(
            kit.structure_check(
                ctx, "schedule.autocall_dates", "autocall_dates", names["schedule.autocall_dates"], [table, k], issues
            )
        )
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果、審查標準與宣告的參考條件表欄位（年利率）。"""
    return [
        *coupon(ctx),
        *coupon_mentions(ctx),
        *document_info(ctx),
        *periods(ctx),
        *ko_terms(ctx),
        *underlying_terms(ctx),
        *ki_terms(ctx),
        *redemption(ctx),
        *scenario(ctx),
        *schedule(ctx),
    ]
