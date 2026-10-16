"""SG 第一章 §15(3) 情境分析（核對規則 §3.4 重印表、§3.7）：假設、重印標的表、較差情境執行價、各情境算式。

總配息 = 已進位的每期配息 × 期數（同 MS／BNP）。配息率與獲利情境平均年化報酬率由 rules/sg.py 依參考條件表核對；
每期配息的預期值也用表上年利率推得的配息率（固定 4 位）。較差／最差情境的數額（無條件進入 4 位）、應實際交付數、
現金交割（無條件進入 2 位）、總報酬率依文件寫的收盤價與匯率驗算（收盤價、匯率是試算值，本身不核對；年化不核對）。
情境標題、期別寫法未知或算式找不到時轉人工覆核。
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal

from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import kit
from .kit import Check, IssuerContext

# 有無自動提前出場 → 情境依序的種類（範本規格 §4.7）
SCENARIO_KINDS = {True: ["best", "middle", "worse", "worst"], False: ["best", "worse", "worst"]}
KIND_ZH = {"best": "較佳情況", "middle": "中間情況", "worse": "較差情況", "worst": "最差情況"}
Q2 = kit.Q2
Q4 = kit.Q4


def monthly_rate(annual: Decimal) -> Decimal:
    """配息率 = 年利率 ÷ 12，四捨五入（half-up）到 4 位（SG 固定 4 位，JPY 也是）。"""
    return (annual / 12).quantize(Q4, ROUND_HALF_UP)


def reprint(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項重印標的表的各價格逐格 = 第 8 項；表頭的百分比 = 第 8 項的 KO／KI／執行價百分比。"""
    f = ctx.ts.f
    pt, st = f("underlying_table"), ctx.ts.scenarios.table
    out = []
    check = Check("doc.scenario_table", "scenario_table", Item.expected("第 15 項重印標的表"), ctx.document).needs(
        pt, st
    )
    if (problem := check.blocked) is not None:
        out.append(problem)
    else:
        out.append(
            check.compare(
                pt.value.prices,
                st.value.prices,
                evidence=list(st.evidence),
                fail_message="第 15 項重印的標的表須與第 8 項逐格相同",
            )
        )
    pcts = {"ko": f("ko_pct"), "ki": f("ki_pct"), "strike": f("strike_pct")}
    check = Check(
        "doc.scenario_header_pct", "scenario_pcts", Item.expected("第 15 項重印標的表百分比"), ctx.document
    ).needs(st)
    if (problem := check.blocked) is not None:
        out.append(problem)
        return out
    expected = {}
    for col in st.value.columns:
        if col == "initial":
            continue
        pf = pcts[col]
        if not pf.ok:
            out.append(check.needs(pf).blocked)
            return out
        expected[col] = pf.value
    out.append(
        check.compare(
            expected, st.value.pcts, evidence=list(st.evidence), fail_message="重印表表頭的百分比須與第 8 項相同"
        )
    )
    return out


def parameters(ctx: IssuerContext) -> list[CheckResult]:
    """假設的總投資金額 = 單位數 × 面額；情境種類依有無自動提前出場（四個／三個）。"""
    sec, f = ctx.ts.scenarios, ctx.ts.f
    rid = "doc.scenario_parameters"
    denom, has = f("denomination"), f("has_autocall")
    out = []
    item = Item.expected("第 15 項假設總投資金額")
    if not denom.ok:
        out.append(Check(rid, "scenario_total", item, ctx.document).review(denom))
    elif sec.total is None:
        out.append(
            kit.calc_result(
                rid,
                "scenario_total",
                item,
                ["第 15 項假設找不到「總投資金額 = U單位面額 = N」或出現多次"],
                kit.evidence(sec.assumptions[:1]),
                review=True,
            )
        )
    else:
        units, amount = sec.total.value.units, sec.total.value.amount
        expected = Decimal(denom.value) * units
        issues = (
            [] if amount == expected else [f"總投資金額 {amount} 應為 {units} 單位 × 面額 {denom.value} = {expected}"]
        )
        out.append(
            kit.calc_result(
                rid, "scenario_total", item, issues, kit.evidence(sec.total.lines), expected=expected, actual=amount
            )
        )
    kinds = [s.kind for s in sec.scenarios]
    check = Check(rid, "scenario_titles", Item.expected("第 15 項情境標題"), ctx.document).needs(has)
    if (problem := check.blocked) is not None:
        out.append(problem)
        return out
    expected_kinds = SCENARIO_KINDS[bool(has.value)]
    out.append(
        check.compare(
            "、".join(KIND_ZH[k] for k in expected_kinds),
            "、".join(KIND_ZH.get(k, k) for k in kinds),
            ok=kinds == expected_kinds,
            fail=S.REVIEW_REQUIRED,
            reason="scenario_unknown_wording",
            evidence=kit.evidence([s.lines[0] for s in sec.scenarios] or sec.lines[:1]),
            fail_message="情境的數量或種類與有無自動提前出場不符（有：較佳、中間、較差、最差；無：較佳、較差、最差）",
        )
    )
    return out


