"""Natixis 專屬規則（docs/rules/natixis-check-rules.md）：月配息率與年利率其他出處、說明書內部交叉驗證、日期表（KO 每日／
每期兩種）、連結標的表表頭、情境假設與情境價格表、空白的受託機構商品代號。

參考條件表欄位、價格推算、審查標準（含 Natixis 分節：固定警語、名稱樣板、發行機構名稱、商品種類）與回填是各上手共用的
規則。說明書有年利率，不讀參考條件表（REFERENCE_FIELDS 為空）。發行人、保證機構名稱、發行不成立門檻與期初定價依作業人員
決定不核對（審查標準沒有對應分節；說明書沒寫期初定價）。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import re

from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import squash
from . import kit
from .iis import IisIssuerContext
from .kit import Check, IssuerContext

ISSUER = "NATIXIS"
REFERENCE_FIELDS: tuple[str, ...] = ()
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_11",
        "description": "警語 (2)～(11) 的全文不核對（只保留禁用語檢查；2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的發行公司名稱、本交易所、貨幣欄不核對；以連結標的表彭博代碼為準",
    },
    {"rule_id": "doc.initial_prices", "description": "期初進場價格本身的外部正確性（沒有權威來源）"},
    {
        "rule_id": "doc.issuer_agent_and_guarantor",
        "description": "發行人（台北分行）與保證機構（無）的名稱、地址電話、發行不成立門檻不核對（2026-10-15 作業人員決定）",
    },
    {
        "rule_id": "doc.initial_pricing",
        "description": "說明書沒有寫期初定價方式（開盤價／收盤價／VWAP），參考條件表「期初定價」欄不和說明書比，只檢查是允許值"
        "（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 16 項情境只核對假設、情境價格表與情境一的期別；配息金額、股數、剩餘現金、報酬率不重算，"
        "情境二～四不核對（2026-10-15 確認）",
    },
    {
        "rule_id": "doc.agents_and_addresses",
        "description": "發行機構地址電話、信用評等、計算代理機構、交易商、付款代理機構、結算系統、準據法、基本公開說明書日期不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：4 檔以上標的、單一標的記憶式或有 KI 每日、KO 每期＋KI 每日／每期、"
        "USD／JPY／AUD 以外的幣別、法國外貿銀行以外的發行主體；範本以外的寫法會轉人工覆核",
    },
]


# ---------------------------------------------------------------- 配息率（核對規則 §3.3）


def coupon(ctx: IssuerContext) -> list[CheckResult]:
    """第 13 項月配息率 = 年利率 ÷ 12（4 位）；第 16 項情境假設年利率、各期配息「x (y%/12)」的 y = 第 13 項年利率。"""
    f = ctx.ts.f
    annual, monthly = f("coupon_pa_pct"), f("monthly_coupon_pct")
    out = [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "月配息率 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            kit.monthly_of_annual(annual.value) if annual.ok else None,
            monthly.value,
            tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="第 13 項月配息率須等於年利率 ÷ 12 四捨五入到 4 位",
        )
    ]
    scen, terms = f("scenario_coupon_pct"), f("scenario_coupon_terms")
    out.append(
        kit.doc_check(
            ctx, "doc.coupon_mentions", "scenario_coupon_pct", "第 16 項情境假設 f. 年利率", annual, scen
        ).compare(annual.value, scen.value, fail_message="情境假設的年利率須等於第 13 項年利率")
    )
    out.append(
        kit.doc_check(
            ctx, "doc.coupon_mentions", "scenario_coupon_terms", "第 16 項各期配息的年利率", annual, terms
        ).compare(
            [annual.value] if annual.ok else None,
            terms.value,
            fail_message="情境各期配息「面額 x (年利率/12)」的年利率須等於第 13 項年利率",
        )
    )
    return out


# ---------------------------------------------------------------- 說明書內部交叉驗證（核對規則 §3.5）


def document_info(ctx: IssuerContext) -> list[CheckResult]:
    """名稱三處（封面標題、第一章第 1 項中文、英文）；第一章第 5 項幣別（中文與 ISO 代碼）、封面幣別代碼；受託機構商品代號空白。"""
    f = ctx.ts.f
    zh = f("name_zh")
    out = []
    for field, name in (("name_title", "封面標題的商品名稱"), ("name_art1_zh", "第一章第 1 項商品中文名稱")):
        pf = f(field)
        out.append(
            kit.doc_check(ctx, "doc.name_consistency", field, name, zh, pf).compare(
                zh.value,
                pf.value,
                ok=zh.ok and pf.ok and squash(zh.value) == squash(pf.value),
                tolerance="忽略空白",
                fail_message="須等於封面「商品中文名稱」",
            )
        )
    en, art1 = f("name_en"), f("name_art1_en")
    out.append(
        kit.doc_check(ctx, "doc.name_consistency", "name_art1_en", "第一章第 1 項商品英文名稱", en, art1).compare(
            en.value,
            art1.value,
            ok=en.ok and art1.ok and squash(en.value) == squash(art1.value),
            tolerance="忽略空白；「(Non Principal」視同「(Non-Principal」（2026-10-15 確認）",
            fail_message="須等於封面「商品英文名稱」",
        )
    )
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
    out.extend(distributor_code(ctx, f("distributor_code")))
    return out


def distributor_code(ctx: IssuerContext | IisIssuerContext, code: ParsedField) -> list[CheckResult]:
    """「受託機構商品代號為」有標籤卻空白 → 轉人工覆核（NX2、NX8，2026-10-15 確認）；有值或沒有標籤的文件不檢查
    （有值時要不要和 TDCC 商品代號比，作業人員沒有決定，樣本也沒有）。"""
    if code.status == FieldStatus.NOT_APPLICABLE:
        return []
    check = Check("doc.distributor_product_code", "distributor_code", Item.expected("受託機構商品代號"), ctx.document)
    check = check.needs(code)
    if (problem := check.blocked) is not None:
        return [problem]
    if squash(code.value):
        return []
    return [
        check.result(
            S.REVIEW_REQUIRED,
            actual="（空白）",
            reason="blank_field",
            message="「受託機構商品代號為」後面是空白，請人工確認（2026-10-15 確認轉人工覆核）",
        )
    ]


def periods(ctx: IssuerContext) -> list[CheckResult]:
    """天期 = 日期表列數 = 中英文名稱月數 = 情境假設 c. 天期與 d. 配息觀察期間數。"""
    f = ctx.ts.f
    tenor, table, zh, en = f("tenor_months"), f("date_table"), f("name_zh"), f("name_en")
    rid = "doc.coupon_periods"

    def months(pf: ParsedField, pattern: str, what: str) -> ParsedField:
        m = re.search(pattern, squash(pf.value)) if pf.ok else None
        if pf.ok and m is None:
            return ParsedField.invalid("name_months", [], f"{what}找不到月數")
        return ParsedField("name_months", pf.status, int(m[1]) if m else None, pf.evidence, note=pf.note)

    zh_months, en_months = months(zh, r"(\d+)個月", "商品中文名稱"), months(en, r"(\d+)-Month", "商品英文名稱")
    out = [
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
    for field, name in (
        ("scenario_tenor", "第 16 項情境假設 c. 天期"),
        ("scenario_periods", "第 16 項情境假設 d. 配息觀察期間數"),
    ):
        pf = f(field)
        out.append(kit.doc_check(ctx, rid, field, name, tenor, pf).compare(tenor.value, pf.value))
    return out


def autocall_terms(ctx: IssuerContext) -> list[CheckResult]:
    """情境一提前到期期別 = Non-Call（Non-Call = 天期時情境一在最後一期提前到期，NX3）；情境假設 e. 的 KO 觀察寫法 = 第 12 項。"""
    f = ctx.ts.f
    k, obs = f("first_callable_period"), f("ko_observation")
    first, wording = f("scenario_first_period"), f("scenario_ko_wording")
    out = [
        kit.doc_check(
            ctx, "doc.autocall_terms", "scenario_first_period", "第 16 項情境一提前到期期別", k, first
        ).compare(k.value, first.value, fail_message="情境一須在第 Non-Call 期提前到期")
    ]
    actual = wording.value if wording.ok else None
    exp = (obs.value, k.value if obs.value == "D" else None) if obs.ok and k.ok else None
    label = {"D": "每日觀察", "P": "每期觀察"}

    def show(v):
        return None if v is None else label[v[0]] + (f"、自第 {v[1]} 期" if v[1] else "")

    out.append(
        kit.doc_check(
            ctx, "doc.autocall_terms", "scenario_ko_wording", "第 16 項情境假設 e. 自動提前到期觀察日", obs, k, wording
        ).compare(
            show(exp),
            show(actual),
            ok=exp is not None and exp == actual,
            fail_message="情境假設的 KO 觀察寫法須與第 12 項相同",
        )
    )
    return out


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KI：觸及事件判定寫法 ⇔ 連結標的表有無界限價格欄 ⇔ 有無「界限價格」定義（不一致轉人工覆核）。"""
    f = ctx.ts.f
    ki, table, definition = f("ki_type"), f("underlying_table"), f("ki_def_pct")
    doc_ki = kit.doc_ki(ki)
    if doc_ki is None:
        return [Check("doc.ki_consistency", "ki_type", Item.expected("第 12、15 項觸及事件"), ctx.document).review(ki)]
    label = {True: "有 KI", False: "無 KI"}
    has = doc_ki != "none"
    return [
        kit.doc_check(ctx, "doc.ki_consistency", "underlying_table", "連結標的表界限價格欄", ki, table).compare(
            label[has],
            label["ki" in table.value["columns"]] if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="連結標的表有無界限價格欄須與觸及事件寫法一致",
        ),
        kit.doc_check(ctx, "doc.ki_consistency", "ki_def_pct", "第 15 項「界限價格」定義", ki).compare(
            label[has],
            label[definition.ok],
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="有無「界限價格」定義須與觸及事件寫法一致",
        ),
    ]


