"""BNP 第一章第 18 項(3) 情境分析（核對規則 §3.7）：情境標題、各情境算式。

重印標的表、假設的商品面額與投資本金、各處月配息率、獲利情境年化報酬率與較差情境執行價格由共用情境規則
（rules/scenario.py，Issue #294）核對。總配息 = 已進位的每期配息 × 期數（同 MS）；
每期配息的預期值也用表上年利率推得的月配息率（依說明書顯示位數四捨五入）。較差／最差情境的股數、剩餘金額、總報酬率
依文件寫的收盤價與匯率驗算（收盤價、匯率是試算值，本身不核對）。情境標題、期別寫法未知或算式找不到時轉人工覆核。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from ..parsers.bnp_scenario import Scenario
from ..schema import CheckResult, Item, OrderValue
from ..schema import CheckStatus as S
from . import kit
from .kit import Check, IssuerContext

# KI 型態 → 情境依序的種類（範本規格 §4.7）：每日／每期觀察 KI 的最佳情況分 (A)(B) 兩個，無 KI 與到期觀察 KI 各一個
SCENARIO_KINDS = {
    "none": ["autocall", "best", "worse", "worst", "extreme"],
    "AM": ["autocall", "best", "worse", "worst", "extreme"],
    "D": ["autocall", "best", "best", "worse", "worst", "extreme"],
    "P": ["autocall", "best", "best", "worse", "worst", "extreme"],
}
KIND_ZH = {
    "autocall": "自動提前贖回情況",
    "best": "最佳情況",
    "worse": "較差情況",
    "worst": "最差情況",
    "extreme": "極端情況",
}


def monthly_rate(annual: Decimal, like: Decimal) -> Decimal:
    """月配息率 = 年利率 ÷ 12，四捨五入（half-up）到說明書顯示的位數（`like` 的小數位數；USD／GBP 4 位、JPY 6 位）。"""
    return kit.shown(annual / 12, like)


def _q(decimals: int) -> Decimal:
    return Decimal(1).scaleb(-decimals)


def titles(ctx: IssuerContext) -> list[CheckResult]:
    """情境種類依 KI 型態（無 KI 與到期觀察五個、每日／每期觀察六個）；假設的商品面額與投資本金由共用情境規則
    `scenario.params` 核對（Issue #294）。"""
    sec, f = ctx.ts.scenarios, ctx.ts.f
    rid = "doc.scenario_parameters"
    out = []
    ki = f("ki_type")
    kinds = [s.kind for s in sec.scenarios]
    check = Check(rid, "scenario_titles", Item.expected("第 18 項情境標題"), ctx.document)
    doc_ki = kit.doc_ki(ki)
    if doc_ki is None or doc_ki not in SCENARIO_KINDS:
        out.append(check.review(ki))
    else:
        expected = SCENARIO_KINDS[doc_ki]
        out.append(
            check.compare(
                "、".join(KIND_ZH[k] for k in expected),
                "、".join(KIND_ZH.get(k, k) for k in kinds),
                ok=kinds == expected,
                fail=S.REVIEW_REQUIRED,
                reason="scenario_unknown_wording",
                evidence=kit.evidence([s.lines[0] for s in sec.scenarios] or sec.lines[:1]),
                fail_message="情境的數量或種類與 KI 型態不符（無 KI 或到期觀察五個、每日／每期觀察六個，以標題判斷）",
            )
        )
    return out


# ---------------------------------------------------------------- 各情境算式（核對規則 §3.7）


@dataclass(frozen=True)
class _Case:
    """一個情境的驗算輸入：情境本身、結果的 rule_id 與欄位前綴、第一章的面額／天期／Non-Call、年利率出處。"""

    rid: str
    tag: str  # 「情境X」：結果欄位為「情境X_<項目>」
    s: Scenario
    denom: Decimal | int
    tenor: int
    k: int  # Non-Call（第一個可提前出場的期別）
    ov: OrderValue | None  # 參考條件表年利率（每期配息的預期值由它推得）

    def field(self, key: str) -> str:
        return f"{self.tag}_{key}"

    def unread(self, key: str, item: Item, why: str, *, ov: bool = False) -> CheckResult:
        """算式找不到或寫法未知：轉人工覆核，證據為情境標題行。"""
        return kit.calc_result(
            self.rid,
            self.field(key),
            item,
            [why],
            kit.evidence(self.s.lines[:1]),
            review=True,
            ov=[self.ov] if ov else (),
        )


