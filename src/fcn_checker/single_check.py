"""單份核對：一份說明書從配對結果到整體狀態的完整順序，所有上手共用（ADR 0005）。

1. 接收批量入口的配對結果（上手、讀出的說明書、對到的參考條件表列；配對失敗時只有辨識結果）
2. 表頭欄位檢查（rules/reference.py）
3. 參考條件表欄位規則（rules/reference.py）
4. 上手說明書內部規則（Issuer.rules；只拿到讀出結果、審查標準與上手宣告的參考條件表欄位）
5. 審查標準規則（rules/review_standard.py）
6. Non-Call(月)、ISIN、發行日、比價日
7. 回填決策（backfill.py）
8. 整體狀態
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import backfill
from .config import ReferenceFormat, ReviewStandard
from .issuers import Issuer
from .orders.reference import OrderRecord, ReferenceRow, ReferenceSheet
from .rules import reference
from .rules.kit import Context, IssuerContext
from .rules.review_standard import review_standard_rules
from .schema import CheckReport, CheckResult, CheckStatus, overall_status
from .standard_fields import TermSheet


@dataclass(frozen=True)
class Paired:
    """配對成功的說明書：上手、讀出結果（同一份只讀一次）、參考條件表的列。"""

    issuer: Issuer
    ts: TermSheet
    row: ReferenceRow


def _issuer_context(paired: Paired, record: OrderRecord, std: ReviewStandard) -> IssuerContext:
    """上手說明書內部規則的輸入：只帶該上手宣告的參考條件表欄位（ADR 0005）。"""
    declared = {key: record.fields.get(key) for key in paired.issuer.reference_fields}
    return IssuerContext(paired.ts, std, paired.issuer.code, declared, record.source)


def check_document(
    pairing: list[CheckResult],
    paired: Paired | None,
    sheet: ReferenceSheet,
    rfmt: ReferenceFormat,
    std: ReviewStandard,
    template: str | None,
    metadata: dict[str, Any],
) -> CheckReport:
    """`pairing` 為辨識與配對的結果；配對失敗（`paired` 為 None）時不執行任何條件規則。"""
    results = list(pairing)
    report = CheckReport(CheckStatus.ERROR, template, results, [], metadata)
    if paired is not None:
        record = sheet.record(paired.row)
        ctx = Context(paired.ts, record, std, rfmt, paired.issuer.code)
        results.extend(reference.column_checks(record))
        results.extend(reference.field_rules(ctx))
        results.extend(paired.issuer.rules(_issuer_context(paired, record, std)))
        results.extend(review_standard_rules(ctx))
        results.append(reference.first_callable_period(ctx))
        decisions = []
        for rule in (backfill.isin, backfill.issue_date, backfill.compare_dates):
            r, cells = rule(ctx, rfmt, paired.row)
            results.append(r)
            decisions.extend(cells)
        report.backfill = decisions
        report.not_covered = [dict(n) for n in paired.issuer.not_covered]
    report.status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
    return report