def calculations(ctx: IssuerContext) -> list[CheckResult]:
    """各情境的算式（核對規則 §3.7）；每個情境一組結果，欄位名稱以「情境X_」開頭。"""
    f, rid = ctx.ts.f, "doc.scenario_calculations"
    sec = ctx.ts.scenarios
    denom, tenor, k, has, rows = (
        f(x) for x in ("denomination", "tenor_months", "first_callable_period", "has_autocall", "underlying_prices")
    )
    annual, ov, problem = kit.annual_rate(ctx, rid, "scenario_calculations")
    if problem:
        return [problem]
    out = []
    for s in sec.scenarios:
        tag = f"情境{s.number}"
        items = {
            "coupon": Item.derived(f"{tag}每期配息", [ov]),
            "cumulative": Item.expected(f"{tag}累積總配息金額"),
            "redemption": Item.expected(f"{tag}贖回金額"),
            "return": Item.expected(f"{tag}總報酬率"),
            "strike": Item.expected(f"{tag}執行價"),
            "shares": Item.expected(f"{tag}數額與應實際交付數"),
            "cash": Item.expected(f"{tag}現金交割"),
            "annualized": Item.expected(f"{tag}平均年化報酬率"),
            "assumed": Item.expected(f"{tag}假設的自動提前出場時點"),
        }
        bad = next((p for p in (denom, tenor, k, has) if not p.ok), None)
        if bad is not None:
            out.extend(
                Check(rid, f"{tag}_{key}", item, ctx.document, ov=(ov,)).review(bad) for key, item in items.items()
            )
            continue
        if s.kind == "unknown":
            out.append(
                kit.calc_result(
                    rid,
                    f"{tag}_coupon",
                    items["coupon"],
                    [f"情境標題「{s.title}」不是已知的種類"],
                    kit.evidence(s.lines[:1]),
                    review=True,
                    ov=[ov],
                )
            )
            continue
        autocall = s.kind == "best" and bool(has.value)
        units = sec.total.value.units if sec.total else None
        if autocall:
            _assumed(out, rid, tag, items["assumed"], s, k.value, f("ko_wording"))
        amount = _coupon(
            out, rid, tag, items["coupon"], s, denom.value, units, tenor.value, k.value, autocall, annual, ov
        )
        _cumulative(out, rid, tag, items["cumulative"], s, amount)
        if s.kind in ("best", "middle"):
            _redemption(out, rid, tag, items["redemption"], s, denom.value, units)
            _profit_return(out, rid, tag, items["return"], s)
        else:
            pct = kit.field_value(ctx.ts, "strike_pct")
            _worse(out, rid, tag, items, s, denom.value, units, rows, tenor.value, pct)
    return out


def _assumed(out, rid, tag, item, s, k, wording) -> None:
    """情境一假設句：D 型寫「於…觀察期間起始日」；P 型寫「於第 k 個評價日」（k = Non-Call）。"""
    field = f"{tag}_assumed"
    if s.assumed is None:
        out.append(
            kit.calc_result(
                rid, field, item, ["找不到「假設於…發生自動提前出場事件」"], kit.evidence(s.lines[:1]), review=True
            )
        )
        return
    a = s.assumed.value
    wtype = wording.value["type"] if wording.ok else None
    expected = "觀察期間起始日" if wtype == "D" else f"第{k}個評價日"
    actual = "觀察期間起始日" if a.observation == "D" else f"第{a.period}個評價日"
    issues = [] if actual == expected else ["情境一假設的自動提前出場時點與第 8 項觀察方式或 Non-Call 不符"]
    out.append(
        kit.calc_result(rid, field, item, issues, kit.evidence(s.assumed.lines), expected=expected, actual=actual)
    )