def calculations(ctx: IssuerContext) -> list[CheckResult]:
    """各情境的算式（核對規則 §3.7）；每個情境一組結果，欄位名稱以「情境X_」開頭。"""
    f, rid = ctx.ts.f, "doc.scenario_calculations"
    denom, tenor, k = f("denomination"), f("tenor_months"), f("first_callable_period")
    annual, ov, problem = kit.annual_rate(ctx, rid, "scenario_calculations")
    if problem:
        return [problem]
    out = []
    for s in ctx.ts.scenarios.scenarios:
        if s.kind == "extreme":
            continue
        tag = f"情境{s.number}"
        items = {
            "coupon": Item.derived(f"{tag}每期配息", [ov]),
            "cumulative": Item.expected(f"{tag}累積總月配息金額"),
            "redemption": Item.expected(f"{tag}贖回金額"),
            "return": Item.expected(f"{tag}總報酬率"),
            "shares": Item.expected(f"{tag}股數"),
            "remainder": Item.expected(f"{tag}剩餘金額"),
            "assumed": Item.expected(f"{tag}假設的自動提前贖回評價日"),
        }
        bad = kit.first_bad((denom, tenor, k))
        if bad is not None:
            out.extend(
                Check(rid, f"{tag}_{key}", item, ctx.document, ov=(ov,)).review(bad) for key, item in items.items()
            )
            continue
        case = _Case(rid, tag, s, denom.value, tenor.value, k.value, ov)
        if s.kind == "unknown":
            out.append(case.unread("coupon", items["coupon"], f"情境標題「{s.title}」不是已知的種類", ov=True))
            continue
        if s.kind == "autocall":
            out.append(_assumed(case, items["assumed"]))
        coupon, amount = _coupon(case, items["coupon"], annual)
        out.append(coupon)
        out.append(_cumulative(case, items["cumulative"], amount))
        if s.kind in ("autocall", "best"):
            out.append(_redemption(case, items["redemption"]))
            out.append(_profit_return(case, items["return"]))
        else:
            out.extend(_worse(case, items))
    return out


def _assumed(c: _Case, item: Item) -> CheckResult:
    """情境一假設句寫「第1 個」自動提前贖回評價日；Non-Call = 天期時寫「第1(j=N)個」（核對規則 §3.7 期別）。"""
    if c.s.assumed is None:
        return c.unread("assumed", item, "找不到「假設於第…個自動提前贖回評價日」")
    a = c.s.assumed.value
    expected = f"第1(j={c.tenor})個" if c.k == c.tenor else "第1個"
    actual = f"第{a.number}(j={a.j})個" if a.j is not None else f"第{a.number}個"
    issues = (
        []
        if actual == expected
        else [
            "情境一假設第 1 個自動提前贖回評價日"
            + ("，Non-Call = 天期時加註 (j=天期)" if c.k == c.tenor else "，不加 j 註記")
        ]
    )
    return kit.calc_result(
        c.rid, c.field("assumed"), item, issues, kit.evidence(c.s.assumed.lines), expected=expected, actual=actual
    )


def _coupon(c: _Case, item: Item, annual: Decimal) -> tuple[CheckResult, Decimal | None]:
    """每期配息算式：期別（情境一 1～Non-Call，其餘 1～天期）、面額、月配息率、配息 = 面額 × 月配息率（half-up 到寫的位數）。

    回傳（結果, 驗算通過時的每期配息）；累積配息用這個值，沒通過時改用文件寫的配息。
    """
    s = c.s
    if s.coupon is None or s.coupon_count != 1:
        why = "找不到每期配息算式" if s.coupon_count == 0 else "每期配息算式的寫法無法辨識或出現多次"
        return c.unread("coupon", item, why, ov=True), None
    calc = s.coupon.value
    last = calc.first if calc.last is None else calc.last
    expected_rate = monthly_rate(annual, calc.rate)
    expected = (Decimal(c.denom) * expected_rate / 100).quantize(_q(calc.decimals), ROUND_HALF_UP)
    want = (1, c.k) if s.kind == "autocall" else (1, c.tenor)
    issues = []
    if (calc.first, last) != want:
        shown = f"第{want[0]}個月" if want[0] == want[1] else f"第{want[0]}~{want[1]}個月"
        issues.append(
            f"配息期別寫「第{calc.first}~{last}個月」，應為「{shown}」"
            + ("（1～Non-Call）" if s.kind == "autocall" else "（1～天期）")
        )
    if calc.denomination != c.denom:
        issues.append(f"算式中的面額 {calc.denomination} 不等於第一章面額 {c.denom}")
    if calc.rate != expected_rate:
        issues.append(f"月配息率 {calc.rate}% 應為 {expected_rate}%（年利率 ÷ 12）")
    if calc.amount != expected:
        issues.append(
            f"每期配息 {calc.amount} 應為 {expected}（面額 × 月配息率 {expected_rate}%，四捨五入到 {calc.decimals} 位）"
        )
    result = kit.calc_result(
        c.rid,
        c.field("coupon"),
        item,
        issues,
        kit.evidence(s.coupon.lines),
        expected=expected,
        actual=calc.amount,
        ov=[c.ov],
    )
    return result, (expected if not issues else None)


