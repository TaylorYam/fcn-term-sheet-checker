"""BARC 專屬核對規則（docs/rules/barc-check-rules.md）：說明書內部規則與審查標準。

參考條件表欄位的比對見 rules/reference.py（各上手共用）；共用工具見 rules/kit.py。
規則只接收標準化後的說明書欄位與審查標準；不讀檔、不改來源值。
每條規則產生一或多筆 CheckResult；抓不到、歧義、未知值一律轉人工覆核，不猜值。
每筆結果的項目名稱寫在規則旁；預期值來自說明書其他位置或推算（錯訊寫「預期」）。
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from ..parsers.barc_schedule import NA, ScheduleRow, Table
from ..schema import CheckResult, Evidence, Item, ParsedField
from ..schema import CheckStatus as S
from ..text import full_brackets, squash
from .kit import (
    HEADER_PCT_ITEM,
    PRICE_LABEL,
    Q4,
    IssuerContext,
    doc_review,
    next_weekday,
    order_value,
    result,
    shown,
    to_decimal,
    to_int,
)

MONTHLY_TOLERANCE = Decimal("0.0001")

# 第二階段或暫不核對的規則：列入報告「未涵蓋」區，不影響也不假裝通過
NOT_COVERED: list[dict[str, str]] = [
    {"rule_id": "field.monthly_ki", "description": "Monthly KI（MKI）說明書判斷方式（尚無樣本，見 Issue #10）"},
    {
        "rule_id": "doc.underlying_names",
        "description": "標的名稱：不核對，上手依彭博代號帶入名稱，以代號為準（核對規則 §6.2）",
    },
    {"rule_id": "doc.initial_prices", "description": "最初價格本身的外部正確性（無權威來源，Issue #38 排除）"},
    {
        "rule_id": "doc.scenario_other_returns",
        "description": "情境分析有利情況的年化報酬率與最差情況的報酬率（取決於假設的持有期間與價格，無固定基準）",
    },
]


ISSUER = "BARC"


# 單份核對以 IssuerContext 呼叫本模組規則；ctx.ts 為 BarcTermSheet

# 本上手規則可讀的參考條件表欄位（ADR 0005 唯一例外：月配息率推算用表上年利率與天期，Issue #54）
REFERENCE_FIELDS = ("coupon_pa_pct", "tenor_months")


# ---------------------------------------------------------------- 推算規則


def monthly_coupon(ctx: IssuerContext) -> CheckResult:
    """月配息率 = 年利率 × 天期 ÷ 12 ÷ 期數（期數 = 說明書配息表列數）；與說明書差 ≤ 0.0001 視為一致。"""
    rid, field = "derive.monthly_coupon", "monthly_coupon_pct"
    pf, table = ctx.ts.f(field), ctx.ts.f("coupon_table")
    annual, ov_a, p1 = order_value(ctx, "coupon_pa_pct", rid, field, pf, to_decimal, "數字", name="年利率 %")
    tenor, ov_t, p2 = order_value(ctx, "tenor_months", rid, field, pf, to_int, "整數", name="天期（月）")
    for p in (p1, p2):
        if p:
            return p
    item = Item.derived("月配息率 %", [ov_a, ov_t])  # 預期值由表上年利率與天期推算
    if not pf.ok:
        return doc_review(rid, field, pf, None, [ov_a, ov_t], item=item)
    if not table.ok:
        return doc_review(rid, field, table, None, [ov_a, ov_t], item=item)
    periods = len(table.value.rows)
    expected = (annual * tenor / 12 / periods).quantize(Q4, ROUND_HALF_UP)
    ok = abs(expected - pf.value) <= MONTHLY_TOLERANCE
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        pf=pf,
        ov=[ov_a, ov_t],
        reason="" if ok else "value_mismatch",
        tolerance="≤ 0.0001",
        message=f"推算：{annual}% × {tenor} ÷ 12 ÷ {periods} 期（說明書配息表列數），四捨五入到 4 位",
        item=item,
    )


def coupon_consistency(ctx: IssuerContext) -> list[CheckResult]:
    """月配息率在 §9(1)、§14、§15(2)、§17(1) 相同；年利率在 §9(1)、§14、§17(1) 相同。"""
    rid = "doc.coupon_consistency"
    out = []
    for kind, field, name in (("monthly", "monthly_coupon_pct", "月配息率 %"), ("annual", "coupon_pa_pct", "年利率 %")):
        item = Item.expected(name)
        mentions = ctx.ts.coupon_mentions[kind]
        evidence = [Evidence.of(ln) for m in mentions for ln in m.lines]
        missing = [m.article for m in mentions if m.value.is_nan()]
        if missing:
            out.append(
                result(
                    rid,
                    field,
                    S.REVIEW_REQUIRED,
                    evidence=evidence,
                    reason="document_missing",
                    message=f"以下條文找不到{'月配息率' if kind == 'monthly' else '年利率'}：{'、'.join(missing)}",
                    item=item,
                )
            )
            continue
        values = sorted({m.value for m in mentions})
        where = {str(v): [m.article for m in mentions if m.value == v] for v in values}
        ok = len(values) == 1
        out.append(
            result(
                rid,
                field,
                S.PASS if ok else S.MISMATCH,
                expected=None,
                actual=values[0] if ok else where,
                evidence=evidence,
                reason="" if ok else "document_inconsistent",
                message="" if ok else "說明書不同條文的值不一致",
                item=item,
            )
        )
    return out


# ---------------------------------------------------------------- 說明書內部規則


# ---------------------------------------------------------------- 配息表與提前出場表（第二階段）


def _row_ev(rows: list[ScheduleRow]) -> list[Evidence]:
    return [Evidence.of(ln) for r in rows for ln in r.lines]


def _header_ev(table: Table) -> list[Evidence]:
    return [Evidence.of(h) for h in table.header]


def _periods(rows: list[ScheduleRow]) -> str:
    return "、".join(f"第 {r.t} 期" for r in rows)


def coupon_dates(ctx: IssuerContext) -> list[CheckResult]:
    """B1、B2：期數 = 天期；評價日、支付日逐期遞增；每期評價日 < 支付日。"""
    rid = "schedule.coupon_dates"
    table, tenor = ctx.ts.f("coupon_table"), ctx.ts.f("tenor_months")
    periods_item = Item.expected("配息期數")
    if not table.ok:
        return [doc_review(rid, "coupon_table", table, item=Item.expected("配息表"))]
    rows = table.value.rows
    out = []
    if not tenor.ok:
        out.append(doc_review(rid, "coupon_periods", tenor, item=periods_item))
    else:
        ok = len(rows) == tenor.value
        out.append(
            result(
                rid,
                "coupon_periods",
                S.PASS if ok else S.MISMATCH,
                expected=tenor.value,
                actual=len(rows),
                evidence=tenor.evidence + _header_ev(table.value),
                reason="" if ok else "value_mismatch",
                message="配息表列數須等於天期（每月一期）",
                item=periods_item,
            )
        )
    bad = []
    for k, r in enumerate(rows):
        v, p = r.get("valuation"), r.get("payment")
        if not (isinstance(v, dt.date) and isinstance(p, dt.date)) or v >= p:
            bad.append(r)
            continue
        prev_v, prev_p = (rows[k - 1].get("valuation"), rows[k - 1].get("payment")) if k else (None, None)
        if k and not (isinstance(prev_v, dt.date) and isinstance(prev_p, dt.date) and prev_v < v and prev_p < p):
            bad.append(r)
    out.append(
        result(
            rid,
            "coupon_date_order",
            S.MISMATCH if bad else S.PASS,
            actual=_periods(bad) or None,
            evidence=_row_ev(bad) or _header_ev(table.value),
            reason="date_order" if bad else "",
            message="每期評價日 < 支付日，且評價日、支付日逐期遞增",
            item=Item.expected("配息表日期順序"),
        )
    )
    return out


def final_period(ctx: IssuerContext) -> list[CheckResult]:
    """A3、A4：末期評價日 = 最終評價日；末期支付日 = 到期日。"""
    rid = "schedule.final_period"
    table = ctx.ts.f("coupon_table")
    out = []
    for field, key, label, name in (
        ("final_valuation_date", "valuation", "末期評價日須等於最終評價日", "末期評價日"),
        ("maturity_date", "payment", "末期支付日須等於到期日", "末期支付日"),
    ):
        pf, item = ctx.ts.f(field), Item.expected(name)
        bad = next((x for x in (table, pf) if not x.ok), None)
        if bad is not None:
            out.append(doc_review(rid, f"last_{key}", bad, item=item))
            continue
        last = table.value.rows[-1]
        ok = last.get(key) == pf.value
        out.append(
            result(
                rid,
                f"last_{key}",
                S.PASS if ok else S.MISMATCH,
                expected=pf.value,
                actual=last.get(key),
                evidence=pf.evidence + _row_ev([last]),
                reason="" if ok else "value_mismatch",
                message=label,
                item=item,
            )
        )
    return out


def autocall_dates(ctx: IssuerContext) -> list[CheckResult]:
    """C1–C4：自動提前出場表與配息表的日期關係。"""
    rid, table_item = "schedule.autocall_dates", Item.expected("提前出場表")
    coupon, ko = ctx.ts.f("coupon_table"), ctx.ts.f("ko_table")
    for x in (coupon, ko):
        if not x.ok:
            return [doc_review(rid, "ko_table", x, item=table_item)]
    ct, kt = coupon.value, ko.value
    if kt.kind == "ko_fixed":
        pairs = (
            ("ko_valuation", "valuation", "C1 自動提前出場評價日 = 同期配息評價日", "自動提前出場評價日"),
            ("early_redemption", "payment", "C2 指定提前現金贖回日 = 同期配息支付日", "指定提前現金贖回日"),
        )
    elif kt.kind == "ko_period":
        pairs = (("end", "valuation", "C3 期末日 = 同期配息評價日", "提前出場期末日"),)
    elif kt.kind == "coupon":
        return [
            result(
                rid,
                "ko_table",
                S.NOT_APPLICABLE,
                evidence=_header_ev(kt),
                message="評價日表兼作自動提前出場評價日，沒有另一張提前出場表可比對",
                item=table_item,
            )
        ]
    else:
        pairs = ()  # 觀察期合併表：期末日即配息評價日，只需檢查期始日（C4）
    out = []
    for ko_key, c_key, label, name in pairs:
        item = Item.expected(name)
        if len(kt.rows) != len(ct.rows):
            out.append(
                result(
                    rid,
                    ko_key,
                    S.MISMATCH,
                    expected=len(ct.rows),
                    actual=len(kt.rows),
                    evidence=_header_ev(kt),
                    reason="period_count",
                    message=f"{label}：提前出場表與配息表期數不同",
                    item=item,
                )
            )
            continue
        bad = [k for k, c in zip(kt.rows, ct.rows, strict=True) if k.get(ko_key) not in (NA, c.get(c_key))]
        out.append(
            result(
                rid,
                ko_key,
                S.MISMATCH if bad else S.PASS,
                actual=_periods(bad) or None,
                evidence=_row_ev(bad) or _header_ev(kt),
                reason="value_mismatch" if bad else "",
                message=label,
                item=item,
            )
        )
    if kt.kind in ("ko_period", "combined"):
        out.append(_period_starts(ctx, rid, kt))
    return out


def _period_starts(ctx: IssuerContext, rid: str, kt: Table) -> CheckResult:
    """C4：第 1 期期始日為 N/A 或發行日後 1 個平日；之後各期 = 前一期期末日後 1 個平日。

    前一期期末日為 N/A（不可提前出場）時，本期期始日也應為 N/A。
    """
    issue, item = ctx.ts.f("issue_date"), Item.expected("提前出場期始日")
    if not issue.ok:
        return doc_review(rid, "start", issue, item=item)
    bad = []
    for k, r in enumerate(kt.rows):
        start = r.get("start")
        prev_end = issue.value if k == 0 else kt.rows[k - 1].get("end")
        if start == NA:
            if k and prev_end != NA:
                bad.append(r)
        elif not isinstance(prev_end, dt.date) or start != next_weekday(prev_end):
            bad.append(r)
    return result(
        rid,
        "start",
        S.MISMATCH if bad else S.PASS,
        actual=_periods(bad) or None,
        evidence=_row_ev(bad) or _header_ev(kt),
        reason="value_mismatch" if bad else "",
        tolerance="平日只排除週末（無假日曆）",
        message="C4 期始日 = 前一期期末日後 1 個平日；第 1 期為 N/A 或發行日後 1 個平日",
        item=item,
    )


def trigger_per_period(ctx: IssuerContext) -> CheckResult:
    """§13(7) 每期觸發百分比 = §15 觸發百分比定義句。"""
    rid, ko, pct = "doc.autocall_trigger_per_period", ctx.ts.f("ko_table"), ctx.ts.f("ko_pct")
    item = Item.expected("提前出場觸發價格百分比")
    if not ko.ok:
        return doc_review(rid, "trigger", ko, item=item)
    if "trigger" not in ko.value.columns:
        return result(
            rid,
            "trigger",
            S.NOT_APPLICABLE,
            evidence=_header_ev(ko.value),
            message="此型態的提前出場表沒有每期觸發百分比欄",
            item=item,
        )
    if not pct.ok:
        return doc_review(rid, "trigger", pct, item=item)

    def callable_(r: ScheduleRow) -> bool:
        return any(isinstance(r.get(k), dt.date) for k in ("end", "ko_valuation"))

    # 不可提前出場的期別為 N/A；可提前出場的期別必須有百分比且等於定義句
    bad = [r for r in ko.value.rows if r.get("trigger") != pct.value and (r.get("trigger") != NA or callable_(r))]
    return result(
        rid,
        "trigger",
        S.MISMATCH if bad else S.PASS,
        expected=pct.value,
        actual=sorted({str(r.get("trigger")) for r in bad}) if bad else pct.value,
        evidence=pct.evidence + _row_ev(bad),
        reason="value_mismatch" if bad else "",
        message="每期觸發百分比須等於 §15 定義句" + (f"；不符：{_periods(bad)}" if bad else ""),
        item=item,
    )


def scenario_price_table(ctx: IssuerContext) -> CheckResult:
    """§16(3) 情境分析重印價格表逐格 = §15 價格表。"""
    rid, s15, s16 = "doc.scenario_price_table", ctx.ts.f("price_table"), ctx.ts.f("scenario_price_table")
    item = Item.expected("情境試算價格表")
    for x in (s15, s16):
        if not x.ok:
            return doc_review(rid, "scenario_price_table", x, item=item)
    a, b = ctx.ts.price_rows, ctx.ts.scenario_rows
    diffs = [] if len(a) == len(b) else [f"列數 {len(a)} ≠ {len(b)}"]
    for k, (r15, r16) in enumerate(zip(a, b, strict=False), 1):
        for col in sorted(set(r15.values) | set(r16.values)):
            if r15.values.get(col) != r16.values.get(col):
                diffs.append(
                    f"第 {k} 檔 {PRICE_LABEL.get(col, '最初價格')}：§15 {r15.values.get(col)}／§16 {r16.values.get(col)}"
                )
    return result(
        rid,
        "scenario_price_table",
        S.MISMATCH if diffs else S.PASS,
        actual=diffs or None,
        evidence=s16.evidence[:6],
        reason="value_mismatch" if diffs else "",
        message="§16(3) 重印價格表須與 §15 價格表逐格相同",
        item=item,
    )


# ---------------------------------------------------------------- 文件內重複出現處（Issue #38）

_NAME_SUFFIX = "（下稱「本商品」）"


def _same_text(a: str, b: str) -> bool:
    """去空白、括號全半形不計（同審查標準商品名稱的寬鬆度）。"""
    return full_brackets(squash(a)) == full_brackets(squash(b))


def _equal(
    rid: str, field: str, name: str, pf: ParsedField, ref: ParsedField, expected: object, message: str
) -> CheckResult:
    """說明書某處（pf）的值須等於另一處（ref）推得的值（expected）；項目名稱為 `name`。"""
    item = Item.expected(name)
    for x in (pf, ref):
        if not x.ok:
            return doc_review(rid, field, x, item=item)
    ok = _same_text(str(pf.value), str(expected)) if isinstance(expected, str) else pf.value == expected
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        evidence=pf.evidence + ref.evidence,
        reason="" if ok else "document_inconsistent",
        message=message,
        item=item,
    )


def name_consistency(ctx: IssuerContext) -> list[CheckResult]:
    """封面標題與第一章第 1 條商品名稱 = 封面「商品中文名稱」（標題不含「（下稱「本商品」）」）。"""
    rid, cover = "doc.name_consistency", ctx.ts.f("name_zh")
    title = _equal(
        rid,
        "title_name",
        "封面標題商品名稱",
        ctx.ts.f("title_name"),
        cover,
        # 括號先統一為全形，半形的「(下稱「本商品」)」也要去掉
        full_brackets(squash(cover.value)).replace(_NAME_SUFFIX, "") if cover.ok else None,
        "封面標題須等於封面「商品中文名稱」（去掉「（下稱「本商品」）」）",
    )
    art1 = _equal(
        rid,
        "art1_name",
        "第一章第 1 條商品名稱",
        ctx.ts.f("art1_name"),
        cover,
        cover.value,
        "第一章第 1 條商品名稱須等於封面「商品中文名稱」",
    )
    return [title, art1]


def distributor_product_code(ctx: IssuerContext) -> CheckResult:
    code = ctx.ts.f("product_code")
    return _equal(
        "doc.distributor_product_code",
        "distributor_product_code",
        "受託或銷售機構商品代號",
        ctx.ts.f("distributor_product_code"),
        code,
        code.value,
        "封面「受託或銷售機構商品代號」須等於「商品代號」",
    )


def currency_consistency(ctx: IssuerContext) -> CheckResult:
    cz = ctx.ts.f("currency_zh")
    return _equal(
        "doc.currency_consistency",
        "art5_currency",
        "第一章第 5 條計價幣別",
        ctx.ts.f("art5_currency"),
        cz,
        cz.value,
        "第一章第 5 條計價幣別須等於封面「計價幣別」",
    )


def scenario_notional(ctx: IssuerContext) -> CheckResult:
    denom = ctx.ts.f("denomination")
    return _equal(
        "doc.scenario_notional",
        "scenario_notional",
        "情境假設每單位面額",
        ctx.ts.f("scenario_notional"),
        denom,
        denom.value,
        "第 16 條情境假設的每單位商品面額須等於第 6 條面額",
    )


def price_header_pct(ctx: IssuerContext) -> list[CheckResult]:
    """§15 價格表與 §16 情境表欄頭「X（為最初價格的N%）」= §15 定義句的百分比（執行價格另與參考條件表 K(%) 比對）。"""
    rid = "doc.price_header_pct"
    out = []
    for field in ("strike_pct", "ko_pct", "ki_pct"):
        item = Item.expected(HEADER_PCT_ITEM[field.removesuffix("_pct")])
        mentions = [h.mention for h in ctx.ts.header_pcts if h.field == field]
        if field == "strike_pct" and not any(m.article == "第15條" for m in mentions):
            out.append(
                result(
                    rid,
                    field,
                    S.REVIEW_REQUIRED,
                    reason="document_missing",
                    message="第 15 條價格表找不到「執行價格（為最初價格的N%）」欄頭",
                    item=item,
                )
            )
        if not mentions:
            continue
        pf = ctx.ts.f(field)
        if not pf.ok:
            out.append(doc_review(rid, field, pf, item=item))
            continue
        bad = [m for m in mentions if m.value != pf.value]
        out.append(
            result(
                rid,
                field,
                S.MISMATCH if bad else S.PASS,
                expected=pf.value,
                actual=sorted({f"{m.article} {m.value}%" for m in bad}) if bad else pf.value,
                evidence=pf.evidence + [Evidence.of(ln) for m in (bad or mentions) for ln in m.lines],
                reason="document_inconsistent" if bad else "",
                message=f"價格表欄頭共 {len(mentions)} 處，須等於第 15 條定義句",
                item=item,
            )
        )
    return out


def coupon_repeats(ctx: IssuerContext) -> list[CheckResult]:
    """§9(3) 相關配息率與 §16 情境試算中每次出現的月配息率 = §14 正式月配息率。"""
    rid, pf = "doc.coupon_repeats", ctx.ts.f("monthly_coupon_pct")
    mentions = ctx.ts.coupon_mentions["repeat"]
    out = []
    obs = ctx.ts.f("ko_observation")
    if obs.ok and obs.value == "D" and not any(m.article == "第9條(3)" for m in mentions):
        # 期間每日觀察型態的 §9(3) 一定列出相關配息率；抓不到代表寫法不同，不能略過
        out.append(
            result(
                rid,
                "第9條(3)",
                S.REVIEW_REQUIRED,
                evidence=obs.evidence,
                reason="document_missing",
                message="期間每日觀察型態的第9條(3)找不到「相關配息率為…%」",
                item=Item.expected("第9條(3)"),
            )
        )
    for article in dict.fromkeys(m.article for m in mentions):
        group, item = [m for m in mentions if m.article == article], Item.expected(article)
        if any(m.value.is_nan() for m in group):
            out.append(
                result(
                    rid,
                    article,
                    S.REVIEW_REQUIRED,
                    reason="document_missing",
                    evidence=[Evidence.of(ln) for m in group if m.value.is_nan() for ln in m.lines],
                    message=f"{article}有月配息率讀不到數值（寫法與範本不同），請人工確認",
                    item=item,
                )
            )
            continue
        if not pf.ok:
            out.append(doc_review(rid, article, pf, item=item))
            continue
        bad = [m for m in group if m.value != pf.value]
        out.append(
            result(
                rid,
                article,
                S.MISMATCH if bad else S.PASS,
                expected=pf.value,
                actual=sorted({str(m.value) for m in bad}) if bad else pf.value,
                evidence=pf.evidence + [Evidence.of(ln) for m in (bad or group) for ln in m.lines],
                reason="document_inconsistent" if bad else "",
                message=f"{article}共 {len(group)} 處月配息率，須等於第 14 條「配息率」",
                item=item,
            )
        )
    return out


def scenario_returns(ctx: IssuerContext) -> list[CheckResult]:
    """§16(3) 有利情況：總報酬率 = 月配息率 × 配息期數；一般情況：總報酬率 = 月配息率 × 總期數、
    平均年化報酬率 = 正式年利率。有利情況的年化率取決於持有期間，不核對（列未涵蓋）。"""
    rid = "doc.scenario_returns"
    monthly, annual, table = ctx.ts.f("monthly_coupon_pct"), ctx.ts.f("coupon_pa_pct"), ctx.ts.f("coupon_table")
    out = []
    for name, label in (("scenario_favourable", "有利情況"), ("scenario_general", "一般情況")):
        pf, total_item = ctx.ts.f(name), Item.expected(f"{label}總報酬率")
        if not pf.ok:
            out.append(doc_review(rid, f"{name}_total", pf, item=total_item))
            continue
        if name == "scenario_general" and not table.ok:
            out.append(doc_review(rid, f"{name}_total", table, item=total_item))
            continue
        if not monthly.ok:
            out.append(doc_review(rid, f"{name}_total", monthly, item=total_item))
            continue
        periods = pf.value["periods"] if name == "scenario_favourable" else len(table.value.rows)
        total = pf.value["total"]
        expected = shown(monthly.value * periods, total)
        ok = expected == total
        out.append(
            result(
                rid,
                f"{name}_total",
                S.PASS if ok else S.MISMATCH,
                expected=expected,
                actual=total,
                evidence=pf.evidence + monthly.evidence,
                reason="" if ok else "value_mismatch",
                tolerance="依說明書顯示位數四捨五入",
                message=f"{label}總報酬率 = 月配息率 {monthly.value}% × {periods} 期",
                item=total_item,
            )
        )
        if name == "scenario_general":
            annualized_item = Item.expected(f"{label}平均年化報酬率")
            if not annual.ok:
                out.append(doc_review(rid, f"{name}_annualized", annual, item=annualized_item))
                continue
            ann = pf.value["annualized"]
            ok = shown(annual.value, ann) == ann
            out.append(
                result(
                    rid,
                    f"{name}_annualized",
                    S.PASS if ok else S.MISMATCH,
                    expected=annual.value,
                    actual=ann,
                    evidence=pf.evidence + annual.evidence,
                    reason="" if ok else "value_mismatch",
                    message="一般情況（持有至到期、全部配息）平均年化報酬率須等於第 14 條年利率",
                    item=annualized_item,
                )
            )
    return out


def observation_t_range(ctx: IssuerContext) -> CheckResult:
    """§13(7) 自動提前出場觀察期定義句（Daily Memory）各段 t 的起訖：
    保證配息期 G ≥ 1 → (G, G) 與 (G+1, 總期數)；G = 0 → (1, 總期數)。總期數 = 配息表列數。"""
    rid, field, item = "doc.observation_t_range", "observation_t_ranges", Item.expected("自動提前出場觀察期")
    obs, mem = ctx.ts.f("ko_observation"), ctx.ts.f("ko_memory")
    for x in (obs, mem):
        if not x.ok:
            return doc_review(rid, field, x, item=item)
    if not (obs.value == "D" and mem.value):
        return result(
            rid,
            field,
            S.NOT_APPLICABLE,
            evidence=obs.evidence,
            message="只有期間每日觀察的記憶式商品有「自動提前出場觀察期」t 範圍定義句",
            item=item,
        )
    pf, g, table = ctx.ts.f(field), ctx.ts.f("guaranteed_periods"), ctx.ts.f("coupon_table")
    for x in (pf, g, table):
        if not x.ok:
            return doc_review(rid, field, x, item=item)
    n = len(table.value.rows)
    expected = [(g.value, g.value), (g.value + 1, n)] if g.value >= 1 else [(1, n)]
    ok = pf.value == expected

    def show(rs: list[tuple[int, int]]) -> str:
        return "；".join(f"t={a}" if a == b else f"t={a}～{b}" for a, b in rs)

    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=show(expected),
        actual=show(pf.value),
        evidence=pf.evidence + g.evidence,
        reason="" if ok else "value_mismatch",
        message=f"依提前出場表推得保證配息期 {g.value}、配息表 {n} 期",
        item=item,
    )


# ---------------------------------------------------------------- 入口


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """月配息率推算，再加上說明書內部規則（價格推算是各上手共用規則，見 rules/derivation.py）。"""
    return [monthly_coupon(ctx), *document_rules(ctx)]


def document_rules(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：不使用參考條件表的值。"""
    return [
        *coupon_consistency(ctx),
        *coupon_dates(ctx),
        *final_period(ctx),
        *autocall_dates(ctx),
        trigger_per_period(ctx),
        scenario_price_table(ctx),
        *name_consistency(ctx),
        distributor_product_code(ctx),
        currency_consistency(ctx),
        scenario_notional(ctx),
        *price_header_pct(ctx),
        *coupon_repeats(ctx),
        *scenario_returns(ctx),
        observation_t_range(ctx),
    ]
