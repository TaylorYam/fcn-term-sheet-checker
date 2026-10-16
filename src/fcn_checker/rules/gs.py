"""GS 專屬規則（docs/rules/gs-check-rules.md）：每月配息率與年利率其他出處、說明書內部交叉驗證、觀察時程表、價格表表頭、
情境 1 期別與情境的申購單位數。情境重複寫出的年利率、面額與情境 4 第 1 檔價格由共用情境規則（rules/scenario.py，
Issue #294）核對。

參考條件表欄位（含期初定價、標的依代號配對）、價格推算、審查標準（含 GS 分節：發行機構名稱、商品種類、最低申購依幣別
倍數）與回填是各上手共用的規則。說明書有年利率，不讀參考條件表（REFERENCE_FIELDS 為空）。保證機構、總代理人名稱與
發行不成立門檻依作業人員決定不核對（審查標準沒有 GS 分節）。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import re

from ..parsers import gs
from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import squash
from . import kit, scenario
from .kit import Check, IssuerContext

ISSUER = "GS"
REFERENCE_FIELDS: tuple[str, ...] = ()
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_10",
        "description": "警語 (2)～(10) 的全文不核對（只保留禁用語檢查；2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的股份發行人名稱、參考交易所、參考幣別不核對；以價格表彭博代碼為準",
    },
    {"rule_id": "doc.initial_prices", "description": "初始參考價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.guarantor_and_agent",
        "description": "保證機構（兩家）、總代理人的名稱與地址電話、發行不成立門檻不核對（2026-10-15 作業人員決定）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 17 項情境的配息金額、股數、畸零金額、報酬率不重算（2026-10-15 確認）；情境裡重複寫出的面額、"
        "年利率、申購單位數、初始投資金額與金額幣別逐處核對（Issue #317），情境 4 價格段跟著標的走、只核對第 1 檔價格與 K %",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構地址電話、信用評等、計算機構、結算系統、財務代理機構、匯率表條款、存託憑證條款、準據法不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：多標的非記憶式、單一標的記憶式、單一標的有 KI、5 檔標的、天期 6／12 以外、"
        "AUD／HKD／EUR／GBP／ZAR 計價、英商高盛國際公司以外的發行主體；範本以外的寫法會轉人工覆核",
    },
]
MEMORY_ZH, MEMORY_EN = "記憶式", "Memory"


# ---------------------------------------------------------------- 配息率（核對規則 §3.3）


def coupon(ctx: IssuerContext) -> list[CheckResult]:
    """第 14 項「約為Y%」= 年利率 ÷ 12（4 位）；第 15 項(2) 最低保證收益率 = 第 14 項（第 17 項情境的年利率由共用情境規則
    `scenario.params` 核對，Issue #294）。"""
    f = ctx.ts.f
    annual, monthly = f("coupon_pa_pct"), f("monthly_coupon_pct")
    expected = kit.monthly_of_annual(annual.value) if annual.ok else None
    out = [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "每月配息率 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            expected,
            monthly.value,
            tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="第 14 項「約為Y%」須等於年利率 ÷ 12 四捨五入到 4 位",
        )
    ]
    for m in ctx.ts.coupon_mentions:
        pf = (
            ParsedField.present("coupon_mention", m.value, [])
            if m.value is not None
            else ParsedField.missing("coupon_mention", f"找不到{m.where}或出現多個")
        )
        out.append(
            kit.doc_check(ctx, "doc.coupon_mentions", "coupon_pa_pct", m.where, annual, pf).compare(
                annual.value, m.value, evidence=list(m.evidence), fail_message="年利率須等於第 14 項配息率的年利率"
            )
        )
    return out


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    """商品代號四處、ISIN 三處、第一章第 1 項中文名稱、第一章第 5 項幣別（中文與 ISO 代碼）。"""
    f = ctx.ts.f
    code, isin = f("product_code"), f("isin")
    out = [
        kit.doc_check(ctx, "doc.product_code", field, name, code, f(field)).compare(
            code.value, f(field).value, fail_message="商品代號須等於第 1 頁受託機構商品代號"
        )
        for field, name in (
            ("product_code_zh", "第 1 項中文名稱括號 TDCC 商品代號"),
            ("product_code_en", "第 2 項英文名稱括號 TDCC Product Code"),
            ("product_code_art1", "第一章第 1 項 TDCC 商品代號"),
        )
    ]
    out += [
        kit.doc_check(ctx, "doc.isin_consistency", field, name, isin, f(field)).compare(
            isin.value, f(field).value, fail_message="ISIN 須與第 1 項中文名稱括號的 ISIN 相同"
        )
        for field, name in (("isin_en", "第 2 項英文名稱括號 ISIN"), ("isin_art1", "第一章第 1 項 ISIN"))
    ]
    cover, ch1 = f("issuer_name_cover"), f("issuer_name_ch1_full")
    out.append(
        kit.doc_check(ctx, "doc.issuer_name", "issuer_name_ch1_full", "第一章第 3 項發行機構全名", cover, ch1).compare(
            cover.value,
            ch1.value,
            ok=cover.ok and ch1.ok and squash(cover.value) == squash(ch1.value),
            tolerance="忽略空白（英文名稱可能一字一行）",
            fail_message="第一章第 3 項發行機構全名須等於第 1 頁第 7 項",
        )
    )
    zh, art1 = f("name_zh"), f("name_art1_zh")
    out.append(
        kit.doc_check(ctx, "doc.name_consistency", "name_art1_zh", "第一章第 1 項商品中文名稱", zh, art1).compare(
            zh.value,
            art1.value,
            ok=zh.ok and art1.ok and squash(zh.value) == squash(art1.value),
            tolerance="忽略空白",
            fail_message="第一章第 1 項商品中文名稱須等於第 1 頁名稱",
        )
    )
    cz, art5, iso = f("currency_zh"), f("currency_art5"), f("currency_art5_iso")
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, art5).compare(
            cz.value, art5.value
        )
    )
    mapped = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    tpl = ctx.issuer_std.product_name
    allowed = {mapped, tpl.ccy_text(mapped) if tpl and mapped else mapped} - {None}
    out.append(
        kit.doc_check(
            ctx, "doc.currency_consistency", "currency_art5_iso", "第一章第 5 項計價幣別代碼", cz, iso
        ).compare(
            "／".join(sorted(allowed)) or None,
            iso.value,
            ok=iso.ok and iso.value in allowed,
            tolerance="人民幣的代碼 CNY 視為 CNH（同名稱樣板 ccy_aliases）",
            fail_message="括號內的幣別代碼須對應第 1 頁中文計價幣別（審查標準幣別對照）",
        )
    )
    return out


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 中英文名稱月數（日期表期數 = 天期由共用規則 `schedule.period_count` 檢查）。"""
    f = ctx.ts.f
    tenor, zh, en = f("tenor_months"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.match(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months, en_months = months(zh, r"(\d+)個月", "商品中文名稱"), months(en, r"(\d+)Months", "商品英文名稱")
    return [
        kit.doc_check(ctx, rid, "name_months_zh", "商品中文名稱月數", tenor, zh_months).compare(
            tenor.value, zh_months.value
        ),
        kit.doc_check(ctx, rid, "name_months_en", "商品英文名稱月數", tenor, en_months).compare(
            tenor.value, en_months.value
        ),
    ]


def autocall_terms(ctx: IssuerContext) -> list[CheckResult]:
    """記憶式句型 ⇔ 名稱「記憶式」／「Memory」；第 15 項(2) 最低保證收益率「首 k 個」、情境 1「第 k 個觀察期間結束日」= 第 16 項 k。"""
    f = ctx.ts.f
    mem, k = f("ko_memory"), f("first_callable_period")
    label = {True: "記憶式", False: "非記憶式"}
    out = []
    for field, word, name in (
        ("name_zh", MEMORY_ZH, "商品中文名稱的記憶式字樣"),
        ("name_en", MEMORY_EN, "商品英文名稱的記憶式字樣"),
    ):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, "doc.ko_memory", field, name, mem, pf).compare(
                label.get(mem.value) if mem.ok else None,
                label.get(squash(word) in squash(pf.value)) if pf.ok else None,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="名稱有無記憶式字樣須與第 16 項自動提前出場事件的句型一致",
            )
        )
    for field, name in (
        ("minimum_return_periods", "第 15 項(2) 最低保證收益率期數"),
        ("scenario_first_period", "第 17 項情境 1 提前出場期別"),
    ):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, "doc.autocall_terms", field, name, k, pf).compare(
                k.value, pf.value, fail_message="須等於第 16 項「提前出場觀察日」的起始期 k（Non-Call）"
            )
        )
    return out


def underlying_terms(ctx: IssuerContext) -> list[CheckResult]:
    """第 10 項標的表代碼集合與檔數 = 第 15 項價格表；單一／多標的寫法（第 16 項句型、價格表表頭有無「(i)」）⇔ 價格表檔數。"""
    f = ctx.ts.f
    uls, art10, style, table = f("underlyings"), f("art10_tickers"), f("memory_style"), f("underlying_table")
    shape = {True: "多標的", False: "單一標的"}
    return [
        kit.doc_check(ctx, "doc.underlying_tickers", "art10_tickers", "第 10 項標的彭博代碼", uls, art10).compare(
            uls.value,
            art10.value,
            ok=uls.ok and art10.ok and sorted(uls.value) == sorted(art10.value),
            tolerance="代碼集合與檔數相同即可，不看順序",
            fail_message="第 10 項各檔彭博代碼須與第 15 項價格表相同（集合與檔數）",
        ),
        kit.doc_check(ctx, "doc.underlying_count", "price_table_header", "價格表表頭「(i)」", uls, table).compare(
            shape.get(len(uls.value) > 1) if uls.ok else None,
            shape.get(gs.indexed(table.value)) if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="價格表表頭有無「(i)」（多標的寫法）須與價格表檔數一致",
        ),
        kit.doc_check(ctx, "doc.underlying_count", "memory_style", "第 16 項單一／多標的句型", uls, style).compare(
            shape.get(len(uls.value) > 1) if uls.ok else None,
            shape.get(style.value == "memory") if style.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 16 項自動提前出場事件的句型（多標的記憶式／單一標的）須與價格表檔數一致",
        ),
    ]


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KI：到期買回寫法 ⇔ 價格表有無下限觸發價格欄 ⇔ 有無「下限觸發價格」定義（不一致轉人工覆核）。"""
    f = ctx.ts.f
    ki, table, definition = f("ki_type"), f("underlying_table"), f("ki_def_pct")
    doc_ki = kit.doc_ki(ki)
    if doc_ki is None:
        return [Check("doc.ki_consistency", "ki_type", Item.expected("第 15 項下限觸發"), ctx.document).review(ki)]
    label = {True: "有 KI", False: "無 KI"}
    has = doc_ki != "none"
    return [
        kit.doc_check(ctx, "doc.ki_consistency", "underlying_table", "價格表下限觸發價格欄", ki, table).compare(
            label[has],
            label["ki" in table.value.columns] if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="價格表有無下限觸發價格欄須與第 15 項到期買回的下限觸發寫法一致",
        ),
        kit.doc_check(ctx, "doc.ki_consistency", "ki_def_pct", "第 15 項「下限觸發價格」定義", ki).compare(
            label[has],
            label[definition.ok],
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="有無「下限觸發價格」定義須與第 15 項到期買回的下限觸發寫法一致",
        ),
    ]