def _cumulative(c: _Case, item: Item, amount: Decimal | None) -> CheckResult:
    s = c.s
    if s.cumulative is None or s.coupon is None:
        return c.unread("cumulative", item, "找不到累積總月配息金額或每期配息算式")
    calc = s.coupon.value
    periods = (calc.last if calc.last is not None else calc.first) - calc.first + 1
    per = amount if amount is not None else calc.amount
    expected = per * periods
    actual = s.cumulative.value.amount
    issues = kit.cumulative_coupon_issues(
        "累積總月配息金額", actual, per, periods, calc.denomination, calc.rate, _q(calc.decimals)
    )
    return kit.calc_result(
        c.rid, c.field("cumulative"), item, issues, kit.evidence(s.cumulative.lines), expected=expected, actual=actual
    )


def _redemption(c: _Case, item: Item) -> CheckResult:
    s = c.s
    if s.redemption is None:
        return c.unread("redemption", item, "找不到贖回金額算式（面額 × 100% = 面額）")
    r = s.redemption.value
    issues = []
    if r.denomination != c.denom or r.amount != c.denom:
        issues.append(f"{r.label}算式的面額 {r.denomination} 與結果 {r.amount} 都應等於第一章面額 {c.denom}")
    return kit.calc_result(
        c.rid, c.field("redemption"), item, issues, kit.evidence(s.redemption.lines), expected=c.denom, actual=r.amount
    )


def _profit_return(c: _Case, item: Item) -> CheckResult:
    """獲利情境總報酬率 = [(面額 + 累積) ÷ 面額] − 1，依說明書顯示位數四捨五入；年化報酬率另由 derive.annualized_return 核對。"""
    s = c.s
    if s.total_return is None:
        return c.unread("return", item, "找不到總報酬率算式")
    tr = s.total_return.value
    issues = []
    if tr.denomination != c.denom or tr.divisor != c.denom:
        issues.append(f"總報酬率算式中的面額不等於第一章面額 {c.denom}")
    if s.cumulative is not None and tr.cumulative != s.cumulative.value.amount:
        issues.append(f"總報酬率算式中的累積配息 {tr.cumulative} 與累積總月配息金額 {s.cumulative.value.amount} 不同")
    expected = kit.shown((tr.denomination + tr.cumulative) / tr.divisor * 100 - 100, tr.pct) if tr.divisor else None
    if expected != tr.pct:
        issues.append(
            f"總報酬率 {tr.pct}% 應為 [({tr.denomination} + {tr.cumulative}) ÷ {tr.divisor}] − 1 = {expected}%"
        )
    return kit.calc_result(
        c.rid, c.field("return"), item, issues, kit.evidence(s.total_return.lines), expected=expected, actual=tr.pct
    )


