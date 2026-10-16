"""DBS 專屬規則（docs/rules/dbs-check-rules.md）：最大利率值（無條件進位）與其他出處、第 1～k 期、說明書內部交叉驗證、
表A、情境投資金額與情境三表現最差連結標的。情境價格表、情境基本假設、情境重複寫出的最大利率值與情境三的價格由共用情境
規則（rules/scenario.py，Issue #294）核對。

參考條件表欄位、價格推算、審查標準（含 DBS 分節：固定警語、名稱樣板（含 Quanto、KI 段與單一標的轉人工覆核）、發行機構名稱、
商品種類、最低金額倍數）與回填是各上手共用的規則。說明書有年利率（最大利率值括號內），不讀參考條件表（REFERENCE_FIELDS 為空）。
發行人、保證機構名稱與期初定價依作業人員決定不核對。KO 每日記憶式、下限觸發事件三處一致、表1 與標的表代碼一致由 parser
檢查（不一致時欄位不合法，共用規則轉人工覆核）。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import re
from decimal import ROUND_CEILING, Decimal

from ..schema import CheckResult, ParsedField
from ..text import full_brackets, squash
from . import kit, scenario
from .kit import Q4, IssuerContext

ISSUER = "DBS"
REFERENCE_FIELDS: tuple[str, ...] = ()
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_11",
        "description": "警語 2～11 的全文不核對（只保留禁用語檢查）；第 2 頁「(請參閱第a-b頁)」頁碼不核對（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的中文名稱、交易所、價格表幣別字不核對；以彭博代碼與表1 價格為準",
    },
    {"rule_id": "doc.initial_prices", "description": "期初價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.issuer_branch_and_guarantor",
        "description": "發行人（台北分公司）名稱電話地址、保證機構（本商品無保證機構）不核對（2026-10-15 作業人員決定）",
    },
    {
        "rule_id": "doc.initial_pricing",
        "description": "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP），參考條件表「期初定價」欄不和說明書比，只檢查是允許值"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "情境分析只核對基本假設、情境價格表、情境一出場期、情境二年化報酬率（2026-10-15 確認）與情境一～三"
        "重複寫出的最大利率值、k、情境三期初價格與表現最差標的價格、金額旁的幣別（Issue #321）；利息金額、股數、零股金額、"
        "報酬率、假設匯率不重算，累積日數／總日數不推算，利息金額位數不核對",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構註冊地、信用評等、計算代理人、保管機構、結算機構、準據法、Quanto 匯率條款、支付日城市、"
        "觀察起始日不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：單一標的（名稱轉人工覆核）、USD／AUD／JPY 以外的幣別（最低申購倍數轉人工覆核）、"
        "非美股標的、Non-Call ≥ 3、KO % ≠ 100、DBS Bank Ltd 以外的發行主體；範本以外的寫法會轉人工覆核",
    },
]


def ceiling_monthly(annual: Decimal) -> Decimal:
    """最大利率值 = 年利率 ÷ 12，無條件進位到 4 位（DBS 條文「無條件進位至小數點後第四位」）。"""
    return (annual / 12).quantize(Q4, ROUND_CEILING)


# ---------------------------------------------------------------- 配息（核對規則 §3.3）


def coupon(ctx: IssuerContext) -> list[CheckResult]:
    """最大利率值 = 年利率 ÷ 12 無條件進位 4 位（情境基本假設與利息算式的最大利率值、情境二年化報酬率由共用情境規則
    `scenario.params` 核對，Issue #294）。"""
    f = ctx.ts.f
    annual, monthly = f("coupon_pa_pct"), f("monthly_coupon_pct")
    return [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "最大利率值 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            ceiling_monthly(annual.value) if annual.ok else None,
            monthly.value,
            tolerance="年利率 ÷ 12 無條件進位到 4 位後比數值",
            fail_message="最大利率值須等於括號內年利率 ÷ 12、無條件進位到 4 位",
        )
    ]


