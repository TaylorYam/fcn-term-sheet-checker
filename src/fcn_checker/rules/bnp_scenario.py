"""BNP 第一章第 18 項(3) 情境分析（核對規則 §3.4 重印表、§3.7）：假設、重印標的表、較差情境執行價、各情境算式。

總配息 = 已進位的每期配息 × 期數（同 MS）。月配息率與獲利情境年化報酬率由 rules/bnp.py 依參考條件表核對；
每期配息的預期值也用表上年利率推得的月配息率（依說明書顯示位數四捨五入）。較差／最差情境的股數、剩餘金額、總報酬率
依文件寫的收盤價與匯率驗算（收盤價、匯率是試算值，本身不核對）。情境標題、期別寫法未知或算式找不到時轉人工覆核。
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from ..schema import CheckResult, Evidence, Item
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


def annual_rate(ctx: IssuerContext, rid: str, field: str):
    """參考條件表年利率（BNP 宣告的 reference_fields）；（值, 儲存格, 有問題時的人工覆核結果）。"""
    return kit.order_value(ctx, "coupon_pa_pct", rid, field, None, kit.to_decimal, "數字", name="年利率 %")


def monthly_rate(annual: Decimal, like: Decimal) -> Decimal:
    """月配息率 = 年利率 ÷ 12，四捨五入（half-up）到說明書顯示的位數（`like` 的小數位數；USD／GBP 4 位、JPY 6 位）。"""
    return kit.shown(annual / 12, like)


def _q(decimals: int) -> Decimal:
    return Decimal(1).scaleb(-decimals)


def _ev(lines) -> list[Evidence]:
    return [Evidence.of(ln) for ln in lines]


def _result(rid, field, item, issues, evidence, *, review=False, expected=None, actual=None, ov=None) -> CheckResult:
    """`item` 為項目（或預期值來自說明書時的項目名稱）；`issues` 為不成立的說明；`review` 表示寫法未知（轉人工覆核）而不是算錯。"""
    item = Item.expected(item) if isinstance(item, str) else item
    return Check(rid, field, item, ov=tuple(ov or ())).compare(
        expected,
        actual,
        ok=not issues,
        fail=S.REVIEW_REQUIRED if review else S.MISMATCH,
        reason="scenario_unknown_wording" if review else "value_mismatch",
        message="；".join(issues),
        evidence=evidence,
    )


def reprint(ctx: IssuerContext) -> list[CheckResult]:
    """第 18 項重印標的表的各價格與百分比逐格 = 第 11 項。"""
    pt, st = ctx.ts.f("underlying_table"), ctx.ts.scenarios.table
    out = []
    for rid, field, name, value in (
        (
            "doc.scenario_table",
            "scenario_table",
            "第 18 項重印標的表",
            lambda t: [r["prices"] for r in t.value["rows"]],
        ),
        ("doc.scenario_header_pct", "scenario_pcts", "第 18 項重印標的表百分比", lambda t: t.value["pcts"]),
    ):
        check = Check(rid, field, Item.expected(name), ctx.document).needs(pt, st)
        if (problem := check.blocked) is not None:
            out.append(problem)
            continue
        out.append(
            check.compare(
                value(pt),
                value(st),
                evidence=list(st.evidence),
                fail_message="第 18 項重印的標的表須與第 11 項逐格相同",
            )
        )
    return out


def parameters(ctx: IssuerContext) -> list[CheckResult]:
    """假設的商品面額、投資本金 = 面額；情境種類依 KI 型態（無 KI 與到期觀察五個、每日／每期觀察六個）。"""
    sec, f = ctx.ts.scenarios, ctx.ts.f
    rid = "doc.scenario_parameters"
    denom = f("denomination")
    out = []
    for field, name, hit, index in (
        ("scenario_denomination", "第 18 項假設商品面額", sec.denomination, 0),
        ("scenario_principal", "第 18 項假設投資本金", sec.principal, 1),
    ):
        item = Item.expected(name)
        if not denom.ok:
            out.append(Check(rid, field, item, ctx.document).review(denom))
        elif hit is None:
            out.append(
                _result(rid, field, name, ["第 18 項假設找不到這個值或出現多次"], _ev(sec.assumptions[:1]), review=True)
            )
        else:
            value = hit.values[index]
            issues = [] if value == denom.value else ["與第一章第 6 項商品面額不同"]
            out.append(_result(rid, field, name, issues, _ev(hit.lines), expected=denom.value, actual=value))
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
                evidence=_ev([s.lines[0] for s in sec.scenarios] or sec.lines[:1]),
                fail_message="情境的數量或種類與 KI 型態不符（無 KI 或到期觀察五個、每日／每期觀察六個，以標題判斷）",
            )
        )
    return out


def worse_strike(ctx: IssuerContext) -> list[CheckResult]:
    """較差／最差情境假設句與股數算式的執行價格 = 第 11 項某一檔標的的執行價格（表現最差標的）。"""
    rows, rid, name = ctx.ts.f("underlying_prices"), "doc.scenario_strike", "較差情境執行價格"
    out = []
    for s in ctx.ts.scenarios.scenarios:
        if s.kind not in ("worse", "worst"):
            continue
        field = f"情境{s.number}_strike"
        if not rows.ok:
            out.append(Check(rid, field, Item.expected(name), ctx.document).review(rows))
            continue
        if s.strike is None:
            out.append(
                _result(rid, field, name, ["找不到「收盤價格…小於其執行價格…」假設句"], _ev(s.lines[:1]), review=True)
            )
            continue
        strike = s.strike.values[2]
        strikes = {r.prices["strike"]: r.ticker for r in rows.value}
        issues = []
        if strike not in strikes:
            issues.append(f"執行價格 {strike} 不是第 11 項任何一檔標的的執行價格")
        if s.shares is not None and s.shares.values[3] != strike:
            issues.append(f"股數算式的執行價格 {s.shares.values[3]} 與假設句的 {strike} 不同")
        out.append(
            _result(
                rid,
                field,
                name,
                issues,
                _ev(s.strike.lines) + (_ev(s.shares.lines) if s.shares else []),
                expected=sorted(strikes),
                actual=strike,
            )
        )
    return out


def calculations(ctx: IssuerContext) -> list[CheckResult]:
    """各情境的算式（核對規則 §3.7）；每個情境一組結果，欄位名稱以「情境X_」開頭。"""
    f, rid = ctx.ts.f, "doc.scenario_calculations"
    denom, tenor, k = f("denomination"), f("tenor_months"), f("first_callable_period")
    annual, ov, problem = annual_rate(ctx, rid, "scenario_calculations")
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
        }
        bad = next((p for p in (denom, tenor, k) if not p.ok), None)
        if bad is not None:
            out.extend(
                Check(rid, f"{tag}_{key}", item, ctx.document, ov=(ov,)).review(bad) for key, item in items.items()
            )
            continue
        if s.kind == "unknown":
            out.append(
                _result(
                    rid,
                    f"{tag}_coupon",
                    items["coupon"],
                    [f"情境標題「{s.title}」不是已知的種類"],
                    _ev(s.lines[:1]),
                    review=True,
                    ov=[ov],
                )
            )
            continue
        amount = _coupon(out, rid, tag, items["coupon"], s, denom.value, tenor.value, k.value, annual, ov)
        _cumulative(out, rid, tag, items["cumulative"], s, amount)
        if s.kind in ("autocall", "best"):
            _redemption(out, rid, tag, items["redemption"], s, denom.value)
            _profit_return(out, rid, tag, items["return"], s, denom.value)
        else:
            _worse(out, rid, tag, items, s, denom.value)
    return out


def _coupon(out, rid, tag, item, s, denom, tenor, k, annual, ov) -> Decimal | None:
    """每期配息算式：期別（情境一 1～Non-Call，其餘 1～天期）、面額、月配息率、配息 = 面額 × 月配息率（half-up 到寫的位數）。"""
    field = f"{tag}_coupon"
    if s.coupon is None or s.coupon_count != 1:
        why = "找不到每期配息算式" if s.coupon_count == 0 else "每期配息算式的寫法無法辨識或出現多次"
        out.append(_result(rid, field, item, [why], _ev(s.lines[:1]), review=True, ov=[ov]))
        return None
    a, b, d, _, rate, amount, _, decimals = s.coupon.values
    b = a if b is None else b
    expected_rate = monthly_rate(annual, rate)
    expected = (Decimal(denom) * expected_rate / 100).quantize(_q(decimals), ROUND_HALF_UP)
    want = (1, k) if s.kind == "autocall" else (1, tenor)
    issues = []
    if (a, b) != want:
        shown = f"第{want[0]}個月" if want[0] == want[1] else f"第{want[0]}~{want[1]}個月"
        issues.append(
            f"配息期別寫「第{a}~{b}個月」，應為「{shown}」"
            + ("（1～Non-Call）" if s.kind == "autocall" else "（1～天期）")
        )
    if d != denom:
        issues.append(f"算式中的面額 {d} 不等於第一章面額 {denom}")
    if rate != expected_rate:
        issues.append(f"月配息率 {rate}% 應為 {expected_rate}%（年利率 ÷ 12）")
    if amount != expected:
        issues.append(
            f"每期配息 {amount} 應為 {expected}（面額 × 月配息率 {expected_rate}%，四捨五入到 {decimals} 位）"
        )
    out.append(_result(rid, field, item, issues, _ev(s.coupon.lines), expected=expected, actual=amount, ov=[ov]))
    return expected if not issues else None


def _cumulative(out, rid, tag, item, s, amount) -> None:
    field = f"{tag}_cumulative"
    if s.cumulative is None or s.coupon is None:
        out.append(_result(rid, field, item, ["找不到累積總月配息金額或每期配息算式"], _ev(s.lines[:1]), review=True))
        return
    a, b, *_ = s.coupon.values
    periods_ = (b if b is not None else a) - a + 1
    per = amount if amount is not None else s.coupon.values[5]
    expected = per * periods_
    actual = s.cumulative.values[0]
    issues = (
        [] if actual == expected else [f"累積總月配息金額 {actual} 應為每期配息 {per} × {periods_} 期 = {expected}"]
    )
    out.append(_result(rid, field, item, issues, _ev(s.cumulative.lines), expected=expected, actual=actual))


def _redemption(out, rid, tag, item, s, denom) -> None:
    field = f"{tag}_redemption"
    if s.redemption is None:
        out.append(
            _result(rid, field, item, ["找不到贖回金額算式（面額 × 100% = 面額）"], _ev(s.lines[:1]), review=True)
        )
        return
    label, d, _, amount, _ = s.redemption.values
    issues = []
    if d != denom or amount != denom:
        issues.append(f"{label}算式的面額 {d} 與結果 {amount} 都應等於第一章面額 {denom}")
    out.append(_result(rid, field, item, issues, _ev(s.redemption.lines), expected=denom, actual=amount))


def _profit_return(out, rid, tag, item, s, denom) -> None:
    """獲利情境總報酬率 = [(面額 + 累積) ÷ 面額] − 1，依說明書顯示位數四捨五入；年化報酬率另由 derive.annualized_return 核對。"""
    field = f"{tag}_return"
    if s.total_return is None:
        out.append(_result(rid, field, item, ["找不到總報酬率算式"], _ev(s.lines[:1]), review=True))
        return
    d1, cum, d2, r, _ = s.total_return.values
    issues = []
    if d1 != denom or d2 != denom:
        issues.append(f"總報酬率算式中的面額不等於第一章面額 {denom}")
    if s.cumulative is not None and cum != s.cumulative.values[0]:
        issues.append(f"總報酬率算式中的累積配息 {cum} 與累積總月配息金額 {s.cumulative.values[0]} 不同")
    expected = kit.shown((d1 + cum) / d2 * 100 - 100, r) if d2 else None
    if expected != r:
        issues.append(f"總報酬率 {r}% 應為 [({d1} + {cum}) ÷ {d2}] − 1 = {expected}%")
    out.append(_result(rid, field, item, issues, _ev(s.total_return.lines), expected=expected, actual=r))


def _worse(out, rid, tag, items, s, denom) -> None:
    """較差／最差情境：股數 = 面額（× 匯率）÷ 執行價（4 位）、整數股數為無條件捨去；剩餘金額 = 收盤價（÷ 匯率）× 畸零股數；
    總報酬率 = [(整數股數 × 收盤價（÷ 匯率）+ 剩餘金額 + 累積配息) ÷ 面額] − 1。收盤價與匯率是文件的試算值。"""
    if s.shares is None:
        out.append(_result(rid, f"{tag}_shares", items["shares"], ["找不到股數算式"], _ev(s.lines[:1]), review=True))
        return
    d, _, fx, strike, _, shares, whole = s.shares.values
    issues = []
    if d != denom:
        issues.append(f"股數算式的面額 {d} 不等於第一章面額 {denom}")
    numerator = d * fx if fx is not None else d
    expected_shares = kit.shown(numerator / strike, shares) if strike else None
    if expected_shares != shares:
        issues.append(f"股數 {shares} 應為 {numerator} ÷ {strike} = {expected_shares}（四捨五入到 4 位）")
    expected_whole = int(shares.quantize(Decimal(1), ROUND_DOWN))
    if whole != expected_whole:
        issues.append(f"整數股數 {whole} 應為 {shares} 無條件捨去 = {expected_whole}")
    out.append(
        _result(
            rid, f"{tag}_shares", items["shares"], issues, _ev(s.shares.lines), expected=expected_shares, actual=shares
        )
    )

    field = f"{tag}_remainder"
    if s.remainder is None:
        out.append(_result(rid, field, items["remainder"], ["找不到剩餘金額算式"], _ev(s.lines[:1]), review=True))
        return
    close, _, fx2, frac, amount, _, decimals = s.remainder.values
    issues = []
    expected_frac = shares - whole
    if frac != expected_frac:
        issues.append(f"畸零股數 {frac} 應為 {shares} − {whole} = {expected_frac}")
    if (fx2 is None) != (fx is None) or (fx is not None and fx2 != fx):
        issues.append("剩餘金額算式的匯率與股數算式不一致")
    unit = close / fx2 if fx2 else close
    expected_amount = (unit * frac).quantize(_q(decimals), ROUND_HALF_UP)
    if amount != expected_amount:
        issues.append(
            f"剩餘金額 {amount} 應為 {close}{f' ÷ {fx2}' if fx2 else ''} × {frac} = {expected_amount}（四捨五入到 {decimals} 位）"
        )
    out.append(
        _result(rid, field, items["remainder"], issues, _ev(s.remainder.lines), expected=expected_amount, actual=amount)
    )

    field = f"{tag}_return"
    if s.worse_return is None:
        out.append(_result(rid, field, items["return"], ["找不到總報酬率算式"], _ev(s.lines[:1]), review=True))
        return
    n, close2, _, divided, cash, _, cum, _, d2, _, r = s.worse_return.values
    issues = []
    if n != whole:
        issues.append(f"總報酬率算式的股數 {n} 與股數算式的整數股數 {whole} 不同")
    if close2 != close:
        issues.append(f"總報酬率算式的收盤價格 {close2} 與剩餘金額算式的 {close} 不同")
    if divided != (fx is not None):
        issues.append("總報酬率算式有無「÷ 匯率」與股數算式是否用匯率不一致")
    if cash != amount:
        issues.append(f"總報酬率算式的現金交割 {cash} 與剩餘金額 {amount} 不同")
    if s.cumulative is not None and cum != s.cumulative.values[0]:
        issues.append(f"總報酬率算式的累積配息 {cum} 與累積總月配息金額 {s.cumulative.values[0]} 不同")
    if d2 != denom:
        issues.append(f"總報酬率算式的總投資金額 {d2} 不等於第一章面額 {denom}")
    value = Decimal(n) * (close2 / fx if (divided and fx) else close2) + cash + cum
    expected = kit.shown(value / d2 * 100 - 100, r) if d2 else None
    if expected != r:
        issues.append(
            f"總報酬率 {r}% 應為 [({n} × {close2}{' ÷ 匯率' if divided else ''} + {cash} + {cum}) ÷ {d2}] − 1 = {expected}%"
        )
    out.append(_result(rid, field, items["return"], issues, _ev(s.worse_return.lines), expected=expected, actual=r))


def run(ctx: IssuerContext) -> list[CheckResult]:
    return [*reprint(ctx), *parameters(ctx), *worse_strike(ctx), *calculations(ctx)]