def tables(ctx: IssuerContext) -> list[CheckResult]:
    """連結標的表表頭 % = 第 14、15 項定義；情境價格表逐格 = 連結標的表、表頭 % 相同；情境假設 a. 面額 = 第一章第 5 項。"""
    f = ctx.ts.f
    table = f("underlying_table")
    out = []
    for col, key, name in (
        ("ko", "ko_def_pct", "連結標的表自動提前到期贖回價格欄頭 %"),
        ("ki", "ki_def_pct", "連結標的表界限價格欄頭 %"),
        ("strike", "strike_def_pct", "連結標的表執行價格欄頭 %"),
    ):
        if table.ok and col not in table.value["pcts"]:
            continue  # 沒有這欄（無 KI）：一致性由 ki_terms 檢查
        pf = f(key)
        out.append(
            kit.doc_check(ctx, "doc.table_header_pct", f"header_{col}", name, pf, table).compare(
                pf.value, table.value["pcts"].get(col) if table.ok else None
            )
        )
    scen, denom, scen_denom = f("scenario_table"), f("denomination"), f("scenario_denomination")
    check = kit.doc_check(
        ctx, "doc.scenario_table", "scenario_table", "第 16 項情境價格表與假設", table, scen, denom, scen_denom
    )
    if table.ok and scen.ok and denom.ok and scen_denom.ok:
        issues = []
        if scen.value["pcts"] != table.value["pcts"]:
            issues.append("情境價格表表頭 % 和連結標的表不同")
        rows, own = scen.value["rows"], table.value["rows"]
        if len(rows) != len(own):
            issues.append(f"情境價格表 {len(rows)} 列，連結標的表 {len(own)} 列")
        else:
            for n, (a, b) in enumerate(zip(rows, own, strict=True), start=1):
                diff = [c for c in b if a.get(c) != b[c]]
                if diff:
                    issues.append(f"第 {n} 列{'、'.join(diff)}和連結標的表不同")
        if scen_denom.value != denom.value:
            issues.append(f"情境假設面額 {scen_denom.value} 不等於每單位商品面額 {denom.value}")
        out.append(
            check.compare(
                None,
                None,
                ok=not issues,
                reason="value_mismatch",
                message="；".join(issues) or "等於連結標的表與每單位商品面額",
            )
        )
    else:
        out.append(check.compare(None, None))
    return out


