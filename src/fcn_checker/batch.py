"""批量核對入口：多份說明書與投資人須知 PDF × 參考條件表 → 逐份核對結果、核對結果檔與核對紀錄（ADR 0004、0007）。

分三段，CLI 與 PANEL 共用：

- `preview_batch(核對設定, 參考條件表, 說明書)`：讀取前取來源快照，唯讀辨識並讀出每份說明書（上手、商品代號、
  對到的參考條件表列），不核對、不寫檔。辨識在 identification.py：每份 PDF 一筆凍結的辨識結果 `Identification`。
- `check_batch(預覽)`：確認來源快照仍有效後，沿用預覽的辨識與讀出逐份核對（不重新讀 PDF），不寫任何檔案。
- 儲存（核對結果檔與核對紀錄）在 saving.py：`save_batch` 回傳儲存收據，不改寫批量核對結果；`run_batch` = 預覽 ＋ 核對 ＋ 儲存。

設定（審查標準、參考條件表格式、上手編號對照、上手註冊表）由呼叫端載入成一個核對設定（check_config.py）傳入。

範本辨識與讀出每份說明書各只做一次（在預覽）；配對成功後由單份核對（single_check.py）依序執行所有規則。
批量入口只負責載入參考條件表、逐份呼叫、組裝記錄資料與單份錯誤隔離。

預覽列 `PreviewRow` 與核對項目 `BatchItem` 都直接讀辨識結果，不複製欄位；類別、狀態標籤、能否回填與放行只看辨識結果與
核對報告，不回頭翻核對結果的原因碼。每份 PDF 各自有類別（整份通過或人工放行 → `BatchItem.fillable`，否則列入錯誤清單）；
一檔商品要說明書與同商品投資人須知都 fillable，才回填這份說明書（`BatchItem.fills_sheet`）；回填流程見 backfill.py。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import fitz
import openpyxl

from . import __version__
from .backfill import BackfillAction
from .check_config import CheckConfig
from .config import ReferenceFormat
from .identification import Identification, PairingProblem, identify, unexpected_result
from .ingestion import IngestionError, SourceSnapshot, error_result
from .messages import STATUS_ZH, problem_message
from .orders.reference import ReferenceSheet, load_reference_sheet
from .schema import CheckReport, CheckResult, CheckStatus, DocKind, Evidence, overall_status
from .single_check import Paired, PairedIis, check_document, check_investor_sheet


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

# 配對有問題時，狀態標籤直接寫原因，不必點進明細才知道要補參考條件表還是檢查檔案。
# 「條件表」是參考條件表的簡稱（狀態欄寬有限，見 CONTEXT.md）；以配對問題為 key，所以不放 messages.py。
_PAIRING_LABELS = {
    PairingProblem.ROW_MISSING: "條件表找不到這筆",
    PairingProblem.ROW_DUPLICATE: "條件表有重複列",
    PairingProblem.SHARED_ROW: "多份對到同一列",
    PairingProblem.ISSUER_MISMATCH: "條件表發行機構不符",
    PairingProblem.PREFIX_MISMATCH: "檔名上手編號不符",
    PairingProblem.NAME_UNRECOGNIZED: "檔名無法辨識",
    PairingProblem.CODE_MISMATCH: "檔名商品代號不符",
    PairingProblem.MISSING_IIS: "這批缺投資人須知",
    PairingProblem.MISSING_TS: "這批缺說明書",
}
# 不能人工放行的配對問題：原因
_UNRELEASABLE = {
    PairingProblem.NAME_UNRECOGNIZED: "檔名無法辨識，請修正檔名後重新載入",
    PairingProblem.CODE_MISMATCH: "檔名的商品代號與說明書封面不同，可能放錯檔案，請修正後重新載入",
    PairingProblem.SHARED_ROW: "同一批有多份文件對到同一列（說明書與投資人須知各只能一份），不能人工放行",
    PairingProblem.MISSING_IIS: "這批缺同商品的投資人須知，請一起選取說明書與投資人須知後重新載入",
    PairingProblem.MISSING_TS: "這批缺同商品的說明書，請一起選取說明書與投資人須知後重新載入",
}


@dataclass
class BatchItem:
    term_sheet: Path  # 這份 PDF（說明書或投資人須知）
    report: CheckReport
    identification: Identification
    partner: BatchItem | None = field(default=None, repr=False, compare=False)  # 同商品的另一份（說明書 ↔ 投資人須知）
    _released: bool = field(default=False, init=False, repr=False)  # 只能經 BatchOutcome.release／cancel_release 改變

    @property
    def issuer(self) -> str | None:
        return self.identification.issuer

    @property
    def product_code(self) -> str | None:
        return self.identification.product_code

    @property
    def reference_row(self) -> int | None:
        return self.identification.reference_row

    @property
    def kind(self) -> DocKind | None:
        return self.identification.kind

    @property
    def released(self) -> bool:
        """人工放行（PANEL）；原判定仍在 report。"""
        return self._released

    @property
    def unsupported(self) -> bool:
        return self.identification.unsupported

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
        """這份通過或人工放行（不列入錯誤清單）；說明書要不要回填另看 `fills_sheet`。"""
        return self.category.fillable

    @property
    def document(self) -> str:
        """錯訊裡文件那一邊的稱呼：說明書或投資人須知（檔名無法辨識時以說明書稱呼）。"""
        return (self.kind or DocKind.TERM_SHEET).value

    @property
    def fills_sheet(self) -> bool:
        """儲存時回填並列入「回填後」：說明書與同商品投資人須知都通過或人工放行（ADR 0007）。"""
        partner = self.partner
        return self.kind == DocKind.TERM_SHEET and self.fillable and partner is not None and partner.fillable

    @property
    def not_filled_reason(self) -> str:
        """說明書本身通過或放行、卻不回填的原因（PANEL 回填決策區顯示）；其他情況為空字串。"""
        if self.kind != DocKind.TERM_SHEET or not self.fillable or self.fills_sheet:
            return ""
        if self.partner is None:
            return "這批沒有同商品的投資人須知，不回填。"
        return "同商品的投資人須知尚未通過或人工放行，不回填。"

    @property
    def status_label(self) -> str:
        """PANEL 清單與 CLI 顯示的白話狀態：配對問題直接寫原因，其餘只寫中文狀態。"""
        category, original = self.category, STATUS_ZH[self.report.status]
        if category == Category.UNSUPPORTED:
            return category.value
        if category == Category.RELEASED:
            return f"{category.value}（原：{original}）"
        problem = self.identification.problem
        # 執行錯誤等其他狀態不被配對原因蓋掉
        if self.report.status == CheckStatus.REVIEW_REQUIRED and problem in _PAIRING_LABELS:
            return _PAIRING_LABELS[problem]
        return original

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
        problem = self.identification.problem
        if problem in _UNRELEASABLE:
            return _UNRELEASABLE[problem]
        if self.reference_row is None:
            return "沒有對到參考條件表的列，沒有地方可以回填"
        if self.kind == DocKind.IIS:  # 投資人須知不回填，沒有回填值要確認
            return ""
        if any(d.action == BackfillAction.MISMATCH for d in report.backfill):
            return "參考條件表回填欄位已有不同的值，請先修正參考條件表再核對"
        if not report.backfill_certain:
            return "回填值無法確定，請人工處理"
        return ""


@dataclass
class BatchOutcome:
    items: list[BatchItem]
    reference_sheet: Path
    reference_format: ReferenceFormat | None = None
    errors: list[CheckResult] = field(default_factory=list)  # 整批錯誤（設定檔、參考條件表）；寫檔錯誤在儲存收據
    snapshot: SourceSnapshot | None = None  # 核對前取的來源快照；核對紀錄的 hash 取自它，儲存前據此確認來源未變更
    metadata: dict[str, Any] = field(default_factory=dict)  # 整批執行 metadata（程式版本、設定檔與參考條件表 hash）

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
            raise IngestionError("result_required", "這份文件不在這次核對結果中，請重新核對後再人工放行。")

    def _set_released(self, item: BatchItem, released: bool) -> None:
        item._released = released


# ---------------------------------------------------------------- 預覽


@dataclass(frozen=True)
class PreviewRow:
    """預覽表的一列：一份 PDF 辨識結果的 view（PANEL 顯示用）；核對直接沿用同一筆辨識結果。"""

    identification: Identification

    @property
    def term_sheet(self) -> Path:
        """這份 PDF（說明書或投資人須知）。"""
        return self.identification.pdf

    @property
    def kind(self) -> DocKind | None:
        """None → 檔名無法辨識。"""
        return self.identification.kind

    @property
    def issuer(self) -> str | None:
        return self.identification.issuer

    @property
    def unsupported(self) -> bool:
        return self.identification.unsupported

    @property
    def product_code(self) -> str | None:
        return self.identification.product_code

    @property
    def product_code_evidence(self) -> tuple[Evidence, ...]:
        return self.identification.product_code_evidence

    @property
    def reference_row(self) -> int | None:
        """對到的參考條件表列號。"""
        return self.identification.reference_row

    @property
    def problem(self) -> str:
        """辨識與配對沒通過的結果訊息；空字串表示可以核對。"""
        return "；".join(r.message for r in self.identification.results if r.status != CheckStatus.PASS)


@dataclass(frozen=True)
class BatchPreview:
    """預覽：每份 PDF 的辨識結果（含讀出結果與對到的參考條件表列，核對直接沿用）；另帶核對要沿用的核對設定與來源快照。"""

    rows: tuple[PreviewRow, ...]
    warnings: tuple[str, ...]  # 參考條件表欄名問題
    reference_sheet: Path
    snapshot: SourceSnapshot  # 讀取前取的來源快照（設定檔的 hash 取自核對設定）
    config: CheckConfig = field(compare=False, repr=False)


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
    rows = tuple(PreviewRow(ident) for ident in identify(term_sheets, sheet, config))
    return BatchPreview(rows, _sheet_warnings(sheet), reference_sheet, snapshot, config)


# ---------------------------------------------------------------- 核對


def _item_metadata(pdf: Path, meta: dict[str, Any], snapshot: SourceSnapshot) -> dict[str, Any]:
    return {
        **meta,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "parser": None,
        "inputs": {**meta["inputs"], "term_sheet": snapshot.meta(pdf)},
    }


def _check_one(ident: Identification, config: CheckConfig, meta: dict[str, Any], snapshot: SourceSnapshot) -> BatchItem:
    metadata = _item_metadata(ident.pdf, meta, snapshot)
    issuer = ident.adapter
    if ident.pages is not None:
        metadata["inputs"]["term_sheet"]["pages"] = ident.pages
    template = None
    if issuer is not None:
        template, version = (
            (issuer.template_id, issuer.parser_version)
            if ident.kind != DocKind.IIS or issuer.iis is None
            else (issuer.iis.template_id, issuer.iis.parser_version)
        )
        metadata["parser"] = {"template": template, "version": version}
    if ident.kind == DocKind.IIS:
        report = check_investor_sheet(ident.results, PairedIis.of(ident), config)
    else:
        report = check_document(ident.results, Paired.of(ident), config)
    report.template = template
    report.metadata = metadata
    return BatchItem(ident.pdf, report, ident)


def check_batch(preview: BatchPreview) -> BatchOutcome:
    """沿用預覽的辨識與讀出逐份核對（不重新讀 PDF），不寫任何檔案。

    預覽後任何來源（參考條件表、說明書、設定檔）變更或讀不到時丟出 IngestionError（source_changed），要求重新預覽。
    """
    snapshot = preview.snapshot
    if not snapshot.still_valid():
        raise IngestionError("source_changed", "參考條件表、說明書或設定檔在預覽後已變更或無法讀取，請重新載入預覽。")
    config = preview.config
    meta = {
        "program_version": __version__,
        "extractor": f"PyMuPDF {fitz.VersionBind}",
        "excel_reader": f"openpyxl {openpyxl.__version__}",
        "inputs": {"reference_sheet": snapshot.meta(preview.reference_sheet)},
        **config.record(),
    }
    items: dict[Identification, BatchItem] = {}
    for row in preview.rows:
        ident = row.identification
        try:
            item = _check_one(ident, config, meta, snapshot)
        except Exception as e:  # 單份非預期錯誤不中斷整批
            pdf = ident.pdf
            report = CheckReport(
                CheckStatus.ERROR, None, [unexpected_result(e, ident.kind)], [], _item_metadata(pdf, meta, snapshot)
            )
            item = BatchItem(pdf, report, ident)
        items[ident] = item
    for item in items.values():  # 同商品另一份的核對項目：辨識結果已互相引用，這裡只對應到項目
        if item.identification.partner is not None:
            item.partner = items[item.identification.partner]
    return BatchOutcome(
        list(items.values()), preview.reference_sheet, config.reference_format, snapshot=snapshot, metadata=meta
    )


def failed_batch(reference_sheet: Path, e: IngestionError) -> BatchOutcome:
    """設定檔或參考條件表本身有問題、沒有核對任何說明書的整批錯誤（CLI 用）。"""
    return BatchOutcome([], Path(reference_sheet), errors=[error_result("input.batch", "批量輸入", e)])
