"""JPM 專屬規則（docs/rules/jpm-check-rules.md）：年利率其他出處、說明書內部交叉驗證、日期表結構、假設價格表。

參考條件表欄位（含標的依代號配對）、價格推算、審查標準（含 JPM 分節：保證機構與總代理人名稱、商品種類）與回填是
各上手共用的規則。只有年利率其他出處要讀參考條件表（ADR 0009，見 REFERENCE_FIELDS）。
KO 觀察方式、第一個可提前出場期與日期表排法的一致由 parser 判定（不一致時那三個欄位轉人工覆核）。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import re

from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import full_brackets, squash
from . import kit
from .kit import Check, IssuerContext

ISSUER = "JPM"
# 本上手規則可讀的參考條件表欄位（ADR 0009，須在 reference_fields 宣告：年利率其他出處依表上年利率核對，同 Citi）
REFERENCE_FIELDS = ("coupon_pa_pct",)
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_10",
        "description": "固定警語 2.～10. 的全文不核對（只保留全文禁用語檢查；2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的中文名稱與交易所不核對；以第 9 項價格表彭博代碼為準",
    },
    {
        "rule_id": "doc.initial_prices",
        "description": "期初價格本身的外部正確性（沒有權威來源）；說明書沒有寫期初定價方式，期初定價欄不核對",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 15 項情境分析只核對假設價格表 = 第 9 項價格表、假設本金 = 面額；情境算式、假設收盤價、適用匯率、"
        "交割股數與平均年化報酬率不核對（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構、總代理人、保證機構的地址電話、總代理人與計算代理機構負責人、信用評等、適用匯率條款不核對"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：D 型多標的非記憶式、P 型 Non-Call < 天期的非記憶式、無自動提前出場、多標的 KI "
        "每日觀察、單一標的 KI 到期觀察、4～5 檔標的、HKD／CNH／EUR／GBP／ZAR 計價；範本以外的寫法會轉人工覆核",
    },
]


def _norm_name(value: str) -> str:
    """名稱比對：忽略空白，括號全半形不計。"""
    return full_brackets(squash(value))


# ---------------------------------------------------------------- 年利率其他出處（核對規則 §3.3，讀參考條件表）


def annual_mentions(ctx: IssuerContext) -> list[CheckResult]:
    """D 型第 8 項自動提前出場配息金額「x Y% x 1/12 x n/N」的 Y = 參考條件表年利率（P 型沒有這處）。"""
    if not ctx.ts.annual_mentions:
        return []
    return kit.rate_mentions(
        ctx,
        "derive.coupon_pa_mentions",
        "coupon_pa_pct",
        "年利率",
        ctx.ts.annual_mentions,
        kit.cmp_pct,
        "依說明書顯示位數四捨五入後比對",
        lambda annual: "自動提前出場配息金額的年利率應等於年利率",
    )


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    code, trustee = f("product_code"), f("trustee_product_code")
    zh, art1 = f("name_zh"), f("name_art1")
    cz, c5 = f("currency_zh"), f("currency_art5")
    return [
        kit.doc_check(
            ctx, "doc.trustee_product_code", "trustee_product_code", "受託機構商品代號", code, trustee
        ).compare(
            code.value,
            trustee.value or "（空白）",
            ok=code.ok and trustee.ok and trustee.value == code.value,
            fail_message="封面受託機構商品代號須等於 TDCC 商品代號" + ("（說明書空白）" if trustee.value == "" else ""),
        ),
        kit.doc_check(ctx, "doc.name_consistency", "name_art1", "第 I 部分第 1 項商品名稱", zh, art1).compare(
            _norm_name(zh.value) if zh.ok else None,
            _norm_name(art1.value) if art1.ok else None,
            tolerance="忽略空白；括號全半形不計",
            fail_message="第 I 部分第 1 項商品名稱須等於封面商品中文名稱（不含「(下稱「本商品」)」）",
        ),
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "第 I 部分第 5 項計價幣別", cz, c5).compare(
            squash(cz.value) if cz.ok else None, squash(c5.value) if c5.ok else None
        ),
    ]


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 日期表列數 = 中英文名稱月數。"""
    f = ctx.ts.f
    tenor, table, zh, en = f("tenor_months"), f("date_table"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months = months(zh, r"^摩根大通(\d+)個月", "商品中文名稱")
    en_months = months(en, r"^JPMorgan(\d+)Months", "商品英文名稱")
    return [
        kit.doc_check(ctx, rid, "date_table", "第 12 項日期表期數", tenor, table).compare(
            tenor.value, len(table.value["rows"]) if table.ok else None, fail_message="日期表列數須等於天期"
        ),
        kit.doc_check(ctx, rid, "name_months_zh", "商品中文名稱月數", tenor, zh_months).compare(
            tenor.value, zh_months.value
        ),
        kit.doc_check(ctx, rid, "name_months_en", "商品英文名稱月數", tenor, en_months).compare(
            tenor.value, en_months.value
        ),
    ]


def underlying_count(ctx: IssuerContext) -> list[CheckResult]:
    """第 8 項單一標的／多標的寫法（有無「最差表現連結標的：」定義句）與第 9 項價格表檔數一致（1 檔／2 檔以上）。"""
    f = ctx.ts.f
    worst, table = f("worst_of_wording"), f("underlying_table")
    label = {True: "多標的（最差表現）", False: "單一標的"}
    return [
        kit.doc_check(ctx, "doc.underlying_count", "worst_of_wording", "第 8 項標的數寫法", table, worst).compare(
            label[len(table.value.tickers) >= 2] if table.ok else None,
            label.get(worst.value) if worst.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 8 項有無「最差表現連結標的：」定義須與第 9 項價格表檔數（1 檔／2 檔以上）一致",
        )
    ]


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KI：第 8 項觸碰事件寫法 ⇔ 有無「觸碰價：」 ⇔ 價格表觸碰價欄（範本規格 §4.4）。"""
    f = ctx.ts.f
    ki, pct, table = f("ki_type"), f("ki_pct"), f("underlying_table")
    doc_ki = kit.doc_ki(ki)
    label = {True: "有 KI", False: "無 KI"}
    check = Check("doc.ki_consistency", "ki_pct", Item.expected("第 8 項觸碰價"), ctx.document)
    if doc_ki is None:
        return [check.review(ki)]
    has_pct = pct.status == FieldStatus.PRESENT
    return [
        check.needs(ki).compare(
            label[doc_ki != "none"],
            label[has_pct] if has_pct or pct.status == FieldStatus.NOT_APPLICABLE else None,
            ok=has_pct == (doc_ki != "none"),
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            evidence=ki.evidence + pct.evidence,
            fail_message="第 8 項有無「觸碰價：」須與觸碰事件的寫法一致",
        ),
        kit.doc_check(ctx, "doc.ki_consistency", "underlying_table", "價格表觸碰價欄", ki, table).compare(
            label[doc_ki != "none"],
            label["ki" in table.value.columns] if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="價格表有無觸碰價欄須與 KI 型態一致",
        ),
    ]


def redemption(ctx: IssuerContext) -> list[CheckResult]:
    """第 8 項到期贖回第 (1) 款 = 每單位面額 x 100%（固定）。"""
    pf = ctx.ts.f("maturity_redemption_pct")
    check = Check("doc.redemption_pct", "maturity_redemption_pct", Item.expected("第 8 項到期贖回"), ctx.document)
    return [check.needs(pf).compare(100, pf.value, fail_message="第 8 項到期贖回第 (1) 款須為每單位面額 x 100%")]


def scenario(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項假設價格表逐格 = 第 9 項價格表（同順序）；假設原計價幣別本金 = 每單位面額（核對規則 §3.4、§3.7）。"""
    f = ctx.ts.f
    table, scen = f("underlying_table"), f("scenario_table")
    check = kit.doc_check(ctx, "doc.scenario_table", "scenario_table", "第 15 項假設價格表", table, scen)
    shape = (
        []
        if not (table.ok and scen.ok)
        else [
            f"假設價格表{what}與第 9 項價格表不同（{a}／{b}）"
            for what, a, b in (
                ("欄位", scen.value.columns, table.value.columns),
                ("列數", len(scen.value.rows), len(table.value.rows)),
            )
            if a != b
        ]
    )
    if shape:  # 欄數或列數不符：表格讀法有疑義，轉人工覆核（核對規則 §3.7）
        result = check.result(S.REVIEW_REQUIRED, reason="document_inconsistent", message="；".join(shape))
    elif table.ok and scen.ok:
        issues = [
            f"第 {i} 列 {'、'.join(diff)} 與第 9 項價格表第 {i} 檔不同"
            for i, (a, b) in enumerate(zip(scen.value.prices, table.value.prices, strict=True), start=1)
            if (diff := [c for c in b if a.get(c) != b[c]])
        ]
        result = check.compare(
            None, None, ok=not issues, reason="value_mismatch", message="；".join(issues) or "逐格等於第 9 項價格表"
        )
    else:
        result = check.compare(None, None)
    denom, principal = f("denomination"), f("scenario_principal")
    return [
        result,
        kit.doc_check(ctx, "doc.scenario_table", "scenario_principal", "第 15 項假設本金", denom, principal).compare(
            denom.value, principal.value, fail_message="第 15 項假設原計價幣別本金須等於每單位面額"
        ),
    ]


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    table, final, maturity, k = (
        f(x) for x in ("date_table", "final_valuation_date", "maturity_date", "first_callable_period")
    )
    names = {"schedule.coupon_dates": "日期表配息支付日與末列", "schedule.autocall_dates": "日期表提前出場期別"}
    if not table.ok:
        return [Check(rid, "date_table", Item.expected(n), ctx.document).review(table) for rid, n in names.items()]
    kind, rows = table.value["kind"], table.value["rows"]
    observed, what = ("end", "觀察期間終止日") if kind == "D-3" else ("valuation", "評價日")
    issues = []
    payments = [r["payment"] for r in rows]
    if any(a >= b for a, b in zip(payments, payments[1:], strict=False)):
        issues.append("配息支付日沒有逐期遞增")
    late = [r["period"] for r in rows if r[observed] is not None and r[observed] >= r["payment"]]
    if late:
        issues.append(f"第 {'、'.join(map(str, late))} 期{what}沒有早於配息支付日")
    if final.ok and rows[-1][observed] != final.value:
        issues.append(f"末列{what} {rows[-1][observed]} 不等於最後評價日 {final.value}")
    if maturity.ok and payments[-1] != maturity.value:
        issues.append(f"末列配息支付日 {payments[-1]} 不等於到期日 {maturity.value}")
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
    if kind == "D-3":  # 觀察期間起始日為 N.A 的期別 = 第 1～k 期（JP2、JP4）；k 讀不到時轉人工覆核
        blank = [r["period"] for r in rows if r["start"] is None]
        issues = (
            []
            if not k.ok or blank == list(range(1, k.value + 1))
            else [f"觀察期間起始日為「N.A」的期別應為第 1～{k.value} 期，實際為 {blank or '無'}"]
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
        *annual_mentions(ctx),
        *document_info(ctx),
        *periods(ctx),
        *underlying_count(ctx),
        *ki_terms(ctx),
        *redemption(ctx),
        *scenario(ctx),
        *schedule(ctx),
    ]
