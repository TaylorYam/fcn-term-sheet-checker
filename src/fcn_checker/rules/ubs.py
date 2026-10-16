"""UBS 專屬規則（docs/rules/ubs-check-rules.md）：配息率與其他出處、說明書內部交叉驗證（受託機構商品代號、名稱、幣別、
發行分行、面額的其他出處）、KO 每日型固定／變動配息期別、KO 條款的 KO %、情境設定參數與情境價格表、配息表。

參考條件表欄位、價格推算、審查標準（含 UBS 分節：固定警語、名稱樣板、發行機構名稱、商品種類、最低金額倍數）與回填是
各上手共用的規則。說明書有年利率（商品基本資料八「年化配息率」），不讀參考條件表（REFERENCE_FIELDS 為空）。發行人、
「總代理人」字樣與期初定價依作業人員決定不核對。觸及下限事件三處一致、價格表名稱 = 第九項標的表、八與十二的提前出場
觀察日寫法一致、到期日兩個日期相同由 parser 檢查（不一致時欄位不合法或歧義，共用規則轉人工覆核）。
"""

from __future__ import annotations

from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import full_brackets, squash
from . import kit
from .kit import Check, IssuerContext

ISSUER = "UBS"
REFERENCE_FIELDS: tuple[str, ...] = ()
BRANCH_PHRASES = ("經由瑞士境外之倫敦分行", "經由倫敦分行", "透過其倫敦分行")
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_10",
        "description": "警語 2～10 的全文不核對（只保留禁用語檢查）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的中文名稱本身、交易所、價格表幣別字不核對；價格表名稱只用來對到第九項的彭博代碼",
    },
    {"rule_id": "doc.initial_prices", "description": "期初價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.guarantor_and_agent",
        "description": "發行人（瑞士商瑞士銀行台北分行）名稱、電話、地址不核對；範本沒有保證機構與總代理人，「總代理人」字樣照原文放行"
        "（2026-10-15 作業人員決定）",
    },
    {
        "rule_id": "doc.initial_pricing",
        "description": "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP），參考條件表「期初定價」欄不和說明書比，只檢查是允許值"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "商品基本資料十五情境只核對設定參數與情境價格表；配息金額、股數、剩餘金額、報酬率、期末匯率不重算，"
        "KO 每日的 nt/Nt 變動配息不推算（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構註冊地與營業地點、信用評等、計算代理機構、結算機構、交付機構、準據法、律師意見不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：USD／JPY／AUD 以外的幣別、KO 每期＋KI、新版 KI 每期、單一標的記憶式、單一標的有 KI、"
        "KO 每期 Non-Call ≥ 2、倫敦分行以外的發行分行；舊版範本（「股權結構型債券」）不支援；範本以外的寫法會轉人工覆核",
    },
]


# ---------------------------------------------------------------- 配息率（核對規則 §3.3）


