"""BBVA 專屬規則（docs/rules/bbva-check-rules.md）：配息率（KO 每日／每期兩種寫法）、說明書內部交叉驗證、日期表（KO 每日／
每期兩種）、價格表表頭、情境假設與情境價格表、記憶式與下限值寫法的一致性。

參考條件表欄位、價格推算、審查標準（含 BBVA 分節：固定警語、名稱樣板、發行機構名稱、商品種類）與回填是各上手共用的
規則。說明書有年利率（KO 每期型在第 16 項情境假設），不讀參考條件表（REFERENCE_FIELDS 為空）。保證機構、總代理人名稱、
第 1 頁受託機構明細與期初定價依作業人員決定不核對。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import re

from ..parsers.bbva import ISSUER_EN
from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import squash
from . import kit
from .kit import Check, IssuerContext

ISSUER = "BBVA"
REFERENCE_FIELDS: tuple[str, ...] = ()
DAY_COUNT = "1/1"  # KO 每期型第 13 項計息天數慣例（樣本都是）
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_9",
        "description": "警語 (2)～(9) 的全文不核對（只保留禁用語檢查）；BV1 第三章多貼的不完整固定警語不另判錯（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的公司名稱、股票幣別、股票 ISIN、交易所不核對；以彭博代碼為準",
    },
    {"rule_id": "doc.initial_prices", "description": "期初價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.guarantor_and_agent",
        "description": "保證機構（西班牙對外銀行）與總代理人（臺北分公司）的名稱、地址電話，第 1 頁受託機構明細 (i)～(iv) 不核對"
        "（2026-10-15 作業人員決定）",
    },
    {
        "rule_id": "doc.initial_pricing",
        "description": "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP），參考條件表「期初定價」欄不和說明書比，只檢查是允許值"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 16 項情境只核對假設的年期、面額、利率、情境價格表與情境一第 1 期配息率；配息金額、股數、剩餘金額、"
        "報酬率不重算，情境一提前出場期別與情境二～四不核對（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.coupon_accrual",
        "description": "KO 每日型配息依期內預定交易日數累計（n/N），每期金額不重算；日圓商品的轉換匯率與價格表幣別字"
        "（標的報價幣別）不核對（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構地址電話、信用評等、計算代理機構、發行及付款代理機構、集中保管機構、準據法、基本公開說明書日期不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：USD／JPY 以外的幣別、KO 每期＋無 KI、KO 每期 Non-Call ≥ 2、多標的的 KO 每日"
        "非記憶式、2 檔或 4 檔以上標的、BBVA GLOBAL MARKETS, B.V. 以外的發行主體；範本以外的寫法會轉人工覆核",
    },
]


def _kind(ctx: IssuerContext) -> str | None:
    table = ctx.ts.f("date_table")
    return table.value["kind"] if table.ok else None


# ---------------------------------------------------------------- 配息率（核對規則 §3.3）


def coupon(ctx: IssuerContext) -> list[CheckResult]:
    """KO 每日：第 13 項 m = 12、第 16 項假設年利率 = 第 13 項、情境一第 1 期配息率 = 年利率 ÷ 12（4 位）。
    KO 每期：第 13 項每期配息率 = 年利率（第 16 項年利率相當於約）÷ 12（4 位）、第 16 項每月利率 = 第 13 項、天數慣例 1/1。"""
    f = ctx.ts.f
    annual = f("coupon_pa_pct")
    rid = "doc.coupon_mentions"
    kind = _kind(ctx)
    if kind is None:
        return [Check(rid, "coupon", Item.expected("配息率"), ctx.document).review(f("date_table"))]
    if kind == "D":
        m, scen, first = f("coupon_m"), f("scenario_coupon_pct"), f("scenario_first_coupon_pct")
        return [
            kit.doc_check(ctx, rid, "coupon_m", "第 13 項配息率公式的 m", m).compare(
                12, m.value, fail_message="KO 每日型配息率公式的 m 須為 12（每月配息）"
            ),
            kit.doc_check(ctx, rid, "scenario_coupon_pct", "第 16 項情境假設年利率", annual, scen).compare(
                annual.value, scen.value, fail_message="情境假設的年利率須等於第 13 項年利率"
            ),
            kit.doc_check(ctx, rid, "scenario_first_coupon_pct", "第 16 項情境一第 1 期配息率", annual, first).compare(
                kit.monthly_of_annual(annual.value) if annual.ok else None,
                first.value,
                tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
                fail_message="情境一第 1 期配息率須等於年利率 ÷ 12 四捨五入到 4 位",
            ),
        ]
    monthly, scen_m, day = f("monthly_coupon_pct"), f("scenario_monthly_pct"), f("day_count")
    return [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "第 13 項每期配息率 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            kit.monthly_of_annual(annual.value) if annual.ok else None,
            monthly.value,
            tolerance="年利率（第 16 項「年利率相當於約」）÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="第 13 項配息率須等於年利率 ÷ 12 四捨五入到 4 位",
        ),
        kit.doc_check(ctx, rid, "scenario_monthly_pct", "第 16 項情境假設每月利率", monthly, scen_m).compare(
            monthly.value, scen_m.value, fail_message="情境假設的每月利率須等於第 13 項配息率"
        ),
        kit.doc_check(ctx, rid, "day_count", "第 13 項計息天數慣例", day).compare(
            DAY_COUNT,
            day.value,
            fail=S.REVIEW_REQUIRED,
            reason="unexpected_wording",
            fail_message="計息天數慣例不是範本規格內的「1/1」，請人工確認",
        ),
    ]


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    """第一章第 1 項中文名稱 = 第 1 頁；第一章第 5 項幣別 = 第 1 頁、英文名稱的 ISO 代碼對應中文幣別；受託機構商品代號 = 商品代號。"""
    f = ctx.ts.f
    zh, art1 = f("name_zh"), f("name_art1_zh")
    out = [
        kit.doc_check(ctx, "doc.name_consistency", "name_art1_zh", "第一章第 1 項商品名稱", zh, art1).compare(
            zh.value,
            art1.value,
            ok=zh.ok and art1.ok and squash(zh.value) == squash(art1.value),
            tolerance="忽略空白",
            fail_message="須等於第 1 頁「商品中文名稱」",
        )
    ]
    cz, art5, en = f("currency_zh"), f("currency_art5"), f("name_en")
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, art5).compare(
            cz.value, art5.value
        )
    )
    iso = re.match(re.escape(ISSUER_EN) + r"\s+([A-Z]{3})\s", en.value) if en.ok else None
    iso_pf = (
        ParsedField("name_en_iso", en.status, iso[1] if iso else None, list(en.evidence), note=en.note)
        if not en.ok or iso
        else ParsedField.invalid("name_en_iso", [], "商品英文名稱讀不到幣別代碼")
    )
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "name_en_iso", "商品英文名稱的幣別代碼", cz, iso_pf).compare(
            ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None,
            iso_pf.value,
            fail_message="英文名稱的幣別代碼須對應第 1 頁中文計價幣別（審查標準幣別對照）",
        )
    )
    code, dist = f("product_code"), f("distributor_code")
    out.append(
        kit.doc_check(
            ctx, "doc.distributor_product_code", "distributor_code", "第 1 頁受託機構商品代號", code, dist
        ).compare(code.value, dist.value, fail_message="須等於第 1 頁商品代號")
    )
    return out


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 日期表列數 = 中英文名稱月數 = 情境假設商品年期。"""
    f = ctx.ts.f
    tenor, table, zh, en = f("tenor_months"), f("date_table"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months, en_months = months(zh, r"(\d+)個月期", "商品中文名稱"), months(en, r"(\d+)-month", "商品英文名稱")
    scen = f("scenario_tenor")
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
        kit.doc_check(ctx, rid, "scenario_tenor", "第 16 項情境假設商品年期", tenor, scen).compare(
            tenor.value, scen.value
        ),
    ]


