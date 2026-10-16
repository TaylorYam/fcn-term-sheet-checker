"""MS 第一章第 18 項情境分析（核對規則 §3.7）：情境標題、各情境金額。

MS 的總配息是「已進位的每期配息 × 期數」（文件算式 `A 美元×k`），與 HSBC 用未進位年利率推算不同。
重印價格表、假設的總投資金額與年期、各處月配息率、獲利情境年化報酬率與較差情境執行價由共用情境規則（rules/scenario.py，
Issue #294）核對；每期配息的預期值用參考條件表年利率推得的月配息率（§3.7）。
情境標題、期數寫法未知或算式找不到時轉人工覆核，不跳過後宣稱通過。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..schema import CheckResult, Item
from . import kit
from .kit import Check, IssuerContext


def titles(ctx: IssuerContext) -> list[CheckResult]:
    """情境標題要有獲利情境與一個較差情境（假設的總投資金額與年期由共用情境規則 `scenario.params` 核對）。"""
    sec = ctx.ts.scenarios
    kinds = [s.kind for s in sec.scenarios]
    issues = (
        [] if kinds.count("worse") == 1 and "profit" in kinds else ["找不到獲利情境與一個較差情境（標題含「較差」）"]
    )
    return [
        kit.calc_result(
            "doc.scenario_parameters",
            "scenario_titles",
            "第 18 項情境標題",
            issues,
            kit.evidence(sec.lines[:1]),
            review=True,
            actual=kinds,
        )
    ]


def calculations(ctx: IssuerContext) -> list[CheckResult]:
    """各情境：每期配息 = 面額 × 月配息率（年利率 ÷ 12，四捨五入到 4 位），half-up 到 2 位；
    獲利情境損益 = 每期配息 × 假設的配息次數；較差情境總配息 = 每期配息 × 天期，
    損益 = 總配息 + 文件寫的到期贖回價值 − 面額。每期配息算式要全部讀得出來，讀不出來的轉人工覆核。"""
    f, rid = ctx.ts.f, "doc.scenario_calculations"
    denom, tenor = f("denomination"), f("tenor_months")
    annual, ov, problem = kit.annual_rate(ctx, rid, "scenario_calculations")
    if problem:
        return [problem]
    out = []
    for s in ctx.ts.scenarios.scenarios:
        if s.kind == "default":
            continue
        tag = f"情境{s.number}"
        coupon_item, pnl_item = Item.derived(f"{tag}每期配息", [ov]), Item.derived(f"{tag}損益", [ov])
        bad = next((p for p in (denom, tenor) if not p.ok), None)
        if bad is not None:
            out.extend(
                Check(rid, field, item, ctx.document, ov=(ov,)).review(bad)
                for field, item in ((f"{tag}_coupon", coupon_item), (f"{tag}_pnl", pnl_item))
            )
            continue
        monthly = kit.monthly_of_annual(annual)
        a = (Decimal(denom.value) * monthly / 100).quantize(kit.Q2, ROUND_HALF_UP)
        issues = []
        for h in s.coupons:
            c = h.value
            if c.denomination != denom.value:
                issues.append(f"算式中的面額 {c.denomination} 不等於第一章面額 {denom.value}")
            if c.amount != (c.denomination * c.rate / 100).quantize(kit.Q2, ROUND_HALF_UP) or c.amount != a:
                issues.append(f"每期配息 {c.amount} 應為 {a}（面額 × 月配息率 {monthly}%，四捨五入到 2 位）")
        evidence = [e for h in s.coupons for e in kit.evidence(h.lines)]
        if not s.coupons or len(s.coupons) != s.coupon_formulas:
            unread = "找不到每期配息算式" if not s.coupons else "有每期配息算式的寫法無法辨識"
            out.append(
                kit.calc_result(
                    rid, f"{tag}_coupon", coupon_item, [unread], kit.evidence(s.lines[:1]), review=True, ov=[ov]
                )
            )
        else:
            out.append(kit.calc_result(rid, f"{tag}_coupon", coupon_item, issues, evidence, expected=a, ov=[ov]))
        out.append(_pnl(rid, s, tag, pnl_item, a, denom.value, tenor.value, ov))
    return out


def _pnl(rid, s, tag, item, a, denom, tenor, ov) -> CheckResult:
    field = f"{tag}_pnl"
    if len(s.pnl) != 1 or s.assumed is None:
        missing = "損益算式" if len(s.pnl) != 1 else "假設的配息次數（「假設在第 k 個…」）"
        return kit.calc_result(
            rid, field, item, [f"找不到{missing}或出現多次"], kit.evidence(s.lines[:1]), review=True, ov=[ov]
        )
    h, assumed = s.pnl[0], s.assumed.value
    p = h.value
    issues = []
    if s.kind == "profit":
        if assumed is None:
            return kit.calc_result(
                rid, field, item, ["獲利情境的假設期數寫法未知"], kit.evidence(s.lines[:1]), review=True, ov=[ov]
            )
        if p.denomination != denom or p.principal != denom:
            issues.append(f"算式中的面額不等於第一章面額 {denom}")
        if p.coupon != a:
            issues.append(f"每期配息 {p.coupon} 應為 {a}")
        if p.periods != assumed:
            issues.append(f"配息次數 {p.periods} 應等於假設的第 {assumed} 期")
        if p.pnl != p.coupon * p.periods:
            issues.append(f"損益 {p.pnl} 應為 {p.coupon} × {p.periods} = {p.coupon * p.periods}")
        expected = a * assumed
    else:
        if assumed is not None:
            return kit.calc_result(
                rid, field, item, ["較差情境的假設期數寫法未知"], kit.evidence(s.lines[:1]), review=True, ov=[ov]
            )
        if p.coupon != a:
            issues.append(f"每期配息 {p.coupon} 應為 {a}")
        if p.periods != tenor:
            issues.append(f"總配息的期數 {p.periods} 應等於天期 {tenor}")
        if p.denomination != denom:
            issues.append(f"算式中的面額 {p.denomination} 不等於第一章面額 {denom}")
        total = p.coupon * p.periods + p.redemption - p.denomination
        if p.pnl != total:
            issues.append(f"損益 {p.pnl} 應為 {p.coupon} × {p.periods} + {p.redemption} − {p.denomination} = {total}")
        expected = a * tenor + p.redemption - denom
    return kit.calc_result(rid, field, item, issues, kit.evidence(h.lines), expected=expected, actual=p.pnl, ov=[ov])


def run(ctx: IssuerContext) -> list[CheckResult]:
    return [*titles(ctx), *calculations(ctx)]