def coupon(ctx: IssuerContext) -> list[CheckResult]:
    """固定配息率 = 年利率 ÷ 12（4 位）；KO 每日的變動配息率與年化（八與十五設定參數）、情境固定配息率與年化 = 八的固定
    配息率與年利率。"""
    f = ctx.ts.f
    annual, monthly = f("coupon_pa_pct"), f("monthly_coupon_pct")
    out = [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "固定配息率 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            kit.monthly_of_annual(annual.value) if annual.ok else None,
            monthly.value,
            tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="商品基本資料八的固定配息率須等於年化配息率 ÷ 12 四捨五入到 4 位",
        )
    ]
    table, observation = f("coupon_table"), f("ko_observation")
    daily = (observation.ok and observation.value == "D") or (table.ok and table.value["kind"] == "D")
    mentions = [("scenario_monthly_pct", "商品基本資料十五設定參數固定配息率", monthly)]
    mentions.append(("scenario_coupon_pa_pct", "商品基本資料十五設定參數年化配息率", annual))
    if daily:
        mentions += [
            ("variable_coupon_pct", "商品基本資料八變動配息率", monthly),
            ("variable_coupon_pa_pct", "商品基本資料八變動配息率的年化配息率", annual),
            ("scenario_variable_coupon_pct", "商品基本資料十五設定參數變動配息率", monthly),
            ("scenario_variable_coupon_pa_pct", "商品基本資料十五設定參數變動配息率的年化配息率", annual),
        ]
    for field, name, base in mentions:
        pf = f(field)
        if pf.status == FieldStatus.NOT_APPLICABLE:
            continue
        out.append(
            kit.doc_check(ctx, "doc.coupon_mentions", field, name, base, pf).compare(
                base.value, pf.value, fail_message="須等於商品基本資料八的固定配息率與年化配息率"
            )
        )
    return out


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    """受託機構商品代號 = TDCC 商品代號；商品基本資料一名稱 = 第 1 頁；商品基本資料五幣別 = 第 1 頁；發行分行三處。"""
    f = ctx.ts.f
    code, trustee = f("product_code"), f("trustee_product_code")
    zh, art1 = f("name_zh"), f("name_art1_zh")
    cz, art5 = f("currency_zh"), f("currency_art5")
    out = [
        kit.doc_check(
            ctx, "doc.trustee_product_code", "trustee_product_code", "受託機構商品代號", code, trustee
        ).compare(code.value, trustee.value, fail_message="受託機構商品代號須等於 TDCC 商品代號"),
        kit.doc_check(ctx, "doc.name_consistency", "name_art1_zh", "商品基本資料一商品名稱", zh, art1).compare(
            zh.value,
            art1.value,
            ok=zh.ok and art1.ok and full_brackets(squash(zh.value)) == full_brackets(squash(art1.value)),
            tolerance="忽略空白、括號全半形與句尾「。」；「瑞銀（定期觀察）」視為「瑞銀定期觀察」",
            fail_message="須等於第 1 頁「商品中文名稱」",
        ),
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "商品基本資料五計價幣別", cz, art5).compare(
            cz.value, art5.value
        ),
    ]
    for field, name in (
        ("issuer_full_cover", "第 1 頁四、發行機構的發行分行"),
        ("issuer_full_ch1", "商品基本資料三、發行機構的發行分行"),
        ("issuer_full_ch2", "相關機構事業概況發行機構的發行分行"),
    ):
        pf = f(field)
        out.append(issuing_branch(Check("doc.issuing_branch", field, Item.expected(name), ctx.document), pf))
    return out


def denomination_mentions(ctx: IssuerContext) -> list[CheckResult]:
    """面額的其他出處 = 八的每單位面額：八「發行價格：N%，即X」的 X = 面額 × N%（寫「每單位面額之N%」時沒有金額，不產生
    結果）、一般交易事項四「每單位面額」、五「申購價金之計算」的金額（Issue #303）。"""
    f = ctx.ts.f
    denom, price, amount = f("denomination"), f("issue_price_pct"), f("issue_price_amount")
    rid = "doc.denomination_mentions"
    out = []
    if amount.status != FieldStatus.NOT_APPLICABLE:
        out.append(
            kit.doc_check(ctx, rid, "issue_price_amount", "商品基本資料八發行價格金額", denom, price, amount).compare(
                denom.value * price.value / 100 if denom.ok and price.ok else None,
                amount.value,
                fail_message="須等於每單位面額 × 發行價格 %",
            )
        )
    for field, name in (
        ("denomination_ch4", "一般交易事項四每單位面額"),
        ("subscription_amount_ch5", "一般交易事項五申購價金之計算的金額"),
    ):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, rid, field, name, denom, pf).compare(
                denom.value, pf.value, fail_message="須等於商品基本資料八的每單位面額"
            )
        )
    return out


