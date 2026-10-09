"""批量核對入口：多份說明書與投資人須知 PDF × 參考條件表 → 逐份核對結果、核對結果檔與核對紀錄（ADR 0004、0007）。

分三段，CLI 與 PANEL 共用：

- `preview_batch(核對設定, 參考條件表, 說明書)`：讀取前取來源快照，唯讀辨識並讀出每份說明書（上手、商品代號、
  對到的參考條件表列），不核對、不寫檔。
- `check_batch(預覽)`：確認來源快照仍有效後，沿用預覽的辨識與讀出逐份核對（不重新讀 PDF），不寫任何檔案。
- 儲存（核對結果檔與核對紀錄）在 saving.py：`save_batch` 回傳儲存收據，不改寫批量核對結果；`run_batch` = 預覽 ＋ 核對 ＋ 儲存。

設定（審查標準、參考條件表格式、上手編號對照、上手註冊表）由呼叫端載入成一個核對設定（check_config.py）傳入。

辨識流程（每份 PDF）：
0. 檔名（不含副檔名）須為 `<12 位商品代號>_TS`（說明書）或 `<12 位商品代號>_IIS`（投資人須知）；
   其他 → 檔名無法辨識（人工覆核、不能放行）。
1. 檔名前三碼（上手編號）查上手編號對照 → 上手；不在對照表或上手沒有該種文件的範本 → 未支援上手。
2. 內容辨識出的上手必須與檔名一致；說明書封面商品代號也要等於檔名的商品代號，否則轉人工覆核。
3. 以商品代號找參考條件表的列（TDCC Code），該列發行機構必須是此上手的寫法。
4. 同一商品代號（檔名）的說明書與投資人須知配成一組；同種文件有多份時，對到列的全部轉人工覆核，不核對也不回填。
5. 這批只有說明書或只有投資人須知、且已對到列時，那一份轉人工覆核（這批缺另一份），不能人工放行。
   另一份在這批裡但本身配對失敗（例：未支援上手、範本不符）時不算缺，但那份沒通過，說明書就不回填。

範本辨識與讀出每份說明書各只做一次（在預覽）；配對成功後由單份核對（single_check.py）依序執行所有規則。
批量入口只負責載入參考條件表、逐份呼叫、組裝記錄資料與單份錯誤隔離。

辨識遇到問題就停，每份 PDF 最多一個配對問題，連同文件種類、上手、商品代號、對到的列記成辨識結果（`Identification`）；
類別、狀態標籤、能否回填與放行只看辨識結果與核對報告，不回頭翻核對結果的原因碼。
每份 PDF 各自有類別（整份通過或人工放行 → `BatchItem.fillable`，否則列入錯誤清單）；一檔商品要說明書與同商品
投資人須知都 fillable，才回填這份說明書（`BatchItem.fills_sheet`）；回填流程見 backfill.py。
"""

from __future__ import annotations

import datetime as dt
import re
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
from .extraction import extract_lines
from .ingestion import IngestionError, SourceSnapshot, error_result, open_pdf
from .investor_sheet import IisSheet
from .issuers import Issuer, by_code, detect, detect_iis
from .messages import STATUS_ZH, problem_message
from .orders.reference import OrderRecord, ReferenceSheet, load_reference_sheet
from .rules.kit import doc_review, read_standard
from .schema import CheckReport, CheckResult, CheckStatus, Evidence, Item, ParsedField, overall_status
from .single_check import Paired, PairedIis, check_document, check_investor_sheet
from .standard_fields import TermSheet


class PairingProblem(StrEnum):
    """配對問題：辨識結果裡讓這份 PDF 不能正常核對或不能放行的那一個原因（每份最多一個，見 CONTEXT.md）。

    值是對應結果的原因碼；原因碼由例外或範本辨識決定的（PDF 讀不到、範本、封面商品代號）取其中一個代表。
    """

    NAME_UNRECOGNIZED = "file_name_unrecognized"
    PDF_UNREADABLE = "pdf_unreadable"  # 找不到、無法開啟、加密或沒有頁面
    UNSUPPORTED = "issuer_unsupported"
    TEMPLATE_UNKNOWN = "template_unknown"
    TEMPLATE_AMBIGUOUS = "template_ambiguous"
    PREFIX_MISMATCH = "issuer_prefix_mismatch"
    UNEXPECTED = "unexpected_error"  # 上手讀出或辨識時的非預期錯誤
    PRODUCT_CODE_UNREADABLE = "product_code_unreadable"  # 說明書封面商品代號讀不到
    CODE_MISMATCH = "file_code_mismatch"  # 說明書封面商品代號與檔名的商品代號不同
    ROW_MISSING = "reference_row_missing"
    ROW_DUPLICATE = "reference_row_duplicate"
    ISSUER_MISMATCH = "reference_issuer_mismatch"
    SHARED_ROW = "reference_row_shared"
    MISSING_IIS = "counterpart_missing_iis"  # 這批有說明書、沒有同商品的投資人須知
    MISSING_TS = "counterpart_missing_ts"  # 這批有投資人須知、沒有同商品的說明書


