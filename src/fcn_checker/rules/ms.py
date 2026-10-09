"""MS 專屬規則（docs/rules/ms-check-rules.md）：月配息率與年化報酬率、說明書內部交叉驗證、日期表結構。

參考條件表欄位、價格推算、審查標準與回填是各上手共用的規則；情境分析見 rules/ms_scenario.py。
只有年利率要讀參考條件表（ADR 0005 的例外，見 REFERENCE_FIELDS）。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
每筆結果的項目名稱寫在規則旁；預期值來自說明書其他位置、推算或審查標準。
"""

from __future__ import annotations

import datetime as dt
import re

from ..schema import CheckResult, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import full_brackets, squash
from . import kit, ms_scenario
from .kit import Check, IssuerContext

ISSUER = "MS"
# 本上手規則可讀的參考條件表欄位（ADR 0005 的例外：說明書沒有年利率，月配息率與年化報酬率依表上年利率核對）
REFERENCE_FIELDS = ("coupon_pa_pct",)
NOT_COVERED = [
    {"rule_id": "doc.underlying_names", "description": "標的名稱與交易所不核對；以彭博代碼為準"},
    {"rule_id": "doc.initial_prices", "description": "期初價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.scenario_worse_details",
        "description": "較差情境的假設收盤價、股數、零股、到期贖回價值與年化報酬率不核對（只驗總配息與損益算式加總）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：非美元幣別、新版每期觀察 KI、P 型非記憶式且 Non-Call < 天期、"
        "D 型 Non-Call > 1、5 檔標的；範本以外的寫法會轉人工覆核",
    },
]
PRODUCT_TYPES = {  # 封面第 6 項商品種類：依標的數（範本規格 §4.5）
    False: "股票或指數股票型基金連結結構型債券",
    True: "股票與/或指數股票型基金連結結構型債券",
}
START_GAP = dt.timedelta(days=4)  # D 型配息週期起始日最晚在前期終止日後 4 個日曆天（遇美國假日順延，工具沒有假日曆）
INCONSISTENT = "document_inconsistent"  # 說明書內部寫法不一致、超出範本規格：轉人工覆核


def _bad(deps: list[ParsedField]) -> ParsedField | None:
    return next((p for p in deps if not p.ok), None)


# ---------------------------------------------------------------- 年利率與月配息率（核對規則 §3.3，讀參考條件表）


def _rate_mentions(ctx: IssuerContext, rid, field, what, mentions, compare, tolerance, message) -> list[CheckResult]:
    """說明書每個出處一筆，與參考條件表年利率推得的值比對；讀不到或出現多個轉人工覆核。

    `compare(年利率, 說明書值)` 回傳（是否一致, 顯示的預期值）；`message(年利率)` 為推算說明。
    """
    annual, ov, problem = ms_scenario.annual_rate(ctx, rid, field)
    if problem:
        return [problem]
    out = []
    for m in mentions:
        check = Check(rid, field, Item.derived(f"{what} %（{m.where}）", [ov]), ctx.document, ov=(ov,))
        if m.value is None:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    evidence=list(m.evidence),
                    reason="document_missing",
                    message=f"說明書{m.where}找不到{what}或出現多個",
                )
            )
            continue
        ok, shown = compare(annual, m.value)
        out.append(
            check.compare(
                shown, m.value, ok=ok, evidence=list(m.evidence), tolerance=tolerance, message=message(annual)
            )
        )
    return out


