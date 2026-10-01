"""核對入口：說明書 PDF ＋ 詢價表 ＋ 審查標準 ＋ 格式設定 → 完整核對結果。CLI 只是它的薄包裝。

說明書依上手註冊表逐一辨識範本：恰好一個命中才以該上手的 parser 與規則核對，
零個或多個命中一律轉人工覆核（docs/issuer-onboarding.md §6）。
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
from .issuers import ISSUERS, Issuer
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


def _error(rule_id: str, field_: str, e: IngestionError) -> CheckResult:
    return CheckResult(
        rule_id=rule_id, field=field_, status=CheckStatus.ERROR, reason_code=e.reason_code, message=str(e)
    )


def _file_meta(path: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {"file": path.name, "sha256": None}
    if path.is_file():
        meta["sha256"] = sha256_of(path)
    return meta


def _detect(lines: Sequence[Line], issuers: Sequence[Issuer]) -> tuple[Issuer | None, CheckResult]:
    """以所有已註冊上手辨識範本；恰好一個命中才回傳該上手。"""
    detections: list[tuple[Issuer, DetectionResult]] = [(i, i.detect(lines)) for i in issuers]
    hits = [(i, d) for i, d in detections if d.matched]
    if len(hits) == 1:
        issuer, det = hits[0]
        return issuer, CheckResult(
            rule_id="template.detect",
            field="template",
            status=CheckStatus.PASS,
            actual=issuer.template_id,
            document_evidence=det.evidence,
            message=f"符合 {issuer.label}範本",
        )
    if hits:
        evidence: list[Evidence] = [e for _, d in hits for e in d.evidence]
        return None, CheckResult(
            rule_id="template.detect",
            field="template",
            status=CheckStatus.REVIEW_REQUIRED,
            reason_code="template_ambiguous",
            actual=[i.template_id for i, _ in hits],
            document_evidence=evidence,
            message="同時符合多個範本，無法確定上手，請人工處理：" + "、".join(i.label for i, _ in hits),
        )
    evidence = [e for _, d in detections for e in d.evidence]
    detail = "；".join(f"{i.label}：" + "；".join(d.failed) for i, d in detections)
    return None, CheckResult(
        rule_id="template.detect",
        field="template",
        status=CheckStatus.REVIEW_REQUIRED,
        reason_code="template_unknown",
        document_evidence=evidence,
        message="不符合任何已支援的範本，請人工處理：" + detail,
    )


def run_check(
    term_sheet: Path,
    order: Path,
    review_standard: Path,
    order_format: Path | None = None,
    *,
    stop_on_pairing_failure: bool = False,
    issuers: Sequence[Issuer] = ISSUERS,
) -> CheckReport:
    """未指定詢價格式設定時，依辨識到的上手使用 `config/order_formats/<上手>.toml`。

    PANEL 可要求配對失敗即停止；預設保持既有 CLI 探勘與核對行為。
    """
    term_sheet, order, review_standard = Path(term_sheet), Path(order), Path(review_standard)
    metadata: dict[str, Any] = {
        "stop_on_pairing_failure": stop_on_pairing_failure,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "parser": {"template": None, "version": None},
        "inputs": {"term_sheet": _file_meta(term_sheet), "order": _file_meta(order)},
        "review_standard": _file_meta(review_standard),
        "order_format": {"file": None, "sha256": None},
    }
    results: list[CheckResult] = []
    std: ReviewStandard | None = None
    fmt: OrderFormat | None = None
    rec: OrderRecord | None = None
    lines = None
    issuer: Issuer | None = None

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
        results.append(_error("input.term_sheet", "說明書", e))

    template_result = None
    if lines is not None:
        issuer, template_result = _detect(lines, issuers)
        if issuer is not None:
            metadata["parser"] = {"template": issuer.template_id, "version": issuer.parser_version}
    if order_format is None and issuer is not None:
        order_format = issuer.default_order_format
    if order_format is not None:
        order_format = Path(order_format)
        metadata["order_format"] = _file_meta(order_format)
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

    template = None
    if std is not None and fmt is not None and rec is not None and template_result is not None:
        results.append(template_result)
        if issuer is not None and fmt.issuer != issuer.code:
            results.append(
                CheckResult(
                    rule_id="order.issuer",
                    field="詢價格式設定",
                    status=CheckStatus.REVIEW_REQUIRED,
                    expected=issuer.code,
                    actual=fmt.issuer,
                    reason_code="order_format_issuer_mismatch",
                    message=f"說明書辨識為 {issuer.code}，詢價格式設定卻是 {fmt.issuer}，可能選錯上手或格式設定",
                )
            )
            issuer = None
        results.extend(order_format_checks(rec))
        if issuer is not None:
            template = issuer.template_id
            _, ts = issuer.parse(lines)
            context = issuer.context(ts, rec, std, fmt)
            pairing = issuer.pairing(context)
            if stop_on_pairing_failure and pairing.status != CheckStatus.PASS:
                results.append(pairing)
            else:
                results.extend(issuer.run_rules(context))
    elif template_result is not None and template_result.status != CheckStatus.PASS:
        results.append(template_result)  # 範本未辨識：即使其他輸入失敗也要說明原因

    status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
    not_covered = list(issuer.not_covered) if issuer is not None else []
    return CheckReport(status, template, results, not_covered, metadata)