# ---------------------------------------------------------------- 日期表（核對規則 §3.5）


def schedule(ctx: IssuerContext) -> list[CheckResult]:
    """D：第 1 期開始日 = 發行日、各期連續、結束日早於同列配息日、末列結束日 = 最終評價日、末列配息日 = 到期日。
    P：配息日遞增、末列 = 到期日；觀察日表末列 = 最終評價日／到期日、觀察日早於同列自動提前到期日。"""
    f = ctx.ts.f
    table, final, maturity, issue = f("date_table"), f("final_valuation_date"), f("maturity_date"), f("issue_date")
    name = "日期表"
    if not table.ok:
        return [Check("schedule.coupon_dates", "date_table", Item.expected(name), ctx.document).review(table)]
    rows = table.value["rows"]
    issues = []
    if table.value["kind"] == "D":
        if issue.ok and rows[0]["start"] != issue.value:
            issues.append(f"第 1 期配息觀察期間開始日 {rows[0]['start']} 不等於發行日 {issue.value}")
        gaps = [b["period"] for a, b in zip(rows, rows[1:], strict=False) if b["start"] != a["end"]]
        if gaps:
            issues.append(f"第 {'、'.join(map(str, gaps))} 期開始日不等於前一期結束日")
        late = [r["period"] for r in rows if r["end"] >= r["payment"]]
        if late:
            issues.append(f"第 {'、'.join(map(str, late))} 期配息觀察期間結束日沒有早於配息日")
        if final.ok and rows[-1]["end"] != final.value:
            issues.append(f"末列配息觀察期間結束日 {rows[-1]['end']} 不等於最終評價日 {final.value}")
    else:
        pays = [r["payment"] for r in rows]
        if any(b <= a for a, b in zip(pays, pays[1:], strict=False)):
            issues.append("配息日沒有逐期遞增")
        ko = table.value["ko_rows"]
        late = [r["t"] for r in ko if r["observation"] >= r["autocall"]]
        if late:
            issues.append(f"自動提前到期觀察日表第 {'、'.join(map(str, late))} 列觀察日沒有早於自動提前到期日")
        if final.ok and ko[-1]["observation"] != final.value:
            issues.append(f"自動提前到期觀察日表末列 {ko[-1]['observation']} 不等於最終評價日 {final.value}")
        if maturity.ok and ko[-1]["autocall"] != maturity.value:
            issues.append(f"自動提前到期觀察日表末列自動提前到期日 {ko[-1]['autocall']} 不等於到期日 {maturity.value}")
    if maturity.ok and rows[-1]["payment"] != maturity.value:
        issues.append(f"末列配息日 {rows[-1]['payment']} 不等於到期日 {maturity.value}")
    return [
        kit.structure_check(ctx, "schedule.coupon_dates", "coupon_dates", name, [table, final, maturity, issue], issues)
    ]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        *coupon(ctx),
        *document_info(ctx),
        *periods(ctx),
        *autocall_terms(ctx),
        *ki_terms(ctx),
        *tables(ctx),
        *schedule(ctx),
    ]