def issuing_branch(check: Check, pf: ParsedField) -> CheckResult:
    """發行機構寫法裡的發行分行是倫敦分行（三種寫法等價）；其他分行轉人工覆核（說明書與投資人須知共用）。"""
    return check.needs(pf).compare(
        "倫敦分行",
        pf.value,
        ok=pf.ok and any(p in squash(pf.value) for p in BRANCH_PHRASES),
        fail=S.REVIEW_REQUIRED,
        reason="variant_not_covered",
        tolerance="「經由瑞士境外之倫敦分行」「經由倫敦分行」「透過其倫敦分行」視為相同",
        fail_message="發行分行不是倫敦分行（目前只支援瑞士銀行經由倫敦分行發行），請人工確認",
    )


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 配息表列數 = 情境設定參數天期。"""
    f = ctx.ts.f
    tenor, table, scen = f("tenor_months"), f("coupon_table"), f("scenario_tenor")
    rid = "doc.coupon_periods"
    return [
        kit.doc_check(ctx, rid, "coupon_table", "商品基本資料八配息表期數", tenor, table).compare(
            tenor.value, len(table.value["rows"]) if table.ok else None, fail_message="配息表列數須等於天期"
        ),
        kit.doc_check(ctx, rid, "scenario_tenor", "商品基本資料十五設定參數天期", tenor, scen).compare(
            tenor.value, scen.value
        ),
    ]


def autocall_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KO 每日：配息表「固定配息率」期數 = Non-Call，八與十五設定參數的「t 係為a 至b」= Non-Call+1 至天期（核對規則
    §3.3）。"""
    f = ctx.ts.f
    k, table, tenor = f("first_callable_period"), f("coupon_table"), f("tenor_months")
    if not (table.ok and table.value["kind"] == "D"):
        return []
    rid = "doc.autocall_terms"
    fixed = [r["period"] for r in table.value["rows"] if r["fixed"]]
    out = [
        kit.doc_check(ctx, rid, "coupon_table", "配息表「固定配息率」的期別", k, table).compare(
            list(range(1, k.value + 1)) if k.ok else None,
            fixed,
            fail_message="配息表前 Non-Call 期的公式須是「固定配息率」、之後是「變動配息率」",
        )
    ]
    if k.ok and tenor.ok and k.value < tenor.value:
        for field, name in (
            ("variable_periods", "「t 係為a 至b 的數字」"),
            ("scenario_variable_periods", "商品基本資料十五設定參數「t 係為a 至b 的數字」"),
        ):
            out.append(
                kit.doc_check(ctx, rid, field, name, k, tenor, f(field)).compare(
                    (k.value + 1, tenor.value),
                    f(field).value,
                    fail_message="變動配息率的期別須是第一個可提前出場期的下一期到天期",
                )
            )
    return out


def ko_pct_mentions(ctx: IssuerContext) -> list[CheckResult]:
    """提前出場給付條款寫出的「提前出場價（即期初價格的Y%）」= 價格表表頭 KO %（UB3 型）。"""
    f = ctx.ts.f
    ko, mentions = f("ko_pct"), f("ko_pct_mentions")
    if not mentions.ok:
        return []
    out = []
    for value, lines in mentions.value:
        pf = ParsedField.present("ko_pct_mention", value, list(lines))
        out.append(
            kit.doc_check(
                ctx, "doc.ko_pct_mentions", "ko_pct_mention", "提前出場給付條款的提前出場價 %", ko, pf
            ).compare(ko.value, value, fail_message="須等於價格表表頭的提前出場價 %")
        )
    return out


