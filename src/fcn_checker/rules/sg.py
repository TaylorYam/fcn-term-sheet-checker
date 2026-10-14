"""SG 專屬規則（docs/rules/sg-check-rules.md）：配息率與平均年化報酬率、說明書內部交叉驗證、日期表結構、固定值重印。

參考條件表欄位、價格推算、審查標準（含 SG 分節：保證機構與總代理人名稱、商品種類、最低申購倍數）與回填是各上手
共用的規則；情境分析見 rules/sg_scenario.py。只有年利率要讀參考條件表（ADR 0005 的例外，見 REFERENCE_FIELDS）。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。每筆結果的項目名稱寫在規則旁；預期值來自說明書其他位置、推算或審查標準。
"""

from __future__ import annotations

import re

from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import squash
from . import kit, sg_scenario
from .kit import Check, IssuerContext

ISSUER = "SG"
# 本上手規則可讀的參考條件表欄位（ADR 0005 的例外：配息率與平均年化報酬率依表上年利率核對，同 BARC／MS／BNP）
REFERENCE_FIELDS = ("coupon_pa_pct",)
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_10",
        "description": "固定警語二～十的全文不核對（只保留七的禁用語檢查；2026-10-13 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的中文名稱、資產種類與交易所不核對；以第 9 項(1) 彭博代碼為準",
    },
    {
        "rule_id": "doc.initial_prices",
        "description": "期初價格本身的外部正確性（沒有權威來源）；參考條件表「期初定價」欄不核對",
    },
    {
        "rule_id": "doc.scenario_worse_details",
        "description": "較差／最差情境的假設收盤價與匯率不核對（數額、應實際交付數、現金交割、總報酬率與平均年化報酬率依文件寫的收盤價與匯率驗算）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構、總代理人、保證機構的地址電話、總代理人負責人、基本公開說明書日期、發行不成立門檻、營業日城市不核對"
        "（2026-10-13 確認）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：D 型多標的非記憶式、新版面的 P 型非記憶式與 KI 每期觀察、單一標的記憶式、5 檔標的、"
        "HKD／AUD／EUR／GBP／ZAR 計價；範本以外的寫法會轉人工覆核",
    },
]
AUTOCALL_DAY_LAG = 3  # D 型「自動提前出場日：為發生自動提前出場事件後第 3 個營業日」
RATE_Q = kit.Q4  # 配息率 = 年利率 ÷ 12，固定四捨五入 4 位（JPY 也 4 位）


def _doc(ctx: IssuerContext, rid: str, field: str, name: str, *deps: ParsedField) -> Check:
    """文件內部比對的結果身分：項目名稱為 `name`（預期值來自同一份文件其他位置），依賴欄位有問題轉人工覆核。"""
    return Check(rid, field, Item.expected(name), ctx.document).needs(*deps)


def _norm_name(value: str) -> str:
    """名稱比對：忽略空白與全形／半形括號（2026-10-13 確認）。"""
    return re.sub(r"[()（）]", "", squash(value))


# ---------------------------------------------------------------- 配息率與平均年化報酬率（核對規則 §3.3，讀參考條件表）


def _rate_mentions(ctx: IssuerContext, rid, field, what, mentions, compare, tolerance, message) -> list[CheckResult]:
    annual, ov, problem = sg_scenario.annual_rate(ctx, rid, field)
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
    """第 8 項配息率、第 15 項假設配息率與各情境算式的配息率 = 參考條件表年利率 ÷ 12，四捨五入（half-up）到 4 位。"""
    return _rate_mentions(
        ctx,
        "derive.monthly_coupon",
        "monthly_coupon_pct",
        "配息率",
        ctx.ts.coupon_mentions,
        lambda annual, value: (sg_scenario.monthly_rate(annual) == value, sg_scenario.monthly_rate(annual)),
        "四捨五入（half-up）到 4 位",
        lambda annual: f"推算：年利率 {annual}% ÷ 12",
    )