class DocKind(StrEnum):
    """PDF 的種類，由檔名結尾決定（ADR 0007）；值是錯訊與 PANEL 用的稱呼。"""

    TERM_SHEET = "說明書"
    IIS = "投資人須知"


_SUFFIXES = {"_TS": DocKind.TERM_SHEET, "_IIS": DocKind.IIS}


def doc_kind(pdf: Path) -> DocKind | None:
    """檔名（不含副檔名）結尾剛好是 `_TS` 或 `_IIS` 才算；不容忍空白、大小寫或其他寫法。"""
    return next((kind for suffix, kind in _SUFFIXES.items() if pdf.stem.endswith(suffix)), None)


def file_code(pdf: Path) -> str | None:
    """檔名結尾前的部分是 12 位數字時為商品代號（例：029199990001_IIS.pdf）。"""
    kind = doc_kind(pdf)
    head = pdf.stem[: pdf.stem.rfind("_")] if kind else ""
    return head if re.fullmatch(r"[0-9]{12}", head) else None


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
# 「條件表」是參考條件表的簡稱（狀態欄寬有限，見 CONTEXT.md）；以本模組的配對問題為 key，所以不放 messages.py。
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


@dataclass(frozen=True)
class Identification:
    """辨識結果：預覽階段對每份 PDF 算出的事實（見 CONTEXT.md）；類別、狀態標籤、能否回填與放行都只看它與核對報告。"""

    kind: DocKind | None  # None → 檔名無法辨識
    issuer: str | None = None  # 上手代號（檔名上手編號查對照表）
    product_code: str | None = None
    reference_row: int | None = None  # 對到的參考條件表列號
    problem: PairingProblem | None = None  # 唯一的配對問題；None 表示配對乾淨
    checked: bool = False  # 規則有沒有跑：有對到列、且不是多份對到同一列（缺另一份時仍為 True）


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
        return self.identification.problem == PairingProblem.UNSUPPORTED

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
        return tuple(
            dict.fromkeys(problem_message(r, self.document) for r in self.report.results if r.status.is_problem)
        )

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


@dataclass(frozen=True)
class PreviewRow:
    term_sheet: Path  # 這份 PDF（說明書或投資人須知）
    kind: DocKind | None  # None → 檔名無法辨識
    issuer: str | None
    unsupported: bool
    product_code: str | None
    product_code_evidence: tuple[Evidence, ...]
    reference_row: int | None  # 對到的參考條件表列號
    problem: str  # 空字串表示可以核對


@dataclass(frozen=True)
class BatchPreview:
    """預覽：每份說明書的辨識結果；另帶核對要沿用的核對設定、讀出結果（含對到的參考條件表列）與來源快照。"""

    rows: tuple[PreviewRow, ...]
    warnings: tuple[str, ...]  # 參考條件表欄名問題
    reference_sheet: Path
    snapshot: SourceSnapshot  # 讀取前取的來源快照（設定檔的 hash 取自核對設定）
    config: CheckConfig = field(compare=False, repr=False)
    _identified: tuple[_Identified, ...] = field(compare=False, repr=False)  # 辨識與讀出結果，核對直接沿用


# ---------------------------------------------------------------- 辨識（預覽與核對共用）


