"""儲存：批量核對結果 → 核對結果檔與核對紀錄，回傳儲存收據（Issue #93）。

`save_batch` 是唯一的儲存進入點，內部順序只在這裡：
1. 以來源快照確認參考條件表、說明書、設定檔都與核對時相同，開啟參考條件表（變更或讀不到時兩個檔都不寫）
2. 依每份 PDF 的類別決定列入錯誤清單的文件，以及要回填的說明書（說明書與同商品投資人須知都通過或人工放行）
3. 回填（backfill.py）→ 產生核對結果檔（result_file.py）
4. 核對結果檔處理完後寫核對紀錄（reporting.py），才記得到核對結果檔路徑與是否已回填

批量核對結果本身不被儲存改寫；同一份結果可以存很多次，每次都有自己的收據。
`run_batch` = 預覽 ＋ 核對 ＋ 儲存（CLI 使用）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from openpyxl.workbook.workbook import Workbook

from . import backfill, reporting, result_file
from .batch import BatchItem, BatchOutcome, check_batch, failed_batch, preview_batch
from .check_config import CheckConfig
from .ingestion import IngestionError, error_result
from .schema import CheckResult, CheckStatus, overall_status

T = TypeVar("T")


@dataclass(frozen=True)
class SaveReceipt:
    """一次儲存的結果：核對結果檔、核對紀錄、已回填的說明書與寫檔錯誤。"""

    status: CheckStatus | None = None  # 整批狀態，含這次的寫檔錯誤（CLI 依此決定結束碼）；取消儲存時為 None
    output: Path | None = None  # 核對結果檔；沒寫成時為 None
    record: Path | None = None  # 核對紀錄；沒寫成時為 None
    errors: tuple[CheckResult, ...] = ()  # 寫檔錯誤（含來源已變更）
    filled_items: tuple[BatchItem, ...] = ()  # 已回填進核對結果檔的說明書
    cancelled: bool = False  # PANEL：使用者取消選資料夾，什麼都沒寫

    @classmethod
    def nothing_to_save(cls, outcome: BatchOutcome) -> SaveReceipt:
        """整批錯誤（設定檔或參考條件表有問題）：沒有核對任何說明書，什麼都不寫。"""
        return cls(outcome.status)

    def filled(self, item: BatchItem) -> bool:
        return any(i is item for i in self.filled_items)

    @property
    def source_changed(self) -> bool:
        return any(e.reason_code == "source_changed" for e in self.errors)

    @property
    def complete(self) -> bool:
        return not self.cancelled and self.output is not None and not self.errors

    @property
    def summary(self) -> str:
        if self.cancelled:
            return "已取消儲存，核對結果仍保留。"
        lines = [f"核對結果檔：{self.output}" if self.output else "核對結果檔：未儲存"]
        lines.extend(e.message for e in self.errors)
        return "\n".join(lines)


def save_batch(outcome: BatchOutcome, out_dir: Path, *, root: Path, now: dt.datetime | None = None) -> SaveReceipt:
    """確認來源都與核對時相同後，寫核對結果檔到 out_dir、核對紀錄到 root/runtime/核對紀錄（都不覆蓋），回傳收據。

    root 是根目錄（CLI 為執行目錄、PANEL 為安裝根目錄）。核對結果檔與核對紀錄用同一個時間戳。
    核對紀錄寫入失敗只記在收據，不影響核對結果檔；寫檔失敗不影響核對結果，可以再儲存一次。
    """
    if outcome.reference_format is None or not outcome.items:  # 整批錯誤：沒有可以儲存的結果
        return SaveReceipt.nothing_to_save(outcome)
    now = now or dt.datetime.now()
    errors: list[CheckResult] = []
    output = record = None
    filled: tuple[BatchItem, ...] = ()
    wb = _attempt(errors, "output.result_file", "核對結果檔", lambda: _open_unchanged_reference(outcome))
    if wb is not None:  # 來源核對後被改過（或讀不到）時，核對結果檔與核對紀錄都不寫
        rfmt = outcome.reference_format
        to_fill = tuple(i for i in outcome.items if i.fills_sheet)
        backfill.apply(wb, rfmt, [i.report for i in to_fill])
        keep = [i.reference_row for i in to_fill if i.reference_row]
        error_rows = [_error_row(i) for i in outcome.items if not i.fillable]
        out = result_file.output_path(Path(out_dir), outcome.reference_sheet, now)
        output = _attempt(
            errors, "output.result_file", "核對結果檔", lambda: result_file.save(wb, rfmt, keep, error_rows, out)
        )
        if output is not None:
            filled = to_fill
        batch_record = reporting.BatchRecord(
            status=_status(outcome, errors),
            result_file=output,
            metadata=outcome.metadata,
            errors=(*outcome.errors, *errors),
            items=tuple(_record_item(i, any(i is f for f in filled)) for i in outcome.items),
        )
        record = _attempt(
            errors, "output.record", "核對紀錄", lambda: reporting.save_record(batch_record, Path(root), now)
        )
    return SaveReceipt(_status(outcome, errors), output, record, tuple(errors), filled)


def _status(outcome: BatchOutcome, errors: Sequence[CheckResult]) -> CheckStatus:
    return overall_status([outcome.status, *(e.status for e in errors)])


def _error_row(item: BatchItem) -> result_file.ErrorRow:
    return result_file.ErrorRow.of(item.term_sheet, item.product_code, item.problem_messages)


def _record_item(item: BatchItem, filled: bool) -> reporting.RecordItem:
    return reporting.RecordItem(
        pdf=item.term_sheet.name,
        document=item.kind.value if item.kind else None,
        partner=item.partner.term_sheet.name if item.partner else None,
        not_filled_reason=item.not_filled_reason,
        issuer=item.issuer,
        product_code=item.product_code,
        reference_row=item.reference_row,
        filled=filled,
        manual_release=item.released,
        report=item.report,
    )


def _open_unchanged_reference(outcome: BatchOutcome) -> Workbook:
    """確認整份來源快照仍一致後，開啟參考條件表準備回填；任何來源變更或讀不到時丟出 IngestionError。"""
    if outcome.snapshot is None or not outcome.snapshot.still_valid():
        raise IngestionError("source_changed", "參考條件表、說明書或設定檔在核對後已變更或無法讀取，請重新核對後再儲存")
    return backfill.open_reference(outcome.reference_sheet)


def _attempt(errors: list[CheckResult], rule_id: str, what: str, step: Callable[[], T]) -> T | None:
    """儲存的一步；失敗（IngestionError）時記進這次的寫檔錯誤並回傳 None，不中斷其他輸出。"""
    try:
        return step()
    except IngestionError as e:
        errors.append(error_result(rule_id, what, e))
        return None


def run_batch(
    config: CheckConfig,
    reference_sheet: Path,
    term_sheets: Sequence[Path],
    out_dir: Path,
    *,
    root: Path,
    now: dt.datetime | None = None,
) -> tuple[BatchOutcome, SaveReceipt]:
    """預覽、核對後立即儲存（CLI 使用）。參考條件表本身有問題、或讀取期間來源被改過時回傳整批錯誤、不核對任何說明書。"""
    try:
        outcome = check_batch(preview_batch(config, reference_sheet, term_sheets))
    except IngestionError as e:
        if e.reason_code == "source_changed":  # CLI 沒有預覽可重新載入
            e = IngestionError(e.reason_code, "參考條件表、說明書或設定檔在核對期間已變更或無法讀取，請重新核對")
        outcome = failed_batch(reference_sheet, e)
        return outcome, SaveReceipt.nothing_to_save(outcome)
    return outcome, save_batch(outcome, out_dir, root=root, now=now)
