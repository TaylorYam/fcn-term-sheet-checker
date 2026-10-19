"""Natixis 專屬規則（docs/rules/natixis-check-rules.md）：月配息率與年利率其他出處、說明書內部交叉驗證、重複寫出的值、日期表（KO 每日／
每期兩種）、第 9 項價格表表頭、空白的受託機構商品代號。第 16 項情境價格表、情境假設與情境重複寫出的面額、年利率、實物交割
算式的執行價格由共用情境規則（rules/scenario.py，Issue #294）核對。

參考條件表欄位、價格推算、審查標準（含 Natixis 分節：固定警語、名稱樣板、發行機構名稱、商品種類）與回填是各上手共用的
規則。說明書有年利率；只讀參考條件表的交易日（KO 每日觀察到最終評價日含不含依交易日改版，Issue #351）。發行人、
保證機構名稱、發行不成立門檻與期初定價依作業人員決定不核對（審查標準沒有對應分節；說明書沒寫期初定價）。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

import datetime as dt
import re

from ..schema import CheckResult, DocKind, FieldStatus, Item, OrderValue, ParsedField
from ..schema import CheckStatus as S
from ..text import squash
from . import kit
from .iis import IisIssuerContext
from .kit import Check, IssuerContext, to_date

ISSUER = "NATIXIS"
REFERENCE_FIELDS = ("trade_date",)
# KO 每日觀察「到最終評價日(含)」的生效交易日（含）；以前寫「(不含)」（上手改條款，2026-10-19 確認，Issue #351）
KO_FINAL_INCLUSIVE_FROM = dt.date(2026, 10, 20)
NOT_COVERED = [
    {
        "rule_id": "doc.fixed_warnings_2_to_11",
        "description": "警語 (2)～(11) 的全文不核對（只保留禁用語檢查；2026-10-15 確認）",
    },
    {
        "rule_id": "doc.underlying_names",
        "description": "連結標的發行公司名稱、本交易所、貨幣欄不核對；以第 9 項價格表彭博代碼為準",
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
        "description": "第 16 項情境的配息金額、股數、剩餘現金、報酬率不重算，情境的假設最終價格與實物交割算式（匯率、股價）"
        "的幣別不核對（2026-10-15 確認）；各情境重複寫出的年利率、面額、贖回 %、執行價格、配息列數與金額幣別逐處核對"
        "（Issue #318）",
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
    """第 13 項月配息率 = 年利率 ÷ 12（4 位）；第 16 項情境假設年利率、各期配息「x (y%/12)」的 y 由共用情境規則
    `scenario.params` 和第 13 項年利率比（Issue #294）。"""
    f = ctx.ts.f
    annual, monthly = f("coupon_pa_pct"), f("monthly_coupon_pct")
    return [
        kit.doc_check(
            ctx, "derive.monthly_coupon", "monthly_coupon_pct", "月配息率 %（年利率 ÷ 12）", annual, monthly
        ).compare(
            kit.monthly_of_annual(annual.value) if annual.ok else None,
            monthly.value,
            tolerance="年利率 ÷ 12 四捨五入（half-up）到 4 位後比數值",
            fail_message="第 13 項月配息率須等於年利率 ÷ 12 四捨五入到 4 位",
        )
    ]


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
    """天期 = 中英文名稱月數（日期表期數由共用規則 `schedule.period_count`、情境假設 c. 天期與 d. 配息觀察期間數由
    `scenario.params` 檢查）。"""
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
    final = f("ko_final_day")
    daily = obs.ok and obs.value == "D"
    deps = (obs, k, final, wording) if daily else (obs, k, wording)
    exp = (obs.value, k.value, final.value) if daily else (obs.value, None, None) if obs.ok else None
    label = {"D": "每日觀察", "P": "每期觀察"}

    def show(v):
        if v is None:
            return None
        return label[v[0]] + (f"、自第 {v[1]} 期" if v[1] else "") + (f"、到最終評價日({v[2]})" if v[2] else "")

    out.append(
        kit.doc_check(
            ctx, "doc.autocall_terms", "scenario_ko_wording", "第 16 項情境假設 e. 自動提前到期觀察日", *deps
        ).compare(
            show(exp),
            show(actual),
            ok=exp is not None and exp == actual,
            fail_message="情境假設的 KO 觀察寫法須與第 12 項相同",
        )
    )
    return out


def ko_final_day(document: DocKind, trade: ParsedField, final: ParsedField, where: str) -> CheckResult:
    """KO 每日觀察「到最終評價日(含)／(不含)」依參考條件表交易日：2026-10-20（含）起只接受「(含)」、以前只接受「(不含)」
    （上手改條款，2026-10-19 確認，Issue #351）。讀不到交易日或寫法轉人工覆核。說明書與投資人須知共用。"""
    check = Check(
        "doc.ko_final_day", "ko_final_day", Item.expected(f"{where}自動提前到期觀察日到最終評價日含不含"), document
    ).needs(final, trade)
    if (problem := check.blocked) is not None:
        return problem
    since = trade.value >= KO_FINAL_INCLUSIVE_FROM
    return check.compare(
        "含" if since else "不含",
        final.value,
        tolerance=f"交易日（參考條件表）{KO_FINAL_INCLUSIVE_FROM}（含）起寫「(含)」，以前寫「(不含)」",
        fail_message=f"交易日 {trade.value} {'在' if since else '早於'} {KO_FINAL_INCLUSIVE_FROM}"
        + ("（含）以後，須寫「到最終評價日(含)」" if since else "，須寫「到最終評價日(不含)」"),
    )


