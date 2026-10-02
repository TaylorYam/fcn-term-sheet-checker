"""PANEL 公開工作流程：參考條件表＋多份說明書的唯讀預覽、核對、失效檢查與手動儲存。

核對與儲存都呼叫批量入口（batch.py），PANEL 不另做規則；按「儲存」之前不寫任何檔案。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .batch import BatchItem, BatchOutcome, BatchPreview, check_batch, preview_batch, save_batch
from .config import resolve_config
from .ingestion import IngestionError, sha256_of
from .issuers import REGISTRY, Issuer
from .schema import CheckResult, CheckStatus


@dataclass(frozen=True)
class PanelOutcome:
    batch: BatchOutcome

    @property
    def ordered_items(self) -> tuple[BatchItem, ...]:
        """有問題的說明書排前面（ERROR、不一致、需人工覆核／未支援上手），再列通過。"""
        return tuple(sorted(self.batch.items, key=lambda i: i.report.status.display_rank))

    @staticmethod
    def ordered_results(item: BatchItem) -> tuple[CheckResult, ...]:
        return tuple(sorted(item.report.results, key=lambda r: r.status.display_rank))

    @property
    def headline(self) -> str:
        if self.batch.errors:
            return "核對未完成：" + "；".join(e.message for e in self.batch.errors)
        items = self.batch.items
        unsupported = sum(i.unsupported for i in items)
        counts = {s: sum(i.report.status == s and not i.unsupported for i in items) for s in CheckStatus}
        parts = [
            f"{counts[CheckStatus.PASS]} 份通過",
            f"{counts[CheckStatus.MISMATCH]} 份不一致",
            f"{counts[CheckStatus.REVIEW_REQUIRED]} 份需人工覆核",
            f"{unsupported} 份未支援上手",
            f"{counts[CheckStatus.ERROR]} 份執行錯誤",
        ]
        tail = "按「儲存」後才會寫出報告，並把通過的說明書回填到新檔。"
        return f"共 {len(items)} 份：" + "、".join(parts) + "。" + tail


@dataclass(frozen=True)
class SaveReceipt:
    cancelled: bool = False
    output: Path | None = None
    report_paths: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.cancelled and self.output is not None and not self.errors

    @property
    def summary(self) -> str:
        if self.cancelled:
            return "已取消儲存，核對結果仍保留。"
        lines = [f"核對結果檔：{self.output}" if self.output else "核對結果檔：未儲存"]
        lines.append(f"報告：已儲存 {len(self.report_paths)} 個檔案")
        lines.extend(self.errors)
        return "\n".join(lines)


class PanelSession:
    """UI 與測試共用的工作階段；任何來源或設定檔變更都使預覽與結果失效。"""

    def __init__(
        self,
        review_standard: Path = Path("config/review_standard.toml"),
        config_dir: Path | None = Path("config"),
        *,
        builtin_config_dir: Path | None = None,
        registry: Sequence[Issuer] = REGISTRY,
    ):
        """config_dir 有參考條件表格式與上手編號對照就用它的；沒有（舊安裝）就用 builtin_config_dir（版本內建設定）。"""
        self.review_standard = Path(review_standard).resolve()
        self.reference_format = resolve_config("reference_sheet.toml", config_dir, builtin_config_dir)
        self.issuer_prefixes = resolve_config("issuer_prefixes.toml", config_dir, builtin_config_dir)
        self.registry = tuple(registry)
        self.reference_sheet: Path | None = None
        self.term_sheets: tuple[Path, ...] = ()
        self.message = "請選取參考條件表與說明書 PDF。"
        self._preview: BatchPreview | None = None
        self._hashes: tuple[str, ...] = ()
        self._outcome: PanelOutcome | None = None

    @property
    def config_paths(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("審查標準", self.review_standard),
            ("參考條件表格式", self.reference_format),
            ("上手編號對照", self.issuer_prefixes),
        )

    def select(self, reference_sheet: Path | None, term_sheets: Sequence[Path]) -> None:
        self.reference_sheet = Path(reference_sheet).resolve() if reference_sheet is not None else None
        self.term_sheets = tuple(Path(p).resolve() for p in term_sheets)
        self._clear()
        self.message = "來源已更新，請重新載入預覽。"

    def _clear(self) -> None:
        self._preview, self._hashes, self._outcome = None, (), None

    def _fingerprints(self) -> tuple[str, ...]:
        if self.reference_sheet is None or not self.term_sheets:
            raise IngestionError("selection_missing", "請先選取參考條件表與至少一份說明書 PDF。")
        paths = (self.reference_sheet, *self.term_sheets, *(p for _, p in self.config_paths))
        return tuple(sha256_of(p) for p in paths)

    def _unchanged(self) -> bool:
        try:
            return self._hashes == self._fingerprints()
        except (OSError, IngestionError):
            return False

    @property
    def preview(self) -> BatchPreview | None:
        if self._preview is not None and not self._unchanged():
            self._clear()
            self.message = "來源檔案或設定檔已變更／無法讀取，請重新載入預覽。"
        return self._preview

    @property
    def outcome(self) -> PanelOutcome | None:
        return self._outcome if self.preview is not None else None

    def load_preview(self) -> BatchPreview:
        self._clear()
        try:
            before = self._fingerprints()
            preview = preview_batch(
                self.term_sheets,
                self.reference_sheet,
                reference_format=self.reference_format,
                issuer_prefixes=self.issuer_prefixes,
                registry=self.registry,
            )
            if before != self._fingerprints():
                raise IngestionError("source_changed", "讀取期間來源已變更，請重新載入預覽。")
        except OSError as e:
            self.message = "來源檔案或設定無法讀取，請確認檔案存在且有讀取權限。"
            raise IngestionError("source_unreadable", self.message) from e
        except IngestionError as e:
            self.message = str(e)
            raise
        self._preview, self._hashes = preview, before
        ready = sum(not r.problem for r in preview.rows)
        self.message = f"預覽已載入：{ready}／{len(preview.rows)} 份可以核對。請確認商品代號與對到的列，再開始核對。"
        return preview

    def start_check(self) -> PanelOutcome:
        if self.preview is None:
            raise IngestionError("preview_required", "請先載入並確認當次預覽，來源變更後須重新載入。")
        self._outcome = None
        hashes = self._hashes
        try:
            batch = check_batch(
                self.term_sheets,
                self.reference_sheet,
                self.review_standard,
                reference_format=self.reference_format,
                issuer_prefixes=self.issuer_prefixes,
                registry=self.registry,
            )
            if hashes != self._fingerprints():
                self._clear()
                raise IngestionError("source_changed", "核對期間來源或設定檔已變更，請重新載入預覽。")
        except OSError as e:
            self._clear()
            self.message = "核對已停止：來源或設定檔無法讀取，請確認檔案與權限後重新載入。"
            raise IngestionError("source_unreadable", self.message) from e
        except IngestionError as e:
            self.message = str(e)
            raise
        self._outcome = PanelOutcome(batch)
        self.message = self._outcome.headline
        return self._outcome

    def save(self, out_dir: Path | None, *, now: dt.datetime | None = None) -> SaveReceipt:
        """寫報告與核對結果檔到 out_dir；out_dir 為 None 表示使用者取消。"""
        if out_dir is None:
            return SaveReceipt(cancelled=True)
        outcome = self.outcome
        if outcome is None:
            raise IngestionError("result_required", "請先核對當次來源；來源變更後須重新載入與核對。")
        batch = save_batch(outcome.batch, Path(out_dir), now=now)
        errors = [f"{i.term_sheet.name} 報告未儲存：{i.save_error}" for i in batch.items if i.save_error]
        errors += [e.message for e in batch.errors]
        if self.outcome is not outcome:
            errors.append("儲存期間來源已變更；已寫入的檔案屬於先前核對，請重新載入。")
        paths = tuple(p for i in batch.items for p in i.report_paths)
        return SaveReceipt(output=batch.output, report_paths=paths, errors=tuple(errors))
