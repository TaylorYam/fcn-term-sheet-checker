"""回填欄位（ISIN Code、發行日、比價日_1～12）的整段流程，所有上手共用（ADR 0004）。

- 每格決策：表上空白 → 回填；已有相同值（含空值寫法）→ 相同；已有不同值 → 不一致，保留原值。
- 只寫入呼叫端交來的說明書：批量入口依每份的類別決定（整份 PASS 或人工放行，`BatchItem.fillable`）。
- 寫入：批量入口以來源快照確認來源與核對時相同後才開檔，回填值寫進記憶體中的工作表（沿用表上既有日期格式）；
  原檔不動，回填結果由核對結果檔（result_file.py）帶出。
- 決策的顯示標籤（`BackfillAction.label`）只在這裡定義，PANEL 使用。

比價日填法：

- D（期間每日觀察）：填 `比價日_{Non-Call}`（開始）與 `比價日_{期數}`（最後一期），其他格填空值寫法；
  Non-Call 等於期數時只有一格。
- P（每期定日觀察）：`比價日_{Non-Call}`～`比價日_{期數}` 每格填該期比價日，其他格填空值寫法。
- 填出來最晚的比價日必須等於說明書的最終比價日，否則轉人工覆核、不回填。

格式設定沒有回填欄位的欄名、或參考條件表缺少該欄時，回填規則轉人工覆核並寫出缺的欄名，不產生決策。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.workbook.workbook import Workbook

from .config import ReferenceFormat
from .ingestion import IngestionError
from .orders.reference import ReferenceRow
from .rules.kit import Context, doc_review, result
from .rules.reference import standard_field
from .schema import CheckReport, CheckResult, Item, OrderValue, ParsedField
from .schema import CheckStatus as S
from .standard_fields import AutocallSchedule

SLOTS = 12
FALLBACK_DATE_FORMAT = "yyyy/m/d"
COLUMN_MISSING = "backfill_column_missing"
COMPARE_DATES = "比價日"  # 比價日_1～12 合起來核對，項目用這個名稱


class BackfillAction(StrEnum):
    FILL = "fill"  # 表上空白，整份核對通過後回填
    MATCH = "match"  # 已有相同值
    MISMATCH = "mismatch"  # 已有不同值，保留原值

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    BackfillAction.FILL: "空白，核對通過後回填",
    BackfillAction.MATCH: "相同",
    BackfillAction.MISMATCH: "不一致，保留原值",
}


@dataclass(frozen=True)
class CellDecision:
    """回填欄位的一格。"""

    column: str  # Excel 欄名
    cell: str  # 儲存格位置，例 F4
    sheet_value: Any
    expected: Any  # 日期、空值寫法或 ISIN
    action: BackfillAction


# ---------------------------------------------------------------- 每格決策


def _header(fmt: ReferenceFormat, std: str) -> str | None:
    return next((h for h, s in fmt.columns.items() if s == std), None)


def _missing_columns(fmt: ReferenceFormat, row: ReferenceRow, stds: Sequence[str]) -> list[str]:
    out = []
    for std in stds:
        header = _header(fmt, std)
        if header is None:
            out.append(f"參考條件表格式設定沒有標準欄位 {std} 的欄名")
        elif std not in row.cells:
            out.append(f"參考條件表缺少「{header}」欄")
    return out


def _column_missing(rid: str, key: str, missing: list[str], ovs: list[OrderValue | None], item: Item) -> CheckResult:
    message = "無法回填：" + "；".join(missing)
    return result(rid, key, S.REVIEW_REQUIRED, ov=ovs, reason=COLUMN_MISSING, message=message, item=item)


def _decide(fmt: ReferenceFormat, row: ReferenceRow, std: str, expected: Any) -> CellDecision:
    """呼叫前已確認格式設定與參考條件表都有這一欄。"""
    ov = row.fields.get(std)
    value = ov.value if ov else None
    if value is None:
        action = BackfillAction.FILL
    elif value == expected:
        action = BackfillAction.MATCH
    else:
        action = BackfillAction.MISMATCH
    return CellDecision(_header(fmt, std) or std, row.cells[std], value, expected, action)


def _fill_message(decisions: list[CellDecision]) -> str:
    n = sum(d.action == BackfillAction.FILL for d in decisions)
    return f"表上空白 {n} 格，整份核對通過後回填" if n else ""


def expected_slots(sched: AutocallSchedule, tenor: int | None, empty: str) -> list[Any] | str:
    """12 格比價日的期望值（不需要填的期別為空值寫法 `empty`）；無法對應格子時回傳原因。"""
    if tenor is not None and sched.periods != tenor:
        return f"說明書提前出場表有 {sched.periods} 期，與天期 {tenor} 個月不同，比價日無法對應到格子"
    if sched.periods > SLOTS:
        return f"說明書有 {sched.periods} 期，超過參考條件表的 {SLOTS} 格比價日"
    if not 1 <= sched.first_callable <= sched.periods:
        return f"第一個可提前出場的期別（{sched.first_callable}）不在第 1～{sched.periods} 期之間"
    if sched.observation == "D":
        callable_ = sorted({sched.first_callable, sched.periods})
    else:
        callable_ = list(range(sched.first_callable, sched.periods + 1))
    missing = [n for n in callable_ if n not in sched.dates]
    if missing:
        return "說明書抓不到以下期別的比價日：" + "、".join(f"第 {n} 期" for n in missing)
    return [sched.dates[n] if n in callable_ else empty for n in range(1, SLOTS + 1)]


# ---------------------------------------------------------------- 回填規則


def _single(
    rid: str, key: str, what: str, name: str, fmt: ReferenceFormat, row: ReferenceRow, pf: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    """只有一格的回填欄位：表上值與說明書值比對並決定這一格的處理。項目為該欄（沒有這欄時用 `name`）。"""
    ov: OrderValue | None = row.fields.get(key)
    item = Item.column(name, [ov])
    missing = _missing_columns(fmt, row, [key])
    if missing:
        return _column_missing(rid, key, missing, [ov], item), []
    if not pf.ok:
        return doc_review(rid, key, pf, ov.value if ov else None, [ov], item=item), []
    d = _decide(fmt, row, key, pf.value)
    ok = d.action != BackfillAction.MISMATCH
    return (
        result(
            rid,
            key,
            S.PASS if ok else S.MISMATCH,
            expected=d.sheet_value,
            actual=pf.value,
            pf=pf,
            ov=[ov],
            reason="" if ok else "value_mismatch",
            message=_fill_message([d]) or ("" if ok else f"表上{what}與說明書不同，保留原值"),
            item=item,
        ),
        [d],
    )


def isin(ctx: Context, fmt: ReferenceFormat, row: ReferenceRow) -> tuple[CheckResult, list[CellDecision]]:
    return _single("backfill.isin", "isin", " ISIN ", "ISIN Code", fmt, row, standard_field(ctx, "isin"))


def issue_date(ctx: Context, fmt: ReferenceFormat, row: ReferenceRow) -> tuple[CheckResult, list[CellDecision]]:
    """說明書發行日取自標準欄位 `issue_date`（BARC 第一章 §13(3)、HSBC 第一章 §15(2)）。"""
    key = "issue_date"
    return _single("backfill.issue_date", key, "發行日", "發行日", fmt, row, standard_field(ctx, key))


def compare_dates(ctx: Context, fmt: ReferenceFormat, row: ReferenceRow) -> tuple[CheckResult, list[CellDecision]]:
    rid, key, sched = "backfill.compare_dates", "compare_dates", standard_field(ctx, "autocall_schedule")
    stds = [f"autocall_date_{n}" for n in range(1, SLOTS + 1)]
    ovs = [row.fields.get(s) for s in stds]
    item = Item.group(COMPARE_DATES, ovs)
    missing = _missing_columns(fmt, row, stds)
    if missing:
        return _column_missing(rid, key, missing, ovs, item), []
    if not sched.ok:
        return doc_review(rid, key, sched, None, ovs, item=item), []
    tenor = standard_field(ctx, "tenor_months")
    slots = expected_slots(sched.value, tenor.value if tenor.ok else None, fmt.empty_value)
    if isinstance(slots, str):
        return (
            result(
                rid,
                key,
                S.REVIEW_REQUIRED,
                pf=sched,
                ov=ovs,
                reason="compare_dates_unmapped",
                message=slots,
                item=item,
            ),
            [],
        )
    decisions = [_decide(fmt, row, s, e) for s, e in zip(stds, slots, strict=True)]
    final = standard_field(ctx, "final_valuation_date")
    if not final.ok:
        return doc_review(rid, key, final, None, ovs, item=item), decisions
    latest = max(d for d in slots if isinstance(d, dt.date))
    if latest != final.value:
        return (
            result(
                rid,
                key,
                S.REVIEW_REQUIRED,
                expected=final.value,
                actual=latest,
                pf=sched,
                ov=ovs,
                reason="compare_dates_max_mismatch",
                message=f"說明書最晚的比價日 {latest.isoformat()} 不等於最終比價日 {final.value.isoformat()}，不回填",
                item=item,
            ),
            decisions,
        )
    bad = [d for d in decisions if d.action == BackfillAction.MISMATCH]
    rule = "D：填第一個可提前出場期與最後一期" if sched.value.observation == "D" else "P：第一個可提前出場期起每期都填"
    return (
        result(
            rid,
            key,
            S.MISMATCH if bad else S.PASS,
            expected={d.column: d.sheet_value for d in bad} or None,
            actual={d.column: d.expected for d in bad} or [d.expected for d in decisions],
            pf=sched,
            ov=ovs,
            reason="value_mismatch" if bad else "",
            message="；".join(x for x in (rule, _fill_message(decisions)) if x)
            + ("；表上值與說明書不同的格子保留原值" if bad else ""),
            item=item,
        ),
        decisions,
    )


# ---------------------------------------------------------------- 寫入


def conflicts_with_sheet(report: CheckReport) -> bool:
    """參考條件表的回填欄位已有和說明書不同的值。"""
    return any(d.action == BackfillAction.MISMATCH for d in report.backfill)


def values_certain(report: CheckReport) -> bool:
    """每個回填欄位都有確定的回填決策（回填規則全部通過）。哪些說明書要回填由批量入口決定。"""
    return bool(report.backfill) and not any(
        r.rule_id.startswith("backfill.") and r.status != S.PASS for r in report.results
    )


def open_reference(path: Path) -> Workbook:
    """開啟參考條件表準備回填（來源是否與核對時相同由批量入口以來源快照確認）。"""
    try:
        return openpyxl.load_workbook(path)  # 不用 data_only：保留公式與格式
    except Exception as e:
        raise IngestionError("reference_unreadable", f"參考條件表無法開啟（可能已損毀或不是 Excel）：{e}") from e


def _date_format(ws: Any, rfmt: ReferenceFormat) -> str:
    """沿用表上既有日期格的顯示格式。"""
    for row in ws.iter_rows(min_row=rfmt.first_data_row):
        for c in row:
            if isinstance(c.value, dt.datetime):
                return c.number_format
    return FALLBACK_DATE_FORMAT


def apply(wb: Workbook, rfmt: ReferenceFormat, reports: Sequence[CheckReport]) -> None:
    """把要回填的說明書（呼叫端已決定：整份 PASS 或人工放行）的「回填」格寫進工作表。"""
    ws = wb[rfmt.sheet]
    fmt = _date_format(ws, rfmt)
    for report in reports:
        for d in report.backfill:
            if d.action != BackfillAction.FILL:
                continue
            cell = ws[d.cell]
            cell.value = d.expected
            if isinstance(d.expected, dt.date):
                cell.number_format = fmt