@dataclass
class _Identified:
    pdf: Path
    results: list[CheckResult]
    kind: DocKind | None = None
    issuer_code: str | None = None
    issuer: Issuer | None = None
    product_code: ParsedField | None = None
    row: OrderRecord | None = None
    ts: TermSheet | None = None  # 說明書讀出結果：同一份只讀一次，核對直接沿用
    iis: IisSheet | None = None  # 投資人須知讀出結果
    pages: int | None = None
    problem: PairingProblem | None = None  # 每份最多一個：辨識遇到問題就停，多份對到同一列與缺另一份只在對到列後發生
    partner: _Identified | None = None  # 同一列的另一種文件

    @property
    def row_no(self) -> int | None:
        return self.row.row if self.row is not None else None

    @property
    def checked(self) -> bool:
        """規則會不會跑：有對到列、且不是多份對到同一列。"""
        return self.row is not None and self.problem != PairingProblem.SHARED_ROW

    @property
    def identification(self) -> Identification:
        pc = self.product_code
        return Identification(
            self.kind,
            self.issuer_code,
            pc.value if pc is not None and pc.ok else None,
            self.row_no,
            self.problem,
            self.checked,
        )

    def stop(self, problem: PairingProblem, result: CheckResult) -> _Identified:
        """辨識遇到配對問題：記下問題與對應的結果，辨識到此為止。"""
        self.results.append(result)
        self.problem = problem
        return self

    def paired(self) -> Paired | None:
        if self.issuer is None or self.ts is None or self.row is None or not self.checked:
            return None
        return Paired(self.issuer, self.ts, self.row)

    def paired_iis(self) -> PairedIis | None:
        if self.issuer is None or self.iis is None or self.row is None or not self.checked or self.pages is None:
            return None
        code = self.product_code.value if self.product_code is not None else ""
        partner = self.partner  # 同商品說明書配對成功才拿來比對
        partner_ts = partner.ts if partner is not None and partner.checked else None
        return PairedIis(self.issuer, self.iis, self.row, partner_ts, self.pages, code)

    def mark_shared(self, row_no: int, what: str, others: str) -> None:
        """同一批有多份同種文件對到同一列：這一列的文件配對改為人工覆核，不核對也不回填。"""
        pairing = next(r for r in self.results if r.rule_id == "batch.pairing" and r.status == CheckStatus.PASS)
        pairing.status, pairing.reason_code = CheckStatus.REVIEW_REQUIRED, PairingProblem.SHARED_ROW.value
        pairing.message = (
            f"同一批有多份{what}對到同一個 TDCC Code {pairing.actual}（參考條件表第 {row_no} 列），其他文件：{others}"
        )
        self.problem = PairingProblem.SHARED_ROW

    def mark_alone(self) -> None:
        """這批沒有同商品的另一種文件：人工覆核、不能放行；規則照常執行，方便先看這份的問題。"""
        missing = DocKind.IIS if self.kind == DocKind.TERM_SHEET else DocKind.TERM_SHEET
        code = self.product_code.value if self.product_code is not None else ""
        suffix = "_IIS" if missing == DocKind.IIS else "_TS"
        problem = PairingProblem.MISSING_IIS if missing == DocKind.IIS else PairingProblem.MISSING_TS
        msg = f"這批沒有同商品的{missing.value}（{code}{suffix}.pdf）；說明書與投資人須知要一起選取、一起核對"
        self.stop(problem, _review("batch.counterpart", "counterpart", Item.note(missing.value), problem, msg))


# 辨識與配對結果的項目：只寫說明，不附雙方值
ISSUER_ITEM, PRODUCT_CODE_ITEM, FILE_NAME_ITEM = Item.note("上手"), Item.note("商品代號"), Item.note("檔名")


def _unexpected(e: Exception) -> CheckResult:
    return CheckResult(
        rule_id="batch.unexpected",
        field="說明書",
        status=CheckStatus.ERROR,
        reason_code="unexpected_error",
        message=f"{type(e).__name__}: {e}",
        item=Item.note("說明書"),
    )


def _review(
    rule_id: str, field_: str, item: Item, reason: PairingProblem, message: str, actual: Any = None
) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        field=field_,
        status=CheckStatus.REVIEW_REQUIRED,
        actual=actual,
        reason_code=reason.value,
        message=message,
        item=item,
    )


