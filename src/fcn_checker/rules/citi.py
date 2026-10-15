"""Citi 專屬規則（docs/rules/citi-check-rules.md）：配息率與年息、說明書內部交叉驗證、日期表結構、假設價格表。

參考條件表欄位（含期初定價、標的依代號配對）、價格推算、審查標準（含 Citi 分節：保證機構與總代理人名稱、商品種類、
發行不成立門檻）與回填是各上手共用的規則。只有年利率要讀參考條件表（ADR 0009，見 REFERENCE_FIELDS）。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。每筆結果的項目名稱寫在規則旁；預期值來自說明書其他位置、推算或審查標準。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import full_brackets, squash
from . import kit
from .kit import Check, IssuerContext

ISSUER = "CITI"
# 本上手規則可讀的參考條件表欄位（ADR 0009，須在 reference_fields 宣告：配息率與年息依表上年利率核對，同 BARC／MS／BNP／SG）
REFERENCE_FIELDS = ("coupon_pa_pct",)
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_13",
        "description": "固定警語 2.～13. 的全文不核對（只保留禁用語與名稱結構檢查；2026-10-14 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的中文名稱、分類與交易所不核對；以第 9 項標的表彭博代碼為準",
    },
    {
        "rule_id": "doc.initial_prices",
        "description": "期初股價本身的外部正確性（沒有權威來源）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 15 項情境分析只核對假設價格表 = 第 9 項標的表；情境算式、投資單位數、假設收盤價、總報酬率與年化報酬率"
        "不核對（2026-10-14 確認）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構、總代理人、保證機構的地址電話、總代理人負責人、信用評等、最終匯率條款不核對（2026-10-14 確認）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：P 型 Non-Call > 1、P 型非記憶式、D 型多標的非記憶式、單一標的記憶式、5 檔標的、"
        "HKD／AUD／EUR／GBP／ZAR 計價、CGMHI 以外的發行主體；範本以外的寫法會轉人工覆核",
    },
]
RATE_Q = kit.Q4  # 配息率 = 年利率 ÷ 12，四捨五入 4 位（文件可能去尾零，比數值）


def _norm_name(value: str) -> str:
    """名稱比對：忽略空白，括號全半形不計。"""
    return full_brackets(squash(value))


def monthly_rate(annual: Decimal) -> Decimal:
    return (annual / 12).quantize(RATE_Q, ROUND_HALF_UP)


# ---------------------------------------------------------------- 配息率與年息（核對規則 §3.3，讀參考條件表）


def monthly_coupon(ctx: IssuerContext) -> list[CheckResult]:
    """第 13 項、第 14 項(2)、第 13 項末段舉例、第 15 項假設 d) 的配息率 = 參考條件表年利率 ÷ 12，四捨五入到 4 位。"""
    return kit.rate_mentions(
        ctx,
        "derive.monthly_coupon",
        "monthly_coupon_pct",
        "配息率",
        ctx.ts.coupon_mentions,
        lambda annual, value: (monthly_rate(annual) == value, monthly_rate(annual)),
        "四捨五入（half-up）到 4 位後比數值（文件可能去尾零）",
        lambda annual: f"推算：年利率 {annual}% ÷ 12",
    )


def annual_mentions(ctx: IssuerContext) -> list[CheckResult]:
    """第 14 項(2)、第 13 項末段舉例的年化配息率 = 參考條件表年利率。"""
    return kit.rate_mentions(
        ctx,
        "derive.coupon_pa_mentions",
        "coupon_pa_pct",
        "年化配息率",
        ctx.ts.annual_mentions,
        kit.cmp_pct,
        "依說明書顯示位數四捨五入後比對",
        lambda annual: "年化配息率應等於年利率",
    )


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    code, trustee = f("product_code"), f("trustee_product_code")
    out = [
        kit.doc_check(
            ctx, "doc.trustee_product_code", "trustee_product_code", "受託或銷售機構商品代號", code, trustee
        ).compare(
            code.value,
            trustee.value or "（空白）",
            ok=code.ok and trustee.ok and trustee.value == code.value,
            fail_message="封面受託或銷售機構商品代號須等於集保商品代號"
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
                tolerance="忽略空白；括號全半形不計",
                fail_message=f"{where}須等於封面商品中文名稱（不含「（下稱「本商品」）」）",
            )
        )
    cz, c5, iso5 = f("currency_zh"), f("currency_art5"), f("currency_art5_iso")
    out.append(
        kit.doc_check(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, c5).compare(
            squash(cz.value) if cz.ok else None, squash(c5.value) if c5.ok else None
        )
    )
    expected_iso = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    out.append(
        kit.doc_check(
            ctx, "doc.currency_consistency", "currency_art5_iso", "第一章第 5 項計價幣別代碼", cz, iso5
        ).compare(
            expected_iso,
            iso5.value,
            ok=expected_iso is not None and iso5.value == expected_iso,
            fail_message="第一章第 5 項括號內的幣別代碼須對應封面計價幣別（審查標準幣別對照）",
        )
    )
    trade, initial = f("trade_date"), f("initial_valuation_date")
    out.append(
        kit.doc_check(
            ctx, "doc.initial_valuation_date", "initial_valuation_date", "第 12 項期初評價日", trade, initial
        ).compare(trade.value, initial.value, fail_message="第 12 項期初評價日須等於交易日")
    )
    return out


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

    zh_months = months(zh, r"CGMHI(\d+)個月", "商品中文名稱")
    en_months = months(en, r"^(\d+)-Month", "商品英文名稱")
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


def _has(pf: ParsedField, word: str) -> bool | None:
    return (word in squash(pf.value)) if pf.ok else None


def autocall_terms(ctx: IssuerContext) -> list[CheckResult]:
    """有無自動提前出場：第 13 項自動提前出場界限 ⇔ 名稱字樣 ⇔ 標的表自動提前出場界限欄 ⇔ 第 8 項 (4)（與日期表型態、第 12 項(9)
    的一致由 parser 判定，不一致時 KO 觀察方式與 Non-Call 轉人工覆核）；
    記憶式：第 13 項段名與記憶條款 ⇔ 名稱「記憶式」；非記憶式的第一章不得出現「記憶式」。"""
    f = ctx.ts.f
    has, zh, en, table = (f(x) for x in ("has_autocall", "name_zh", "name_en", "underlying_table"))
    label = {True: "有自動提前出場", False: "無自動提前出場"}
    out = []
    for field, pf, word, name in (
        ("name_zh", zh, "自動提前出場", "商品中文名稱的自動提前出場字樣"),
        ("name_en", en, "Autocall", "商品英文名稱的 Autocall 字樣"),
    ):
        out.append(
            kit.doc_check(ctx, "doc.autocall_wording", field, name, has, pf).compare(
                label.get(has.value) if has.ok else None,
                label.get(_has(pf, word)) if pf.ok else None,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="名稱有無自動提前出場字樣須與第 13 項有無自動提前出場界限一致",
            )
        )
    art8 = f("art8_autocall")
    out.append(
        kit.doc_check(ctx, "doc.autocall_wording", "art8_autocall", "第 8 項 (4) 發生自動提前出場", has, art8).compare(
            label.get(has.value) if has.ok else None,
            label.get(art8.value) if art8.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 8 項有無「(4) 發生自動提前出場」須與第 13 項有無自動提前出場界限一致",
        )
    )
    out.append(
        kit.doc_check(ctx, "doc.autocall_wording", "underlying_table", "標的表自動提前出場界限欄", has, table).compare(
            label.get(has.value) if has.ok else None,
            label.get("ko" in table.value["columns"]) if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="標的表有無自動提前出場界限欄須與第 13 項一致",
        )
    )
    mem, words = f("ko_memory"), f("memory_wording_ch1")
    mem_label = {True: "記憶式", False: "非記憶式"}
    out.append(
        kit.doc_check(ctx, "doc.ko_memory", "name_zh", "商品中文名稱的記憶式字樣", mem, zh).compare(
            mem_label.get(mem.value) if mem.ok else None,
            mem_label.get(_has(zh, "記憶式")) if zh.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="名稱有無記憶式字樣須與第 13 項記憶式自動提前出場事件一致",
        )
    )
    check = kit.doc_check(ctx, "doc.memory_wording", "memory_wording_ch1", "非記憶式不得寫「記憶式」", mem, words)
    if mem.ok and mem.value:
        out.append(check.compare("記憶式", "記憶式", message="記憶式商品，不檢查「記憶式」用字"))
    else:
        out.append(
            check.compare(
                0,
                words.value,
                evidence=list(words.evidence),
                fail_message="非記憶式商品，第一章卻出現「記憶式」（2026-10-14 確認：非記憶式就應該寫非記憶式）",
            )
        )
    return out


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KI：第 8 項觸及下限事件 ⇔ 有無「下限價：」 ⇔ 標的表下限價欄；KI 每期觀察的 t=1-n 的 n = 天期。"""
    f = ctx.ts.f
    ki, pct, table, tenor = f("ki_type"), f("ki_pct"), f("underlying_table"), f("tenor_months")
    doc_ki = kit.doc_ki(ki)
    label = {True: "有 KI", False: "無 KI"}
    out = []
    check = Check("doc.ki_consistency", "ki_pct", Item.expected("第 8 項下限價"), ctx.document)
    if doc_ki is None:
        return [check.review(ki)]
    has_pct = pct.status == FieldStatus.PRESENT
    out.append(
        check.needs(ki).compare(
            label[doc_ki != "none"],
            label[has_pct] if has_pct or pct.status == FieldStatus.NOT_APPLICABLE else None,
            ok=has_pct == (doc_ki != "none"),
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            evidence=ki.evidence + pct.evidence,
            fail_message="第 8 項有無「下限價：」須與觸及下限事件的寫法一致",
        )
    )
    out.append(
        kit.doc_check(ctx, "doc.ki_consistency", "underlying_table", "標的表下限價欄", ki, table).compare(
            label[doc_ki != "none"],
            label["ki" in table.value["columns"]] if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="標的表有無下限價欄須與 KI 型態一致",
        )
    )
    if doc_ki == "P":
        out.append(
            kit.doc_check(ctx, "doc.ki_consistency", "ki_periods", "觸及下限事件觀察日期數（t=1-n）", tenor, ki)
            .needs(f("ki_periods"))
            .compare(
                tenor.value, f("ki_periods").value, fail_message="KI 每期觀察「在任t 個觀察日 (t=1-n)」的 n 須等於天期"
            )
        )
    return out


