"""批量核對入口：多份說明書 PDF × 參考條件表 → 逐份核對結果、報告與回填後的新檔（ADR 0004）。

分三段，CLI 與 PANEL 共用：

- `preview_batch`：唯讀辨識每份說明書（上手、商品代號、對到的參考條件表列），不核對、不寫檔。
- `check_batch`：逐份核對，回傳結果與回填決策，不寫任何檔案。
- `save_batch`：寫每份報告、回填新檔與「核對結果」工作表。`run_batch` = 核對 ＋ 儲存（CLI 使用）。

辨識流程（每份說明書）：
1. 檔名前三碼（上手編號）查上手編號對照 → 上手；不在對照表或上手沒有範本 → 未支援上手。
2. 說明書內容辨識出的上手、說明書商品代號前三碼都必須與檔名一致，否則轉人工覆核。
3. 以商品代號找參考條件表的列（TDCC Code），該列發行機構必須是此上手的寫法。

範本辨識與讀出每份說明書各只做一次；配對成功後由單份核對（single_check.py）依序執行所有規則。
批量入口只負責載入設定與參考條件表、逐份呼叫、單份錯誤隔離與儲存。

只有整份核對 PASS 的說明書才回填；結果另存新檔（原檔不動、不覆蓋既有檔案）。回填流程見 backfill.py。
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
import openpyxl
from openpyxl.workbook.workbook import Workbook

from . import __version__, backfill
from .config import (
    ReferenceFormat,
    ReviewStandard,
    load_issuer_prefixes,
    load_reference_format,
    load_review_standard,
)
from .extraction import extract_lines
from .ingestion import IngestionError, error_result, file_meta, open_pdf
from .issuers import REGISTRY, Issuer, by_code, detect
from .orders.reference import ReferenceRow, ReferenceSheet, load_reference_sheet
from .reporting import STATUS_ZH, write_reports
from .rules.common import doc_review, read_standard
from .schema import CheckReport, CheckResult, CheckStatus, Evidence, ParsedField, overall_status
from .single_check import Paired, check_document
from .standard_fields import TermSheet

RESULT_SHEET = "核對結果"
UNSUPPORTED = "issuer_unsupported"
RESULT_HEADERS = ("PDF 檔名", "商品代號", "上手", "整體狀態", "問題數", "問題摘要", "已回填", "報告檔名")
DEFAULT_REFERENCE_FORMAT = Path("config/reference_sheet.toml")
DEFAULT_ISSUER_PREFIXES = Path("config/issuer_prefixes.toml")


@dataclass
class BatchItem:
    term_sheet: Path
    report: CheckReport
    issuer: str | None = None
    product_code: str | None = None
    filled: bool = False
    report_paths: tuple[Path, ...] = ()
    save_error: str = ""  # 這份報告儲存失敗的原因

    @property
    def unsupported(self) -> bool:
        return any(r.reason_code == UNSUPPORTED for r in self.report.results)

    @property
    def status_label(self) -> str:
        if self.unsupported:
            return "未支援上手"
        return f"{self.report.status.value}（{STATUS_ZH[self.report.status]}）"


@dataclass
class BatchOutcome:
    status: CheckStatus
    items: list[BatchItem]
    reference_sheet: Path
    reference_format: ReferenceFormat | None = None
    output: Path | None = None  # 回填後的新檔；尚未儲存或儲存失敗時為 None
    errors: list[CheckResult] = field(default_factory=list)  # 整批錯誤（設定檔、參考條件表、寫檔）
    saved: bool = False
    reference_sha256: str | None = None  # 核對時參考條件表的 hash；儲存前據此確認檔案未變更

    def refresh_status(self) -> None:
        statuses = [i.report.status for i in self.items] + [e.status for e in self.errors]
        self.status = overall_status(statuses) if self.items else CheckStatus.ERROR


@dataclass(frozen=True)
class PreviewRow:
    term_sheet: Path
    issuer: str | None
    unsupported: bool
    product_code: str | None
    product_code_evidence: tuple[Evidence, ...]
    reference_row: int | None  # 對到的參考條件表列號
    problem: str  # 空字串表示可以核對


@dataclass(frozen=True)
class BatchPreview:
    rows: tuple[PreviewRow, ...]
    warnings: tuple[str, ...]  # 參考條件表欄名問題


# ---------------------------------------------------------------- 辨識（預覽與核對共用）


@dataclass
class _Identified:
    results: list[CheckResult]
    issuer_code: str | None = None
    issuer: Issuer | None = None
    product_code: ParsedField | None = None
    row: ReferenceRow | None = None
    ts: TermSheet | None = None  # 讀出結果：同一份說明書只讀一次，核對直接沿用
    pages: int | None = None

    @property
    def paired(self) -> Paired | None:
        if self.issuer is None or self.ts is None or self.row is None:
            return None
        return Paired(self.issuer, self.ts, self.row)


def _unexpected(e: Exception) -> CheckResult:
    return CheckResult(
        rule_id="batch.unexpected",
        field="說明書",
        status=CheckStatus.ERROR,
        reason_code="unexpected_error",
        message=f"{type(e).__name__}: {e}",
    )


def _review(rule_id: str, field_: str, reason: str, message: str, actual: Any = None) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        field=field_,
        status=CheckStatus.REVIEW_REQUIRED,
        actual=actual,
        reason_code=reason,
        message=message,
    )


def _identify(
    pdf: Path, sheet: ReferenceSheet, rfmt: ReferenceFormat, prefixes: dict[str, str], registry: Sequence[Issuer]
) -> _Identified:
    out = _Identified([])
    results = out.results
    try:
        doc = open_pdf(pdf)
        try:
            lines = extract_lines(doc)
            out.pages = doc.page_count
        finally:
            doc.close()
    except IngestionError as e:
        results.append(error_result("input.term_sheet", "說明書", e))
        return out

    prefix = pdf.name[:3]
    out.issuer_code = prefixes.get(prefix)
    if out.issuer_code is None:
        msg = f"檔名上手編號「{prefix}」不在上手編號對照表，未支援上手"
        results.append(_review("batch.issuer_prefix", "issuer", UNSUPPORTED, msg))
        return out
    issuer = by_code(out.issuer_code, registry)
    if issuer is None:
        code = out.issuer_code
        msg = f"上手編號 {prefix} 對應 {code}，但 {code} 還沒有說明書範本，未支援上手"
        results.append(_review("batch.issuer_prefix", "issuer", UNSUPPORTED, msg))
        return out

    detected, template_result = detect(lines, registry)
    results.append(template_result)
    if detected is None:
        return out
    if detected is not issuer:
        results.append(
            _review(
                "batch.issuer_prefix",
                "issuer",
                "issuer_prefix_mismatch",
                f"檔名上手編號 {prefix} 對應 {issuer.code}，但說明書內容是 {detected.code} 範本，可能檔名取錯或檔案放錯",
                actual=detected.code,
            )
        )
        return out
    out.issuer = issuer
    try:
        out.ts = issuer.read(lines)
    except Exception as e:  # 上手讀出失敗：這份轉執行錯誤，不中斷整批（預覽也一樣）
        results.append(_unexpected(e))
        return out

    pc = out.product_code = read_standard(out.ts, "product_code")
    if not pc.ok:
        results.append(doc_review("batch.pairing", "product_code", pc))
        return out
    if not str(pc.value).startswith(prefix):
        msg = f"說明書商品代號 {pc.value} 的前三碼與檔名上手編號 {prefix} 不同"
        results.append(_review("batch.issuer_prefix", "product_code", "issuer_prefix_mismatch", msg, actual=pc.value))
        return out

    rows = sheet.find(pc.value)
    if not rows:
        msg = f"參考條件表找不到 TDCC Code {pc.value} 的列"
        results.append(_review("batch.pairing", "product_code", "reference_row_missing", msg))
        return out
    if len(rows) > 1:
        msg = f"參考條件表有 {len(rows)} 列 TDCC Code 為 {pc.value}"
        r = _review("batch.pairing", "product_code", "reference_row_duplicate", msg)
        r.order_source = [x.product_code.source for x in rows]
        results.append(r)
        return out
    row = rows[0]
    expected_issuer = rfmt.issuer_values.get(issuer.code)
    if expected_issuer is None or row.issuer.value != expected_issuer:
        r = _review(
            "batch.pairing",
            "issuer",
            "reference_issuer_mismatch",
            f"參考條件表該列發行機構是「{row.issuer.value}」，{issuer.code} 應為「{expected_issuer}」",
            actual=row.issuer.value,
        )
        r.expected, r.order_source = expected_issuer, [row.issuer.source]
        results.append(r)
        return out
    out.row = row
    results.append(
        CheckResult(
            rule_id="batch.pairing",
            field="product_code",
            status=CheckStatus.PASS,
            expected=row.product_code.value,
            actual=pc.value,
            document_evidence=pc.evidence,
            order_source=[row.product_code.source],
            message=f"對應參考條件表第 {row.row} 列",
        )
    )
    return out


def _sheet_warnings(sheet: ReferenceSheet) -> tuple[str, ...]:
    return (
        *(f"未找到欄名：{h}" for h in sheet.missing_columns),
        *(f"未知欄名：{v.value}（{v.source}）" for v in sheet.unknown_columns),
        *(f"重複欄名：{v.value}（{v.source}）" for v in sheet.duplicate_columns),
    )


def _load_sheet(reference_sheet: Path, rfmt: ReferenceFormat) -> ReferenceSheet:
    sheet = load_reference_sheet(reference_sheet, rfmt)
    if RESULT_SHEET in sheet.sheet_names:
        raise IngestionError("reference_result_sheet_exists", f"參考條件表已經有「{RESULT_SHEET}」工作表，請改用原始檔")
    return sheet


def preview_batch(
    term_sheets: Sequence[Path],
    reference_sheet: Path,
    *,
    reference_format: Path = DEFAULT_REFERENCE_FORMAT,
    issuer_prefixes: Path = DEFAULT_ISSUER_PREFIXES,
    registry: Sequence[Issuer] = REGISTRY,
) -> BatchPreview:
    """唯讀預覽；設定檔或參考條件表本身有問題時丟出 IngestionError。"""
    rfmt = load_reference_format(Path(reference_format))
    prefixes = load_issuer_prefixes(Path(issuer_prefixes))
    sheet = _load_sheet(Path(reference_sheet), rfmt)
    rows = []
    for pdf in map(Path, term_sheets):
        found = _identify(pdf, sheet, rfmt, prefixes, registry)
        problems = [r for r in found.results if r.status != CheckStatus.PASS]
        pc = found.product_code
        rows.append(
            PreviewRow(
                pdf,
                found.issuer_code,
                any(r.reason_code == UNSUPPORTED for r in problems),
                pc.value if pc is not None and pc.ok else None,
                tuple(pc.evidence) if pc is not None else (),
                found.row.row if found.row is not None else None,
                "；".join(r.message for r in problems),
            )
        )
    return BatchPreview(tuple(rows), _sheet_warnings(sheet))


# ---------------------------------------------------------------- 核對


def _item_metadata(pdf: Path, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **meta,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "parser": None,
        "inputs": {**meta["inputs"], "term_sheet": file_meta(pdf)},
    }


def _check_one(
    pdf: Path,
    sheet: ReferenceSheet,
    rfmt: ReferenceFormat,
    std: ReviewStandard,
    prefixes: dict[str, str],
    registry: Sequence[Issuer],
    meta: dict[str, Any],
) -> BatchItem:
    metadata = _item_metadata(pdf, meta)
    found = _identify(pdf, sheet, rfmt, prefixes, registry)
    issuer = found.issuer
    if found.pages is not None:
        metadata["inputs"]["term_sheet"]["pages"] = found.pages
    if issuer is not None:
        metadata["parser"] = {"template": issuer.template_id, "version": issuer.parser_version}
    template = issuer.template_id if issuer is not None else None
    report = check_document(found.results, found.paired, sheet, rfmt, std, template, metadata)
    item = BatchItem(pdf, report, issuer=found.issuer_code)
    if found.product_code is not None and found.product_code.ok:
        item.product_code = found.product_code.value
    return item


def check_batch(
    term_sheets: Sequence[Path],
    reference_sheet: Path,
    review_standard: Path,
    *,
    reference_format: Path = DEFAULT_REFERENCE_FORMAT,
    issuer_prefixes: Path = DEFAULT_ISSUER_PREFIXES,
    registry: Sequence[Issuer] = REGISTRY,
) -> BatchOutcome:
    """逐份核對，不寫任何檔案。設定檔或參考條件表本身有問題時，回傳整批錯誤、不核對任何說明書。"""
    reference_sheet = Path(reference_sheet)
    try:
        std = load_review_standard(Path(review_standard))
        rfmt = load_reference_format(Path(reference_format))
        prefixes = load_issuer_prefixes(Path(issuer_prefixes))
        sheet = _load_sheet(reference_sheet, rfmt)
    except IngestionError as e:
        return BatchOutcome(CheckStatus.ERROR, [], reference_sheet, errors=[error_result("input.batch", "批量輸入", e)])

    meta = {
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "inputs": {"reference_sheet": file_meta(reference_sheet)},
        "review_standard": {**file_meta(Path(review_standard)), "version": std.version},
        "reference_format": {**file_meta(Path(reference_format)), "version": rfmt.version},
    }
    items: list[BatchItem] = []
    for pdf in map(Path, term_sheets):
        try:
            item = _check_one(pdf, sheet, rfmt, std, prefixes, registry, meta)
        except Exception as e:  # 單份非預期錯誤不中斷整批
            item = BatchItem(pdf, CheckReport(CheckStatus.ERROR, None, [_unexpected(e)], [], _item_metadata(pdf, meta)))
        items.append(item)
    outcome = BatchOutcome(
        CheckStatus.ERROR, items, reference_sheet, rfmt, reference_sha256=meta["inputs"]["reference_sheet"]["sha256"]
    )
    outcome.refresh_status()
    return outcome


# ---------------------------------------------------------------- 儲存


def _summary(report: CheckReport) -> tuple[int, str]:
    problems = [r for r in report.results if r.status.is_problem]
    parts = [f"{r.rule_id} {r.field}：{r.message or r.reason_code}" for r in problems[:5]]
    if len(problems) > 5:
        parts.append(f"等 {len(problems)} 項")
    return len(problems), "；".join(parts)


def _result_sheet(wb: Workbook, items: list[BatchItem], filled: list[bool]) -> None:
    ws = wb.create_sheet(RESULT_SHEET)
    ws.append(RESULT_HEADERS)
    for item, was_filled in zip(items, filled, strict=True):
        n, summary = _summary(item.report)
        md = next((p for p in item.report_paths if p.suffix == ".md"), None)
        ws.append(
            (
                item.term_sheet.name,
                item.product_code,
                item.issuer,
                item.status_label,
                n,
                summary,
                "是" if was_filled else "否",
                md.name if md else None,
            )
        )


def output_path(reference_sheet: Path, now: dt.datetime) -> Path:
    return reference_sheet.with_name(f"{reference_sheet.stem}_回填_{now:%Y%m%d-%H%M%S}.xlsx")


def save_batch(outcome: BatchOutcome, reports_dir: Path, *, now: dt.datetime | None = None) -> BatchOutcome:
    """寫每份報告（不覆蓋）、回填新檔與「核對結果」工作表；逐份記錄失敗，不清除核對結果。"""
    if outcome.reference_format is None or not outcome.items:
        return outcome
    now = now or dt.datetime.now()
    outcome.output = None  # 同一份結果可以再儲存一次：清掉上一次的儲存狀態
    outcome.errors = [e for e in outcome.errors if not e.rule_id.startswith("output.")]
    for item in outcome.items:
        item.filled, item.report_paths, item.save_error = False, (), ""
        stem = f"{item.term_sheet.stem.replace(' ', '')}_{now:%Y%m%d-%H%M%S}"
        try:
            item.report_paths = write_reports(item.report, Path(reports_dir), stem)
        except OSError as e:
            item.save_error = str(e)
    try:
        wb = backfill.open_reference(outcome.reference_sheet, outcome.reference_sha256)
    except IngestionError as e:
        outcome.errors.append(error_result("output.reference_sheet", "回填新檔", e))
    else:
        filled = backfill.apply(wb, outcome.reference_format, [i.report for i in outcome.items])
        _result_sheet(wb, outcome.items, filled)
        out = output_path(outcome.reference_sheet, now)
        buf = io.BytesIO()
        wb.save(buf)
        try:
            backfill.write_new(buf.getvalue(), out)
        except IngestionError as e:
            outcome.errors.append(error_result("output.reference_sheet", "回填新檔", e))
        else:
            outcome.output = out
            for item, ok in zip(outcome.items, filled, strict=True):
                item.filled = ok
    outcome.saved = True
    outcome.refresh_status()
    return outcome


def run_batch(
    term_sheets: Sequence[Path],
    reference_sheet: Path,
    review_standard: Path,
    reports_dir: Path,
    *,
    reference_format: Path = DEFAULT_REFERENCE_FORMAT,
    issuer_prefixes: Path = DEFAULT_ISSUER_PREFIXES,
    registry: Sequence[Issuer] = REGISTRY,
    now: dt.datetime | None = None,
) -> BatchOutcome:
    """核對後立即儲存（CLI 使用）。"""
    outcome = check_batch(
        term_sheets,
        reference_sheet,
        review_standard,
        reference_format=reference_format,
        issuer_prefixes=issuer_prefixes,
        registry=registry,
    )
    return save_batch(outcome, reports_dir, now=now)