def monthly_coupon(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項、第 18 項假設與各情境算式的每個月配息率 = 參考條件表年利率 ÷ 12，四捨五入（half-up）到 4 位。"""
    return _rate_mentions(
        ctx,
        "derive.monthly_coupon",
        "monthly_coupon_pct",
        "月配息率",
        ctx.ts.coupon_mentions,
        lambda annual, value: (ms_scenario.monthly_rate(annual) == value, ms_scenario.monthly_rate(annual)),
        "四捨五入（half-up）到 4 位",
        lambda annual: f"推算：年利率 {annual}% ÷ 12",
    )


def annualized_return(ctx: IssuerContext) -> list[CheckResult]:
    """第 18 項獲利情境（標題不含「較差」）每處年化報酬率 = 參考條件表年利率（依說明書位數四捨五入）。"""
    return _rate_mentions(
        ctx,
        "derive.annualized_return",
        "annualized_return_pct",
        "年化報酬率",
        ctx.ts.annualized_mentions,
        kit.cmp_pct,
        "依說明書顯示位數四捨五入後比對",
        lambda annual: "獲利情境的年化報酬率應等於年利率",
    )


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.4、§3.5）


def _short_name(value: str) -> str:
    return re.sub(r"（下稱「本商品」）$", "", full_brackets(squash(value)))


def _doc(ctx: IssuerContext, rid: str, field: str, name: str, *deps: ParsedField) -> Check:
    """文件內部比對的結果身分：項目名稱為 `name`（預期值來自同一份文件其他位置），依賴欄位有問題轉人工覆核。"""
    return Check(rid, field, Item.expected(name), ctx.document).needs(*deps)


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    code, trustee = f("product_code"), f("trustee_product_code")
    out = [
        _doc(ctx, "doc.trustee_product_code", "trustee_product_code", "受託機構商品代號", code, trustee).compare(
            code.value,
            trustee.value or "（空白）",
            ok=code.ok and trustee.ok and trustee.value == code.value,
            fail_message="封面第 2 項受託機構商品代號須等於商品代號"
            + ("（說明書空白）" if trustee.value == "" else ""),
        )
    ]
    zh, art1 = f("name_zh"), f("name_art1")
    out.append(
        _doc(ctx, "doc.name_consistency", "name_art1", "第一章第 1 項商品名稱", zh, art1).compare(
            _short_name(zh.value) if zh.ok else None,
            full_brackets(squash(art1.value)) if art1.ok else None,
            fail_message="第一章第 1 項商品中文名稱須等於封面名稱（不含「(下稱「本商品」)」；忽略空白、括號全半形）",
        )
    )
    cz, c5 = f("currency_zh"), f("currency_art5")
    out.append(
        _doc(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, c5).compare(
            squash(cz.value) if cz.ok else None, squash(c5.value) if c5.ok else None
        )
    )
    uls, kind = f("underlyings"), f("product_type")
    out.append(
        _doc(ctx, "doc.product_type", "product_type", "商品種類", uls, kind).compare(
            PRODUCT_TYPES[len(uls.value) >= 2] if uls.ok else None,
            squash(kind.value) if kind.ok else None,
            fail_message="封面第 6 項商品種類依標的數：1 檔與 2 檔以上寫法不同",
        )
    )
    return out


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 日期表列數 = 名稱月數 = 第 15 項期別終點；第 15 項期別起點 D 型為 2、P 型為 1。"""
    f = ctx.ts.f
    tenor, table, zh, rng = f("tenor_months"), f("date_table"), f("name_zh"), f("coupon_range")
    rid = "doc.coupon_periods"
    months = re.search(r"發行(\d+)個月期", squash(zh.value)) if zh.ok else None
    name_months = ParsedField("name_months", zh.status, int(months[1]) if months else None, zh.evidence, note=zh.note)
    if zh.ok and months is None:
        name_months = ParsedField.invalid("name_months", [], "商品名稱找不到「N 個月期」")
    out = [
        _doc(ctx, rid, "date_table", "第 14 項(6) 日期表期數", tenor, table).compare(
            tenor.value, len(table.value["rows"]) if table.ok else None, fail_message="日期表列數須等於天期"
        ),
        _doc(ctx, rid, "name_months", "商品名稱月數", tenor, name_months).compare(tenor.value, name_months.value),
    ]
    start = 2 if table.ok and table.value["type"] == "D" else 1
    out.append(
        _doc(ctx, rid, "coupon_range", "第 15 項配息期別", tenor, table, rng).compare(
            (start, tenor.value),
            rng.value,
            fail_message="第 15 項「j 係為 a 至 N」：N 須等於天期，a 在 D 型為 2、P 型為 1",
        )
    )
    return out


def ko_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KO 觀察方式（日期表型 = 第 17 項寫法）、記憶式（名稱 = 第 17 項）、自動提前出場價欄 ⇔ Non-Call < 天期、
    價格表彭博代碼 = 第 11 項。前三項不一致轉人工覆核（寫法超出範本規格）。"""
    f = ctx.ts.f
    table, obs = f("date_table"), f("ko_observation_art17")
    out = [
        _doc(ctx, "doc.ko_observation", "ko_observation", "KO 觀察方式（日期表型與第 17 項）", table, obs).compare(
            table.value["type"] if table.ok else None,
            obs.value["type"] if obs.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason=INCONSISTENT,
            fail_message="第 14 項(6) 日期表型與第 17 項觀察日寫法不一致",
        )
    ]
    named, mem = f("name_memory"), f("ko_memory")
    out.append(
        _doc(ctx, "doc.ko_memory", "ko_memory", "記憶式（商品名稱與第 17 項）", named, mem).compare(
            named.value,
            mem.value,
            fail=S.REVIEW_REQUIRED,
            reason=INCONSISTENT,
            fail_message="商品名稱有無「（記憶式自動提前出場）」與第 17 項記憶事件寫法不一致",
        )
    )
    pt, k, tenor = f("price_table"), f("first_callable_period"), f("tenor_months")
    out.append(
        _doc(ctx, "doc.ko_column", "ko_column", "價格表自動提前出場價欄", pt, k, tenor).compare(
            "有" if k.ok and tenor.ok and k.value < tenor.value else "無",
            "有" if pt.ok and "ko" in pt.value["columns"] else "無",
            fail=S.REVIEW_REQUIRED,
            reason=INCONSISTENT,
            fail_message="第一個可提前出場期早於到期時價格表要有自動提前出場價欄，Non-Call = 天期時不會有",
        )
    )
    uls, art11 = f("underlyings"), f("underlyings_art11")
    out.append(
        _doc(ctx, "doc.underlying_tickers", "underlyings_art11", "第 11 項標的彭博代碼", uls, art11).compare(
            uls.value, art11.value, fail_message="第 11 項標的表的彭博代碼須依序等於第 16 項價格表"
        )
    )
    return out


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def _structure_check(ctx: IssuerContext, rid: str, field: str, name: str, deps, issues, tolerance=None) -> CheckResult:
    """日期表結構：`issues` 為不成立的說明，全部成立才通過。"""
    return _doc(ctx, rid, field, name, *deps).compare(
        None, None, ok=not issues, reason="schedule_inconsistent", message="；".join(issues), tolerance=tolerance
    )


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    table, final, maturity, issue = (
        f(x) for x in ("date_table", "final_valuation_date", "maturity_date", "issue_date")
    )
    obs, k = f("ko_observation_art17"), f("first_callable_period")
    names = {
        "schedule.coupon_dates": "日期表配息日與期末日期",
        "schedule.period_starts": "日期表配息週期起始日",
        "schedule.autocall_dates": "提前出場觀察日",
    }
    problem = _bad([table])
    if problem is not None:
        return [Check(rid, "date_table", Item.expected(n), ctx.document).review(problem) for rid, n in names.items()]
    kind, rows = table.value["type"], table.value["rows"]
    observed = "end" if kind == "D" else "pricing"
    label = "配息週期終止日" if kind == "D" else "定價日"
    out = []

    # 配息日遞增、末期配息日 = 到期日、終止日／定價日 < 配息日、末期終止日／定價日 = 期末定價日
    issues = []
    payments = [r["payment"] for r in rows]
    if any(a >= b for a, b in zip(payments, payments[1:], strict=False)):
        issues.append("配息日沒有逐期遞增")
    late = [r["period"] for r in rows if r[observed] >= r["payment"]]
    if late:
        issues.append(f"第 {'、'.join(map(str, late))} 期{label}沒有早於配息日")
    deps = [table, final, maturity]
    if _bad(deps) is None:
        if payments[-1] != maturity.value:
            issues.append(f"末期配息日 {payments[-1]} 不等於到期日 {maturity.value}")
        if rows[-1][observed] != final.value:
            issues.append(f"末期{label} {rows[-1][observed]} 不等於期末定價日 {final.value}")
    out.append(
        _structure_check(ctx, "schedule.coupon_dates", "coupon_dates", names["schedule.coupon_dates"], deps, issues)
    )

    if kind == "D":  # 第 1 期起始日 = 發行日；前期終止日 < 起始日 ≤ 前期終止日 + 4 個日曆天
        issues = []
        if issue.ok and rows[0]["start"] != issue.value:
            issues.append(f"第 1 期起始日 {rows[0]['start']} 不等於發行日 {issue.value}")
        for prev, row in zip(rows, rows[1:], strict=False):
            if not prev["end"] < row["start"] <= prev["end"] + START_GAP:
                issues.append(
                    f"第 {row['period']} 期起始日 {row['start']} 不在前期終止日 {prev['end']} 後 4 個日曆天內"
                )
        out.append(
            _structure_check(
                ctx,
                "schedule.period_starts",
                "period_starts",
                names["schedule.period_starts"],
                [table, issue],
                issues,
                tolerance="前期終止日 < 起始日 ≤ 前期終止日 + 4 個日曆天（遇美國假日順延；工具沒有假日曆）",
            )
        )

    # 提前出場：D 型第 17 項觀察起訖日；P 型自動提前出場日欄
    deps = [table, obs, k, final]
    issues = []
    if _bad(deps) is None and obs.value["type"] == kind:
        n = k.value
        if not 1 <= n <= len(rows):
            issues.append(f"第一個可提前出場期 {n} 不在第 1～{len(rows)} 期之間")
        elif kind == "D":
            if obs.value["start"] != rows[n - 1]["end"]:
                issues.append(
                    f"第 17 項觀察起日 {obs.value['start']} 不等於第 {n} 期配息週期終止日 {rows[n - 1]['end']}"
                )
            if obs.value["end"] != final.value:
                issues.append(f"第 17 項觀察訖日 {obs.value['end']} 不等於期末定價日 {final.value}")
        else:
            for r in rows:
                want = r["payment"] if n <= r["period"] and n < len(rows) else None
                if r["autocall"] != want:
                    shown = want or "無"
                    issues.append(f"第 {r['period']} 期自動提前出場日應為 {shown}，說明書為 {r['autocall'] or '無'}")
    out.append(
        _structure_check(
            ctx, "schedule.autocall_dates", "autocall_dates", names["schedule.autocall_dates"], deps, issues
        )
    )
    return out


def redemption_start(ctx: IssuerContext) -> CheckResult:
    """第四章開始受理贖回日期 = 發行日的下一個平日（只排除週末；遇假日不同時由作業人員人工放行）。"""
    issue, start = ctx.ts.f("issue_date"), ctx.ts.f("redemption_start")
    return _doc(ctx, "doc.redemption_start_date", "redemption_start", "開始受理贖回日期", issue, start).compare(
        kit.next_weekday(issue.value) if issue.ok else None,
        start.value,
        fail_message="第四章開始受理贖回日期須為發行日的下一個平日（只排除週末）",
    )


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果、審查標準與宣告的參考條件表欄位（年利率）。"""
    return [
        *monthly_coupon(ctx),
        *annualized_return(ctx),
        *document_info(ctx),
        *periods(ctx),
        *ko_terms(ctx),
        *schedule(ctx),
        redemption_start(ctx),
        *ms_scenario.run(ctx),
    ]