def memory_terms(ctx: IssuerContext) -> list[CheckResult]:
    """記憶式（第 15 項「最佳表現值」）時第 14 項到期條件另有「最佳表現值大於或等於x%（出場值）」，x = KO %；
    非記憶式（「最差表現值」）時沒有這個條件（範本規格 §4.2）。"""
    f = ctx.ts.f
    memory, ko, at_maturity = f("ko_memory"), f("ko_def_pct"), f("ko_at_maturity_pct")
    check = kit.doc_check(ctx, "doc.memory_consistency", "ko_at_maturity_pct", "第 14 項到期條件的出場值", memory)
    if (problem := check.blocked) is not None:
        return [problem]
    if not memory.value:
        has = at_maturity.status != FieldStatus.MISSING
        return [
            check.compare(
                "沒有出場值條件",
                "有出場值條件" if has else "沒有出場值條件",
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="非記憶式（最差表現值）的到期條件不應有「最佳表現值…（出場值）」，請人工確認",
            )
        ]
    return [
        check.needs(ko, at_maturity).compare(
            ko.value, at_maturity.value, fail_message="記憶式到期條件的出場值須等於第 15 項 KO %"
        )
    ]


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """下限值：到期條件有「（下限值）」⇔ 價格表有下限值欄；無 KI 時到期門檻 % = 轉換比例（不一致轉人工覆核）。"""
    f = ctx.ts.f
    ki, table = f("ki_type"), f("price_table")
    doc_ki = kit.doc_ki(ki)
    if doc_ki is None:
        return [Check("doc.ki_consistency", "ki_type", Item.expected("第 14 項下限值寫法"), ctx.document).review(ki)]
    label = {True: "有 KI", False: "無 KI"}
    has = doc_ki != "none"
    out = [
        kit.doc_check(ctx, "doc.ki_consistency", "price_table", "價格表下限值欄", ki, table).compare(
            label[has],
            label["ki" in table.value.columns] if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="價格表有無下限值欄須與到期條件的下限值寫法一致",
        )
    ]
    if not has:
        barrier, strike = f("no_ki_barrier_pct"), f("strike_def_pct")
        out.append(
            kit.doc_check(
                ctx, "doc.ki_consistency", "no_ki_barrier_pct", "第 14 項無 KI 的到期門檻 %", strike, barrier
            ).compare(
                strike.value,
                barrier.value,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="無 KI 時到期條件的門檻須等於轉換比例，請人工確認",
            )
        )
    return out


