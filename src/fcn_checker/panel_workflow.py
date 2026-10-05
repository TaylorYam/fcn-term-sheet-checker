"""PANEL 公開工作流程：參考條件表＋多份說明書的唯讀預覽、核對、失效檢查與手動儲存。

核對與儲存都呼叫批量入口（batch.py），PANEL 不另做規則；按「儲存」之前不寫任何檔案。
每次載入預覽時載入一次核對設定；核對沿用預覽的辨識與讀出，同一份說明書只讀一次。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .batch import (
    BatchItem,
    BatchOutcome,
    BatchPreview,
    Category,
    check_batch,
    preview_batch,
)
from .check_config import CONFIG_DIR, DEFAULTS, ConfigPaths
from .ingestion import IngestionError, SourceSnapshot
from .issuers import REGISTRY, Issuer
from .saving import SaveReceipt, save_batch
from .schema import CheckResult


@dataclass(frozen=True)
class PanelOutcome:
    batch: BatchOutcome

    @property
    def ordered_items(self) -> tuple[BatchItem, ...]:
        """有問題的說明書排前面（ERROR、不一致、需人工覆核／未支援上手），再列通過與人工放行。"""
        return tuple(sorted(self.batch.items, key=lambda i: i.status.display_rank))

    def selection_after_release_change(self, item: BatchItem) -> BatchItem:
        """放行後選清單第一份（下一份要處理的說明書），不跟著放行的那份排到下方；取消放行後仍選那份。"""
        return self.ordered_items[0] if item.released else item

    @staticmethod
    def ordered_results(item: BatchItem) -> tuple[CheckResult, ...]:
        return tuple(sorted(item.report.results, key=lambda r: r.status.display_rank))

    @property
    def headline(self) -> str:
        if self.batch.errors:
            return "核對未完成：" + "；".join(e.message for e in self.batch.errors)
        items = self.batch.items
        parts = [f"{sum(i.category == c for i in items)} 份{c.value}" for c in Category]
        tail = "按「儲存核對結果」後才會寫出核對結果檔（通過與人工放行的說明書回填在「回填後」）。"
        return f"共 {len(items)} 份：" + "、".join(parts) + "。" + tail


class ReleaseState(NamedTuple):
    """PANEL 人工放行按鈕：能否按下，以及要顯示的不能放行原因（空字串表示不顯示）。"""

    allowed: bool
    reason: str


class PanelSession:
    """UI 與測試共用的工作階段；任何來源或設定檔變更都使預覽與結果失效。

    讀取 preview／outcome 不讀檔、不改狀態；來源是否變更由 check_sources 明確檢查（開始核對、放行、
    儲存時也各檢查一次）。PANEL 視窗在背景執行緒用 snapshot.still_valid() 計算，回主執行緒再 invalidate。
    """

    def __init__(
        self,
        review_standard: Path = DEFAULTS.review_standard,
        config_dir: Path | None = CONFIG_DIR,
        *,
        builtin_config_dir: Path | None = None,
        registry: Sequence[Issuer] = REGISTRY,
        install_root: Path | None = None,
    ):
        """config_dir 有參考條件表格式與上手編號對照就用它的；沒有就用 builtin_config_dir（雙擊入口傳入專案的 config；未指定時為開發環境的 repo config）。

        install_root 是根目錄（雙擊入口給的安裝根目錄；沒有時為執行目錄），核對紀錄寫到它的 runtime/核對紀錄。
        """
        self.install_root = Path(install_root or ".").resolve()
        self.paths = ConfigPaths.with_fallback(review_standard, config_dir, builtin_config_dir)
        self.registry = tuple(registry)
        self.reference_sheet: Path | None = None
        self.term_sheets: tuple[Path, ...] = ()
        self.message = "請選取參考條件表與說明書 PDF。"
        self._preview: BatchPreview | None = None  # 帶著核對設定、讀出結果與來源快照
        self._outcome: PanelOutcome | None = None
        self._receipt: SaveReceipt | None = None  # 目前結果最近一次儲存的收據；放行改變後即不再是目前的結果

    @property
    def review_standard(self) -> Path:
        return self.paths.review_standard

    @property
    def reference_format(self) -> Path:
        return self.paths.reference_format

    @property
    def issuer_prefixes(self) -> Path:
        return self.paths.issuer_prefixes

    @property
    def config_paths(self) -> tuple[tuple[str, Path], ...]:
        return self.paths.labelled

    def select(self, reference_sheet: Path | None, term_sheets: Sequence[Path]) -> None:
        self.reference_sheet = Path(reference_sheet).resolve() if reference_sheet is not None else None
        self.term_sheets = tuple(Path(p).resolve() for p in term_sheets)
        self._clear()
        self.message = "來源已更新，請重新載入預覽。"

    def _clear(self) -> None:
        self._preview, self._outcome, self._receipt = None, None, None

    @property
    def preview(self) -> BatchPreview | None:
        return self._preview

    @property
    def outcome(self) -> PanelOutcome | None:
        return self._outcome

    @property
    def receipt(self) -> SaveReceipt | None:
        """目前結果最近一次儲存的收據（含已回填的說明書）；還沒存過或存過後又改了放行時為 None。"""
        return self._receipt

    @property
    def snapshot(self) -> SourceSnapshot | None:
        """目前預覽與結果所依據的來源快照；沒有預覽時為 None。"""
        return self._preview.snapshot if self._preview is not None else None

    def invalidate(self, snapshot: SourceSnapshot) -> None:
        """snapshot 已確認失效：它仍是目前的快照時清除預覽與結果（背景檢查回來時已重新載入就不動）。"""
        if snapshot is not self.snapshot:
            return
        if self._preview is not None:
            self._clear()
            self.message = "來源檔案或設定檔已變更／無法讀取，請重新載入預覽。"

    def check_sources(self) -> bool:
        """明確檢查來源：預覽仍依據相同的來源時為 True；變更或讀不到時清除預覽與結果並回傳 False。"""
        snapshot = self.snapshot
        if snapshot is None:
            return False
        if snapshot.still_valid():
            return True
        self.invalidate(snapshot)
        return False

    def _require_sources(self) -> None:
        if not self.check_sources():
            raise IngestionError("source_changed", "來源檔案或設定檔已變更，結果已失效，請重新載入預覽與核對。")

    def load_preview(self) -> BatchPreview:
        self._clear()
        try:
            if self.reference_sheet is None or not self.term_sheets:
                raise IngestionError("selection_missing", "請先選取參考條件表與至少一份說明書 PDF。")
            config = self.paths.load(self.registry)  # 設定檔有問題時在這裡回報
            preview = preview_batch(config, self.reference_sheet, self.term_sheets)
            if not preview.snapshot.still_valid():
                raise IngestionError("source_changed", "讀取期間來源已變更，請重新載入預覽。")
        except OSError as e:
            self.message = "來源檔案或設定無法讀取，請確認檔案存在且有讀取權限。"
            raise IngestionError("source_unreadable", self.message) from e
        except IngestionError as e:
            self.message = str(e)
            raise
        self._preview = preview
        ready = sum(not r.problem for r in preview.rows)
        self.message = f"預覽已載入：{ready}／{len(preview.rows)} 份可以核對。請確認商品代號與對到的列，再開始核對。"
        return preview

    def start_check(self) -> PanelOutcome:
        """沿用預覽的辨識與讀出核對，不重讀 PDF；預覽後來源或設定檔變更時清除預覽，要求重新載入。"""
        preview = self.preview
        if preview is None:
            raise IngestionError("preview_required", "請先載入並確認當次預覽，來源變更後須重新載入。")
        self._outcome, self._receipt = None, None
        try:
            batch = check_batch(preview)  # 先以預覽的來源快照確認來源未變更
        except IngestionError as e:
            self._clear()
            self.message = str(e)
            raise
        self._outcome = PanelOutcome(batch)
        self.message = self._outcome.headline
        return self._outcome

    def _current(self, item: BatchItem) -> PanelOutcome:
        outcome = self.outcome
        if outcome is None or not any(i is item for i in outcome.batch.items):
            raise IngestionError("result_required", "這份結果已失效，請重新核對後再人工放行。")
        return outcome

    def _current_checked(self, item: BatchItem) -> PanelOutcome:
        """放行／取消放行前：先確認是當次結果，再明確檢查來源。"""
        outcome = self._current(item)
        self._require_sources()
        return outcome

    def release_problem(self, item: BatchItem) -> str:
        """不能人工放行的原因（PANEL 顯示用）；空字串表示可以放行。結果已失效時也不能放行。"""
        try:
            self._current(item)
        except IngestionError as e:
            return str(e)
        return item.release_problem

    def release_state(self, item: BatchItem) -> ReleaseState:
        """PANEL 放行按鈕：能否按下，以及要顯示的不能放行原因（已通過的不必說明）。"""
        problem = self.release_problem(item)
        shown = "" if item.category == Category.PASSED else problem
        return ReleaseState(not problem, shown)

    def release(self, item: BatchItem) -> None:
        """人工放行：視同通過，儲存時回填、不列入錯誤清單；重新載入或重新核對即清除。"""
        outcome = self._current_checked(item)
        outcome.batch.release(item)
        self._after_release_change(outcome)

    def cancel_release(self, item: BatchItem) -> None:
        outcome = self._current_checked(item)
        outcome.batch.cancel_release(item)
        self._after_release_change(outcome)

    def _after_release_change(self, outcome: PanelOutcome) -> None:
        was_saved = self._receipt is not None and self._receipt.output is not None
        self._receipt = None  # 上一次儲存的核對結果檔不再是目前的結果
        stale = "上一次儲存的核對結果檔已不是目前的結果，請再儲存一次。" if was_saved else ""
        self.message = stale + outcome.headline

    def save(self, out_dir: Path | None, *, now: dt.datetime | None = None) -> SaveReceipt:
        """寫核對結果檔到 out_dir、核對紀錄到根目錄；out_dir 為 None 表示使用者取消。"""
        if out_dir is None:
            return SaveReceipt(cancelled=True)
        outcome = self.outcome
        if outcome is None:
            raise IngestionError("result_required", "請先核對當次來源；來源變更後須重新載入與核對。")
        receipt = save_batch(outcome.batch, Path(out_dir), root=self.install_root, now=now)  # 儲存前確認整份來源快照
        if receipt.source_changed:
            self.invalidate(outcome.batch.snapshot)
            raise IngestionError("source_changed", "來源檔案或設定檔已變更，結果已失效，請重新載入預覽與核對。")
        self._receipt = receipt
        return receipt