def _coupon(out, rid, tag, item, s, denom, units, tenor, k, autocall, annual, ov) -> Decimal | None:
    """每期配息算式：期別（情境一有自動提前出場時 1～Non-Call，其餘 1～天期）、單位數、面額、配息率、總投資金額、
    配息 = 總投資金額 × 配息率（half-up 到 2 位）。"""
    field = f"{tag}_coupon"
    if s.coupon is None or s.coupon_count != 1:
        why = "找不到每期配息算式" if s.coupon_count == 0 else "每期配息算式的寫法無法辨識或出現多次"
        out.append(kit.calc_result(rid, field, item, [why], kit.evidence(s.lines[:1]), review=True, ov=[ov]))
        return None
    c = s.coupon.value
    a, u, d, rate, total, rate2, amount = c.first, c.units, c.denomination, c.rate, c.total, c.rate_again, c.amount
    b = a if c.last is None else c.last
    expected_rate = monthly_rate(annual)
    want = (1, k) if autocall else (1, tenor)
    issues = []
    if (a, b) != want:
        shown = f"第{want[0]}個" if want[0] == want[1] else f"第{want[0]}~{want[1]}個"
        issues.append(
            f"配息期別寫「第{a}~{b}個」，應為「{shown}」" + ("（1～Non-Call）" if autocall else "（1～天期）")
        )
    if units is not None and u != units:
        issues.append(f"算式中的單位數 {u} 不等於假設的 {units} 單位")
    if d != denom:
        issues.append(f"算式中的面額 {d} 不等於第一章面額 {denom}")
    if total != d * u:
        issues.append(f"總投資金額 {total} 應為 {u} × {d}")
    if rate != expected_rate or rate2 != expected_rate:
        issues.append(f"配息率 {rate}% 應為 {expected_rate}%（年利率 ÷ 12）")
    expected = (total * expected_rate / 100).quantize(Q2, ROUND_HALF_UP)
    if amount != expected:
        issues.append(f"每期配息 {amount} 應為 {expected}（總投資金額 × 配息率 {expected_rate}%，四捨五入到 2 位）")
    out.append(
        kit.calc_result(
            rid, field, item, issues, kit.evidence(s.coupon.lines), expected=expected, actual=amount, ov=[ov]
        )
    )
    return expected if not issues else None


def _cumulative(out, rid, tag, item, s, amount) -> None:
    field = f"{tag}_cumulative"
    if s.cumulative is None or s.coupon is None:
        out.append(
            kit.calc_result(
                rid, field, item, ["找不到累積總配息金額或每期配息算式"], kit.evidence(s.lines[:1]), review=True
            )
        )
        return
    c = s.coupon.value
    periods_ = (c.last if c.last is not None else c.first) - c.first + 1
    per = amount if amount is not None else c.amount
    expected = per * periods_
    actual = s.cumulative.value.amount
    issues = kit.cumulative_coupon_issues("累積總配息金額", actual, per, periods_, c.total, c.rate, Q2)
    out.append(
        kit.calc_result(rid, field, item, issues, kit.evidence(s.cumulative.lines), expected=expected, actual=actual)
    )


def _redemption(out, rid, tag, item, s, denom, units) -> None:
    field = f"{tag}_redemption"
    if s.redemption is None:
        out.append(
            kit.calc_result(
                rid,
                field,
                item,
                ["找不到贖回金額算式（單位數 × 面額 × 100% = 總投資金額）"],
                kit.evidence(s.lines[:1]),
                review=True,
            )
        )
        return
    r = s.redemption.value
    label, u, d, total, amount = r.label, r.units, r.denomination, r.total, r.amount
    expected = Decimal(denom) * (units if units is not None else u)
    issues = []
    if d != denom or (units is not None and u != units):
        issues.append(f"{label}算式的單位數 {u} 與面額 {d} 應為假設的單位數與第一章面額 {denom}")
    if total != expected or amount != expected:
        issues.append(f"{label}算式的總投資金額 {total} 與結果 {amount} 都應等於 {expected}")
    out.append(
        kit.calc_result(rid, field, item, issues, kit.evidence(s.redemption.lines), expected=expected, actual=amount)
    )