def _worse(c: _Case, items: dict[str, Item]) -> list[CheckResult]:
    """較差／最差情境：股數 = 面額（× 匯率）÷ 執行價（4 位）、整數股數為無條件捨去；剩餘金額 = 收盤價（÷ 匯率）× 畸零股數；
    總報酬率 = [(整數股數 × 收盤價（÷ 匯率）+ 剩餘金額 + 累積配息) ÷ 面額] − 1。收盤價與匯率是文件的試算值。

    後一項用到前一項的值（股數 → 剩餘金額 → 總報酬率），前一項找不到算式時後面不再驗算。
    """
    s = c.s
    if s.shares is None:
        return [c.unread("shares", items["shares"], "找不到股數算式")]
    sh = s.shares.value
    issues = []
    if sh.denomination != c.denom:
        issues.append(f"股數算式的面額 {sh.denomination} 不等於第一章面額 {c.denom}")
    numerator = sh.denomination * sh.fx if sh.fx is not None else sh.denomination
    expected_shares = kit.shown(numerator / sh.strike, sh.shares) if sh.strike else None
    if expected_shares != sh.shares:
        issues.append(f"股數 {sh.shares} 應為 {numerator} ÷ {sh.strike} = {expected_shares}（四捨五入到 4 位）")
    expected_whole = int(sh.shares.quantize(Decimal(1), ROUND_DOWN))
    if sh.whole != expected_whole:
        issues.append(f"整數股數 {sh.whole} 應為 {sh.shares} 無條件捨去 = {expected_whole}")
    out = [
        kit.calc_result(
            c.rid,
            c.field("shares"),
            items["shares"],
            issues,
            kit.evidence(s.shares.lines),
            expected=expected_shares,
            actual=sh.shares,
        )
    ]

    if s.remainder is None:
        return [*out, c.unread("remainder", items["remainder"], "找不到剩餘金額算式")]
    rm = s.remainder.value
    issues = []
    expected_frac = sh.shares - sh.whole
    if rm.fraction != expected_frac:
        issues.append(f"畸零股數 {rm.fraction} 應為 {sh.shares} − {sh.whole} = {expected_frac}")
    if (rm.fx is None) != (sh.fx is None) or (sh.fx is not None and rm.fx != sh.fx):
        issues.append("剩餘金額算式的匯率與股數算式不一致")
    unit = rm.close / rm.fx if rm.fx else rm.close
    expected_amount = (unit * rm.fraction).quantize(_q(rm.decimals), ROUND_HALF_UP)
    if rm.amount != expected_amount:
        divided = f" ÷ {rm.fx}" if rm.fx else ""
        issues.append(
            f"剩餘金額 {rm.amount} 應為 {rm.close}{divided} × {rm.fraction} = {expected_amount}（四捨五入到 {rm.decimals} 位）"
        )
    out.append(
        kit.calc_result(
            c.rid,
            c.field("remainder"),
            items["remainder"],
            issues,
            kit.evidence(s.remainder.lines),
            expected=expected_amount,
            actual=rm.amount,
        )
    )

    if s.worse_return is None:
        return [*out, c.unread("return", items["return"], "找不到總報酬率算式")]
    wr = s.worse_return.value
    issues = []
    if wr.whole != sh.whole:
        issues.append(f"總報酬率算式的股數 {wr.whole} 與股數算式的整數股數 {sh.whole} 不同")
    if wr.close != rm.close:
        issues.append(f"總報酬率算式的收盤價格 {wr.close} 與剩餘金額算式的 {rm.close} 不同")
    if wr.divided_by_fx != (sh.fx is not None):
        issues.append("總報酬率算式有無「÷ 匯率」與股數算式是否用匯率不一致")
    if wr.cash != rm.amount:
        issues.append(f"總報酬率算式的現金交割 {wr.cash} 與剩餘金額 {rm.amount} 不同")
    if s.cumulative is not None and wr.cumulative != s.cumulative.value.amount:
        issues.append(f"總報酬率算式的累積配息 {wr.cumulative} 與累積總月配息金額 {s.cumulative.value.amount} 不同")
    if wr.total != c.denom:
        issues.append(f"總報酬率算式的總投資金額 {wr.total} 不等於第一章面額 {c.denom}")
    close = wr.close / sh.fx if (wr.divided_by_fx and sh.fx) else wr.close
    value = Decimal(wr.whole) * close + wr.cash + wr.cumulative
    expected = kit.shown(value / wr.total * 100 - 100, wr.pct) if wr.total else None
    if expected != wr.pct:
        fx_text = " ÷ 匯率" if wr.divided_by_fx else ""
        issues.append(
            f"總報酬率 {wr.pct}% 應為 [({wr.whole} × {wr.close}{fx_text} + {wr.cash} + {wr.cumulative}) ÷ {wr.total}] − 1"
            f" = {expected}%"
        )
    out.append(
        kit.calc_result(
            c.rid,
            c.field("return"),
            items["return"],
            issues,
            kit.evidence(s.worse_return.lines),
            expected=expected,
            actual=wr.pct,
        )
    )
    return out


def run(ctx: IssuerContext) -> list[CheckResult]:
    return [*titles(ctx), *calculations(ctx)]
