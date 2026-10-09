"""單份核對：一份說明書從配對結果到整體狀態的完整順序，所有上手共用（ADR 0005）。

1. 接收批量入口的配對結果（上手、讀出的說明書、對到的參考條件表列；配對失敗時只有辨識結果）
2. 表頭欄位檢查（rules/reference.py）
3. 參考條件表欄位規則（rules/reference.py）
4. 各上手共用的說明書推算規則（rules/derivation.py：價格推算）
5. 上手說明書內部規則（Issuer.rules；只拿到讀出結果、審查標準與上手宣告的參考條件表欄位）
6. 審查標準規則（rules/review_standard.py）
7. Non-Call(月)、TS、IIS、ISIN、發行日、比價日、VWAP 商品的各標的價格
8. 回填決策（backfill.py）
9. 整體狀態

投資人須知（ADR 0007）另有 `check_investor_sheet`：配對結果 → 投資人須知規則（rules/iis.py：頁數、文件本身、
參考條件表、同商品說明書）→ 範本專屬規則（IisTemplate.rules，例：MS）→ 審查標準規則（只核對範本有的項目）→ 整體狀態；
不回填。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import backfill
from .check_config import CheckConfig
from .config import ReviewStandard
from .investor_sheet import IisSheet
from .issuers import Issuer
from .orders.reference import OrderRecord, ReferenceRow
from .rules import derivation, iis, reference
from .rules.kit import Context, IssuerContext
from .rules.review_standard import iis_review_standard_rules, review_standard_rules
from .schema import CheckReport, CheckResult, CheckStatus, overall_status
from .standard_fields import TermSheet


@dataclass(frozen=True)
class Paired:
    """配對成功的說明書：上手、讀出結果（同一份只讀一次）、參考條件表的列與轉成的下單資料。"""

    issuer: Issuer
    ts: TermSheet
    row: ReferenceRow
    record: OrderRecord


def _issuer_context(paired: Paired, std: ReviewStandard) -> IssuerContext:
    """上手說明書內部規則的輸入：只帶該上手宣告的參考條件表欄位（ADR 0005）。"""
    record = paired.record
    declared = {key: record.fields.get(key) for key in paired.issuer.reference_fields}
    return IssuerContext(paired.ts, std, paired.issuer.code, declared, record.source)


def check_document(pairing: list[CheckResult], paired: Paired | None, config: CheckConfig) -> CheckReport:
    """`pairing` 為辨識與配對的結果；配對失敗（`paired` 為 None）時不執行任何條件規則。

    回傳的報告不含範本 ID 與記錄資料（metadata），由批量入口補上。
    """
    std, rfmt = config.review_standard, config.reference_format
    results = list(pairing)
    report = CheckReport(CheckStatus.ERROR, None, results, [])
    if paired is not None:
        record = paired.record
        ctx = Context(paired.ts, record, std, rfmt, paired.issuer.code)
        results.extend(reference.column_checks(record))
        results.extend(reference.field_rules(ctx))
        results.extend(derivation.prices(ctx))
        results.extend(paired.issuer.rules(_issuer_context(paired, std)))
        results.extend(review_standard_rules(ctx))
        results.append(reference.first_callable_period(ctx))
        decisions, backfill_results = [], []
        rules = (
            backfill.checked_marks,
            backfill.isin,
            backfill.issue_date,
            backfill.compare_dates,
            backfill.underlying_prices,
        )
        for rule in rules:
            r, cells = rule(ctx, rfmt, paired.row)
            if r is not None:  # 價格欄只有期初定價 VWAP 時才是回填欄位
                backfill_results.append(r)
            decisions.extend(cells)
        results.extend(backfill_results)
        report.backfill = decisions
        report.backfill_certain = bool(decisions) and all(r.status == CheckStatus.PASS for r in backfill_results)
        report.not_covered = [dict(n) for n in paired.issuer.not_covered]
    report.status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
    return report


@dataclass(frozen=True)
class PairedIis:
    """配對成功的投資人須知：上手、讀出結果、參考條件表的列，以及同商品說明書的讀出結果（這批沒有時為 None）。"""

    issuer: Issuer
    sheet: IisSheet
    row: ReferenceRow
    record: OrderRecord
    term_sheet: TermSheet | None
    pages: int
    file_code: str  # 檔名前 12 碼


def check_investor_sheet(pairing: list[CheckResult], paired: PairedIis | None, config: CheckConfig) -> CheckReport:
    """投資人須知的單份核對；配對失敗（`paired` 為 None）時不執行任何條件規則。不回填。"""
    results = list(pairing)
    report = CheckReport(CheckStatus.ERROR, None, results, [])
    if paired is not None:
        base = Context(paired.sheet, paired.record, config.review_standard, config.reference_format, paired.issuer.code)
        ctx = iis.IisContext(base, paired.sheet, paired.term_sheet, paired.pages, paired.file_code)
        results.extend(iis.run_all(ctx))
        template = paired.issuer.iis
        if template is not None and template.rules is not None:
            results.extend(iis.as_iis(template.rules(iis.IisIssuerContext(paired.sheet, paired.term_sheet))))
        trade = iis.trade_date(ctx)
        results.extend(iis.as_iis(iis_review_standard_rules(base, paired.sheet, trade=trade)))
        report.not_covered = [dict(n) for n in template.not_covered] if template is not None else []
    report.status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
    return report