def _profit_return(out, rid, tag, item, s) -> None:
    """獲利情境總報酬率 = (贖回金額 + 累積) ÷ 總投資金額 − 1，依說明書顯示位數四捨五入；平均年化另由 derive.annualized_return 核對。"""
    field = f"{tag}_return"
    if s.total_return is None:
        out.append(kit.calc_result(rid, field, item, ["找不到總報酬率算式"], kit.evidence(s.lines[:1]), review=True))
        return
    tr = s.total_return.value
    total_sum, total, r = tr.total_sum, tr.total, tr.pct
    issues = []
    if s.cumulative is not None and s.redemption is not None:
        want = s.redemption.value.amount + s.cumulative.value.amount
        if total_sum != want:
            issues.append(f"總報酬率算式的合計 {total_sum} 應為贖回金額 + 累積總配息金額 = {want}")
    if s.redemption is not None and total != s.redemption.value.total:
        issues.append(f"總報酬率算式的總投資金額 {total} 與贖回金額算式的 {s.redemption.value.total} 不同")
    expected = kit.shown(total_sum / total * 100 - 100, r) if total else None
    if expected != r:
        issues.append(f"總報酬率 {r}% 應為 ({total_sum} ÷ {total}) − 1 = {expected}%")
    out.append(
        kit.calc_result(rid, field, item, issues, kit.evidence(s.total_return.lines), expected=expected, actual=r)
    )