def _identify(pdf: Path, sheet: ReferenceSheet, config: CheckConfig) -> _Identified:
    registry = config.registry
    out = _Identified(pdf, [], kind=doc_kind(pdf))
    results = out.results
    if out.kind is None or file_code(pdf) is None:
        need = "「<12 位商品代號>_TS」（說明書）或「<12 位商品代號>_IIS」（投資人須知）"
        msg = f"檔名須為{need}，無法辨識；請修正檔名後重新載入"
        problem = PairingProblem.NAME_UNRECOGNIZED
        return out.stop(problem, _review("batch.file_name", "file_name", FILE_NAME_ITEM, problem, msg))
    what = out.kind.value
    try:
        doc = open_pdf(pdf)
        try:
            lines = extract_lines(doc)
            out.pages = doc.page_count
        finally:
            doc.close()
    except IngestionError as e:
        return out.stop(PairingProblem.PDF_UNREADABLE, error_result("input.term_sheet", what, e))

    prefix = pdf.name[:3]
    out.issuer_code = config.issuer_prefixes.get(prefix)
    if out.issuer_code is None:
        msg = f"檔名上手編號「{prefix}」不在上手編號對照表，未支援上手"
        problem = PairingProblem.UNSUPPORTED
        return out.stop(problem, _review("batch.issuer_prefix", "issuer", ISSUER_ITEM, problem, msg))
    issuer = by_code(out.issuer_code, registry)
    if issuer is None or (out.kind == DocKind.IIS and issuer.iis is None):
        code = out.issuer_code
        msg = f"上手編號 {prefix} 對應 {code}，但 {code} 還沒有{what}範本，未支援上手"
        problem = PairingProblem.UNSUPPORTED
        return out.stop(problem, _review("batch.issuer_prefix", "issuer", ISSUER_ITEM, problem, msg))

    detected, template_result = (detect if out.kind == DocKind.TERM_SHEET else detect_iis)(lines, registry)
    if detected is None:  # 範本不符或多重命中：原因碼就是配對問題的值
        return out.stop(PairingProblem(template_result.reason_code), template_result)
    results.append(template_result)
    if detected is not issuer:
        problem = PairingProblem.PREFIX_MISMATCH
        msg = f"檔名上手編號 {prefix} 對應 {issuer.code}，但{what}內容是 {detected.code} 範本，可能檔名取錯或檔案放錯"
        return out.stop(problem, _review("batch.issuer_prefix", "issuer", ISSUER_ITEM, problem, msg, detected.code))
    out.issuer = issuer
    try:
        if issuer.iis is not None and out.kind == DocKind.IIS:
            out.iis = issuer.iis.read(lines)
        else:
            out.ts = issuer.read(lines)
    except Exception as e:  # 上手讀出失敗：這份轉執行錯誤，不中斷整批（預覽也一樣）
        return out.stop(PairingProblem.UNEXPECTED, _unexpected(e))

    if out.kind == DocKind.IIS:  # 投資人須知以檔名前 12 碼配對（HSBC 範本沒有商品代號；封面有的另由規則核對）
        pc = out.product_code = ParsedField.present("product_code", file_code(pdf), [])
    else:
        pc = out.product_code = read_standard(out.ts, "product_code")
        if not pc.ok:
            problem = PairingProblem.PRODUCT_CODE_UNREADABLE
            return out.stop(problem, doc_review("batch.pairing", "product_code", pc, item=PRODUCT_CODE_ITEM))
        if not str(pc.value).startswith(prefix):
            problem = PairingProblem.PREFIX_MISMATCH
            msg = f"說明書商品代號 {pc.value} 的前三碼與檔名上手編號 {prefix} 不同"
            r = _review("batch.issuer_prefix", "product_code", PRODUCT_CODE_ITEM, problem, msg, actual=pc.value)
            return out.stop(problem, r)
        if pc.value != file_code(pdf):  # 同商品的兩份以檔名的商品代號配成一組，說明書檔名不能和封面不同
            problem = PairingProblem.CODE_MISMATCH
            msg = f"說明書封面商品代號 {pc.value} 與檔名的商品代號 {file_code(pdf)} 不同，可能檔名取錯或檔案放錯"
            r = _review("batch.file_name", "product_code", PRODUCT_CODE_ITEM, problem, msg, actual=pc.value)
            return out.stop(problem, r)

    rows = sheet.find(pc.value)
    if not rows:
        problem = PairingProblem.ROW_MISSING
        msg = f"參考條件表找不到 TDCC Code {pc.value} 的列"
        return out.stop(problem, _review("batch.pairing", "product_code", PRODUCT_CODE_ITEM, problem, msg))
    if len(rows) > 1:
        problem = PairingProblem.ROW_DUPLICATE
        msg = f"參考條件表有 {len(rows)} 列 TDCC Code 為 {pc.value}"
        r = _review("batch.pairing", "product_code", PRODUCT_CODE_ITEM, problem, msg)
        r.order_source = [x.product_code.source for x in rows]
        return out.stop(problem, r)
    row = rows[0]
    expected_issuer = config.reference_format.issuer_values.get(issuer.code)
    if expected_issuer is None or row.issuer.value != expected_issuer:
        problem = PairingProblem.ISSUER_MISMATCH
        r = _review(
            "batch.pairing",
            "issuer",
            ISSUER_ITEM,
            problem,
            f"參考條件表該列發行機構是「{row.issuer.value}」，{issuer.code} 應為「{expected_issuer}」",
            actual=row.issuer.value,
        )
        r.expected, r.order_source = expected_issuer, [row.issuer.source]
        return out.stop(problem, r)
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
    """先辨識全部 PDF，再依檔名的商品代號配成一組（一份說明書＋一份投資人須知）；單份非預期錯誤不中斷整批。

    同一商品有多份同種文件 → 對到列的全部轉人工覆核；只有一種文件且已對到列 → 那一份轉人工覆核（這批缺另一份）。
    """
    identified = []
    for pdf in map(Path, term_sheets):
        try:
            found = _identify(pdf, sheet, config)
        except Exception as e:
            found = _Identified(pdf, [_unexpected(e)], kind=doc_kind(pdf), problem=PairingProblem.UNEXPECTED)
        identified.append(found)
    by_code: dict[str, list[_Identified]] = {}
    for found in identified:
        code = file_code(found.pdf)
        if found.kind is not None and code is not None:
            by_code.setdefault(code, []).append(found)
    for group in by_code.values():
        kinds = [f.kind for f in group]
        duplicated = [k for k in DocKind if kinds.count(k) > 1]
        if duplicated:
            what = "、".join(k.value for k in duplicated)
            for found in group:
                if found.row is not None:
                    found.mark_shared(found.row.row, what, _other_names(found, group))
        elif len(group) == 2:
            first, second = group
            first.partner, second.partner = second, first
        elif group[0].row is not None:
            group[0].mark_alone()
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
        pc, ident = found.product_code, found.identification
        rows.append(
            PreviewRow(
                found.pdf,
                ident.kind,
                ident.issuer,
                ident.problem == PairingProblem.UNSUPPORTED,
                ident.product_code,
                tuple(pc.evidence) if pc is not None else (),
                ident.reference_row,
                "；".join(r.message for r in problems),
            )
        )
    warnings = _sheet_warnings(sheet)
    return BatchPreview(tuple(rows), warnings, reference_sheet, snapshot, config, tuple(identified))