def tables(ctx: IssuerContext) -> list[CheckResult]:
    """價格表表頭 K／KI % = 第 15 項定義；情境假設的最低申購單位數 = 第四章第 4 項（情境 4 第 1 檔的初始參考價格、執行價格
    與 K % 由共用情境規則核對，Issue #294）。"""
    f = ctx.ts.f
    table = f("underlying_table")
    out = []
    for col, key, name in (
        ("strike", "strike_def_pct", "價格表執行價格欄頭 %"),
        ("ki", "ki_def_pct", "價格表下限觸發價格欄頭 %"),
    ):
        if table.ok and col not in table.value.pcts:
            continue  # 沒有這欄（無 KI）：一致性由 ki_terms 檢查
        pf = f(key)
        out.append(
            kit.doc_check(ctx, "doc.table_header_pct", f"header_{col}", name, pf, table).compare(
                pf.value, table.value.pcts.get(col) if table.ok else None
            )
        )
    units, ch4 = f("scenario_units"), f("minimum_subscription_units")
    out.append(  # 讀不到任一邊轉人工覆核（Issue #294 決定 6）
        kit.doc_check(
            ctx, "doc.scenario_units", "scenario_units", "第 17 項情境假設最低申購單位數", ch4, units
        ).compare(ch4.value, units.value, fail_message="須等於第四章第 4 項最低申購單位數")
    )
    return out