def tables(ctx: IssuerContext) -> list[CheckResult]:
    """情境價格表逐格與表頭 % = 八的價格表；設定參數每單位面額、初始投資金額 = 商品面額，發行價格 = 八的發行價格。"""
    f = ctx.ts.f
    table, scen = f("underlying_table"), f("scenario_table")
    denom, price = f("denomination"), f("issue_price_pct")
    scen_denom, scen_invest, scen_price = (
        f("scenario_denomination"),
        f("scenario_investment"),
        f("scenario_issue_price_pct"),
    )
    check = kit.doc_check(
        ctx,
        "doc.scenario_table",
        "scenario_table",
        "商品基本資料十五情境價格表與設定參數",
        table,
        scen,
        denom,
        price,
        scen_denom,
        scen_invest,
        scen_price,
    )
    if check.blocked is not None:
        return [check.blocked]
    issues = []
    if scen.value.pcts != table.value.pcts:
        issues.append("情境價格表表頭 % 和商品基本資料八價格表不同")
    rows, own = scen.value.prices, table.value.prices
    if len(rows) != len(own):
        issues.append(f"情境價格表 {len(rows)} 列，商品基本資料八價格表 {len(own)} 列")
    else:
        for n, (a, b) in enumerate(zip(rows, own, strict=True), start=1):
            diff = [c for c in b if a.get(c) != b[c]]
            if diff:
                issues.append(f"第 {n} 列{'、'.join(diff)}和商品基本資料八價格表不同")
    if scen_denom.value != denom.value:
        issues.append(f"設定參數每單位面額 {scen_denom.value} 不等於商品面額 {denom.value}")
    if scen_invest.value != denom.value:
        issues.append(f"設定參數每單位面額初始投資金額 {scen_invest.value} 不等於商品面額 {denom.value}")
    if scen_price.value != price.value:
        issues.append(f"設定參數發行價格 {scen_price.value}% 不等於商品基本資料八發行價格 {price.value}%")
    return [
        check.compare(
            None,
            None,
            ok=not issues,
            reason="value_mismatch",
            message="；".join(issues) or "等於商品基本資料八價格表、商品面額與發行價格",
        )
    ]


# ---------------------------------------------------------------- 配息表（核對規則 §3.5）


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    """給付日遞增、末列給付日 = 到期日；P：末列配息觀察日 = 期末訂價日、觀察日早於給付日；D：末列結束日 = 期末訂價日、
    開始日 ≤ 結束日 < 給付日、第 1 期開始日晚於發行日。"""
    f = ctx.ts.f
    table, final, maturity, issue = (
        f("coupon_table"),
        f("final_valuation_date"),
        f("maturity_date"),
        f("issue_date"),
    )
    name = "配息表"
    if not table.ok:
        return [Check("schedule.coupon_dates", "coupon_table", Item.expected(name), ctx.document).review(table)]
    rows = table.value["rows"]
    issues = []
    pays = [r["payment"] for r in rows]
    if any(b <= a for a, b in zip(pays, pays[1:], strict=False)):
        issues.append("配息給付日沒有逐期遞增")
    if maturity.ok and pays[-1] != maturity.value:
        issues.append(f"末列配息給付日 {pays[-1]} 不等於到期日 {maturity.value}")
    if table.value["kind"] == "D":
        bad = [r["period"] for r in rows if not (r["start"] <= r["end"] < r["payment"])]
        if bad:
            issues.append(f"第 {'、'.join(map(str, bad))} 期的開始日、結束日、給付日先後不對")
        if final.ok and rows[-1]["end"] != final.value:
            issues.append(f"末列配息觀察期間結束日 {rows[-1]['end']} 不等於期末訂價日 {final.value}")
        if issue.ok and rows[0]["start"] <= issue.value:
            issues.append(f"第 1 期配息觀察期間開始日 {rows[0]['start']} 沒有晚於發行日 {issue.value}")
    else:
        bad = [r["period"] for r in rows if r["observation"] >= r["payment"]]
        if bad:
            issues.append(f"第 {'、'.join(map(str, bad))} 期配息觀察日沒有早於給付日")
        if final.ok and rows[-1]["observation"] != final.value:
            issues.append(f"末列配息觀察日 {rows[-1]['observation']} 不等於期末訂價日 {final.value}")
    return [
        kit.structure_check(ctx, "schedule.coupon_dates", "coupon_dates", name, [table, final, maturity, issue], issues)
    ]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        *coupon(ctx),
        *document_info(ctx),
        *denomination_mentions(ctx),
        *periods(ctx),
        *autocall_terms(ctx),
        *ko_pct_mentions(ctx),
        *tables(ctx),
        *schedule(ctx),
    ]