# ---------------------------------------------------------------- 核對


def _item_metadata(pdf: Path, meta: dict[str, Any], snapshot: SourceSnapshot) -> dict[str, Any]:
    return {
        **meta,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "parser": None,
        "inputs": {**meta["inputs"], "term_sheet": snapshot.meta(pdf)},
    }


def _check_one(found: _Identified, config: CheckConfig, meta: dict[str, Any], snapshot: SourceSnapshot) -> BatchItem:
    pdf = found.pdf
    metadata = _item_metadata(pdf, meta, snapshot)
    issuer = found.issuer
    if found.pages is not None:
        metadata["inputs"]["term_sheet"]["pages"] = found.pages
    template = None
    if issuer is not None:
        template, version = (
            (issuer.template_id, issuer.parser_version)
            if found.kind != DocKind.IIS or issuer.iis is None
            else (issuer.iis.template_id, issuer.iis.parser_version)
        )
        metadata["parser"] = {"template": template, "version": version}
    if found.kind == DocKind.IIS:
        report = check_investor_sheet(found.results, found.paired_iis(), config)
    else:
        report = check_document(found.results, found.paired(), config)
    report.template = template
    report.metadata = metadata
    return BatchItem(pdf, report, found.identification)


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
    items: list[BatchItem] = []
    for found in preview._identified:
        try:
            item = _check_one(found, config, meta, snapshot)
        except Exception as e:  # 單份非預期錯誤不中斷整批
            pdf = found.pdf
            item = BatchItem(
                pdf,
                CheckReport(CheckStatus.ERROR, None, [_unexpected(e)], [], _item_metadata(pdf, meta, snapshot)),
                found.identification,
            )
        items.append(item)
    by_found = {id(f): i for f, i in zip(preview._identified, items, strict=True)}
    for found, item in zip(preview._identified, items, strict=True):
        if found.partner is not None:
            item.partner = by_found[id(found.partner)]
    return BatchOutcome(items, preview.reference_sheet, config.reference_format, snapshot=snapshot, metadata=meta)


def failed_batch(reference_sheet: Path, e: IngestionError) -> BatchOutcome:
    """設定檔或參考條件表本身有問題、沒有核對任何說明書的整批錯誤（CLI 用）。"""
    return BatchOutcome([], Path(reference_sheet), errors=[error_result("input.batch", "批量輸入", e)])