# ---------------------------------------------------------------- 重複寫出的值（Issue #317）


def repeated(ctx: IssuerContext) -> list[CheckResult]:
    """重複寫出的值逐處等於正式條款：每單位面額 = 第一章第 6 項；「每單位面額 × X%」的 X = 100；情境總報酬率的申購單位數
    = 第四章第 4 項最低申購單位數；情境初始投資金額 = 最低申購單位數 × 每一單位金額；天期 = 第一章第 13 項(1)。"""
    f = ctx.ts.f
    denom, units, unit, tenor = f("denomination"), f("minimum_subscription_units"), f("unit_amount"), f("tenor_months")
    investment = (
        ParsedField("investment", FieldStatus.PRESENT, units.value * unit.value, list(units.evidence))
        if units.ok and unit.ok
        else next(p for p in (units, unit) if not p.ok)
    )
    hundred = ParsedField.present("redemption_pct", gs.REDEMPTION_PCT, [])
    rules = {  # 種類 → (rule_id, 欄位, 正式條款, 不符說明)
        "denomination": ("doc.denomination_mentions", "denomination", denom, "須等於第一章第 6 項每單位面額"),
        "redemption_pct": ("doc.redemption_pct", "redemption_pct", hundred, "到期買回金額須為每單位面額 × 100%"),
        "units": ("doc.scenario_units", "scenario_units", units, "須等於第四章第 4 項最低申購單位數"),
        "investment": (
            "doc.scenario_units",
            "scenario_investment",
            investment,
            "須等於第四章第 4 項最低申購單位數 × 每一單位金額",
        ),
        "tenor": ("doc.tenor_mentions", "tenor_months", tenor, "須等於第一章第 13 項(1) 年期"),
    }
    out = []
    for kind, (rid, field, source, fail) in rules.items():
        for m in ctx.ts.repeated.get(kind, []):
            pf = m.parsed(field)
            out.append(
                kit.doc_check(ctx, rid, field, m.where, source, pf).compare(
                    source.value, m.value, evidence=list(m.evidence), fail_message=fail
                )
            )
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        *coupon(ctx),
        *document_info(ctx),
        *periods(ctx),
        *autocall_terms(ctx),
        *underlying_terms(ctx),
        *ki_terms(ctx),
        *tables(ctx),
        *repeated(ctx),
        *scenario.run(ctx),  # 第 17 項情境的共用檢查（Issue #294）；其餘上手切換後改由單份核對呼叫
    ]
