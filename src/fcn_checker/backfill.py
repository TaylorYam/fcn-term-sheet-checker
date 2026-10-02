"""回填欄位（ISIN Code、比價日_1～12）的整段流程，所有上手共用（ADR 0004）。

- 每格決策：表上空白 → 回填；已有相同值（含空值寫法）→ 相同；已有不同值 → 不一致，保留原值。
- 只有整份核對 PASS 的說明書才寫入（`fillable`）。
- 寫入新檔：沿用表上既有日期格式；原檔不動、不覆蓋既有檔案；開檔前確認參考條件表與核對時相同。
- 決策的顯示標籤（`BackfillAction.label`）只在這裡定義，報告與 PANEL 共用。

比價日填法：

- D（期間每日觀察）：只填 `比價日_{Non-Call}`，其他格填空值寫法。
- P（每期定日觀察）：`比價日_{Non-Call}`～`比價日_{期數}` 每格填該期比價日，其他格填空值寫法。

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
from .ingestion import IngestionError, sha256_of
from .orders.reference import ReferenceRow
from .rules.common import Context, doc_review, result
from .rules.reference import standard_field
from .schema import CheckReport, CheckResult, OrderValue, ParsedField
from .schema import CheckStatus as S
from .standard_fields import AutocallSchedule

SLOTS = 12
FALLBACK_DATE_FORMAT = "yyyy/m/d"
COLUMN_MISSING = "backfill_column_missing"


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


def _column_missing(rid: str, key: str, missing: list[str], ovs: list[OrderValue | None]) -> CheckResult:
    return result(rid, key, S.REVIEW_REQUIRED, ov=ovs, reason=COLUMN_MISSING, message="無法回填：" + "；".join(missing))


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
    last = sched.first_callable if sched.observation == "D" else sched.periods
    callable_ = range(sched.first_callable, last + 1)
    missing = [n for n in callable_ if n not in sched.dates]
    if missing:
        return "說明書抓不到以下期別的比價日：" + "、".join(f"第 {n} 期" for n in missing)
    return [sched.dates[n] if n in callable_ else empty for n in range(1, SLOTS + 1)]


# ---------------------------------------------------------------- 回填規則


def isin(
    ctx: Context, fmt: ReferenceFormat, row: ReferenceRow, pf: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    rid, key = "backfill.isin", "isin"
    ov: OrderValue | None = row.fields.get(key)
    missing = _missing_columns(fmt, row, [key])
    if missing:
        return _column_missing(rid, key, missing, [ov]), []
    if not pf.ok:
        return doc_review(rid, key, pf, ov.value if ov else None, [ov]), []
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
            message=_fill_message([d]) or ("" if ok else "表上 ISIN 與說明書不同，保留原值"),
        ),
        [d],
    )


def compare_dates(
    ctx: Context, fmt: ReferenceFormat, row: ReferenceRow, sched: ParsedField
) -> tuple[CheckResult, list[CellDecision]]:
    rid, key = "backfill.compare_dates", "compare_dates"
    stds = [f"autocall_date_{n}" for n in range(1, SLOTS + 1)]
    ovs = [row.fields.get(s) for s in stds]
    missing = _missing_columns(fmt, row, stds)
    if missing:
        return _column_missing(rid, key, missing, ovs), []
    if not sched.ok:
        return doc_review(rid, key, sched, None, ovs), []
    tenor = standard_field(ctx, "tenor_months")
    slots = expected_slots(sched.value, tenor.value if tenor.ok else None, fmt.empty_value)
    if isinstance(slots, str):
        return (
            result(rid, key, S.REVIEW_REQUIRED, pf=sched, ov=ovs, reason="compare_dates_unmapped", message=slots),
            [],
        )
    decisions = [_decide(fmt, row, s, e) for s, e in zip(stds, slots, strict=True)]
    bad = [d for d in decisions if d.action == BackfillAction.MISMATCH]
    rule = "D：只填第一個可提前出場期" if sched.value.observation == "D" else "P：第一個可提前出場期起每期都填"
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
        ),
        decisions,
    )


# ---------------------------------------------------------------- 寫入新檔


def fillable(report: CheckReport) -> bool:
    """只有整份核對 PASS 的說明書才回填。"""
    return report.status == S.PASS


def open_reference(path: Path, expected_sha256: str | None) -> Workbook:
    """開啟參考條件表準備回填；檔案與核對時記錄的 hash 不同（或無法讀取）時丟出 IngestionError。"""
    try:
        changed = expected_sha256 is None or sha256_of(path) != expected_sha256
    except OSError:
        changed = True
    if changed:
        raise IngestionError("reference_changed", "參考條件表在核對後已變更或無法讀取，請重新核對後再儲存")
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


def apply(wb: Workbook, rfmt: ReferenceFormat, reports: Sequence[CheckReport]) -> list[bool]:
    """把整份 PASS 的說明書的「回填」格寫進工作表；回傳每份是否回填（與 reports 同順序）。"""
    ws = wb[rfmt.sheet]
    fmt = _date_format(ws, rfmt)
    filled = []
    for report in reports:
        ok = fillable(report)
        if ok:
            for d in report.backfill:
                if d.action != BackfillAction.FILL:
                    continue
                cell = ws[d.cell]
                cell.value = d.expected
                if isinstance(d.expected, dt.date):
                    cell.number_format = fmt
        filled.append(ok)
    return filled


def write_new(data: bytes, out: Path) -> None:
    """寫出回填新檔；檔案已存在時不覆蓋，丟出 IngestionError。"""
    try:
        with out.open("xb") as f:
            f.write(data)
    except OSError as e:
        raise IngestionError("output_exists", f"無法寫入新檔 {out.name}：{e}") from e
