"""核對入口：說明書 PDF ＋ 詢價表 ＋ 審查標準 ＋ 格式設定 → 完整核對結果。CLI 只是它的薄包裝。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
import openpyxl

from . import __version__
from .config import OrderFormat, ReviewStandard, load_order_format, load_review_standard
from .extraction import extract_lines
from .ingestion import IngestionError, open_pdf, sha256_of
from .orders.inquiry import OrderRecord, load_inquiry
from .parsers import barc as barc_parser
from .rules import barc as barc_rules
from .schema import CheckResult, CheckStatus, overall_status


@dataclass
class CheckReport:
    status: CheckStatus
    template: str | None
    results: list[CheckResult]
    not_covered: list[dict[str, str]]
    metadata: dict[str, Any] = field(default_factory=dict)


def _error(rule_id: str, field_: str, e: IngestionError) -> CheckResult:
    return CheckResult(
        rule_id=rule_id, field=field_, status=CheckStatus.ERROR, reason_code=e.reason_code, message=str(e)
    )


def _file_meta(path: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {"file": path.name, "sha256": None}
    if path.is_file():
        meta["sha256"] = sha256_of(path)
    return meta


def run_check(term_sheet: Path, order: Path, review_standard: Path, order_format: Path) -> CheckReport:
    term_sheet, order = Path(term_sheet), Path(order)
    review_standard, order_format = Path(review_standard), Path(order_format)
    metadata: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "parser": {"template": barc_parser.TEMPLATE_ID, "version": barc_parser.PARSER_VERSION},
        "inputs": {"term_sheet": _file_meta(term_sheet), "order": _file_meta(order)},
        "review_standard": _file_meta(review_standard),
        "order_format": _file_meta(order_format),
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
        fmt = load_order_format(order_format)
        metadata["order_format"].update(issuer=fmt.issuer, version=fmt.version)
    except IngestionError as e:
        results.append(_error("input.order_format", "詢價格式設定", e))
    if fmt is not None:
        try:
            rec = load_inquiry(order, fmt)
        except IngestionError as e:
            results.append(_error("input.order", "詢價表", e))
    try:
        doc = open_pdf(term_sheet)
        try:
            lines = extract_lines(doc)
            metadata["inputs"]["term_sheet"]["pages"] = doc.page_count
        finally:
            doc.close()
    except IngestionError as e:
        results.append(_error("input.term_sheet", "說明書", e))

    template = None
    if std is not None and fmt is not None and rec is not None and lines is not None:
        detection, ts = barc_parser.parse(lines)
        if detection.matched:
            template = barc_parser.TEMPLATE_ID
            results.append(
                CheckResult(
                    rule_id="template.barc",
                    field="template",
                    status=CheckStatus.PASS,
                    actual=barc_parser.TEMPLATE_ID,
                    document_evidence=detection.evidence,
                    message="符合 BARC 中文產品說明書範本",
                )
            )
        else:
            results.append(
                CheckResult(
                    rule_id="template.barc",
                    field="template",
                    status=CheckStatus.REVIEW_REQUIRED,
                    reason_code="template_unknown",
                    document_evidence=detection.evidence,
                    message="不是 BARC 中文產品說明書範本，請人工處理：" + "；".join(detection.failed),
                )
            )
        results.extend(barc_rules.order_format_checks(rec))
        if detection.matched:
            results.extend(barc_rules.run_all(barc_rules.Context(ts, rec, std, fmt)))

    status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
    return CheckReport(status, template, results, list(barc_rules.NOT_COVERED), metadata)