def reference_trade_date(ov: OrderValue | None) -> ParsedField:
    """參考條件表交易日 → 欄位；沒有值時是缺漏（同投資人須知刊印日期規則的寫法）。"""
    value = to_date(ov.value) if ov is not None else None
    if value is None:
        return ParsedField.missing("trade_date", "參考條件表沒有交易日，無法決定 KO 觀察日的寫法")
    return ParsedField.present("trade_date", value, [])


def ko_final_day_terms(ctx: IssuerContext) -> list[CheckResult]:
    """說明書第 12 項 KO 每日觀察到最終評價日含不含（每期型沒有這句，不檢查）；第 16 項情境假設 e. 由 `autocall_terms` 比對。"""
    obs = ctx.ts.f("ko_observation")
    if not (obs.ok and obs.value == "D"):
        return []
    trade = reference_trade_date(ctx.sheet_field("trade_date"))
    return [ko_final_day(ctx.document, trade, ctx.ts.f("ko_final_day"), "第 12 項")]


def ki_terms(ctx: IssuerContext) -> list[CheckResult]:
    """KI：觸及事件判定寫法 ⇔ 第 9 項價格表有無界限價格欄 ⇔ 有無「界限價格」定義（不一致轉人工覆核）。"""
    f = ctx.ts.f
    ki, table, definition = f("ki_type"), f("underlying_table"), f("ki_def_pct")
    doc_ki = kit.doc_ki(ki)
    if doc_ki is None:
        return [Check("doc.ki_consistency", "ki_type", Item.expected("第 12、15 項觸及事件"), ctx.document).review(ki)]
    label = {True: "有 KI", False: "無 KI"}
    has = doc_ki != "none"
    return [
        kit.doc_check(ctx, "doc.ki_consistency", "underlying_table", "第 9 項價格表界限價格欄", ki, table).compare(
            label[has],
            label["ki" in table.value.columns] if table.ok else None,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="第 9 項價格表有無界限價格欄須與觸及事件寫法一致",
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
    """第 9 項價格表表頭 % = 第 14、15 項定義（情境價格表由共用情境規則 `scenario.reprint` 核對，Issue #294）。"""
    f = ctx.ts.f
    table = f("underlying_table")
    out = []
    for col, key, name in (
        ("ko", "ko_def_pct", "第 9 項價格表自動提前到期贖回價格欄頭 %"),
        ("ki", "ki_def_pct", "第 9 項價格表界限價格欄頭 %"),
        ("strike", "strike_def_pct", "第 9 項價格表執行價格欄頭 %"),
    ):
        if table.ok and col not in table.value.pcts:
            continue  # 沒有這欄（無 KI）：一致性由 ki_terms 檢查
        pf = f(key)
        out.append(
            kit.doc_check(ctx, "doc.table_header_pct", f"header_{col}", name, pf, table).compare(
                pf.value, table.value.pcts.get(col) if table.ok else None
            )
        )
    return out


def repeated_terms(ctx: IssuerContext) -> list[CheckResult]:
    """重複寫出的值 = 正式條款（Issue #318，比照 BBVA #305）：情境一自動提前到期贖回 % = 第 14 項；第 15 項情況一公式、
    2.a 與情境的到期贖回 % = 第 15 項情況一；情境一配息列數 = Non-Call（第一個可提前到期期），其他情境 = 天期。情境配息
    算式、原始投資本金、實物交割算式的面額與執行價格由共用情境規則核對（Issue #294）。"""
    f = ctx.ts.f
    tenor, first = f("tenor_months"), f("first_callable_period")
    out = []
    for container, rid, expected, what in (
        (
            "autocall_redemption_mentions",
            "doc.redemption_pct",
            f("autocall_redemption_pct"),
            "第 14 項自動提前到期贖回金額 %",
        ),
        (
            "maturity_redemption_mentions",
            "doc.redemption_pct",
            f("maturity_redemption_pct"),
            "第 15 項情況一到期贖回 %",
        ),
    ):
        pf = f(container)
        if not pf.ok:
            out.append(Check(rid, container, Item.expected(what), ctx.document).review(pf))
            continue
        for occ in pf.value:
            got = occ.value.value if occ.value.ok else None
            out.append(
                kit.doc_check(ctx, rid, occ.field, f"{occ.where}{occ.name}", expected, occ.value).compare(
                    [expected.value] * len(got) if expected.ok and got else None,
                    list(got) if got else None,
                    fail_message=f"每一處都須等於{what}",
                )
            )
    rows = f("scenario_row_counts")
    if not rows.ok:
        out.append(
            Check("doc.coupon_periods", "scenario_row_counts", Item.expected("情境配息列數"), ctx.document).review(rows)
        )
    else:
        for n, occ in enumerate(rows.value, start=1):
            base, what = (first, "Non-Call（第一個可提前到期期）") if n == 1 else (tenor, "天期")
            out.append(
                kit.doc_check(ctx, "doc.coupon_periods", occ.field, f"{occ.where}{occ.name}", base, occ.value).compare(
                    base.value, occ.value.value, fail_message=f"情境各期配息的列數須等於{what}"
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
        *ko_final_day_terms(ctx),
        *ki_terms(ctx),
        *tables(ctx),
        *repeated_terms(ctx),
    ]