def _worse(out, rid, tag, items, s, denom, units, rows, tenor, strike_pct) -> None:
    """較差／最差情境：執行價 = 第 8 項該檔執行價；數額 = 面額 ÷ 執行價（÷ 匯率換算）無條件進入 4 位；應實際交付數 = 向下取整；
    現金交割 = 收盤價 × 畸零數（× 匯率）無條件進入 2 位；總單位 = 單位數 × 應實際交付數；總報酬率 = [總投資 × 收盤價 ÷ 執行價 + 累積] ÷ 總投資 − 1；
    平均年化報酬率 = 未四捨五入的總報酬率 × 12 ÷ 天期（依文件顯示位數四捨五入）。"""
    if s.close is None:
        out.append(
            kit.calc_result(
                rid,
                f"{tag}_strike",
                items["strike"],
                ["找不到「連結標的(k=n)收盤價=…小於下限價／執行價」假設句"],
                kit.evidence(s.lines[:1]),
                review=True,
            )
        )
        return
    kidx, close = s.close.value.k, s.close.value.close
    strike_expected = initial = None
    if rows.ok:
        prices = list(rows.value)
        idx = (kidx or 1) - 1
        if 0 <= idx < len(prices):
            strike_expected = prices[idx].prices.get("strike")
            initial = prices[idx].prices.get("initial")
    if s.shares is None:
        for key in ("strike", "shares"):
            out.append(
                kit.calc_result(
                    rid, f"{tag}_{key}", items[key], ["找不到數額算式"], kit.evidence(s.lines[:1]), review=True
                )
            )
        return
    q = s.shares.value
    d, strike, fx, quantity = q.denomination, q.strike, q.fx, q.quantity
    issues = []
    if strike_expected is None:
        out.append(
            kit.calc_result(
                rid,
                f"{tag}_strike",
                items["strike"],
                ["對不到第 8 項標的表的那一檔（k）"],
                kit.evidence(s.shares.lines),
                review=True,
            )
        )
    else:
        if not kit.scenario_price_ok(strike, strike_expected, initial, strike_pct):  # 兩種進位都接受（#259）
            issues.append(f"執行價 {strike} 不是第 8 項第 {kidx or 1} 檔的執行價 {strike_expected}")
        out.append(
            kit.calc_result(
                rid,
                f"{tag}_strike",
                items["strike"],
                issues,
                kit.evidence(s.shares.lines),
                expected=strike_expected,
                actual=strike,
            )
        )
    issues = []
    if d != denom:
        issues.append(f"數額算式的面額 {d} 不等於第一章面額 {denom}")
    if (fx is None) != (s.fx is None) or (fx is not None and s.fx is not None and fx != s.fx.value):
        issues.append("數額算式的匯率與「最終匯率定價」不一致")
    divisor = strike * fx if fx is not None else strike
    expected_q = (Decimal(d) / divisor).quantize(Q4, ROUND_CEILING) if divisor else None
    if expected_q != quantity:
        issues.append(f"數額 {quantity} 應為 {d} ÷ {divisor} = {expected_q}（無條件進入至 4 位）")
    expected_whole = int(quantity.quantize(Decimal(1), ROUND_DOWN))
    if s.whole is None:
        issues.append("找不到應實際交付數")
    elif s.whole.value != expected_whole:
        issues.append(f"應實際交付數 {s.whole.value} 應為 {quantity} 向下取整 = {expected_whole}")
    out.append(
        kit.calc_result(
            rid,
            f"{tag}_shares",
            items["shares"],
            issues,
            kit.evidence(s.shares.lines) + (kit.evidence(s.whole.lines) if s.whole else []),
            expected=expected_q,
            actual=quantity,
        )
    )

    field = f"{tag}_cash"
    if s.cash is None:
        out.append(
            kit.calc_result(rid, field, items["cash"], ["找不到現金交割算式"], kit.evidence(s.lines[:1]), review=True)
        )
        return
    cs = s.cash.value
    close2, frac, fx2, amount = cs.close, cs.fraction, cs.fx, cs.amount
    issues = []
    if close2 != close:
        issues.append(f"現金交割算式的收盤價 {close2} 與假設句的 {close} 不同")
    expected_frac = quantity - expected_whole
    if frac != expected_frac:
        issues.append(f"畸零數 {frac} 應為 {quantity} − {expected_whole} = {expected_frac}")
    if (fx2 is None) != (fx is None) or (fx2 is not None and fx2 != fx):
        issues.append("現金交割算式的匯率與數額算式不一致")
    per = close * frac * (fx2 if fx2 is not None else 1)
    expected_cash = per.quantize(Q2, ROUND_CEILING)
    if amount != expected_cash:
        issues.append(
            f"現金交割 {amount} 應為 {close} × {frac}{f' × {fx2}' if fx2 else ''} = {expected_cash}（無條件進入至 2 位）"
        )
    if s.holdings is not None:
        h = s.holdings.value
        u, total_units, total_cash = h.units, h.total_units, h.total_cash
        if units is not None and u != units:
            issues.append(f"「總投資金額 = {u}單位面額」與假設的 {units} 單位不同")
        if total_units != u * expected_whole:
            issues.append(f"到期日將獲得 {total_units} 單位，應為 {u} × {expected_whole} = {u * expected_whole}")
        if total_cash != (amount * u):
            issues.append(f"到期日將獲得現金 {total_cash}，應為 {amount} × {u} = {amount * u}")
    else:
        issues.append("找不到「總投資金額 = U單位面額，因此，到期日將獲得…」")
    out.append(
        kit.calc_result(
            rid,
            field,
            items["cash"],
            issues,
            kit.evidence(s.cash.lines) + (kit.evidence(s.holdings.lines) if s.holdings else []),
            expected=expected_cash,
            actual=amount,
        )
    )

    field = f"{tag}_return"
    if s.worse_return is None:
        out.append(
            kit.calc_result(
                rid, field, items["return"], ["找不到現金結算總報酬率算式"], kit.evidence(s.lines[:1]), review=True
            )
        )
        return
    wr = s.worse_return.value
    total, close3, strike3, cum, total2 = wr.total, wr.close, wr.strike, wr.cumulative, wr.divisor
    r, annualized = wr.pct, wr.annualized
    issues = []
    if close3 != close:
        issues.append(f"總報酬率算式的收盤價 {close3} 與假設句的 {close} 不同")
    if strike3 != strike:
        issues.append(f"總報酬率算式的執行價 {strike3} 與數額算式的 {strike} 不同")
    if s.cumulative is not None and cum != s.cumulative.value.amount:
        issues.append(f"總報酬率算式的累積配息 {cum} 與累積總配息金額 {s.cumulative.value.amount} 不同")
    if total != total2 or (units is not None and total != Decimal(denom) * units):
        issues.append(f"總報酬率算式的總投資金額 {total}／{total2} 應為單位數 × 面額")
    exact = (total * close3 / strike3 + cum) / total2 * 100 - 100 if strike3 and total2 else None
    expected = kit.shown(exact, r) if exact is not None else None
    if expected != r:
        issues.append(f"總報酬率 {r}% 應為 [{total} × ({close3} ÷ {strike3}) + {cum}] ÷ {total2} − 1 = {expected}%")
    out.append(
        kit.calc_result(
            rid, field, items["return"], issues, kit.evidence(s.worse_return.lines), expected=expected, actual=r
        )
    )

    expected = kit.shown(exact * 12 / tenor, annualized) if exact is not None and tenor else None
    issues = []
    if expected != annualized:
        issues.append(f"平均年化報酬率 {annualized}% 應為總報酬率 × 12 ÷ {tenor} = {expected}%")
    out.append(
        kit.calc_result(
            rid,
            f"{tag}_annualized",
            items["annualized"],
            issues,
            kit.evidence(s.worse_return.lines),
            expected=expected,
            actual=annualized,
        )
    )


def run(ctx: IssuerContext) -> list[CheckResult]:
    return [*reprint(ctx), *parameters(ctx), *calculations(ctx)]