def autocall_terms(ctx: IssuerContext) -> list[CheckResult]:
    """配息「第1~k個觀察期間」、情境基本假設「第1~k」與「從第k個觀察期間結束日」、情境一「第1~k個觀察期間之利息金額」
    的 k、情境一出場期 − 1 = Non-Call。"""
    f = ctx.ts.f
    k = f("first_callable_period")
    rid = "doc.autocall_terms"
    first = f("scenario_first_period")
    first_k = ParsedField(
        first.name, first.status, first.value - 1 if first.ok else None, first.evidence, note=first.note
    )
    out = [
        kit.doc_check(ctx, rid, field, name, k, f(field)).compare(k.value, f(field).value, fail_message=message)
        for field, name, message in (
            ("full_rate_periods", "配息資料「第1~k個觀察期間」的 k", "利率為最大利率值的期數須等於第一個可提前出場期"),
            ("scenario_full_rate_periods", "情境基本假設「第1~k個觀察期間」的 k", "須等於第一個可提前出場期"),
            ("scenario_ko_start", "情境基本假設自動提前出場決定日的起始期", "須等於強制贖回的自動提前出場決定日起始期"),
            ("scenario_k_mention", "情境一「第1~k個觀察期間之利息金額」的 k", "須等於第一個可提前出場期（Issue #321）"),
        )
    ]
    out.append(
        kit.doc_check(ctx, rid, "scenario_first_period", "情境一出場的觀察期間 − 1", k, first_k).compare(
            k.value, first_k.value, fail_message="有利情況假設出場的觀察期間須是第一個可提前出場期的下一期"
        )
    )
    return out


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def _name(s: str) -> str:
    """名稱比對：去空白、括號統一全形（商品基本資料的名稱全形／半形括號混用）。"""
    return full_brackets(squash(s))


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    """商品基本資料名稱 = 封面；兩個商品代號相同；商品基本資料幣別（中文與 ISO 代碼）、封面幣別代碼。"""
    f = ctx.ts.f
    zh, basic = f("name_zh"), f("name_basic")
    out = [
        kit.doc_check(ctx, "doc.name_consistency", "name_basic", "商品基本資料商品名稱", zh, basic).compare(
            zh.value,
            basic.value,
            ok=zh.ok and basic.ok and _name(zh.value) == _name(basic.value),
            tolerance="忽略空白；全形／半形括號不計",
            fail_message="須等於封面「商品中文名稱」",
        )
    ]
    code, dist = f("product_code"), f("distributor_code")
    out.append(
        kit.doc_check(
            ctx, "doc.product_code_consistency", "distributor_code", "封面受託或銷售機構商品代號", code, dist
        ).compare(code.value, dist.value, fail_message="受託或銷售機構商品代號須等於商品代號")
    )
    cz, basic_ccy = f("currency_zh"), f("currency_basic")
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "currency_basic", "商品基本資料計價幣別", cz, basic_ccy).compare(
            cz.value, basic_ccy.value
        )
    )
    mapped = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    tpl = ctx.issuer_std.product_name
    if mapped and tpl is not None:  # DBS 括號內的代碼和英文名稱同一套寫法（人民幣寫 RMB，Issue #296）
        mapped = tpl.ccy_text(mapped)
    for field, name in (("currency_basic_iso", "商品基本資料計價幣別代碼"), ("currency_cover_iso", "封面計價幣別代碼")):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, "doc.currency_consistency", field, name, cz, pf).compare(
                mapped, pf.value, fail_message="括號內的幣別代碼須對應封面中文計價幣別（審查標準幣別對照）"
            )
        )
    return out


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 中英文名稱月數 = 情境基本假設商品年期（日期表期數由共用規則 `schedule.period_count` 檢查）。"""
    f = ctx.ts.f
    tenor, zh, en = f("tenor_months"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months, en_months = months(zh, r"(\d+)個月期", "商品中文名稱"), months(en, r"(\d+)-Month", "商品英文名稱")
    return [
        kit.doc_check(ctx, rid, "name_months_zh", "商品中文名稱月數", tenor, zh_months).compare(
            tenor.value, zh_months.value
        ),
        kit.doc_check(ctx, rid, "name_months_en", "商品英文名稱月數", tenor, en_months).compare(
            tenor.value, en_months.value
        ),
    ]


def scenario_investment(ctx: IssuerContext) -> list[CheckResult]:
    """情境基本假設投資金額 `{面額} X {發行價格}% X {單位數}= X幣別{金額}` 的金額 = 面額 X 發行價格 X 單位數（Issue #321，比照
    Nomura #316）；算式代入的面額、發行價格由共用情境規則 `scenario.params` 核對（Issue #294）。"""
    f = ctx.ts.f
    units, principal, shown_pct, amount = (
        f("scenario_units"),
        f("scenario_investment_principal"),
        f("scenario_investment_pct"),
        f("scenario_investment"),
    )
    check = kit.doc_check(
        ctx, "doc.scenario_units", "scenario_investment", "情境基本假設投資金額", units, principal, shown_pct, amount
    )
    if (problem := check.blocked) is not None:
        return [problem]
    expected = principal.value * shown_pct.value / 100 * units.value
    if expected == expected.to_integral_value():  # 整數金額照說明書寫法不帶小數
        expected = int(expected)
    return [
        check.compare(
            f"{principal.value:,} X {shown_pct.value}% X {units.value} = {expected:,}",
            f"{principal.value:,} X {shown_pct.value}% X {units.value} = {amount.value:,}",
            ok=amount.value == expected,
            fail_message="投資金額須為面額 X 發行價格 X 投資單位數",
        )
    ]


def scenario3(ctx: IssuerContext) -> list[CheckResult]:
    """情境三「表現最差連結標的為X，其最終價格(x)低於其執行價格／下限觸發價格(y)」：x = 最終價格表 X 那一列；無 KI 寫執行
    價格、KI 到期觀察寫下限觸發價格（Issue #321）。最終價格表的期初價格與 y 由共用情境規則 `scenario.strike` 和表1 比
    （Issue #294）；表現最差是哪一檔、股數與金額不重算。"""
    f = ctx.ts.f
    worst, ki = f("scenario3_worst"), f("ki_type")
    check = kit.doc_check(ctx, "doc.scenario_table", "scenario3_worst", "情境三表現最差連結標的的價格", ki, worst)
    if (problem := check.blocked) is not None:
        return [problem]
    w = worst.value
    column = "ki" if ki.value == "AM" else "strike"
    words = {"strike": "執行價格", "ki": "下限觸發價格"}
    return [
        check.compare(
            f"{w['ticker']} 最終價格 {w['table_final']:,}（最終價格表）、寫{words[column]}",
            f"{w['ticker']} 最終價格 {w['final']:,}、寫{words[w['column']]}",
            ok=w["final"] == w["table_final"] and w["column"] == column,
            fail_message="最終價格須等於最終價格表；無 KI 寫執行價格、KI 到期觀察寫下限觸發價格",
        )
    ]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        *coupon(ctx),
        *autocall_terms(ctx),
        *document_info(ctx),
        *periods(ctx),
        *scenario.run(ctx),  # 情境的共用檢查（Issue #294）；其餘上手切換後改由單份核對呼叫
        *scenario_investment(ctx),
        *scenario3(ctx),
    ]
