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

from collections.abc import Sequence
from dataclasses import dataclass

from . import backfill
from .check_config import CheckConfig
from .config import ReviewStandard
from .identification import Identification
from .investor_sheet import IisSheet
from .issuers import Issuer
from .orders.reference import OrderRecord
from .rules import derivation, iis, reference
from .rules.kit import Context, IssuerContext
from .rules.review_standard import iis_review_standard_rules, review_standard_rules
from .schema import CheckReport, CheckResult, CheckStatus, DocKind, overall_status
from .standard_fields import TermSheet


@dataclass(frozen=True)
class Paired:
    """配對成功的說明書：上手、讀出結果（同一份只讀一次）、對到的參考條件表列；由辨識結果建立。"""

    issuer: Issuer
    ts: TermSheet
    row: OrderRecord

    @classmethod
    def of(cls, ident: Identification) -> Paired | None:
        """有上手、讀出結果、對到的列且規則會跑才算配對成功；否則 None（不執行任何條件規則）。"""
        if ident.adapter is None or ident.term_sheet is None or ident.row is None or not ident.checked:
            return None
        return cls(ident.adapter, ident.term_sheet, ident.row)


def _issuer_context(paired: Paired, std: ReviewStandard) -> IssuerContext:
    """上手說明書內部規則的輸入：只帶該上手宣告的參考條件表欄位（ADR 0005）。"""
    row = paired.row
    declared = {key: row.get(key) for key in paired.issuer.reference_fields}
    return IssuerContext(paired.ts, std, paired.issuer.code, declared, row.source)


def check_document(pairing: Sequence[CheckResult], paired: Paired | None, config: CheckConfig) -> CheckReport:
    """`pairing` 為辨識與配對的結果；配對失敗（`paired` 為 None）時不執行任何條件規則。

    回傳的報告不含範本 ID 與記錄資料（metadata），由批量入口補上。
    """
    std, rfmt = config.review_standard, config.reference_format
    results = list(pairing)
    report = CheckReport(CheckStatus.ERROR, None, results, [])
    if paired is not None:
        ctx = Context(paired.ts, paired.row, std, rfmt, paired.issuer.code)
        results.extend(reference.column_checks(paired.row))
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
            r, cells = rule(ctx)
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
    row: OrderRecord
    term_sheet: TermSheet | None
    pages: int
    file_code: str  # 檔名前 12 碼

    @classmethod
    def of(cls, ident: Identification) -> PairedIis | None:
        """配對成功的投資人須知；同商品說明書配對成功（規則有跑）才拿它的讀出結果來比對。"""
        if ident.adapter is None or ident.investor_sheet is None or ident.row is None or ident.pages is None:
            return None
        if not ident.checked:
            return None
        partner = ident.partner
        term_sheet = partner.term_sheet if partner is not None and partner.checked else None
        return cls(ident.adapter, ident.investor_sheet, ident.row, term_sheet, ident.pages, ident.product_code or "")


def check_investor_sheet(pairing: Sequence[CheckResult], paired: PairedIis | None, config: CheckConfig) -> CheckReport:
    """投資人須知的單份核對；配對失敗（`paired` 為 None）時不執行任何條件規則。不回填。"""
    results = list(pairing)
    report = CheckReport(CheckStatus.ERROR, None, results, [])
    if paired is not None:
        ctx = Context(
            paired.sheet,
            paired.row,
            config.review_standard,
            config.reference_format,
            paired.issuer.code,
            document=DocKind.IIS,
            pages=paired.pages,
            file_code=paired.file_code,
            term_sheet=paired.term_sheet,
        )
        results.extend(iis.run_all(ctx))
        template = paired.issuer.iis
        if template is not None and template.rules is not None:
            results.extend(template.rules(iis.IisIssuerContext(paired.sheet, paired.term_sheet)))
        results.extend(iis_review_standard_rules(ctx, trade=iis.trade_date(ctx)))
        report.not_covered = [dict(n) for n in template.not_covered] if template is not None else []
    for r in results:  # 這份 PDF 的結果（含辨識與配對）都屬於投資人須知，錯訊的文件那一邊依此稱呼
        r.document = DocKind.IIS
    report.status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
    return report
