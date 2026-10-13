"""BNP 專屬規則（docs/rules/bnp-check-rules.md）：月配息率與年化報酬率、說明書內部交叉驗證、日期表結構、固定值重印。

參考條件表欄位、價格推算、審查標準（含 BNP 分節：保證機構、商品種類、最低金額倍數、發行不成立門檻）與回填是各上手
共用的規則；情境分析見 rules/bnp_scenario.py。只有年利率要讀參考條件表（ADR 0005 的例外，見 REFERENCE_FIELDS）。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。每筆結果的項目名稱寫在規則旁；預期值來自說明書其他位置、推算或審查標準。
"""

from __future__ import annotations

import datetime as dt
import re

from ..schema import CheckResult, Evidence, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import full_brackets, squash
from . import bnp_scenario, kit
from .kit import Check, IssuerContext

ISSUER = "BNP"
# 本上手規則可讀的參考條件表欄位（ADR 0005 的例外：月配息率與年化報酬率依表上年利率核對，同 BARC／MS）
REFERENCE_FIELDS = ("coupon_pa_pct",)
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_9",
        "description": "固定警語 (2)～(9) 的全文不核對（只保留 (7)(8) 的禁用語檢查與 (5) 名稱的結構檢查，2026-10-13 確認）",
    },
    {"rule_id": "doc.underlying_names", "description": "連結標的中文名稱、交易所與貨幣列不核對；以彭博代碼為準"},
    {"rule_id": "doc.initial_prices", "description": "期初價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.scenario_worse_details",
        "description": "較差／最差情境的假設收盤價、匯率與年化報酬率不核對（股數、剩餘金額、總報酬率依文件寫的收盤價與匯率驗算）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "總代理人、計算代理機構、保證機構地址、受託機構設立日期、基本公開說明書「年度更新」段不核對"
        "（2026-10-13 確認）；基本公開說明書日期只做文件內三處一致",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：單一標的記憶式、P 型 Non-Call > 1 且 < 天期、5 檔標的、CNH／HKD／AUD／EUR／ZAR 計價；"
        "範本以外的寫法會轉人工覆核",
    },
]
START_GAP = dt.timedelta(days=4)  # D 型配息期開始日最晚在前期結束日後 4 個日曆天（遇假日順延，工具沒有假日曆）
BASE_PROSPECTUS_PLACES = 3  # 基本公開說明書日期：封面警語 (7)、第三章 §6(7)、第五章 §2(1)


# ---------------------------------------------------------------- 年利率與月配息率（核對規則 §3.3，讀參考條件表）