def annualized_return(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項獲利情境（較佳、中間）每處平均年化報酬率 = 參考條件表年利率（依說明書位數四捨五入）。"""
    return _rate_mentions(
        ctx,
        "derive.annualized_return",
        "annualized_return_pct",
        "平均年化報酬率",
        ctx.ts.annualized_mentions,
        kit.cmp_pct,
        "依說明書顯示位數四捨五入後比對",
        lambda annual: "獲利情境的平均年化報酬率應等於年利率",
    )


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    code, trustee = f("product_code"), f("trustee_product_code")
    out = [
        _doc(ctx, "doc.trustee_product_code", "trustee_product_code", "受託或銷售機構商品代號", code, trustee).compare(
            code.value,
            trustee.value or "（空白）",
            ok=code.ok and trustee.ok and trustee.value == code.value,
            fail_message="封面受託或銷售機構商品代號須等於商品代號" + ("（說明書空白）" if trustee.value == "" else ""),
        )
    ]
    isin, isin26 = f("isin"), f("isin_art26")
    out.append(
        _doc(ctx, "doc.isin_consistency", "isin_art26", "第一章第 26 項 ISIN", isin, isin26).compare(
            isin.value, isin26.value, fail_message="第一章第 26 項 ISIN 代碼須等於封面 ISIN"
        )
    )
    zh = f("name_zh")
    for field, name, where in (
        ("cover_title", "封面標題名稱", "封面標題"),
        ("name_art1", "第一章第 1 項商品名稱", "第一章第 1 項商品名稱"),
    ):
        pf = f(field)
        out.append(
            _doc(ctx, "doc.name_consistency", field, name, zh, pf).compare(
                _norm_name(zh.value) if zh.ok else None,
                _norm_name(pf.value) if pf.ok else None,
                tolerance="忽略空白與全形／半形括號",
                fail_message=f"{where}須等於封面商品中文名稱（不含「（下稱「本商品」）」；忽略空白與括號）",
            )
        )
    cz, c5 = f("currency_zh"), f("currency_art5")
    out.append(
        _doc(ctx, "doc.currency_consistency", "currency_art5", "第一章第 5 項計價幣別", cz, c5).compare(
            squash(cz.value) if cz.ok else None, squash(c5.value) if c5.ok else None
        )
    )
    return out


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 日期表列數 = 中英文名稱月數 = 第 15 項假設天期。"""
    f = ctx.ts.f
    tenor, table, zh, en = f("tenor_months"), f("date_table"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    sec = ctx.ts.scenarios
    scen = (
        ParsedField.present("scenario_tenor", sec.tenor.values[0], list(sec.tenor.lines))
        if sec.tenor
        else ParsedField.missing("scenario_tenor", "第 15 項假設找不到「天期：N個月」")
    )
    zh_months = months(zh, r"計價(\d+)個月", "商品中文名稱")
    en_months = months(en, r"(\d+)-month", "商品英文名稱")
    return [
        _doc(ctx, rid, "date_table", "第 12 項日期表期數", tenor, table).compare(
            tenor.value, len(table.value["rows"]) if table.ok else None, fail_message="日期表列數須等於天期"
        ),
        _doc(ctx, rid, "name_months_zh", "商品中文名稱月數", tenor, zh_months).compare(tenor.value, zh_months.value),
        _doc(ctx, rid, "name_months_en", "商品英文名稱月數", tenor, en_months).compare(tenor.value, en_months.value),
        _doc(ctx, rid, "scenario_tenor", "第 15 項假設天期", tenor, scen).compare(tenor.value, scen.value),
    ]


def _has(pf: ParsedField, word: str) -> bool | None:
    return (word in squash(pf.value)) if pf.ok else None


def autocall_terms(ctx: IssuerContext) -> list[CheckResult]:
    """有無自動提前出場：第 8 項（自動提前出場事件段）⇔ 名稱字樣 ⇔ 標的表自動提前出場價欄 ⇔ 第 8 項觀察寫法與日期表表頭；
    記憶式：第 8 項記憶事件 ⇔ 名稱「記憶式／Memory」。不一致轉人工覆核（寫法超出範本規格）。"""
    f = ctx.ts.f
    has, zh, en, table, wording, dtable = (
        f(x) for x in ("has_autocall", "name_zh", "name_en", "underlying_table", "ko_wording", "date_table")
    )
    label = {True: "有自動提前出場", False: "無自動提前出場"}
    out = []
    for field, pf, word, name in (
        ("name_zh", zh, "自動提前出場", "商品中文名稱的自動提前出場字樣"),
        ("name_en", en, "Autocallable", "商品英文名稱的 Autocallable 字樣"),
    ):
        out.append(
            _doc(ctx, "doc.autocall_wording", field, name, has, pf).compare(
                label.get(has.value) if has.ok else None,
                label.get(_has(pf, word)) if pf.ok else None,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="名稱有無自動提前出場字樣須與第 8 項有無自動提前出場事件一致",
            )
        )
    out.append(
        _doc(ctx, "doc.autocall_wording", "underlying_table", "標的表自動提前出場價欄", has, table).compare(
            label.get(has.value) if has.ok else None,
            label.get("ko" in table.value["columns"]) if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="標的表有無自動提前出場價欄須與第 8 項有無自動提前出場事件一致",
        )
    )
    kinds = {"D": "每日觀察", "P": "每期觀察", "none": "無自動提前出場"}
    out.append(
        _doc(
            ctx, "doc.ko_observation", "ko_observation", "KO 觀察方式（第 8 項寫法與日期表）", wording, dtable
        ).compare(
            kinds.get(wording.value["type"]) if wording.ok else None,
            ("每日觀察" if dtable.value["type"] == "D" else "每期觀察／無自動提前出場") if dtable.ok else None,
            ok=wording.ok and dtable.ok and (wording.value["type"] == "D") == (dtable.value["type"] == "D"),
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 8 項觀察期間的寫法與第 12 項日期表表頭不一致",
        )
    )
    mem = f("ko_memory")
    mem_label = {True: "記憶式", False: "非記憶式"}
    for field, pf, word, name in (
        ("name_zh", zh, "記憶式", "商品中文名稱的記憶式字樣"),
        ("name_en", en, "Memory", "商品英文名稱的 Memory 字樣"),
    ):
        out.append(
            _doc(ctx, "doc.ko_memory", field, name, mem, pf).compare(
                mem_label.get(mem.value) if mem.ok else None,
                mem_label.get(_has(pf, word)) if pf.ok else None,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="名稱有無記憶式字樣須與第 8 項有無記憶事件一致",
            )
        )
    return out


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KI：第 8 項到期贖回款（KI 型態）⇔ 有無「下限價：」行 ⇔ 標的表下限價欄；最差表現 k 數 = 標的數。"""
    f = ctx.ts.f
    ki, pct, table, uls, k = f("ki_type"), f("ki_pct"), f("underlying_table"), f("underlyings"), f("worst_k")
    doc_ki = kit.doc_ki(ki)
    label = {True: "有 KI", False: "無 KI"}
    out = []
    check = Check("doc.ki_consistency", "ki_pct", Item.expected("第 8 項下限價"), ctx.document)
    if doc_ki is None:
        out.append(check.review(ki))
    else:
        has_pct = pct.status == FieldStatus.PRESENT
        out.append(
            check.needs(ki).compare(
                label[doc_ki != "none"],
                label[has_pct] if has_pct or pct.status == FieldStatus.NOT_APPLICABLE else None,
                ok=has_pct == (doc_ki != "none"),
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                evidence=ki.evidence + pct.evidence,
                fail_message="第 8 項有無「下限價：」須與到期贖回款的觀察寫法一致",
            )
        )
        out.append(
            _doc(ctx, "doc.ki_consistency", "underlying_table", "標的表下限價欄", ki, table).compare(
                label[doc_ki != "none"],
                label["ki" in table.value["columns"]] if table.ok else None,
                fail=S.REVIEW_REQUIRED,
                reason="document_inconsistent",
                fail_message="標的表有無下限價欄須與 KI 型態一致",
            )
        )
    out.append(
        _doc(ctx, "doc.worst_of_count", "worst_k", "最差表現之連結標的 k 數", uls, k).compare(
            len(uls.value) if uls.ok else None,
            k.value,
            fail_message="第 8 項「連結標的(k)(k=1、2…)」的個數須等於標的數",
        )
    )
    return out


def redemption(ctx: IssuerContext) -> list[CheckResult]:
    """第 8 項自動提前出場贖回金額（有自動提前出場時）、到期贖回金額 = 每單位面額 × 100%（固定）。"""
    f = ctx.ts.f
    out = []
    for field, name in (
        ("autocall_redemption_pct", "第 8 項自動提前出場贖回金額"),
        ("maturity_redemption_pct", "第 8 項到期贖回金額"),
    ):
        pf = f(field)
        check = Check("doc.redemption_pct", field, Item.expected(name), ctx.document)
        if pf.status == FieldStatus.NOT_APPLICABLE:
            out.append(check.not_applicable(pf))
            continue
        out.append(check.needs(pf).compare(100, pf.value, fail_message=f"{name}須為每單位面額 × 100%"))
    return out


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def _structure_check(ctx: IssuerContext, rid: str, field: str, name: str, deps, issues, tolerance=None) -> CheckResult:
    return _doc(ctx, rid, field, name, *deps).compare(
        None, None, ok=not issues, reason="schedule_inconsistent", message="；".join(issues), tolerance=tolerance
    )


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    f = ctx.ts.f
    table, maturity, wording, k = (f(x) for x in ("date_table", "maturity_date", "ko_wording", "first_callable_period"))
    names = {
        "schedule.coupon_dates": "日期表配息日與末期標籤",
        "schedule.period_starts": "日期表配息週期起始日",
        "schedule.autocall_dates": "提前出場期別與觀察期間",
    }
    if not table.ok:
        return [Check(rid, "date_table", Item.expected(n), ctx.document).review(table) for rid, n in names.items()]
    kind, rows = table.value["type"], table.value["rows"]
    observed = "end" if kind == "D" else "pricing"
    label = "配息週期終止日" if kind == "D" else "評價日"
    out = []

    issues = []
    payments = [r["payment"] for r in rows]
    if any(a >= b for a, b in zip(payments, payments[1:], strict=False)):
        issues.append("配息日沒有逐期遞增")
    late = [r["period"] for r in rows if r[observed] is not None and r[observed] >= r["payment"]]
    if late:
        issues.append(f"第 {'、'.join(map(str, late))} 期{label}沒有早於配息日")
    last = rows[-1]["period"]
    for key, text in (("final_label_rows", "（最終評價日）"), ("maturity_label_rows", "（到期日）")):
        marked = table.value[key]
        if last not in marked:
            issues.append(f"末列沒有標「{text}」")
        if other := sorted({p for p in marked if p != last}):
            issues.append(f"第 {'、'.join(map(str, other))} 期不是末列卻標「{text}」")
    if maturity.ok and payments[-1] != maturity.value:
        issues.append(f"末期配息日 {payments[-1]} 不等於到期日 {maturity.value}")
    out.append(
        _structure_check(
            ctx, "schedule.coupon_dates", "coupon_dates", names["schedule.coupon_dates"], [table, maturity], issues
        )
    )

    if kind == "D":
        issues = []
        if k.ok and wording.ok and wording.value["type"] == "D":
            n = k.value
            blank = [r["period"] for r in rows if r["start"] is None]
            if blank != list(range(1, n + 1)):
                issues.append(f"起始日為「不適用」的期別應為第 1～{n} 期，實際為 {blank or '無'}")
            if n < len(rows) and rows[n]["start"] != wording.value["start"]:
                issues.append(
                    f"第 {n + 1} 期起始日 {rows[n]['start']} 不等於第 8 項觀察期間起始日 {wording.value['start']}"
                )
        for prev, row in zip(rows, rows[1:], strict=False):
            if prev["end"] is not None and row["start"] is not None and row["start"] != prev["end"]:
                issues.append(f"第 {row['period']} 期起始日 {row['start']} 不等於前期終止日 {prev['end']}")
        out.append(
            _structure_check(
                ctx,
                "schedule.period_starts",
                "period_starts",
                names["schedule.period_starts"],
                [table, k, wording],
                issues,
            )
        )

    issues = []
    if k.ok and wording.ok:
        n, wtype = k.value, wording.value["type"]
        if not 1 <= n <= len(rows):
            issues.append(f"第一個可提前出場期 {n} 不在第 1～{len(rows)} 期之間")
        elif wtype == "D":
            pr, day = f("prorate_range"), f("autocall_day")
            if pr.ok and pr.value != (n + 1, len(rows)):
                issues.append(
                    f"配息按比例條款寫第 {pr.value[0]}-{pr.value[1]} 個配息週期，應為第 {n + 1}-{len(rows)} 個"
                )
            elif not pr.ok:
                issues.append("找不到配息按比例條款「惟若自動提前出場事件發生於第k-n個配息週期」")
            if day.ok and day.value != AUTOCALL_DAY_LAG:
                issues.append(f"自動提前出場日寫事件後第 {day.value} 個營業日，應為第 {AUTOCALL_DAY_LAG} 個")
            elif not day.ok:
                issues.append("找不到「自動提前出場日：為發生自動提前出場事件後第3個營業日」")
        elif wtype == "P":
            if wording.value["n"] != len(rows):
                issues.append(f"第 8 項「任一評價日i (i={n}-{wording.value['n']})」的期數不等於日期表 {len(rows)} 期")
            for r in rows:
                want = r["period"] >= n
                if (r["pricing"] is not None) != want:
                    issues.append(f"第 {r['period']} 期評價日{'應有日期' if want else '應為「不適用」'}")
        else:
            for r in rows:
                want = r["period"] == len(rows)
                if (r["pricing"] is not None) != want:
                    issues.append(
                        f"無自動提前出場：第 {r['period']} 期評價日{'應有日期' if want else '應為「不適用」'}"
                    )
    out.append(
        _structure_check(
            ctx,
            "schedule.autocall_dates",
            "autocall_dates",
            names["schedule.autocall_dates"],
            [table, k, wording],
            issues,
        )
    )
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果、審查標準與宣告的參考條件表欄位（年利率）。"""
    return [
        *monthly_coupon(ctx),
        *annualized_return(ctx),
        *document_info(ctx),
        *periods(ctx),
        *autocall_terms(ctx),
        *ki_terms(ctx),
        *redemption(ctx),
        *schedule(ctx),
        *sg_scenario.run(ctx),
    ]
