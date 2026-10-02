"""批量核對入口：多份說明書 PDF × 參考條件表 → 逐份核對報告＋回填後的新檔（ADR 0004）。

流程（每份說明書）：
1. 檔名前三碼（上手編號）查上手編號對照 → 上手；不在對照表或上手沒有範本 → 未支援上手。
2. 說明書內容辨識出的上手、說明書商品代號前三碼都必須與檔名一致，否則轉人工覆核。
3. 以商品代號找參考條件表的列（TDCC Code），該列發行機構必須是此上手的寫法。
4. 表上事先填好的欄位逐一核對；ISIN 與比價日依說明書決定回填或比對。

只有整份核對 PASS 的說明書才回填；結果另存新檔（原檔不動、不覆蓋既有檔案），並新增「核對結果」工作表。
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

from . import __version__
from .checker import CheckReport, _detect, _error, _file_meta
from .config import (
    ReferenceFormat,
    ReviewStandard,
    load_issuer_prefixes,
    load_reference_format,
    load_review_standard,
)
from .extraction import extract_lines
from .ingestion import IngestionError, open_pdf
from .issuers import REGISTRY, Issuer, by_code
from .orders.reference import ReferenceSheet, load_reference_sheet
from .reporting import STATUS_ZH, write_reports
from .rules import reference
from .rules.common import doc_review, order_format_checks
from .schema import CheckResult, CheckStatus, Line, overall_status

RESULT_SHEET = "核對結果"
UNSUPPORTED = "issuer_unsupported"
PROBLEMS = (CheckStatus.ERROR, CheckStatus.MISMATCH, CheckStatus.REVIEW_REQUIRED)
RESULT_HEADERS = ("PDF 檔名", "商品代號", "上手", "整體狀態", "問題數", "問題摘要", "已回填", "報告檔名")
FALLBACK_DATE_FORMAT = "yyyy/m/d"


@dataclass
class BatchItem:
    term_sheet: Path
    report: CheckReport
    issuer: str | None = None
    product_code: str | None = None
    filled: bool = False
    report_paths: tuple[Path, ...] = ()

    @property
    def unsupported(self) -> bool:
        return any(r.reason_code == UNSUPPORTED for r in self.report.results)


@dataclass
class BatchOutcome:
    status: CheckStatus
    items: list[BatchItem]
    output: Path | None  # 回填後的新檔；整批錯誤時為 None
    errors: list[CheckResult] = field(default_factory=list)  # 整批錯誤（設定檔、參考條件表、寫檔）


def _review(rule_id: str, field_: str, reason: str, message: str, actual: Any = None) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        field=field_,
        status=CheckStatus.REVIEW_REQUIRED,
        actual=actual,
        reason_code=reason,
        message=message,
    )


def _item_metadata(pdf: Path, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **meta,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "parser": None,
        "inputs": {**meta["inputs"], "term_sheet": _file_meta(pdf)},
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
    results: list[CheckResult] = []
    item = BatchItem(pdf, CheckReport(CheckStatus.ERROR, None, results, [], metadata))

    def done() -> BatchItem:
        item.report.status = overall_status([r.status for r in results]) if results else CheckStatus.ERROR
        return item

    try:
        doc = open_pdf(pdf)
        try:
            lines: list[Line] = extract_lines(doc)
            metadata["inputs"]["term_sheet"]["pages"] = doc.page_count
        finally:
            doc.close()
    except IngestionError as e:
        results.append(_error("input.term_sheet", "說明書", e))
        return done()

    prefix = pdf.name[:3]
    code = prefixes.get(prefix)
    if code is None:
        results.append(
            _review(
                "batch.issuer_prefix", "issuer", UNSUPPORTED, f"檔名上手編號「{prefix}」不在上手編號對照表，未支援上手"
            )
        )
        return done()
    item.issuer = code
    issuer = by_code(code, registry)
    if issuer is None:
        results.append(
            _review(
                "batch.issuer_prefix",
                "issuer",
                UNSUPPORTED,
                f"上手編號 {prefix} 對應 {code}，但 {code} 還沒有說明書範本，未支援上手",
            )
        )
        return done()

    detected, template_result = _detect(lines, registry)
    results.append(template_result)
    if detected is None:
        return done()
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
        return done()
    metadata["parser"] = {"template": issuer.template_id, "version": issuer.parser_version}
    item.report.template = issuer.template_id

    pc = issuer.product_code(lines)
    if not pc.ok:
        results.append(doc_review("batch.pairing", "product_code", pc))
        return done()
    item.product_code = pc.value
    if not str(pc.value).startswith(prefix):
        results.append(
            _review(
                "batch.issuer_prefix",
                "product_code",
                "issuer_prefix_mismatch",
                f"說明書商品代號 {pc.value} 的前三碼與檔名上手編號 {prefix} 不同",
                actual=pc.value,
            )
        )
        return done()

    rows = sheet.find(pc.value)
    if not rows:
        results.append(
            _review(
                "batch.pairing", "product_code", "reference_row_missing", f"參考條件表找不到 TDCC Code {pc.value} 的列"
            )
        )
        return done()
    if len(rows) > 1:
        r = _review(
            "batch.pairing",
            "product_code",
            "reference_row_duplicate",
            f"參考條件表有 {len(rows)} 列 TDCC Code 為 {pc.value}",
        )
        r.order_source = [x.product_code.source for x in rows]
        results.append(r)
        return done()
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
        return done()
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

    record = sheet.record(row)
    results.extend(order_format_checks(record))
    _, ts = issuer.parse(lines)
    ctx = issuer.context(ts, record, std, rfmt)
    results.extend(issuer.reference_rules(ctx))
    sched = issuer.autocall_schedule(ts)
    results.append(reference.first_callable_period(ctx, sched))
    isin_result, isin_cells = reference.isin(ctx, rfmt, row, issuer.isin(ts))
    dates_result, date_cells = reference.compare_dates(ctx, rfmt, row, sched)
    results.extend([isin_result, dates_result])
    item.report.backfill = isin_cells + date_cells
    item.report.not_covered = [dict(n) for n in issuer.reference_not_covered]
    return done()


def _open_for_writing(path: Path) -> Workbook:
    try:
        wb = openpyxl.load_workbook(path)  # 不用 data_only：保留公式與格式
    except Exception as e:
        raise IngestionError("reference_unreadable", f"參考條件表無法開啟（可能已損毀或不是 Excel）：{e}") from e
    if RESULT_SHEET in wb.sheetnames:
        raise IngestionError("reference_result_sheet_exists", f"參考條件表已經有「{RESULT_SHEET}」工作表，請改用原始檔")
    return wb


def _date_format(ws: Any, rfmt: ReferenceFormat) -> str:
    """沿用表上既有日期格的顯示格式。"""
    for row in ws.iter_rows(min_row=rfmt.first_data_row):
        for c in row:
            if isinstance(c.value, dt.datetime):
                return c.number_format
    return FALLBACK_DATE_FORMAT


def _summary(report: CheckReport) -> tuple[int, str]:
    problems = [r for r in report.results if r.status in PROBLEMS]
    parts = [f"{r.rule_id} {r.field}：{r.message or r.reason_code}" for r in problems[:5]]
    if len(problems) > 5:
        parts.append(f"等 {len(problems)} 項")
    return len(problems), "；".join(parts)


def _status_label(item: BatchItem) -> str:
    if item.unsupported:
        return "未支援上手"
    return f"{item.report.status.value}（{STATUS_ZH[item.report.status]}）"


def _fill(wb: Workbook, rfmt: ReferenceFormat, items: list[BatchItem]) -> list[BatchItem]:
    ws = wb[rfmt.sheet]
    fmt = _date_format(ws, rfmt)
    filled = []
    for item in items:
        if item.report.status != CheckStatus.PASS:
            continue
        for d in item.report.backfill:
            if d.action != "fill":
                continue
            cell = ws[d.cell]
            cell.value = d.expected
            if isinstance(d.expected, dt.date):
                cell.number_format = fmt
        filled.append(item)
    return filled


def _result_sheet(wb: Workbook, items: list[BatchItem], filled: list[BatchItem]) -> None:
    ws = wb.create_sheet(RESULT_SHEET)
    ws.append(RESULT_HEADERS)
    for item in items:
        n, summary = _summary(item.report)
        ws.append(
            (
                item.term_sheet.name,
                item.product_code,
                item.issuer,
                _status_label(item),
                n,
                summary,
                "是" if item in filled else "否",
                item.report_paths[-1].name if item.report_paths else None,
            )
        )


def output_path(reference_sheet: Path, now: dt.datetime) -> Path:
    return reference_sheet.with_name(f"{reference_sheet.stem}_回填_{now:%Y%m%d-%H%M%S}.xlsx")


def run_batch(
    term_sheets: Sequence[Path],
    reference_sheet: Path,
    review_standard: Path,
    reports_dir: Path,
    *,
    reference_format: Path = Path("config/reference_sheet.toml"),
    issuer_prefixes: Path = Path("config/issuer_prefixes.toml"),
    registry: Sequence[Issuer] = REGISTRY,
    now: dt.datetime | None = None,
) -> BatchOutcome:
    reference_sheet = Path(reference_sheet)
    now = now or dt.datetime.now()
    try:
        std = load_review_standard(Path(review_standard))
        rfmt = load_reference_format(Path(reference_format))
        prefixes = load_issuer_prefixes(Path(issuer_prefixes))
        sheet = load_reference_sheet(reference_sheet, rfmt)
        wb = _open_for_writing(reference_sheet)
    except IngestionError as e:
        return BatchOutcome(CheckStatus.ERROR, [], None, [_error("input.batch", "批量輸入", e)])

    meta = {
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "inputs": {"reference_sheet": _file_meta(reference_sheet)},
        "review_standard": {**_file_meta(Path(review_standard)), "version": std.version},
        "reference_format": {**_file_meta(Path(reference_format)), "version": rfmt.version},
    }
    items: list[BatchItem] = []
    for pdf in map(Path, term_sheets):
        try:
            item = _check_one(pdf, sheet, rfmt, std, prefixes, registry, meta)
        except Exception as e:  # 單份非預期錯誤不中斷整批
            err = CheckResult(
                rule_id="batch.unexpected",
                field="說明書",
                status=CheckStatus.ERROR,
                reason_code="unexpected_error",
                message=f"{type(e).__name__}: {e}",
            )
            item = BatchItem(pdf, CheckReport(CheckStatus.ERROR, None, [err], [], _item_metadata(pdf, meta)))
        item.report_paths = write_reports(item.report, Path(reports_dir), pdf.stem.replace(" ", ""))
        items.append(item)

    filled = _fill(wb, rfmt, items)
    _result_sheet(wb, items, filled)
    out = output_path(reference_sheet, now)
    errors: list[CheckResult] = []
    buf = io.BytesIO()
    wb.save(buf)
    try:
        with out.open("xb") as f:
            f.write(buf.getvalue())
    except OSError as e:
        errors.append(
            _error(
                "output.reference_sheet", "回填新檔", IngestionError("output_exists", f"無法寫入新檔 {out.name}：{e}")
            )
        )
        out, filled = None, []
    for item in filled:
        item.filled = True
    status = (
        overall_status([i.report.status for i in items] + [e.status for e in errors]) if items else CheckStatus.ERROR
    )
    return BatchOutcome(status, items, out, errors)
