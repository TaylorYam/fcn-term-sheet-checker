"""批量核對入口：多份說明書 PDF × 參考條件表 → 逐份核對結果、核對結果檔與核對紀錄（ADR 0004）。

分三段，CLI 與 PANEL 共用：

- `preview_batch`：唯讀辨識每份說明書（上手、商品代號、對到的參考條件表列），不核對、不寫檔。
- `check_batch`：逐份核對，回傳結果與回填決策，不寫任何檔案。
- `save_batch`：寫核對結果檔（result_file.py）與根目錄的核對紀錄（reporting.py）。`run_batch` = 核對 ＋ 儲存（CLI 使用）。

辨識流程（每份說明書）：
1. 檔名前三碼（上手編號）查上手編號對照 → 上手；不在對照表或上手沒有範本 → 未支援上手。
2. 說明書內容辨識出的上手、說明書商品代號前三碼都必須與檔名一致，否則轉人工覆核。
3. 以商品代號找參考條件表的列（TDCC Code），該列發行機構必須是此上手的寫法。
4. 同一批有多份說明書對到同一列時，這幾份全部轉人工覆核，不核對也不回填。

範本辨識與讀出每份說明書各只做一次；配對成功後由單份核對（single_check.py）依序執行所有規則。
批量入口只負責載入設定與參考條件表、逐份呼叫、單份錯誤隔離與儲存。

只有整份核對 PASS 或人工放行（PANEL）的說明書才回填；回填結果只寫進核對結果檔（原檔不動、不覆蓋既有檔案）。回填流程見 backfill.py。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import fitz
import openpyxl

from . import __version__, backfill, reporting, result_file
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
from .messages import STATUS_ZH, problem_message
from .orders.reference import ReferenceRow, ReferenceSheet, load_reference_sheet
from .rules.common import doc_review, read_standard
from .schema import CheckReport, CheckResult, CheckStatus, Evidence, ParsedField, overall_status
from .single_check import Paired, check_document
from .standard_fields import TermSheet

UNSUPPORTED = "issuer_unsupported"
SHARED_ROW = "reference_row_shared"
RELEASABLE = (CheckStatus.MISMATCH, CheckStatus.REVIEW_REQUIRED)  # 可以人工放行的原判定
T = TypeVar("T")
DEFAULT_REFERENCE_FORMAT = Path("config/reference_sheet.toml")
DEFAULT_ISSUER_PREFIXES = Path("config/issuer_prefixes.toml")


@dataclass
class BatchItem:
    term_sheet: Path
    report: CheckReport
    issuer: str | None = None
    product_code: str | None = None
    reference_row: int | None = None  # 對到的參考條件表列號
    filled: bool = False
    released: bool = False  # 人工放行（只有 PANEL 會設定）；原判定仍在 report

    @property
    def unsupported(self) -> bool:
        return any(r.reason_code == UNSUPPORTED for r in self.report.results)

    @property
    def status(self) -> CheckStatus:
        """有效狀態：人工放行視同 PASS。"""
        return CheckStatus.PASS if self.released else self.report.status

    @property
    def fillable(self) -> bool:
        """整份通過或人工放行才回填。"""
        return self.released or backfill.fillable(self.report)

    @property
    def status_label(self) -> str:
        if self.unsupported:
            return "未支援上手"
        if self.released:
            return f"人工放行（原：{STATUS_ZH[self.report.status]}）"
        return f"{self.report.status.value}（{STATUS_ZH[self.report.status]}）"

    @property
    def release_problem(self) -> str:
        """不能人工放行的原因；空字串表示可以放行。只有回填值確定且不和參考條件表打架時才能放行。"""
        report = self.report
        if report.status == CheckStatus.PASS:
            return "已經通過，不需要人工放行"
        if self.unsupported:
            return "未支援上手，沒有可以回填的值"
        if report.status not in RELEASABLE:
            return "執行錯誤，沒有可以回填的值"
        if any(r.reason_code == SHARED_ROW for r in report.results):
            return "同一批有多份說明書對到同一列，不能人工放行"
        if self.reference_row is None or not report.backfill:
            return "沒有對到參考條件表的列，沒有地方可以回填"
        if any(d.action == backfill.BackfillAction.MISMATCH for d in report.backfill):
            return "參考條件表回填欄位已有不同的值，請先修正參考條件表再核對"
        if any(r.rule_id.startswith("backfill.") and r.status != CheckStatus.PASS for r in report.results):
            return "回填值無法確定，請人工處理"
        return ""


@dataclass
class BatchOutcome:
    status: CheckStatus
    items: list[BatchItem]
    reference_sheet: Path
    reference_format: ReferenceFormat | None = None
    output: Path | None = None  # 核對結果檔；尚未儲存或儲存失敗時為 None
    errors: list[CheckResult] = field(default_factory=list)  # 整批錯誤（設定檔、參考條件表、寫檔）
    saved: bool = False
    reference_sha256: str | None = None  # 核對時參考條件表的 hash；儲存前據此確認檔案未變更
    metadata: dict[str, Any] = field(default_factory=dict)  # 整批執行 metadata（程式版本、設定檔與參考條件表 hash）
    record: Path | None = None  # 核對紀錄；尚未儲存或寫入失敗時為 None

    def refresh_status(self) -> None:
        statuses = [i.status for i in self.items] + [e.status for e in self.errors]
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
    pdf: Path
    results: list[CheckResult]
    issuer_code: str | None = None
    issuer: Issuer | None = None
    product_code: ParsedField | None = None
    row: ReferenceRow | None = None
    ts: TermSheet | None = None  # 讀出結果：同一份說明書只讀一次，核對直接沿用
    pages: int | None = None
    shared: bool = False  # 同一批有其他說明書對到同一列

    @property
    def row_no(self) -> int | None:
        return self.row.row if self.row is not None else None

    @property
    def paired(self) -> Paired | None:
        if self.issuer is None or self.ts is None or self.row is None or self.shared:
            return None
        return Paired(self.issuer, self.ts, self.row)

    def mark_shared(self, row_no: int, others: str) -> None:
        """同一批有其他說明書對到同一列：配對改為人工覆核，不核對也不回填。"""
        pairing = next(r for r in self.results if r.rule_id == "batch.pairing" and r.status == CheckStatus.PASS)
        pairing.status, pairing.reason_code = CheckStatus.REVIEW_REQUIRED, SHARED_ROW
        pairing.message = (
            f"同一批有多份說明書對到同一個 TDCC Code {pairing.actual}（參考條件表第 {row_no} 列），其他說明書：{others}"
        )
        self.shared = True


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
    out = _Identified(pdf, [])
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


def _other_names(found: _Identified, group: Sequence[_Identified]) -> str:
    """同一列其他說明書的檔名；檔名相同時改列完整路徑，同一個檔案選了兩次時註明。"""
    names = [f.pdf.name for f in group]
    labels = []
    for f in group:
        if f is found:
            continue
        if f.pdf.resolve() == found.pdf.resolve():
            labels.append(f"{f.pdf.name}（同一個檔案重複選取）")
        else:
            labels.append(str(f.pdf) if names.count(f.pdf.name) > 1 else f.pdf.name)
    return "、".join(labels)


def _identify_all(
    term_sheets: Sequence[Path],
    sheet: ReferenceSheet,
    rfmt: ReferenceFormat,
    prefixes: dict[str, str],
    registry: Sequence[Issuer],
) -> list[_Identified]:
    """先辨識全部說明書，同一批有多份對到參考條件表同一列時全部轉人工覆核；單份非預期錯誤不中斷整批。"""
    identified = []
    for pdf in map(Path, term_sheets):
        try:
            found = _identify(pdf, sheet, rfmt, prefixes, registry)
        except Exception as e:
            found = _Identified(pdf, [_unexpected(e)])
        identified.append(found)
    by_row: dict[int, list[_Identified]] = {}
    for found in identified:
        if found.row is not None:
            by_row.setdefault(found.row.row, []).append(found)
    for row_no, group in by_row.items():
        if len(group) > 1:
            for found in group:
                found.mark_shared(row_no, _other_names(found, group))
    return identified


def _sheet_warnings(sheet: ReferenceSheet) -> tuple[str, ...]:
    return (
        *(f"未找到欄名：{h}" for h in sheet.missing_columns),
        *(f"未知欄名：{v.value}（{v.source}）" for v in sheet.unknown_columns),
        *(f"重複欄名：{v.value}（{v.source}）" for v in sheet.duplicate_columns),
    )


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
    sheet = load_reference_sheet(Path(reference_sheet), rfmt)
    rows = []
    for found in _identify_all(term_sheets, sheet, rfmt, prefixes, registry):
        problems = [r for r in found.results if r.status != CheckStatus.PASS]
        pc = found.product_code
        rows.append(
            PreviewRow(
                found.pdf,
                found.issuer_code,
                any(r.reason_code == UNSUPPORTED for r in problems),
                pc.value if pc is not None and pc.ok else None,
                tuple(pc.evidence) if pc is not None else (),
                found.row_no,
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
    found: _Identified,
    sheet: ReferenceSheet,
    rfmt: ReferenceFormat,
    std: ReviewStandard,
    meta: dict[str, Any],
) -> BatchItem:
    pdf = found.pdf
    metadata = _item_metadata(pdf, meta)
    issuer = found.issuer
    if found.pages is not None:
        metadata["inputs"]["term_sheet"]["pages"] = found.pages
    if issuer is not None:
        metadata["parser"] = {"template": issuer.template_id, "version": issuer.parser_version}
    template = issuer.template_id if issuer is not None else None
    report = check_document(found.results, found.paired, sheet, rfmt, std, template, metadata)
    item = BatchItem(pdf, report, issuer=found.issuer_code, reference_row=found.row_no)
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
        sheet = load_reference_sheet(reference_sheet, rfmt)
    except IngestionError as e:
        return BatchOutcome(CheckStatus.ERROR, [], reference_sheet, errors=[error_result("input.batch", "批量輸入", e)])

    meta = {
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "inputs": {"reference_sheet": file_meta(reference_sheet)},
        "review_standard": {**file_meta(Path(review_standard)), "version": std.version},
        "reference_format": {**file_meta(Path(reference_format)), "version": rfmt.version},
        "issuer_prefixes": file_meta(Path(issuer_prefixes)),
    }
    items: list[BatchItem] = []
    for found in _identify_all(term_sheets, sheet, rfmt, prefixes, registry):
        try:
            item = _check_one(found, sheet, rfmt, std, meta)
        except Exception as e:  # 單份非預期錯誤不中斷整批
            pdf = found.pdf
            item = BatchItem(pdf, CheckReport(CheckStatus.ERROR, None, [_unexpected(e)], [], _item_metadata(pdf, meta)))
        items.append(item)
    outcome = BatchOutcome(
        CheckStatus.ERROR,
        items,
        reference_sheet,
        rfmt,
        reference_sha256=meta["inputs"]["reference_sheet"]["sha256"],
        metadata=meta,
    )
    outcome.refresh_status()
    return outcome


# ---------------------------------------------------------------- 儲存


def _error_row(item: BatchItem) -> result_file.ErrorRow:
    """錯誤清單的一列。TDCC Code 取說明書封面商品代號；取不到時用檔名前 12 碼（12 位數字才算），否則留白。"""
    head = item.term_sheet.name[:12]
    code = item.product_code or (head if re.fullmatch(r"[0-9]{12}", head) else None)
    problems = [r for r in item.report.results if r.status.is_problem]
    messages = dict.fromkeys(problem_message(r) for r in problems)  # 同一句錯訊只列一次
    return result_file.ErrorRow(code, item.term_sheet.name, "\n".join(messages))


def save_batch(outcome: BatchOutcome, out_dir: Path, *, root: Path, now: dt.datetime | None = None) -> BatchOutcome:
    """確認參考條件表與核對時相同後，寫核對結果檔到 out_dir、核對紀錄到 root/runtime/核對紀錄（都不覆蓋）。

    root 是根目錄（CLI 為執行目錄、PANEL 為安裝根目錄）。核對紀錄寫入失敗只記成整批錯誤，不影響核對結果檔；
    寫檔失敗不清除核對結果，可以再儲存一次。
    """
    if outcome.reference_format is None or not outcome.items:
        return outcome
    now = now or dt.datetime.now()
    outcome.output = outcome.record = None  # 同一份結果可以再儲存一次：清掉上一次的儲存狀態
    outcome.errors = [e for e in outcome.errors if not e.rule_id.startswith("output.")]
    for item in outcome.items:
        item.filled = False
    outcome.saved = True
    rfmt, items = outcome.reference_format, outcome.items
    wb = _attempt(
        outcome,
        "output.result_file",
        "核對結果檔",
        lambda: backfill.open_reference(outcome.reference_sheet, outcome.reference_sha256),
    )
    if wb is not None:  # 參考條件表核對後被改過（或讀不到）時，核對結果檔與核對紀錄都不寫
        filled = [i.fillable for i in items]
        backfill.apply(wb, rfmt, [i.report for i in items if i.fillable])
        keep = [i.reference_row for i in items if i.fillable and i.reference_row]
        errors = [_error_row(i) for i in items if not i.fillable]
        out = result_file.output_path(Path(out_dir), outcome.reference_sheet, now)
        outcome.output = _attempt(
            outcome, "output.result_file", "核對結果檔", lambda: result_file.save(wb, rfmt, keep, errors, out)
        )
        if outcome.output is not None:
            for item, ok in zip(items, filled, strict=True):
                item.filled = ok
        outcome.refresh_status()
        outcome.record = _attempt(
            outcome, "output.record", "核對紀錄", lambda: reporting.save_record(outcome, Path(root), now)
        )
    outcome.refresh_status()
    return outcome


def _attempt(outcome: BatchOutcome, rule_id: str, what: str, step: Callable[[], T]) -> T | None:
    """儲存的一步；失敗（IngestionError）時記成整批錯誤並回傳 None，不中斷其他輸出。"""
    try:
        return step()
    except IngestionError as e:
        outcome.errors.append(error_result(rule_id, what, e))
        return None


def run_batch(
    term_sheets: Sequence[Path],
    reference_sheet: Path,
    review_standard: Path,
    out_dir: Path,
    *,
    root: Path,
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
    return save_batch(outcome, out_dir, root=root, now=now)