def tables(ctx: IssuerContext) -> list[CheckResult]:
    """價格表表頭 % = 第 14、15 項定義；第 9 項標的 = 價格表標的（同順序）；情境價格表逐格 = 價格表、表頭 % 相同；
    情境假設總投資金額 ÷ 單位數 = 每單位面額。"""
    f = ctx.ts.f
    table = f("price_table")
    out = []
    for col, key, name in (
        ("ko", "ko_def_pct", "價格表出場值欄頭 %"),
        ("strike", "strike_def_pct", "價格表轉換比例欄頭 %"),
        ("ki", "ki_def_pct", "價格表下限值欄頭 %"),
    ):
        if table.ok and col not in table.value.pcts:
            continue  # 沒有這欄（無 KI）：一致性由 ki_terms 檢查
        pf = f(key)
        out.append(
            kit.doc_check(ctx, "doc.table_header_pct", f"header_{col}", name, pf, table).compare(
                pf.value, table.value.pcts.get(col) if table.ok else None
            )
        )
    listed = f("underlying_list")
    out.append(
        kit.doc_check(ctx, "doc.underlying_list", "underlying_list", "第 9 項連結標的表的標的", table, listed).compare(
            table.value.tickers if table.ok else None,
            listed.value,
            fail_message="第 9 項連結標的表的彭博代碼與順序須等於第 14 項價格表",
        )
    )
    scen, denom, units, amount = f("scenario_table"), f("denomination"), f("scenario_units"), f("scenario_amount")
    check = kit.doc_check(
        ctx, "doc.scenario_table", "scenario_table", "第 16 項情境價格表與假設", table, scen, denom, units, amount
    )
    if table.ok and scen.ok and denom.ok and units.ok and amount.ok:
        issues = []
        if scen.value.pcts != table.value.pcts:
            issues.append("情境價格表表頭 % 和第 14 項價格表不同")
        if scen.value.tickers != table.value.tickers:
            issues.append("情境價格表的標的和第 14 項價格表不同")
        rows, own = scen.value.prices, table.value.prices
        if len(rows) != len(own):
            issues.append(f"情境價格表 {len(rows)} 列，第 14 項價格表 {len(own)} 列")
        else:
            for n, (a, b) in enumerate(zip(rows, own, strict=True), start=1):
                diff = [c for c in b if a.get(c) != b[c]]
                if diff:
                    issues.append(f"第 {n} 列{'、'.join(diff)}和第 14 項價格表不同")
        if not units.value or amount.value != denom.value * units.value:
            issues.append(f"情境假設總投資金額 {amount.value} 不等於 {units.value} 單位 × 每單位面額 {denom.value}")
        out.append(
            check.compare(
                None,
                None,
                ok=not issues,
                reason="value_mismatch",
                message="；".join(issues) or "等於第 14 項價格表與每單位面額",
            )
        )
    else:
        out.append(check.compare(None, None))
    return out


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    """D：第 1 期起始日 = 發行日、下一期起始日晚於前一期終止日、終止日早於同列配息日、末列終止日 = 贖回評價日。
    P：配息日遞增；自動提前贖回表的觀察日兩欄相同、觀察日早於同列自動提前贖回日、觀察期間起訖 = 表首末觀察日。
    兩型的末列配息日 = 到期日。"""
    f = ctx.ts.f
    table, final, maturity, issue = f("date_table"), f("final_valuation_date"), f("maturity_date"), f("issue_date")
    period = f("ko_period")
    name = "日期表"
    if not table.ok:
        return [Check("schedule.coupon_dates", "date_table", Item.expected(name), ctx.document).review(table)]
    rows = table.value["rows"]
    issues = []
    if table.value["kind"] == "D":
        if issue.ok and rows[0]["start"] != issue.value:
            issues.append(f"第 1 期配息觀察期間起始日 {rows[0]['start']} 不等於發行日 {issue.value}")
        early = [b["period"] for a, b in zip(rows, rows[1:], strict=False) if b["start"] <= a["end"]]
        if early:
            issues.append(f"第 {'、'.join(map(str, early))} 期起始日沒有晚於前一期終止日")
        late = [r["period"] for r in rows if r["end"] >= r["payment"]]
        if late:
            issues.append(f"第 {'、'.join(map(str, late))} 期配息觀察期間終止日沒有早於配息日")
        if final.ok and rows[-1]["end"] != final.value:
            issues.append(f"末列配息觀察期間終止日 {rows[-1]['end']} 不等於贖回評價日 {final.value}")
    else:
        pays = [r["payment"] for r in rows]
        if any(b <= a for a, b in zip(pays, pays[1:], strict=False)):
            issues.append("配息日沒有逐期遞增")
        ko = table.value["ko_rows"]
        same = [r["t"] for r in ko if r["observation_j"] != r["observation"]]
        if same:
            issues.append(
                f"自動提前贖回觀察日表第 {'、'.join(map(str, same))} 列的「觀察日」和「自動提前贖回觀察日」不同"
            )
        late = [r["t"] for r in ko if r["observation"] >= r["autocall"]]
        if late:
            issues.append(f"自動提前贖回觀察日表第 {'、'.join(map(str, late))} 列觀察日沒有早於自動提前贖回日")
        if period.ok and (period.value["start"], period.value["end"]) != (ko[0]["observation"], ko[-1]["observation"]):
            issues.append(
                f"自動提前贖回觀察期間 {period.value['start']}～{period.value['end']} 不等於觀察日表首末列"
                f" {ko[0]['observation']}～{ko[-1]['observation']}"
            )
    if maturity.ok and rows[-1]["payment"] != maturity.value:
        issues.append(f"末列配息日 {rows[-1]['payment']} 不等於到期日 {maturity.value}")
    return [
        kit.structure_check(
            ctx, "schedule.coupon_dates", "coupon_dates", name, [table, final, maturity, issue, period], issues
        )
    ]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        *coupon(ctx),
        *document_info(ctx),
        *periods(ctx),
        *memory_terms(ctx),
        *ki_terms(ctx),
        *tables(ctx),
        *schedule(ctx),
    ]
