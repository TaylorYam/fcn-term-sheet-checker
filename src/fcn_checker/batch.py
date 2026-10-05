"""批量核對入口：多份說明書 PDF × 參考條件表 → 逐份核對結果、核對結果檔與核對紀錄（ADR 0004）。

分三段，CLI 與 PANEL 共用：

- `preview_batch(核對設定, 參考條件表, 說明書)`：讀取前取來源快照，唯讀辨識並讀出每份說明書（上手、商品代號、
  對到的參考條件表列），不核對、不寫檔。
- `check_batch(預覽)`：確認來源快照仍有效後，沿用預覽的辨識與讀出逐份核對（不重新讀 PDF），不寫任何檔案。
- `save_batch`：寫核對結果檔（result_file.py）與根目錄的核對紀錄（reporting.py）。
- `run_batch` = 預覽 ＋ 核對 ＋ 儲存（CLI 使用）。

設定（審查標準、參考條件表格式、上手編號對照、上手註冊表）由呼叫端載入成一個核對設定（check_config.py）傳入。

辨識流程（每份說明書）：
1. 檔名前三碼（上手編號）查上手編號對照 → 上手；不在對照表或上手沒有範本 → 未支援上手。
2. 說明書內容辨識出的上手、說明書商品代號前三碼都必須與檔名一致，否則轉人工覆核。
3. 以商品代號找參考條件表的列（TDCC Code），該列發行機構必須是此上手的寫法。
4. 同一批有多份說明書對到同一列時，這幾份全部轉人工覆核，不核對也不回填。

範本辨識與讀出每份說明書各只做一次（在預覽）；配對成功後由單份核對（single_check.py）依序執行所有規則。
批量入口只負責載入參考條件表、逐份呼叫、組裝記錄資料、單份錯誤隔離與儲存。

只有整份核對 PASS 或人工放行（PANEL）的說明書才回填；回填結果只寫進核對結果檔（原檔不動、不覆蓋既有檔案）。回填流程見 backfill.py。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

import fitz
import openpyxl
from openpyxl.workbook.workbook import Workbook

from . import __version__, backfill, reporting, result_file
from .check_config import CheckConfig
from .config import ReferenceFormat
from .extraction import extract_lines
from .ingestion import IngestionError, SourceSnapshot, error_result, open_pdf
from .issuers import Issuer, by_code, detect
from .messages import STATUS_ZH, problem_message
from .orders.reference import ReferenceRow, ReferenceSheet, load_reference_sheet
from .rules.kit import doc_review, read_standard
from .schema import CheckReport, CheckResult, CheckStatus, Evidence, Item, ParsedField, overall_status
from .single_check import Paired, check_document
from .standard_fields import TermSheet

UNSUPPORTED = "issuer_unsupported"
SHARED_ROW = "reference_row_shared"
T = TypeVar("T")


class Category(StrEnum):
    """每份說明書在整批中的類別（PANEL 標題份數、狀態標籤、儲存時回填或列入錯誤清單都依它）。"""

    PASSED = "通過"
    RELEASED = "人工放行"
    MISMATCH = "不一致"
    REVIEW = "需人工覆核"
    UNSUPPORTED = "未支援上手"
    ERROR = "執行錯誤"

    @property
    def fillable(self) -> bool:
        """整份通過或人工放行才回填，其餘列入錯誤清單。"""
        return self in (Category.PASSED, Category.RELEASED)


_BY_STATUS = {
    CheckStatus.PASS: Category.PASSED,
    CheckStatus.MISMATCH: Category.MISMATCH,
    CheckStatus.REVIEW_REQUIRED: Category.REVIEW,
    CheckStatus.ERROR: Category.ERROR,
}


@dataclass
class BatchItem:
    term_sheet: Path
    report: CheckReport
    issuer: str | None = None
    product_code: str | None = None
    reference_row: int | None = None  # 對到的參考條件表列號
    filled: bool = False
    _released: bool = field(default=False, init=False, repr=False)  # 只能經 BatchOutcome.release／cancel_release 改變

    @property
    def released(self) -> bool:
        """人工放行（PANEL）；原判定仍在 report。"""
        return self._released

    @property
    def unsupported(self) -> bool:
        return any(r.reason_code == UNSUPPORTED for r in self.report.results)

    @property
    def category(self) -> Category:
        if self.unsupported:
            return Category.UNSUPPORTED
        if self.released:
            return Category.RELEASED
        return _BY_STATUS[self.report.status]

    @property
    def status(self) -> CheckStatus:
        """有效狀態：人工放行視同 PASS。"""
        return CheckStatus.PASS if self.released else self.report.status

    @property
    def fillable(self) -> bool:
        return self.category.fillable

    @property
    def status_label(self) -> str:
        category, original = self.category, STATUS_ZH[self.report.status]
        if category == Category.UNSUPPORTED:
            return category.value
        if category == Category.RELEASED:
            return f"{category.value}（原：{original}）"
        return f"{self.report.status.value}（{original}）"

    @property
    def problem_messages(self) -> tuple[str, ...]:
        """這份說明書的錯訊，同一句只列一次（錯誤清單與 PANEL 放行確認共用）。"""
        return tuple(dict.fromkeys(problem_message(r) for r in self.report.results if r.status.is_problem))

    @property
    def release_problem(self) -> str:
        """不能人工放行的原因；空字串表示可以放行。只有回填值確定且不和參考條件表打架時才能放行。"""
        report, category = self.report, self.category  # 已人工放行的仍依原判定檢查（可重複放行）
        if category == Category.PASSED:
            return "已經通過，不需要人工放行"
        if category == Category.UNSUPPORTED:
            return "未支援上手，沒有可以回填的值"
        if category == Category.ERROR:
            return "執行錯誤，沒有可以回填的值"
        if any(r.reason_code == SHARED_ROW for r in report.results):
            return "同一批有多份說明書對到同一列，不能人工放行"
        if self.reference_row is None:
            return "沒有對到參考條件表的列，沒有地方可以回填"
        if backfill.conflicts_with_sheet(report):
            return "參考條件表回填欄位已有不同的值，請先修正參考條件表再核對"
        if not backfill.values_certain(report):
            return "回填值無法確定，請人工處理"
        return ""


@dataclass
class BatchOutcome:
    items: list[BatchItem]
    reference_sheet: Path
    reference_format: ReferenceFormat | None = None
    output: Path | None = None  # 核對結果檔；尚未儲存或儲存失敗時為 None
    errors: list[CheckResult] = field(default_factory=list)  # 整批錯誤（設定檔、參考條件表、寫檔）
    snapshot: SourceSnapshot | None = None  # 核對前取的來源快照；核對紀錄的 hash 取自它，儲存前據此確認來源未變更
    metadata: dict[str, Any] = field(default_factory=dict)  # 整批執行 metadata（程式版本、設定檔與參考條件表 hash）
    record: Path | None = None  # 核對紀錄；尚未儲存或寫入失敗時為 None

    @property
    def status(self) -> CheckStatus:
        """整批狀態：每份的有效狀態（人工放行視同 PASS）與整批錯誤。"""
        if not self.items:
            return CheckStatus.ERROR
        return overall_status([i.status for i in self.items] + [e.status for e in self.errors])

    def release(self, item: BatchItem) -> None:
        """人工放行：視同通過，儲存時回填、不列入錯誤清單；不能放行時丟出 IngestionError（原因見 release_problem）。"""
        self._require_member(item)
        if item.release_problem:
            raise IngestionError("release_refused", item.release_problem)
        self._set_released(item, True)

    def cancel_release(self, item: BatchItem) -> None:
        self._require_member(item)
        self._set_released(item, False)

    def _require_member(self, item: BatchItem) -> None:
        if not any(i is item for i in self.items):
            raise IngestionError("result_required", "這份說明書不在這次核對結果中，請重新核對後再人工放行。")

    def _set_released(self, item: BatchItem, released: bool) -> None:
        if item.released != released:
            item._released = released
            self.clear_saved()  # 上一次儲存的核對結果檔不再是目前的結果，要再儲存一次

    def clear_saved(self) -> None:
        """清掉上一次儲存的狀態（核對結果檔、核對紀錄、已回填、寫檔錯誤）。"""
        self.output = self.record = None
        self.errors = [e for e in self.errors if not e.rule_id.startswith("output.")]
        for item in self.items:
            item.filled = False


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
    """預覽：每份說明書的辨識結果；另帶核對要沿用的核對設定、參考條件表、讀出結果與來源快照。"""

    rows: tuple[PreviewRow, ...]
    warnings: tuple[str, ...]  # 參考條件表欄名問題
    reference_sheet: Path
    snapshot: SourceSnapshot  # 讀取前取的來源快照（設定檔的 hash 取自核對設定）
    config: CheckConfig = field(compare=False, repr=False)
    _sheet: ReferenceSheet = field(compare=False, repr=False)
    _identified: tuple[_Identified, ...] = field(compare=False, repr=False)  # 辨識與讀出結果，核對直接沿用


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

    def paired(self, sheet: ReferenceSheet) -> Paired | None:
        if self.issuer is None or self.ts is None or self.row is None or self.shared:
            return None
        return Paired(self.issuer, self.ts, self.row, sheet.record(self.row))

    def mark_shared(self, row_no: int, others: str) -> None:
        """同一批有其他說明書對到同一列：配對改為人工覆核，不核對也不回填。"""
        pairing = next(r for r in self.results if r.rule_id == "batch.pairing" and r.status == CheckStatus.PASS)
        pairing.status, pairing.reason_code = CheckStatus.REVIEW_REQUIRED, SHARED_ROW
        pairing.message = (
            f"同一批有多份說明書對到同一個 TDCC Code {pairing.actual}（參考條件表第 {row_no} 列），其他說明書：{others}"
        )
        self.shared = True


# 辨識與配對結果的項目：只寫說明，不附雙方值
ISSUER_ITEM, PRODUCT_CODE_ITEM = Item.note("上手"), Item.note("商品代號")


def _unexpected(e: Exception) -> CheckResult:
    return CheckResult(
        rule_id="batch.unexpected",
        field="說明書",
        status=CheckStatus.ERROR,
        reason_code="unexpected_error",
        message=f"{type(e).__name__}: {e}",
        item=Item.note("說明書"),
    )


def _review(rule_id: str, field_: str, item: Item, reason: str, message: str, actual: Any = None) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        field=field_,
        status=CheckStatus.REVIEW_REQUIRED,
        actual=actual,
        reason_code=reason,
        message=message,
        item=item,
    )


def _identify(pdf: Path, sheet: ReferenceSheet, config: CheckConfig) -> _Identified:
    registry = config.registry
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
    out.issuer_code = config.issuer_prefixes.get(prefix)
    if out.issuer_code is None:
        msg = f"檔名上手編號「{prefix}」不在上手編號對照表，未支援上手"
        results.append(_review("batch.issuer_prefix", "issuer", ISSUER_ITEM, UNSUPPORTED, msg))
        return out
    issuer = by_code(out.issuer_code, registry)
    if issuer is None:
        code = out.issuer_code
        msg = f"上手編號 {prefix} 對應 {code}，但 {code} 還沒有說明書範本，未支援上手"
        results.append(_review("batch.issuer_prefix", "issuer", ISSUER_ITEM, UNSUPPORTED, msg))
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
                ISSUER_ITEM,
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
        results.append(doc_review("batch.pairing", "product_code", pc, item=PRODUCT_CODE_ITEM))
        return out
    if not str(pc.value).startswith(prefix):
        msg = f"說明書商品代號 {pc.value} 的前三碼與檔名上手編號 {prefix} 不同"
        results.append(
            _review(
                "batch.issuer_prefix", "product_code", PRODUCT_CODE_ITEM, "issuer_prefix_mismatch", msg, actual=pc.value
            )
        )
        return out

    rows = sheet.find(pc.value)
    if not rows:
        msg = f"參考條件表找不到 TDCC Code {pc.value} 的列"
        results.append(_review("batch.pairing", "product_code", PRODUCT_CODE_ITEM, "reference_row_missing", msg))
        return out
    if len(rows) > 1:
        msg = f"參考條件表有 {len(rows)} 列 TDCC Code 為 {pc.value}"
        r = _review("batch.pairing", "product_code", PRODUCT_CODE_ITEM, "reference_row_duplicate", msg)
        r.order_source = [x.product_code.source for x in rows]
        results.append(r)
        return out
    row = rows[0]
    expected_issuer = config.reference_format.issuer_values.get(issuer.code)
    if expected_issuer is None or row.issuer.value != expected_issuer:
        r = _review(
            "batch.pairing",
            "issuer",
            ISSUER_ITEM,
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
            item=PRODUCT_CODE_ITEM,
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


def _identify_all(term_sheets: Sequence[Path], sheet: ReferenceSheet, config: CheckConfig) -> list[_Identified]:
    """先辨識全部說明書，同一批有多份對到參考條件表同一列時全部轉人工覆核；單份非預期錯誤不中斷整批。"""
    identified = []
    for pdf in map(Path, term_sheets):
        try:
            found = _identify(pdf, sheet, config)
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


def preview_batch(config: CheckConfig, reference_sheet: Path, term_sheets: Sequence[Path]) -> BatchPreview:
    """唯讀預覽：讀取前先取來源快照，再辨識並讀出每份說明書（核對直接沿用）；參考條件表本身有問題時丟出 IngestionError。"""
    reference_sheet, term_sheets = Path(reference_sheet), tuple(map(Path, term_sheets))
    snapshot = SourceSnapshot.take((reference_sheet, *term_sheets), taken=config.files)
    sheet = load_reference_sheet(reference_sheet, config.reference_format)
    identified = _identify_all(term_sheets, sheet, config)
    rows = []
    for found in identified:
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
    warnings = _sheet_warnings(sheet)
    return BatchPreview(tuple(rows), warnings, reference_sheet, snapshot, config, sheet, tuple(identified))


# ---------------------------------------------------------------- 核對


def _item_metadata(pdf: Path, meta: dict[str, Any], snapshot: SourceSnapshot) -> dict[str, Any]:
    return {
        **meta,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "parser": None,
        "inputs": {**meta["inputs"], "term_sheet": snapshot.meta(pdf)},
    }


def _check_one(
    found: _Identified, sheet: ReferenceSheet, config: CheckConfig, meta: dict[str, Any], snapshot: SourceSnapshot
) -> BatchItem:
    pdf = found.pdf
    metadata = _item_metadata(pdf, meta, snapshot)
    issuer = found.issuer
    if found.pages is not None:
        metadata["inputs"]["term_sheet"]["pages"] = found.pages
    if issuer is not None:
        metadata["parser"] = {"template": issuer.template_id, "version": issuer.parser_version}
    report = check_document(found.results, found.paired(sheet), config)
    report.template = issuer.template_id if issuer is not None else None
    report.metadata = metadata
    item = BatchItem(pdf, report, issuer=found.issuer_code, reference_row=found.row_no)
    if found.product_code is not None and found.product_code.ok:
        item.product_code = found.product_code.value
    return item


def check_batch(preview: BatchPreview) -> BatchOutcome:
    """沿用預覽的辨識與讀出逐份核對（不重新讀 PDF），不寫任何檔案。

    預覽後任何來源（參考條件表、說明書、設定檔）變更或讀不到時丟出 IngestionError（source_changed），要求重新預覽。
    """
    snapshot = preview.snapshot
    if not snapshot.still_valid():
        raise IngestionError("source_changed", "參考條件表、說明書或設定檔在預覽後已變更或無法讀取，請重新載入預覽。")
    config, sheet = preview.config, preview._sheet
    meta = {
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "inputs": {"reference_sheet": snapshot.meta(preview.reference_sheet)},
        **config.record(),
    }
    items: list[BatchItem] = []
    for found in preview._identified:
        try:
            item = _check_one(found, sheet, config, meta, snapshot)
        except Exception as e:  # 單份非預期錯誤不中斷整批
            pdf = found.pdf
            item = BatchItem(
                pdf, CheckReport(CheckStatus.ERROR, None, [_unexpected(e)], [], _item_metadata(pdf, meta, snapshot))
            )
        items.append(item)
    return BatchOutcome(items, preview.reference_sheet, config.reference_format, snapshot=snapshot, metadata=meta)


def failed_batch(reference_sheet: Path, e: IngestionError) -> BatchOutcome:
    """設定檔或參考條件表本身有問題、沒有核對任何說明書的整批錯誤（CLI 用）。"""
    return BatchOutcome([], Path(reference_sheet), errors=[error_result("input.batch", "批量輸入", e)])


# ---------------------------------------------------------------- 儲存


def _error_row(item: BatchItem) -> result_file.ErrorRow:
    """錯誤清單的一列。TDCC Code 取說明書封面商品代號；取不到時用檔名前 12 碼（12 位數字才算），否則留白。"""
    head = item.term_sheet.name[:12]
    code = item.product_code or (head if re.fullmatch(r"[0-9]{12}", head) else None)
    return result_file.ErrorRow(code, item.term_sheet.name, "\n".join(item.problem_messages))


def save_batch(outcome: BatchOutcome, out_dir: Path, *, root: Path, now: dt.datetime | None = None) -> BatchOutcome:
    """確認來源（參考條件表、說明書、設定檔）都與核對時相同後，寫核對結果檔到 out_dir、核對紀錄到 root/runtime/核對紀錄（都不覆蓋）。

    root 是根目錄（CLI 為執行目錄、PANEL 為安裝根目錄）。核對紀錄寫入失敗只記成整批錯誤，不影響核對結果檔；
    寫檔失敗不清除核對結果，可以再儲存一次。
    """
    if outcome.reference_format is None or not outcome.items:
        return outcome
    now = now or dt.datetime.now()
    outcome.clear_saved()  # 同一份結果可以再儲存一次
    rfmt, items = outcome.reference_format, outcome.items
    wb = _attempt(
        outcome,
        "output.result_file",
        "核對結果檔",
        lambda: _open_unchanged_reference(outcome),
    )
    if wb is not None:  # 來源核對後被改過（或讀不到）時，核對結果檔與核對紀錄都不寫
        filled = [i.fillable for i in items]
        to_fill = [i for i, ok in zip(items, filled, strict=True) if ok]
        backfill.apply(wb, rfmt, [i.report for i in to_fill])
        keep = [i.reference_row for i in to_fill if i.reference_row]
        errors = [_error_row(i) for i, ok in zip(items, filled, strict=True) if not ok]
        out = result_file.output_path(Path(out_dir), outcome.reference_sheet, now)
        outcome.output = _attempt(
            outcome, "output.result_file", "核對結果檔", lambda: result_file.save(wb, rfmt, keep, errors, out)
        )
        if outcome.output is not None:
            for item, ok in zip(items, filled, strict=True):
                item.filled = ok
        outcome.record = _attempt(
            outcome, "output.record", "核對紀錄", lambda: reporting.save_record(outcome, Path(root), now)
        )
    return outcome


def _open_unchanged_reference(outcome: BatchOutcome) -> Workbook:
    """確認整份來源快照仍一致後，開啟參考條件表準備回填；任何來源變更或讀不到時丟出 IngestionError。"""
    if outcome.snapshot is None or not outcome.snapshot.still_valid():
        raise IngestionError("source_changed", "參考條件表、說明書或設定檔在核對後已變更或無法讀取，請重新核對後再儲存")
    return backfill.open_reference(outcome.reference_sheet)


def _attempt(outcome: BatchOutcome, rule_id: str, what: str, step: Callable[[], T]) -> T | None:
    """儲存的一步；失敗（IngestionError）時記成整批錯誤並回傳 None，不中斷其他輸出。"""
    try:
        return step()
    except IngestionError as e:
        outcome.errors.append(error_result(rule_id, what, e))
        return None


def run_batch(
    config: CheckConfig,
    reference_sheet: Path,
    term_sheets: Sequence[Path],
    out_dir: Path,
    *,
    root: Path,
    now: dt.datetime | None = None,
) -> BatchOutcome:
    """預覽、核對後立即儲存（CLI 使用）。參考條件表本身有問題、或讀取期間來源被改過時回傳整批錯誤、不核對任何說明書。"""
    try:
        outcome = check_batch(preview_batch(config, reference_sheet, term_sheets))
    except IngestionError as e:
        if e.reason_code == "source_changed":  # CLI 沒有預覽可重新載入
            e = IngestionError(e.reason_code, "參考條件表、說明書或設定檔在核對期間已變更或無法讀取，請重新核對")
        return failed_batch(reference_sheet, e)
    return save_batch(outcome, out_dir, root=root, now=now)
