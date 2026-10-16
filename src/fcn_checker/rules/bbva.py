"""BBVA 專屬規則（docs/rules/bbva-check-rules.md）：配息率（KO 每日／每期兩種寫法）、說明書內部交叉驗證、日期表（KO 每日／
每期兩種）、價格表表頭、記憶式與下限值寫法的一致性。第 16 項情境價格表、情境假設與情境二～四第 1 期配息率由共用情境規則
（rules/scenario.py，Issue #294）核對。

參考條件表欄位、價格推算、審查標準（含 BBVA 分節：固定警語、名稱樣板、發行機構名稱、商品種類）與回填是各上手共用的
規則。說明書有年利率（KO 每期型在第 16 項情境假設），不讀參考條件表（REFERENCE_FIELDS 為空）。保證機構、總代理人名稱
與期初定價依作業人員決定不核對；第 14 項最佳表現值起算日、第 16 項情境與第 18 項重複寫出的值（Issue #305）要等於
正式條款（第 1 頁受託機構明細由 adapter 交出、共用審查標準規則核對）。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
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
        "description": "保證機構（西班牙對外銀行）與總代理人（臺北分公司）的名稱、地址電話，第 1 頁受託機構明細 (ii) 設立日期"
        "不核對（2026-10-15 作業人員決定；(i)、(iii)、(iv) 依 Issue #305 有就由共用審查標準規則核對）",
    },
    {
        "rule_id": "doc.initial_pricing",
        "description": "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP），參考條件表「期初定價」欄不和說明書比，只檢查是允許值"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 16 項情境只核對假設的年期、面額、利率、情境價格表、情境一第 1 期配息率與重複寫出的值（第 1 期配息率、"
        "下限值 %、出場值 %、觀察起算日、金額幣別；Issue #305）；配息金額、股數、剩餘金額、報酬率不重算，情境一提前出場期別"
        "不核對（2026-10-15 確認）",
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
    """KO 每日：第 13 項 m = 12、情境一第 1 期配息率（說明書寫出的月配息率）= 年利率 ÷ 12（4 位）。
    KO 每期：第 13 項每期配息率 = 年利率（第 16 項年利率相當於約）÷ 12（4 位）、天數慣例 1/1。
    第 16 項情境假設的年利率、每月利率由共用情境規則 `scenario.params` 核對（Issue #294）。"""
    f = ctx.ts.f
    annual = f("coupon_pa_pct")
    rid = "doc.coupon_mentions"
    kind = _kind(ctx)
    if kind is None:
        return [Check(rid, "coupon", Item.expected("配息率"), ctx.document).review(f("date_table"))]
    if kind == "D":
        m, first = f("coupon_m"), f("scenario_first_coupon_pct")
        return [
            kit.doc_check(ctx, rid, "coupon_m", "第 13 項配息率公式的 m", m).compare(
                12, m.value, fail_message="KO 每日型配息率公式的 m 須為 12（每月配息）"
            ),
            kit.doc_check(
                ctx,
                "derive.monthly_coupon",
                "scenario_first_coupon_pct",
                "第 16 項情境一第 1 期配息率 %（年利率 ÷ 12）",
                annual,
                first,
            ).compare(
                kit.monthly_of_annual(annual.value) if annual.ok else None,
                first.value,
                tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
                fail_message="情境一第 1 期配息率須等於年利率 ÷ 12 四捨五入到 4 位",
            ),
        ]
    monthly, day = f("monthly_coupon_pct"), f("day_count")
    return [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "第 13 項每期配息率 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            kit.monthly_of_annual(annual.value) if annual.ok else None,
            monthly.value,
            tolerance="年利率（第 16 項「年利率相當於約」）÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="第 13 項配息率須等於年利率 ÷ 12 四捨五入到 4 位",
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
    """天期 = 中英文名稱月數 = 情境假設商品年期（日期表期數由共用規則 `schedule.period_count` 檢查）。"""
    f = ctx.ts.f
    tenor, zh, en = f("tenor_months"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months, en_months = months(zh, r"(\d+)個月期", "商品中文名稱"), months(en, r"(\d+)-month", "商品英文名稱")
    return [
        kit.doc_check(ctx, rid, "name_months_zh", "商品中文名稱月數", tenor, zh_months).compare(
            tenor.value, zh_months.value
        ),
        kit.doc_check(ctx, rid, "name_months_en", "商品英文名稱月數", tenor, en_months).compare(
            tenor.value, en_months.value
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
    """價格表表頭 % = 第 14、15 項定義；第 9 項標的 = 價格表標的（同順序）（情境價格表與情境假設總投資金額由共用情境規則
    核對，Issue #294）。"""
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
    return out


# ---------------------------------------------------------------- 重複寫出的值（Issue #305）


def repeated_terms(ctx: IssuerContext) -> list[CheckResult]:
    """重複寫出的值 = 正式條款：第 14 項最佳表現值的觀察起算日（記憶式才有）與情境觀察起算日 = 第 12 項 (10) 觀察期間
    起日；情境下限值 % = KI %（無 KI 時 = 轉換比例）；情境出場值 % = KO %；第 18 項 (1) 的 % = KO %。情境二～四第 1 期
    配息率（KO 每日型）由共用情境規則 `scenario.params` 和情境一比（Issue #294）。"""
    f = ctx.ts.f
    start, memory, best = f("ko_period_start"), f("ko_memory"), f("best_value_start")
    out = []
    if best.status != FieldStatus.MISSING or (memory.ok and memory.value):
        out.append(
            kit.doc_check(
                ctx, "doc.ko_period_mentions", "best_value_start", "第 14 項最佳表現值的觀察起算日", start, best
            ).compare(start.value, best.value, fail_message="須等於第 12 項 (10) 自動提前贖回觀察期間起日")
        )
    ki = kit.doc_ki(f("ki_type"))
    barrier = f("strike_def_pct") if ki == "none" else f("ki_def_pct")
    barrier_name = "轉換比例（無 KI 的到期門檻）" if ki == "none" else "第 14 項下限值 %"
    for container, rid, expected, what in (
        ("scenario_start_mentions", "doc.ko_period_mentions", start, "第 12 項 (10) 自動提前贖回觀察期間起日"),
        ("scenario_barrier_mentions", "doc.ki_consistency", barrier, barrier_name),
        ("scenario_ko_mentions", "doc.ko_mentions", f("ko_def_pct"), "第 15 項 KO %"),
    ):
        pf = f(container)
        if not pf.ok:
            out.append(Check(rid, container, Item.expected(what), ctx.document).review(pf))
            continue
        for occ in pf.value:
            out.append(
                kit.doc_check(ctx, rid, occ.field, f"{occ.where}{occ.name}", expected, occ.value).compare(
                    expected.value, occ.value.value, fail_message=f"須等於{what}"
                )
            )
    ko, art18 = f("ko_def_pct"), f("art18_ko_pct")
    out.append(
        kit.doc_check(ctx, "doc.ko_mentions", "art18_ko_pct", "第 18 項 (1) 自動提前贖回事件的 %", ko, art18).compare(
            ko.value, art18.value, fail_message="須等於第 15 項 KO %"
        )
    )
    return out


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def autocall_table(ctx: IssuerContext) -> list[CheckResult]:
    """P：自動提前贖回觀察日表的「觀察日」與「自動提前贖回觀察日」兩欄相同、觀察期間起訖 = 表首末列觀察日。
    日期表的期數、順序與末期日期由共用規則（rules/schedule.py）檢查；D 型沒有這張表。"""
    f = ctx.ts.f
    table, period = f("date_table"), f("ko_period")
    check = Check("schedule.autocall_dates", "ko_rows", Item.expected("自動提前贖回觀察日表"), ctx.document).needs(
        table, period
    )
    if table.ok and table.value["kind"] == "D":
        return []
    if (problem := check.blocked) is not None:
        return [problem]
    ko, issues = table.value["ko_rows"], []
    same = [r["t"] for r in ko if r["observation_j"] != r["observation"]]
    if same:
        issues.append(f"第 {'、'.join(map(str, same))} 列的「觀察日」和「自動提前贖回觀察日」不同")
    if (period.value["start"], period.value["end"]) != (ko[0]["observation"], ko[-1]["observation"]):
        issues.append(
            f"自動提前贖回觀察期間 {period.value['start']}～{period.value['end']} 不等於觀察日表首末列"
            f" {ko[0]['observation']}～{ko[-1]['observation']}"
        )
    return [check.compare(None, None, ok=not issues, reason="schedule_inconsistent", message="；".join(issues))]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        *coupon(ctx),
        *document_info(ctx),
        *periods(ctx),
        *memory_terms(ctx),
        *ki_terms(ctx),
        *tables(ctx),
        *autocall_table(ctx),
        *repeated_terms(ctx),
    ]