def redemption(ctx: IssuerContext) -> list[CheckResult]:
    """第 8 項(3)(a) 到期給付 = 每單位商品面額的 100%（固定）。"""
    pf = ctx.ts.f("maturity_redemption_pct")
    check = Check("doc.redemption_pct", "maturity_redemption_pct", Item.expected("第 8 項到期給付"), ctx.document)
    return [check.needs(pf).compare(100, pf.value, fail_message="第 8 項到期給付須為每單位商品面額的 100%")]


def tables(ctx: IssuerContext) -> list[CheckResult]:
    """§9 標的表表頭 % = 第 8、13 項的 K／KI／KO %；§15 假設價格表逐格 = §9 標的表（代碼、各價格、表頭 %）。"""
    f = ctx.ts.f
    table, scen = f("underlying_table"), f("scenario_table")
    out = []
    for col, key, name in (
        ("strike", "strike_pct", "標的表執行價欄頭 %"),
        ("ki", "ki_pct", "標的表下限價欄頭 %"),
        ("ko", "ko_pct", "標的表自動提前出場界限欄頭 %"),
    ):
        pf = f(key)
        if table.ok and col not in table.value["pcts"]:
            continue  # 沒有這欄（無 KI／無自動提前出場）：一致性由 ki_terms、autocall_terms 檢查
        out.append(
            kit.doc_check(ctx, "doc.table_header_pct", f"header_{col}", name, pf, table).compare(
                pf.value, table.value["pcts"].get(col) if table.ok else None
            )
        )
    check = kit.doc_check(ctx, "doc.scenario_table", "scenario_table", "第 15 項假設價格表", table, scen)
    if table.ok and scen.ok:
        issues = []
        if scen.value["tickers"] != table.value["tickers"]:
            issues.append(f"代碼 {scen.value['tickers']} 不等於標的表 {table.value['tickers']}")
        if scen.value["columns"] != table.value["columns"]:
            issues.append("欄位與標的表不同")
        if scen.value["pcts"] != table.value["pcts"]:
            issues.append("表頭百分比與標的表不同")
        for i, (a, b) in enumerate(zip(scen.value["rows"], table.value["rows"], strict=False), start=1):
            diff = [c for c in b if a.get(c) != b[c]]
            if diff:
                issues.append(f"第 {i} 檔 {'、'.join(diff)} 與標的表不同")
        out.append(
            check.compare(
                None, None, ok=not issues, reason="value_mismatch", message="；".join(issues) or "逐格等於第 9 項標的表"
            )
        )
    else:
        out.append(check.compare(None, None))
    return out


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    table, final, maturity, k = (
        f(x) for x in ("date_table", "final_valuation_date", "maturity_date", "first_callable_period")
    )
    names = {"schedule.coupon_dates": "日期表配息日與末列", "schedule.autocall_dates": "日期表提前出場期別"}
    if not table.ok:
        return [Check(rid, "date_table", Item.expected(n), ctx.document).review(table) for rid, n in names.items()]
    kind, rows = table.value["type"], table.value["rows"]
    observed = {"D": "end", "P": "observation"}.get(kind)
    out = []
    issues = []
    payments = [r["payment"] for r in rows]
    if any(a >= b for a, b in zip(payments, payments[1:], strict=False)):
        issues.append("配息日沒有逐期遞增")
    if observed:
        late = [r["period"] for r in rows if r[observed] is not None and r[observed] >= r["payment"]]
        if late:
            issues.append(
                f"第 {'、'.join(map(str, late))} 期{'配息觀察期間結束日' if kind == 'D' else '觀察日'}沒有早於配息日"
            )
        if final.ok and rows[-1][observed] != final.value:
            issues.append(
                f"末列{'結束日' if kind == 'D' else '觀察日'} {rows[-1][observed]} 不等於期末評價日 {final.value}"
            )
    if maturity.ok and payments[-1] != maturity.value:
        issues.append(f"末列配息日 {payments[-1]} 不等於到期日 {maturity.value}")
    out.append(
        kit.structure_check(
            ctx,
            "schedule.coupon_dates",
            "coupon_dates",
            names["schedule.coupon_dates"],
            [table, final, maturity],
            issues,
        )
    )
    issues = []
    if kind == "D" and k.ok:
        n = k.value
        blank = [r["period"] for r in rows if r["start"] is None and r["end"] is None]
        if blank != list(range(1, n)):
            issues.append(f"開始日與結束日都是「不適用」的期別應為第 1～{n - 1} 期，實際為 {blank or '無'}")
        if rows[n - 1]["end"] is None:
            issues.append(f"第 {n} 期沒有配息觀察期間結束日")
        for prev, row in zip(rows, rows[1:], strict=False):
            if row["period"] > n and row["start"] != prev["end"]:
                issues.append(f"第 {row['period']} 期開始日 {row['start']} 不等於前期結束日 {prev['end']}")
    if kind == "D" or (kind == "P" and k.ok):
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
        *annual_mentions(ctx),
        *document_info(ctx),
        *periods(ctx),
        *autocall_terms(ctx),
        *ki_terms(ctx),
        *redemption(ctx),
        *tables(ctx),
        *schedule(ctx),
    ]
