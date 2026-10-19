"""CA 專屬規則（docs/rules/ca-check-rules.md）：月配息率與其他出處、說明書內部交叉驗證、配息表與 KO 觀察日表（KO 每日／
每期兩種）、多標的非記憶式、贖回 % 與情境重複寫出的贖回 %（Issue #319）。情境基本假設、情境價格表與情境重複寫出的
配息率、面額、配息月數由共用情境規則（rules/scenario.py，Issue #294）核對。

參考條件表欄位、價格推算、審查標準（含 CA 分節：固定警語、名稱樣板、發行機構名稱、商品種類、允許片語）與回填是各上手
共用的規則。說明書有年利率（第 16 項年化配息率），不讀參考條件表（REFERENCE_FIELDS 為空）。保證機構、總代理人名稱、
發行不成立門檻與期初定價依作業人員決定不核對。界限事件三處一致、價格表列數 = 第 10 項標的數由 parser 檢查（不一致時
欄位不合法，共用規則轉人工覆核）。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..schema import CheckResult, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import squash
from . import kit
from .kit import Check, IssuerContext

ISSUER = "CA"
REFERENCE_FIELDS: tuple[str, ...] = ()
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_12",
        "description": "警語 2～12 的全文不核對（只保留禁用語檢查；第三章 6.(4) 受託機構多「綜合」特例放行；2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的中文名稱、交易所、價格表幣別字不核對；以第 10 項彭博代碼與價格表價格為準",
    },
    {"rule_id": "doc.initial_prices", "description": "期初訂價本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.guarantor_and_agent",
        "description": "保證機構與總代理人的名稱、地址電話、發行不成立門檻不核對（2026-10-15 作業人員決定）",
    },
    {
        "rule_id": "doc.initial_pricing",
        "description": "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP），參考條件表「期初定價」欄不和說明書比，只檢查是允許值"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 16 項情境只核對基本假設、情境價格表、配息月數與重複寫出的配息率、面額、贖回 %、幣別（Issue #319）；"
        "配息金額、累積配息、股數、剩餘金額、報酬率、情境三的執行價與收盤價、假設匯率不重算，"
        "KO 每日的 n/N 比例配息不推算（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構註冊辦公地點、信用評等、計算代理機構、交易商、付款代理機構、結算機構、準據法不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：單一標的記憶式、KO 每期＋KI、"
        "KO 每期 Non-Call 介於 2 和天期之間、USD／EUR／JPY 以外的幣別、Crédit Agricole CIB Financial Solutions 以外的發行主體；"
        "範本以外的寫法會轉人工覆核",
    },
]


# ---------------------------------------------------------------- 配息率（核對規則 §3.3）


def coupon(ctx: IssuerContext) -> list[CheckResult]:
    """配息表配息率 = 年利率 ÷ 12（4 位）；第 15 項 (4) 最低保證配息率 = 配息表配息率（第 16 項情境的配息率由共用情境規則
    `scenario.params` 核對，Issue #294）。"""
    f = ctx.ts.f
    annual, monthly = f("coupon_pa_pct"), f("monthly_coupon_pct")
    out = [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "配息表配息率 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            kit.monthly_of_annual(annual.value) if annual.ok else None,
            monthly.value,
            tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="第 9 項配息表的配息率須等於第 16 項年化配息率 ÷ 12 四捨五入到 4 位",
        )
    ]
    rate = f("guaranteed_coupon_pct")
    out.append(
        kit.doc_check(
            ctx, "doc.coupon_mentions", "guaranteed_coupon_pct", "第 15 項 (4) 最低保證配息率", monthly, rate
        ).compare(monthly.value, rate.value, fail_message="最低保證配息率須等於配息表的配息率")
    )
    note = f("coupon_note_pct")
    out.append(
        kit.doc_check(
            ctx, "doc.coupon_mentions", "coupon_note_pct", "第 9 項 (a) 投資人請注意的月配息率", monthly, note
        ).compare(monthly.value, note.value, fail_message="舉例的月配息率須等於配息表的配息率（Issue #319）")
    )
    return out


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    """第一章第 1 項中文名稱 = 封面；三個 (ISIN：…) 相同；第一章第 5 項幣別（中文與 ISO 代碼）、封面幣別代碼。"""
    f = ctx.ts.f
    zh, art1 = f("name_zh"), f("name_art1_zh")
    out = [
        kit.doc_check(ctx, "doc.name_consistency", "name_art1_zh", "第一章第 1 項商品名稱", zh, art1).compare(
            zh.value,
            art1.value,
            ok=zh.ok and art1.ok and squash(zh.value) == squash(art1.value),
            tolerance="忽略空白",
            fail_message="須等於封面「商品中文名稱」",
        )
    ]
    isin = f("isin")
    out += [
        kit.doc_check(ctx, "doc.isin_consistency", field, name, isin, f(field)).compare(
            isin.value, f(field).value, fail_message="ISIN 須與封面商品中文名稱後的 ISIN 相同"
        )
        for field, name in (("isin_en", "封面商品英文名稱後的 ISIN"), ("isin_art1", "第一章第 1 項商品名稱後的 ISIN"))
    ]
    cz, art5 = f("currency_zh"), f("currency_art5")
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, art5).compare(
            cz.value, art5.value
        )
    )
    mapped = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    for field, name in (("currency_art5_iso", "第一章第 5 項計價幣別代碼"), ("currency_cover_iso", "封面計價幣別代碼")):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, "doc.currency_consistency", field, name, cz, pf).compare(
                mapped, pf.value, fail_message="括號內的幣別代碼須對應封面中文計價幣別（審查標準幣別對照）"
            )
        )
    return out


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 中英文名稱月數（日期表期數由共用規則 `schedule.period_count`、情境基本假設天期由 `scenario.params` 檢查）。"""
    f = ctx.ts.f
    tenor, zh, en = f("tenor_months"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months, en_months = months(zh, r"(\d+)個月", "商品中文名稱"), months(en, r"(\d+)-Month", "商品英文名稱")
    return [
        kit.doc_check(ctx, rid, "name_months_zh", "商品中文名稱月數", tenor, zh_months).compare(
            tenor.value, zh_months.value
        ),
        kit.doc_check(ctx, rid, "name_months_en", "商品英文名稱月數", tenor, en_months).compare(
            tenor.value, en_months.value
        ),
    ]


def autocall_terms(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項 (4) 最低保證配息率的月數、情境一配息月數（第 1／第 1~k 個月）= Non-Call。"""
    f = ctx.ts.f
    k = f("first_callable_period")
    months = f("guaranteed_coupon_months")
    first = f("scenario_first_months")
    return [
        kit.doc_check(
            ctx, "doc.autocall_terms", "guaranteed_coupon_months", "第 15 項 (4) 最低保證配息率的月數", k, months
        ).compare(k.value, months.value, fail_message="「第一個月／前k個月」的月數須等於第一個可提前出場期"),
        kit.doc_check(ctx, "doc.autocall_terms", "scenario_first_months", "第 16 項情境一的配息月數", k, first).compare(
            k.value, first.value, fail_message="情境一「第 1~k 個月配息金額」的 k 須等於第一個可提前出場期"
        ),
    ]