def monthly_coupon(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項兩處、第 18 項假設與各情境算式的每個月配息率 = 參考條件表年利率 ÷ 12，四捨五入（half-up）到說明書顯示的位數
    （USD／GBP 4 位、JPY 6 位）。"""
    return kit.rate_mentions(
        ctx,
        "derive.monthly_coupon",
        "monthly_coupon_pct",
        "月配息率",
        ctx.ts.coupon_mentions,
        lambda annual, value: (
            bnp_scenario.monthly_rate(annual, value) == value,
            bnp_scenario.monthly_rate(annual, value),
        ),
        "四捨五入（half-up）到說明書顯示的位數",
        lambda annual: f"推算：年利率 {annual}% ÷ 12",
    )


def annualized_return(ctx: IssuerContext) -> list[CheckResult]:
    """第 18 項獲利情境（自動提前贖回、最佳情況）每處年化報酬率 = 參考條件表年利率（依說明書位數四捨五入）。"""
    return kit.rate_mentions(
        ctx,
        "derive.annualized_return",
        "annualized_return_pct",
        "年化報酬率",
        ctx.ts.annualized_mentions,
        kit.cmp_pct,
        "依說明書顯示位數四捨五入後比對",
        lambda annual: "獲利情境的年化報酬率應等於年利率",
    )


def min_coupon_rate(ctx: IssuerContext) -> CheckResult:
    """第 17 項「最低配息率為Y%（年化）」= 第 15 項年利率。"""
    f = ctx.ts.f
    annual, low = f("coupon_pa_pct"), f("min_coupon_rate")
    return kit.doc_check(ctx, "doc.min_coupon_rate", "min_coupon_rate", "第 17 項最低保證配息率", annual, low).compare(
        annual.value,
        low.value,
        ok=annual.ok and low.ok and kit.shown(annual.value, low.value) == low.value,
        tolerance="依說明書顯示位數四捨五入後比對",
        fail_message="第 17 項最低保證配息率須等於第 15 項年利率",
    )


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def _norm_name(value: str) -> str:
    return full_brackets(squash(value))


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    code, trustee = f("product_code"), f("trustee_product_code")
    out = [
        kit.doc_check(
            ctx, "doc.trustee_product_code", "trustee_product_code", "受託機構商品代號", code, trustee
        ).compare(
            code.value,
            trustee.value or "（空白）",
            ok=code.ok and trustee.ok and trustee.value == code.value,
            fail_message="封面第 1 項受託機構商品代號須等於商品代號"
            + ("（說明書空白）" if trustee.value == "" else ""),
        )
    ]
    zh = f("name_zh")
    for field, name, where in (
        ("cover_title", "封面標題名稱", "封面標題"),
        ("name_art1", "第一章第 1 項商品名稱", "第一章第 1 項商品名稱"),
    ):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, "doc.name_consistency", field, name, zh, pf).compare(
                _norm_name(zh.value) if zh.ok else None,
                _norm_name(pf.value) if pf.ok else None,
                fail_message=f"{where}須等於封面第 2 項商品中文名稱（不含「(下稱「商品」)」；忽略空白、括號全半形）",
            )
        )
    cz, c5 = f("currency_zh"), f("currency_art5")
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, c5).compare(
            squash(cz.value) if cz.ok else None, squash(c5.value) if c5.ok else None
        )
    )
    return out


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 日期表列數 = 商品名稱「N 個月期」。"""
    f = ctx.ts.f
    tenor, table, zh = f("tenor_months"), f("date_table"), f("name_zh")
    rid = "doc.coupon_periods"
    months = re.search(r"法國巴黎銀行(\d+)個月期", squash(zh.value)) if zh.ok else None
    name_months = ParsedField("name_months", zh.status, int(months[1]) if months else None, zh.evidence, note=zh.note)
    if zh.ok and months is None:
        name_months = ParsedField.invalid("name_months", [], "商品名稱找不到「N 個月期」")
    return [
        kit.doc_check(ctx, rid, "date_table", "第 14 項日期表期數", tenor, table).compare(
            tenor.value, len(table.value["rows"]) if table.ok else None, fail_message="日期表列數須等於天期"
        ),
        kit.doc_check(ctx, rid, "name_months", "商品名稱月數", tenor, name_months).compare(
            tenor.value, name_months.value
        ),
    ]


def ko_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KO 觀察方式（日期表表頭 = 第 14 項寫法）、記憶式（第 16 項 = 第 20 項(1)）；不一致轉人工覆核（寫法超出範本規格）。"""
    f = ctx.ts.f
    table, wording = f("date_table"), f("ko_wording")
    out = [
        kit.doc_check(
            ctx, "doc.ko_observation", "ko_observation", "KO 觀察方式（日期表與第 14 項寫法）", table, wording
        ).compare(
            table.value["type"] if table.ok else None,
            wording.value["type"] if wording.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 14 項日期表表頭與 (6)(7)(8) 的寫法不一致",
        )
    ]
    mem, mem20 = f("ko_memory"), f("ko_memory_art20")
    label = {True: "記憶式", False: "非記憶式"}
    out.append(
        kit.doc_check(ctx, "doc.ko_memory", "ko_memory_art20", "記憶式（第 16 項與第 20 項(1)）", mem, mem20).compare(
            label.get(mem.value) if mem.ok else None,
            label.get(mem20.value) if mem20.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 16 項與第 20 項(1) 的自動提前贖回事件定義句一個是記憶式、一個不是",
        )
    )
    return out


def redemption(ctx: IssuerContext) -> list[CheckResult]:
    """第 16 項自動提前贖回金額、第 17 項到期贖回金額 = 商品面額 × 100%（固定）。"""
    f = ctx.ts.f
    return [
        kit.doc_check(ctx, "doc.redemption_pct", field, name, f(field)).compare(
            100, f(field).value, fail_message=f"{name}須為商品面額 × 100%"
        )
        for field, name in (
            ("autocall_redemption_pct", "第 16 項自動提前贖回金額"),
            ("maturity_redemption_pct", "第 17 項到期贖回金額"),
        )
    ]


def base_prospectus(ctx: IssuerContext) -> CheckResult:
    """基本公開說明書公告日期：封面警語 (7)、第三章 §6(7)、第五章 §2(1) 三處相同（只做文件內一致）。"""
    pf = ctx.ts.f("base_prospectus_dates")
    check = Check(
        "doc.base_prospectus_date", "base_prospectus_dates", Item.expected("基本公開說明書日期"), ctx.document
    )
    if pf.status == FieldStatus.AMBIGUOUS:
        return check.result(
            S.MISMATCH,
            actual=pf.candidates,
            evidence=pf.evidence,
            reason="document_inconsistent",
            message="封面警語 (7)、第三章第 6 項(7)、第五章第 2 項(1) 的基本公開說明書公告日期不同",
        )
    check = check.needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    count = len(pf.evidence)
    return check.compare(
        BASE_PROSPECTUS_PLACES,
        count,
        fail=S.REVIEW_REQUIRED,
        reason="occurrence_count",
        message=f"三處日期相同：{pf.value}",
        fail_message=f"；應出現 {BASE_PROSPECTUS_PLACES} 處，找到 {count} 處",
    )


def page_count(ctx: IssuerContext) -> CheckResult:
    """每頁頁首「共M頁」的 M = PDF 實際頁數（含沒有文字層的附件頁），且每頁相同。"""
    headers = ctx.ts.page_headers
    check = Check("doc.page_count", "page_count", Item.expected("頁首總頁數"), ctx.document)
    if not headers:
        return check.result(S.REVIEW_REQUIRED, reason="document_missing", message="找不到頁首「第N頁，共M頁」")
    pages = ctx.pages
    if pages is None:
        return check.result(S.REVIEW_REQUIRED, reason="document_missing", message="不知道 PDF 頁數，無法核對頁首總頁數")
    totals = sorted({m for _, m, _ in headers})
    bad = [ln for _, m, ln in headers if m != pages]
    return check.compare(
        pages,
        totals if len(totals) > 1 else totals[0],
        ok=not bad,
        evidence=[Evidence.of(ln) for ln in (bad or [headers[0][2]])],
        fail_message="頁首「共M頁」須等於 PDF 頁數",
    )


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    table, final, maturity, issue = (
        f(x) for x in ("date_table", "final_valuation_date", "maturity_date", "issue_date")
    )
    k = f("first_callable_period")
    names = {
        "schedule.coupon_dates": "日期表配息日與期末日期",
        "schedule.period_starts": "日期表配息期開始日",
        "schedule.autocall_dates": "自動提前贖回評價日",
    }
    if not table.ok:
        return [Check(rid, "date_table", Item.expected(n), ctx.document).review(table) for rid, n in names.items()]
    kind, rows = table.value["type"], table.value["rows"]
    observed = "end" if kind == "D" else "pricing"
    label = "配息期結束日" if kind == "D" else "自動提前贖回評價日"
    out = []

    issues = []
    payments = [r["payment"] for r in rows]
    if any(a >= b for a, b in zip(payments, payments[1:], strict=False)):
        issues.append("配息日沒有逐期遞增")
    late = [r["period"] for r in rows if r[observed] is not None and r[observed] >= r["payment"]]
    if late:
        issues.append(f"第 {'、'.join(map(str, late))} 期{label}沒有早於配息日")
    deps = [table, final, maturity]
    if kit.first_bad(deps) is None:
        if payments[-1] != maturity.value:
            issues.append(f"末期配息日 {payments[-1]} 不等於到期日 {maturity.value}")
        if rows[-1][observed] != final.value:
            issues.append(f"末期{label} {rows[-1][observed] or '不適用'} 不等於期末評價日 {final.value}")
    out.append(
        kit.structure_check(ctx, "schedule.coupon_dates", "coupon_dates", names["schedule.coupon_dates"], deps, issues)
    )

    if kind == "D":  # 第 1 期開始日 = 發行日；前期結束日 < 開始日 ≤ 前期結束日 + 4 個日曆天
        issues = []
        if issue.ok and rows[0]["start"] != issue.value:
            issues.append(f"第 1 期開始日 {rows[0]['start']} 不等於發行日 {issue.value}")
        for prev, row in zip(rows, rows[1:], strict=False):
            if not prev["end"] < row["start"] <= prev["end"] + START_GAP:
                issues.append(
                    f"第 {row['period']} 期開始日 {row['start']} 不在前期結束日 {prev['end']} 後 4 個日曆天內"
                )
        out.append(
            kit.structure_check(
                ctx,
                "schedule.period_starts",
                "period_starts",
                names["schedule.period_starts"],
                [table, issue],
                issues,
                tolerance="前期結束日 < 開始日 ≤ 前期結束日 + 4 個日曆天（遇假日順延；工具沒有假日曆）",
            )
        )

    # 自動提前贖回評價日：D 型第 14 項(8) 的 k 在期數內；P 型「不適用」列 = 第 1～k−1 期、第 k 期起每期都有日期
    issues = []
    if k.ok:
        n = k.value
        if not 1 <= n <= len(rows):
            issues.append(f"第一個可提前出場期 {n} 不在第 1～{len(rows)} 期之間")
        elif kind == "P":
            for r in rows:
                want = r["period"] >= n
                if (r["pricing"] is not None) != want:
                    shown = "應有日期" if want else "應為「不適用」"
                    issues.append(f"第 {r['period']} 期自動提前贖回評價日{shown}")
    out.append(
        kit.structure_check(
            ctx, "schedule.autocall_dates", "autocall_dates", names["schedule.autocall_dates"], [table, k], issues
        )
    )
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果、審查標準與宣告的參考條件表欄位（年利率）。"""
    return [
        *monthly_coupon(ctx),
        *annualized_return(ctx),
        min_coupon_rate(ctx),
        *document_info(ctx),
        *periods(ctx),
        *ko_terms(ctx),
        *redemption(ctx),
        *schedule(ctx),
        base_prospectus(ctx),
        page_count(ctx),
        *bnp_scenario.run(ctx),
    ]
