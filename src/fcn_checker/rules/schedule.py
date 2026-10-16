"""日期表的結構檢查：各上手共用，只讀標準欄位 `schedule_table`（Issue #289；docs/rules/shared-check-rules.md §6.1）。

六項檢查各自一個結果，項目名稱前加上上手交出的表位置（例：第一章第 13 條配息表期數）：

- `schedule.period_count`：期數 = 天期，期別依序為第 1～n 期。
- `schedule.payment_order`：配息日逐期遞增。
- `schedule.observation_order`：觀察日逐期遞增，且同期觀察日早於配息日。
- `schedule.last_observation`：末期觀察日 = 最終評價日。
- `schedule.last_payment`：末期配息日 = 到期日。
- `schedule.period_start`：期始日依上手宣告的定義（表上沒有期始日欄時不查）。

錯訊只寫哪一期、兩邊各是多少；各上手獨有的交叉驗證（例：BARC 提前出場表與配息表比對）留在各上手規則。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from ..schema import CheckResult, Evidence, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import DateRow, DateTable, PeriodStart
from .kit import TRADING_DAY_TOLERANCE, Check, IssuerContext, StartVerdict, period_start, read_standard, uncertain_start

FIELD = "schedule_table"
WHERE = "日期表"  # 讀不到日期表時（不知道表的位置）的項目名稱開頭

NAMES = {
    "schedule.period_count": ("period_count", "期數"),
    "schedule.payment_order": ("payment_order", "配息日順序"),
    "schedule.observation_order": ("observation_order", "觀察日與配息日"),
    "schedule.last_observation": ("last_observation", "末期觀察日"),
    "schedule.last_payment": ("last_payment", "末期配息日"),
    "schedule.period_start": ("period_start", "期始日"),
}

RULE_TEXT = {
    "previous_end": "期始日 = 前一期期末日",
    "next_trading_day": "期始日 = 前一期期末日後的下一個一籃子預定交易日",
}
FIRST_TEXT = {
    "issue_date": "第 1 期期始日 = 發行日",
    "after_issue": {
        "previous_end": "第 1 期期始日 = 發行日",
        "next_trading_day": "第 1 期期始日 = 發行日後的下一個一籃子預定交易日",
    },
}


def _check(ctx: IssuerContext, rid: str, where: str) -> Check:
    field, name = NAMES[rid]
    return Check(rid, field, Item.expected(f"{where}{name}"), ctx.document)


def _ev(rows: Sequence[DateRow]) -> list[Evidence]:
    return [e for r in rows for e in r.evidence]


def _periods(rows: Sequence[DateRow]) -> str | None:
    return "、".join(f"第 {r.period} 期" for r in rows) or None


def run(ctx: IssuerContext) -> list[CheckResult]:
    """六項日期表檢查；日期表讀不到時前五項轉人工覆核（期始日要看上手宣告，讀不到表就不知道有沒有期始日欄）。"""
    table = read_standard(ctx.ts, FIELD)
    if table.status == FieldStatus.NOT_APPLICABLE:
        return []
    if not table.ok:
        return [_check(ctx, rid, WHERE).review(table) for rid in list(NAMES)[:5]]
    t: DateTable = table.value
    return [
        period_count(ctx, table),
        payment_order(ctx, table),
        observation_order(ctx, table),
        last_date(ctx, table, "schedule.last_observation", "observation", "final_valuation_date", "最終評價日"),
        last_date(ctx, table, "schedule.last_payment", "payment", "maturity_date", "到期日"),
        *([] if t.start is None else [period_starts(ctx, table, t.start)]),
    ]


def period_count(ctx: IssuerContext, table: ParsedField) -> CheckResult:
    t: DateTable = table.value
    tenor = ctx.ts.f("tenor_months")
    check = _check(ctx, "schedule.period_count", t.where).needs(table, tenor)
    labels = [r.period for r in t.rows]
    in_order = labels == list(range(1, len(labels) + 1))
    return check.compare(
        tenor.value,
        len(t.rows),
        ok=tenor.ok and len(t.rows) == tenor.value and in_order,
        reason="period_count",
        message="期數須等於天期",
        fail_message="" if in_order else "；期別不是依序第 1～n 期：" + "、".join(map(str, labels)),
        evidence=list(tenor.evidence) + list(table.evidence),
    )


def _increasing(rows: Sequence[DateRow], key: str, what: str) -> tuple[list[DateRow], list[str]]:
    """有 `key` 日期的列逐期遞增：（沒有晚於前一期的列, 說明）。"""
    bad, notes, prev = [], [], None
    for r in rows:
        d = getattr(r, key)
        if d is None:
            continue
        if prev is not None and d <= getattr(prev, key):
            bad.append(r)
            notes.append(f"第 {r.period} 期{what} {d} 沒有晚於第 {prev.period} 期 {getattr(prev, key)}")
        prev = r
    return bad, notes


def payment_order(ctx: IssuerContext, table: ParsedField) -> CheckResult:
    t: DateTable = table.value
    missing = [r for r in t.rows if r.payment is None]
    bad, notes = _increasing(t.rows, "payment", "配息日")
    notes = [f"第 {r.period} 期沒有配息日" for r in missing] + notes
    flagged = missing + bad
    return (
        _check(ctx, "schedule.payment_order", t.where)
        .needs(table)
        .compare(
            None,
            _periods(flagged),
            ok=not flagged,
            reason="date_order",
            message="配息日須逐期遞增",
            fail_message="：" + "；".join(notes) if notes else "",
            evidence=_ev(flagged) or list(table.evidence),
        )
    )


def observation_order(ctx: IssuerContext, table: ParsedField) -> CheckResult:
    t: DateTable = table.value
    late = [r for r in t.rows if r.observation is not None and r.payment is not None and r.observation >= r.payment]
    notes = [f"第 {r.period} 期觀察日 {r.observation} 沒有早於配息日 {r.payment}" for r in late]
    bad, order_notes = _increasing(t.rows, "observation", "觀察日")
    flagged = late + [r for r in bad if r not in late]
    return (
        _check(ctx, "schedule.observation_order", t.where)
        .needs(table)
        .compare(
            None,
            _periods(sorted(flagged, key=lambda r: r.period)),
            ok=not flagged,
            reason="date_order",
            message="觀察日須逐期遞增，且同期觀察日早於配息日",
            fail_message="：" + "；".join(notes + order_notes) if flagged else "",
            evidence=_ev(flagged) or list(table.evidence),
        )
    )


def last_date(ctx: IssuerContext, table: ParsedField, rid: str, key: str, field: str, what: str) -> CheckResult:
    """末列的 `key` 日期 = 標準欄位 `field`（最終評價日／到期日）。"""
    t: DateTable = table.value
    pf = ctx.ts.f(field)
    last = t.rows[-1]
    return (
        _check(ctx, rid, t.where)
        .needs(table, pf)
        .compare(
            pf.value,
            getattr(last, key),
            message=f"須等於{what}",
            evidence=list(pf.evidence) + (list(last.evidence) or list(table.evidence)),
        )
    )


def _verdict(ctx: IssuerContext, rule: str, prev: dt.date, start: dt.date) -> StartVerdict:
    if rule == "next_trading_day":
        return period_start(ctx, prev, start)
    return StartVerdict(S.PASS) if start == prev else StartVerdict(S.MISMATCH, f"應為 {prev}")


def period_starts(ctx: IssuerContext, table: ParsedField, decl: PeriodStart) -> CheckResult:
    """期始日依上手宣告的定義：開頭 `blank` 期空白（不可提前出場的期別），之後每期都有期始日、不晚於同期期末日，
    並等於前一期期末日（`previous_end`）或其後的下一個一籃子預定交易日（`next_trading_day`，交易所假日表，`kit.period_start`）。
    第 1 期有期始日時依 `first` 和發行日比；前一期沒有期末日（不可提前出場）時這一期不和它比。"""
    t: DateTable = table.value
    check = _check(ctx, "schedule.period_start", t.where).needs(table)
    parts = []
    if decl.blank:
        parts.append(f"第 1～{decl.blank} 期期始日空白")
    parts.append(RULE_TEXT[decl.rule])
    if decl.first is not None and decl.blank == 0:
        text = FIRST_TEXT[decl.first]
        parts.append(text if isinstance(text, str) else text[decl.rule])
    message = "；".join(parts)
    tolerance = TRADING_DAY_TOLERANCE if decl.rule == "next_trading_day" else None
    if decl.blank is None:
        return check.result(
            S.REVIEW_REQUIRED,
            evidence=list(table.evidence),
            reason="schedule_blank_unknown",
            message="第一個可提前出場期讀不到，無法確認開頭哪幾期的期始日應為空白",
        )
    issue = ctx.ts.f("issue_date")
    flagged: list[DateRow] = []
    notes: list[str] = []
    sure = False  # 有確定不一致的期別（其他都只是無法確認是否因交易所假日順延）
    for i, r in enumerate(t.rows):
        verdict = StartVerdict(S.PASS)
        if i < decl.blank:
            if r.start is not None:
                verdict = StartVerdict(S.MISMATCH, "期始日應空白")
        elif r.start is None:
            verdict = StartVerdict(S.MISMATCH, "沒有期始日")
        elif r.end is not None and r.start > r.end:
            verdict = StartVerdict(S.MISMATCH, f"期始日 {r.start} 晚於期末日 {r.end}")
        elif i == 0:
            if decl.first is None:
                continue
            if not issue.ok:
                return check.review(issue)
            v = _verdict(ctx, "previous_end" if decl.first == "issue_date" else decl.rule, issue.value, r.start)
            verdict = StartVerdict(v.status, f"期始日{v.note}")
        elif (prev := t.rows[i - 1].end) is not None:  # 前一期沒有期末日（不可提前出場）時不和它比
            v = _verdict(ctx, decl.rule, prev, r.start)
            verdict = StartVerdict(v.status, f"期始日{v.note}")
        if verdict.status != S.PASS:
            flagged.append(r)
            notes.append(f"第 {r.period} 期{verdict.note}")
            sure |= verdict.status == S.MISMATCH
    if flagged and not sure:
        return uncertain_start(check, notes, actual=_periods(flagged), evidence=_ev(flagged))
    return check.compare(
        None,
        _periods(flagged),
        ok=not flagged,
        evidence=_ev(flagged) or list(table.evidence),
        tolerance=tolerance,
        message=message,
        fail_message="".join(f"；{n}" for n in notes),
    )