def memory(ctx: IssuerContext) -> list[CheckResult]:
    """多標的又沒有記憶式兩句（非記憶式）：寫「所有連結標的於預定自動提前出場觀察日之收盤價…」是已知的非記憶式寫法，
    交給參考條件表 KO(memo) 比對（Issue #344）；其他句型未知 → 轉人工覆核（2026-10-15 確認）。"""
    f = ctx.ts.f
    mem, uls, wording = f("ko_memory"), f("underlyings"), f("ko_memory_wording")
    if not (mem.ok and uls.ok) or mem.value or len(uls.value) == 1:
        return []
    if wording.ok and wording.value[2]:
        return []
    return [
        Check("doc.ko_memory", "ko_memory", Item.expected("多標的非記憶式"), ctx.document).result(
            S.REVIEW_REQUIRED,
            actual=f"{len(uls.value)} 檔、非記憶式",
            evidence=mem.evidence,
            reason="variant_not_covered",
            message="多標的又沒有記憶式句型（「或任何先前的觀察日」「無須於同一觀察日」），也不是「所有連結標的於預定自動提前出場"
            "觀察日之收盤價…」：句型未知，請人工確認是否為非記憶式",
        )
    ]


# ---------------------------------------------------------------- 贖回 % 與重複寫出的值（Issue #319）


def repeated_terms(ctx: IssuerContext) -> list[CheckResult]:
    """第 9 項 (b)、(c) 贖回金額「商品面額 x 100%」；情境一～三贖回金額行的 % = 第 9 項 (b)／(c)（比照 BBVA #305；
    金額、股數、報酬率不重算）。情境重複寫出的配息率、面額、配息月數由共用情境規則 `scenario.params` 核對（Issue #294）。"""
    f = ctx.ts.f
    out = [
        Check("doc.redemption_pct", field, Item.expected(name), ctx.document)
        .needs(f(field))
        .compare(Decimal(100), f(field).value, fail_message="贖回金額須為商品面額 x 100%")
        for field, name in (
            ("ko_redemption_pct", "第 9 項 (b) 自動提前出場贖回金額"),
            ("maturity_redemption_pct", "第 9 項 (c) 到期贖回金額"),
        )
    ]
    for container, rid, expected, what in (
        ("scenario_ko_redemption_mentions", "doc.redemption_pct", f("ko_redemption_pct"), "第 9 項 (b) 的 %"),
        (
            "scenario_maturity_redemption_mentions",
            "doc.redemption_pct",
            f("maturity_redemption_pct"),
            "第 9 項 (c) 的 %",
        ),
    ):
        pf = f(container)
        if not pf.ok:
            out.append(Check(rid, container, Item.expected(what), ctx.document).review(pf))
            continue
        for occ in pf.value:
            out.append(
                kit.doc_check(ctx, rid, occ.field, f"{occ.where}的{occ.name}", expected, occ.value).compare(
                    expected.value, occ.value.value, fail_message=f"須等於{what}"
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
        *memory(ctx),
        *repeated_terms(ctx),
    ]
