"""MS 第一章第 18 項情境分析（核對規則 §3.5 價格表重印、§3.7）：假設、重印價格表、較差情境執行價、各情境金額。

MS 的總配息是「已進位的每期配息 × 期數」（文件算式 `A 美元×k`），與 HSBC 用未進位年利率推算不同。
月配息率與獲利情境年化報酬率由 rules/ms.py 依參考條件表核對；這裡只用說明書。
情境標題、期數寫法未知或算式找不到時轉人工覆核，不跳過後宣稱通過。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..schema import CheckResult, Evidence, Item, ParsedField
from ..schema import CheckStatus as S
from . import kit
from .kit import IssuerContext

Q2 = Decimal("0.01")  # 每期配息四捨五入到 2 位


def _ev(lines) -> list[Evidence]:
    return [Evidence.of(ln) for ln in lines]


def _result(rid, field, name, issues, evidence, *, review=False, expected=None, actual=None) -> CheckResult:
    """`issues` 為不成立的說明；`review` 表示寫法未知（轉人工覆核）而不是算錯。"""
    ok = not issues
    status = S.PASS if ok else (S.REVIEW_REQUIRED if review else S.MISMATCH)
    reason = "" if ok else ("scenario_unknown_wording" if review else "value_mismatch")
    return kit.result(
        rid,
        field,
        status,
        expected=expected,
        actual=actual,
        evidence=evidence,
        reason=reason,
        message="；".join(issues),
        item=Item.expected(name),
    )


def _rows(table: ParsedField) -> list[dict]:
    return [{"ticker": r["ticker"], "prices": r["prices"]} for r in table.value["rows"]]


def reprint(ctx: IssuerContext) -> list[CheckResult]:
    """第 18 項重印價格表的彭博代碼、各價格與表頭百分比逐格 = 第 16 項。"""
    pt, st = ctx.ts.f("price_table"), ctx.ts.scenarios.table
    out = []
    for rid, field, name, value in (
        ("doc.scenario_table", "scenario_table", "第 18 項重印價格表", _rows),
        ("doc.scenario_header_pct", "scenario_headers", "第 18 項重印價格表欄頭百分比", lambda t: t.value["headers"]),
    ):
        bad = next((p for p in (pt, st) if not p.ok), None)
        if bad is not None:
            out.append(kit.doc_review(rid, field, bad, item=Item.expected(name)))
            continue
        expected, actual = value(pt), value(st)
        ok = expected == actual
        out.append(
            kit.result(
                rid,
                field,
                S.PASS if ok else S.MISMATCH,
                expected=expected,
                actual=actual,
                evidence=list(st.evidence),
                reason="" if ok else "value_mismatch",
                message="" if ok else "第 18 項重印的價格表須與第 16 項逐格相同",
                item=Item.expected(name),
            )
        )
    return out


def parameters(ctx: IssuerContext) -> list[CheckResult]:
    """假設的總投資金額 = 面額、年期 = 天期；情境標題要有獲利情境與一個較差情境。"""
    sec, f = ctx.ts.scenarios, ctx.ts.f
    rid = "doc.scenario_parameters"
    out = []
    for field, name, hit, pf in (
        ("scenario_denomination", "第 18 項假設總投資金額", sec.denomination, f("denomination")),
        ("scenario_tenor", "第 18 項假設年期", sec.tenor, f("tenor_months")),
    ):
        item = Item.expected(name)
        if not pf.ok:
            out.append(kit.doc_review(rid, field, pf, item=item))
        elif hit is None:
            out.append(_result(rid, field, name, ["第 18 項假設找不到這個值或出現多次"], [], review=True))
        else:
            ok = hit.values[0] == pf.value
            out.append(
                _result(
                    rid,
                    field,
                    name,
                    [] if ok else ["與第一章不同"],
                    _ev(hit.lines),
                    expected=pf.value,
                    actual=hit.values[0],
                )
            )
    kinds = [s.kind for s in sec.scenarios]
    issues = (
        [] if kinds.count("worse") == 1 and "profit" in kinds else ["找不到獲利情境與一個較差情境（標題含「較差」）"]
    )
    out.append(
        _result(rid, "scenario_titles", "第 18 項情境標題", issues, _ev(sec.lines[:1]), review=True, actual=kinds)
    )
    return out


def worse_strike(ctx: IssuerContext) -> list[CheckResult]:
    """較差情境的「執行價 =N」= 第 16 項該標的的執行價。"""
    pt, rid, name = ctx.ts.f("price_table"), "doc.scenario_strike", "較差情境執行價"
    out = []
    for s in ctx.ts.scenarios.scenarios:
        if s.kind != "worse":
            continue
        if not pt.ok:
            out.append(kit.doc_review(rid, "scenario_strike", pt, item=Item.expected(name)))
            continue
        if len(s.strike) != 1:
            out.append(_result(rid, "scenario_strike", name, ["較差情境找不到執行價"], _ev(s.lines[:1]), review=True))
            continue
        ticker, strike = s.strike[0].values
        rows = [r for r in pt.value["rows"] if r["ticker"].replace(" ", "") == ticker]
        if len(rows) != 1:
            out.append(
                _result(rid, "scenario_strike", name, [f"較差情境的標的 {ticker} 不在價格表"], _ev(s.strike[0].lines))
            )
            continue
        expected = rows[0]["prices"]["strike"]
        issues = [] if strike == expected else ["與第 16 項價格表的執行價不同"]
        out.append(
            _result(rid, "scenario_strike", name, issues, _ev(s.strike[0].lines), expected=expected, actual=strike)
        )
    return out


def calculations(ctx: IssuerContext) -> list[CheckResult]:
    """各情境：每期配息 = 面額 × 月配息率（half-up 到 2 位）；獲利情境損益 = 每期配息 × 假設的配息次數；
    較差情境總配息 = 每期配息 × 天期，損益 = 總配息 + 文件寫的到期贖回價值 − 面額。"""
    f, rid = ctx.ts.f, "doc.scenario_calculations"
    denom, monthly, tenor = f("denomination"), f("monthly_coupon_pct"), f("tenor_months")
    out = []
    for s in ctx.ts.scenarios.scenarios:
        if s.kind == "default":
            continue
        tag = f"情境{s.number}"
        coupon_name, pnl_name = f"{tag}每期配息", f"{tag}損益"
        bad = next((p for p in (denom, monthly, tenor) if not p.ok), None)
        if bad is not None:
            out.extend(
                kit.doc_review(rid, field, bad, item=Item.expected(name))
                for field, name in ((f"{tag}_coupon", coupon_name), (f"{tag}_pnl", pnl_name))
            )
            continue
        a = (Decimal(denom.value) * monthly.value / 100).quantize(Q2, ROUND_HALF_UP)
        issues = []
        for h in s.coupons:
            d, rate, amount = h.values
            if d != denom.value:
                issues.append(f"算式中的面額 {d} 不等於第一章面額 {denom.value}")
            if amount != (d * rate / 100).quantize(Q2, ROUND_HALF_UP) or amount != a:
                issues.append(f"每期配息 {amount} 應為 {a}（面額 × 月配息率 {monthly.value}%，四捨五入到 2 位）")
        evidence = [e for h in s.coupons for e in _ev(h.lines)]
        if s.coupons:
            out.append(_result(rid, f"{tag}_coupon", coupon_name, issues, evidence, expected=a))
        else:
            out.append(
                _result(rid, f"{tag}_coupon", coupon_name, ["找不到每期配息算式"], _ev(s.lines[:1]), review=True)
            )
        out.append(_pnl(rid, s, tag, pnl_name, a, denom.value, tenor.value))
    return out


def _pnl(rid, s, tag, name, a, denom, tenor) -> CheckResult:
    field = f"{tag}_pnl"
    if len(s.pnl) != 1 or s.assumed is None:
        missing = "損益算式" if len(s.pnl) != 1 else "假設的配息次數（「假設在第 k 個…」）"
        return _result(rid, field, name, [f"找不到{missing}或出現多次"], _ev(s.lines[:1]), review=True)
    h, (assumed,) = s.pnl[0], s.assumed.values
    issues = []
    if s.kind == "profit":
        d1, amount, k, d2, pnl = h.values
        if assumed is None:
            return _result(rid, field, name, ["獲利情境的假設期數寫法未知"], _ev(s.lines[:1]), review=True)
        if d1 != denom or d2 != denom:
            issues.append(f"算式中的面額不等於第一章面額 {denom}")
        if amount != a:
            issues.append(f"每期配息 {amount} 應為 {a}")
        if k != assumed:
            issues.append(f"配息次數 {k} 應等於假設的第 {assumed} 期")
        if pnl != amount * k:
            issues.append(f"損益 {pnl} 應為 {amount} × {k} = {amount * k}")
        expected = a * assumed
    else:
        amount, k, value, d, pnl = h.values
        if assumed is not None:
            return _result(rid, field, name, ["較差情境的假設期數寫法未知"], _ev(s.lines[:1]), review=True)
        if amount != a:
            issues.append(f"每期配息 {amount} 應為 {a}")
        if k != tenor:
            issues.append(f"總配息的期數 {k} 應等於天期 {tenor}")
        if d != denom:
            issues.append(f"算式中的面額 {d} 不等於第一章面額 {denom}")
        if pnl != amount * k + value - d:
            issues.append(f"損益 {pnl} 應為 {amount} × {k} + {value} − {d} = {amount * k + value - d}")
        expected = a * tenor + value - denom
    return _result(rid, field, name, issues, _ev(h.lines), expected=expected, actual=h.values[-1])


def run(ctx: IssuerContext) -> list[CheckResult]:
    return [*reprint(ctx), *parameters(ctx), *worse_strike(ctx), *calculations(ctx)]
