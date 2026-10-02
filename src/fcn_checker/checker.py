"""核對入口：說明書 PDF ＋ 詢價表 ＋ 審查標準 ＋ 格式設定 → 完整核對結果。CLI 只是它的薄包裝。

範本由上手註冊表（issuers.py）逐一辨識；恰好一個上手命中才繼續核對。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
import openpyxl

from . import __version__
from .config import OrderFormat, ReviewStandard, load_order_format, load_review_standard
from .extraction import extract_lines
from .ingestion import IngestionError, open_pdf, sha256_of
from .issuers import REGISTRY, Issuer
from .orders.inquiry import OrderRecord, load_inquiry
from .rules.common import order_format_checks
from .schema import CheckResult, CheckStatus, DetectionResult, Evidence, Line, overall_status


@dataclass
class CheckReport:
    status: CheckStatus
    template: str | None
    results: list[CheckResult]
    not_covered: list[dict[str, str]]
    metadata: dict[str, Any] = field(default_factory=dict)
    backfill: list[Any] = field(default_factory=list)  # 參考條件表流程的回填決策（rules.reference.CellDecision）


def _error(rule_id: str, field_: str, e: IngestionError) -> CheckResult:
    return CheckResult(
        rule_id=rule_id, field=field_, status=CheckStatus.ERROR, reason_code=e.reason_code, message=str(e)
    )


def _file_meta(path: Path | None) -> dict[str, Any]:
    meta: dict[str, Any] = {"file": path.name if path else None, "sha256": None}
    if path is not None and path.is_file():
        meta["sha256"] = sha256_of(path)
    return meta


def _detect(lines: list[Line], registry: Sequence[Issuer]) -> tuple[Issuer | None, CheckResult]:
    """以每家已註冊上手辨識範本；恰好一個命中才回傳該上手，零個或多個命中轉人工覆核。"""
    detections: list[tuple[Issuer, DetectionResult]] = [(i, i.detect(lines)) for i in registry]
    hits = [(i, d) for i, d in detections if d.matched]
    rid = "template.detect"
    if len(hits) == 1:
        issuer, det = hits[0]
        return issuer, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.PASS,
            actual=issuer.template_id,
            document_evidence=det.evidence,
            message=f"符合 {issuer.label} 範本",
        )
    if hits:
        return None, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.REVIEW_REQUIRED,
            actual=[i.template_id for i, _ in hits],
            reason_code="template_ambiguous",
            document_evidence=[e for _, d in hits for e in d.evidence],
            message="同時符合多個範本，無法確定上手，請人工處理：" + "、".join(i.label for i, _ in hits),
        )
    evidence: list[Evidence] = [e for _, d in detections for e in d.evidence]
    reasons = "；".join(f"{i.code}：" + "；".join(d.failed) for i, d in detections)
    return None, CheckResult(
        rule_id=rid,
        field="template",
        status=CheckStatus.REVIEW_REQUIRED,
        reason_code="template_unknown",
        document_evidence=evidence,
        message="不是已支援的說明書範本，請人工處理：" + reasons,
    )


def _issuer_mismatch(issuer: Issuer, fmt: OrderFormat) -> CheckResult:
    return CheckResult(
        rule_id="order.issuer",
        field="詢價格式設定",
        status=CheckStatus.REVIEW_REQUIRED,
        expected=issuer.code,
        actual=fmt.issuer,
        reason_code="issuer_mismatch",
        message=f"說明書是 {issuer.code} 範本，但詢價格式設定是 {fmt.issuer}，可能拿錯詢價表或格式設定",
    )


def run_check(
    term_sheet: Path,
    order: Path,
    review_standard: Path,
    order_format: Path | None = None,
    *,
    stop_on_pairing_failure: bool = False,
    registry: Sequence[Issuer] = REGISTRY,
) -> CheckReport:
    """order_format 未指定時，依辨識到的上手使用其預設詢價格式設定。

    PANEL 可要求配對失敗即停止；預設保持既有 CLI 探勘與核對行為。
    """
    term_sheet, order, review_standard = Path(term_sheet), Path(order), Path(review_standard)
    metadata: dict[str, Any] = {
        "stop_on_pairing_failure": stop_on_pairing_failure,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "parser": None,
        "inputs": {"term_sheet": _file_meta(term_sheet), "order": _file_meta(order)},
        "review_standard": _file_meta(review_standard),
    }
    results: list[CheckResult] = []
    std: ReviewStandard | None = None
    fmt: OrderFormat | None = None
    rec: OrderRecord | None = None
    lines = None

    try:
        std = load_review_standard(review_standard)
        metadata["review_standard"].update(version=std.version, effective_date=std.effective_date.isoformat())
    except IngestionError as e:
        results.append(_error("input.review_standard", "審查標準", e))
    try:
        doc = open_pdf(term_sheet)
        try:
            lines = extract_lines(doc)
            metadata["inputs"]["term_sheet"]["pages"] = doc.page_count
        finally:
            doc.close()
    except IngestionError as e:
        pdf_error = _error("input.term_sheet", "說明書", e)
    else:
        pdf_error = None

    issuer, template_result = _detect(lines, registry) if lines is not None else (None, None)
    if issuer is not None:
        metadata["parser"] = {"template": issuer.template_id, "version": issuer.parser_version}
    fmt_path = Path(order_format) if order_format is not None else (issuer.order_format if issuer else None)
    metadata["order_format"] = _file_meta(fmt_path)
    if fmt_path is not None:
        try:
            fmt = load_order_format(fmt_path)
            metadata["order_format"].update(issuer=fmt.issuer, version=fmt.version)
        except IngestionError as e:
            results.append(_error("input.order_format", "詢價格式設定", e))
    if fmt is not None:
        try:
            rec = load_inquiry(order, fmt)
        except IngestionError as e:
            results.append(_error("input.order", "詢價表", e))
    if pdf_error is not None:
        results.append(pdf_error)

    template = None
    ready = std is not None and template_result is not None and (rec is not None or fmt_path is None)
    if ready:
        results.append(template_result)
        if rec is not None:
            results.extend(order_format_checks(rec))
        if issuer is not None and fmt is not None and rec is not None:
            template = issuer.template_id
            if fmt.issuer != issuer.code:
                results.append(_issuer_mismatch(issuer, fmt))
            else:
                _, ts = issuer.parse(lines)
                context = issuer.context(ts, rec, std, fmt)
                pairing = issuer.pairing(context)
                if stop_on_pairing_failure and pairing.status != CheckStatus.PASS:
                    results.append(pairing)
                else:
                    results.extend(issuer.run_rules(context))

    status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
    not_covered = [dict(n) for n in issuer.not_covered] if issuer is not None and ready else []
    return CheckReport(status, template, results, not_covered, metadata)
