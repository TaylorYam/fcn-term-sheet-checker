"""PANEL 公開工作流程：來源預覽、核對及失效檢查及手動保存報告。"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .checker import CheckReport, run_check
from .config import load_order_format
from .extraction import extract_lines
from .ingestion import IngestionError, open_pdf, sha256_of
from .issuers import REGISTRY, Issuer
from .orders.inquiry import load_inquiry
from .reporting import to_json, to_markdown
from .schema import CheckResult, CheckStatus, Evidence


@dataclass(frozen=True)
class SaveReceipt:
    paths: tuple[Path, ...] = ()
    error: str = ""
    cancelled: bool = False
    source_changed: bool = False

    @property
    def complete(self) -> bool:
        return len(self.paths) == 2 and not self.error and not self.source_changed

    @property
    def summary(self) -> str:
        if self.cancelled:
            return "已取消儲存，核對結果仍保留。"
        saved = {p.suffix: p for p in self.paths}
        lines = [f"{label}：{saved.get(ext, '未儲存')}" for label, ext in (("JSON", ".json"), ("Markdown", ".md"))]
        if self.error:
            lines.append("儲存失敗：" + self.error)
        if self.source_changed:
            lines.append("儲存期間來源已變更；已寫入檔案屬於先前核對，請重新載入。")
        return "\n".join(lines)


@dataclass(frozen=True)
class TemplateChoice:
    issuer: str
    template: str
    label: str


SUPPORTED_TEMPLATES = tuple(TemplateChoice(i.code, i.template_id, i.label) for i in REGISTRY)


def _issuer_for(issuer: str, template: str) -> Issuer | None:
    return next((i for i in REGISTRY if i.code == issuer and i.template_id == template), None)


@dataclass(frozen=True)
class Condition:
    label: str
    value: str | Decimal | dt.date | bool | None
    source: str


@dataclass(frozen=True)
class Preview:
    product_code: str | None
    product_code_note: str
    product_code_evidence: tuple[Evidence, ...]
    conditions: tuple[Condition, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PanelOutcome:
    report: CheckReport
    stopped: bool

    @property
    def headline(self) -> str:
        if self.stopped:
            reasons = [
                r.message for r in self.report.results if r.status not in (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE)
            ]
            return "核對已停止，請人工處理：" + "；".join(reasons)
        if self.report.status == CheckStatus.REVIEW_REQUIRED:
            return "有項目需人工覆核；其餘欄位已繼續核對。"
        if self.report.status == CheckStatus.MISMATCH:
            return "有不一致項目，請依頁碼與原文人工核對。"
        if self.report.not_covered:
            return "已核對項目一致；待處理項目仍需人工核對。"
        return "已核對項目一致。"

    @property
    def ordered_results(self) -> tuple[CheckResult, ...]:
        priority = {
            CheckStatus.ERROR: 0,
            CheckStatus.MISMATCH: 1,
            CheckStatus.REVIEW_REQUIRED: 2,
            CheckStatus.PASS: 3,
            CheckStatus.NOT_APPLICABLE: 4,
        }
        return tuple(sorted(self.report.results, key=lambda r: priority[r.status]))


class PanelSession:
    """UI 與測試共用的單筆工作階段；任何來源變更都使預覽失效。"""

    def __init__(
        self,
        order_format: Path | None = None,
        review_standard: Path = Path("config/review_standard.toml"),
        order_formats_dir: Path | None = None,
    ):
        """order_format 未指定時，依選取的上手使用預設詢價格式設定。

        order_formats_dir 指定時從該資料夾取各上手設定檔（雙擊入口用，不依賴工作目錄）。
        """
        self._order_format = Path(order_format).resolve() if order_format is not None else None
        base = Path(order_formats_dir) if order_formats_dir is not None else None
        self._default_formats = {
            i.code: ((base / i.order_format.name) if base else i.order_format).resolve() for i in REGISTRY
        }
        self.review_standard = Path(review_standard).resolve()
        self.term_sheet: Path | None = None
        self.order: Path | None = None
        self.issuer = SUPPORTED_TEMPLATES[0].issuer
        self.template = SUPPORTED_TEMPLATES[0].template
        self.message = "請選取 TS PDF 與 Excel 詢價表。"
        self._preview: Preview | None = None
        self._hashes: tuple[str, ...] = ()
        self._outcome: PanelOutcome | None = None
        self._review_hash: str | None = None

    @property
    def order_format(self) -> Path:
        if self._order_format is not None:
            return self._order_format
        return self._default_formats.get(self.issuer, self._default_formats[REGISTRY[0].code])

    def select(
        self,
        term_sheet: Path | None,
        order: Path | None,
        *,
        issuer=SUPPORTED_TEMPLATES[0].issuer,
        template=SUPPORTED_TEMPLATES[0].template,
    ):
        self.term_sheet = Path(term_sheet).resolve() if term_sheet is not None else None
        self.order = Path(order).resolve() if order is not None else None
        self.issuer, self.template = issuer, template
        self._preview, self._hashes = None, ()
        self._outcome, self._review_hash = None, None
        self.message = "來源已更新，請重新載入預覽。"

    def _fingerprints(self) -> tuple[str, ...]:
        if self.term_sheet is None or self.order is None:
            raise IngestionError("selection_missing", "請先選取 TS PDF 與 Excel 詢價表。")
        return tuple(sha256_of(path) for path in (self.term_sheet, self.order, self.order_format))

    @property
    def preview(self) -> Preview | None:
        if self._preview is not None:
            try:
                unchanged = self._hashes == self._fingerprints()
                if unchanged and self._outcome is not None:
                    unchanged = self._review_hash == sha256_of(self.review_standard)
            except OSError:
                unchanged = False
            if not unchanged:
                self._preview, self._hashes = None, ()
                self._outcome, self._review_hash = None, None
                self.message = "來源檔案或格式設定已變更／無法讀取，請重新載入預覽。"
        return self._preview

    def load_preview(self) -> Preview:
        self._preview, self._hashes = None, ()
        self._outcome, self._review_hash = None, None
        try:
            chosen = _issuer_for(self.issuer, self.template)
            if chosen is None:
                supported = "、".join(c.label for c in SUPPORTED_TEMPLATES)
                raise IngestionError("template_unsupported", f"目前只支援 {supported}，請重新選取 issuer 與模板。")
            if self.term_sheet is None or self.order is None:
                raise IngestionError("selection_missing", "請先選取 TS PDF 與 Excel 詢價表。")
            before = self._fingerprints()
            fmt = load_order_format(self.order_format)
            if fmt.issuer != self.issuer:
                raise IngestionError("issuer_mismatch", "Excel 格式設定的 issuer 與目前選取不符。")
            with open_pdf(self.term_sheet) as doc:
                lines = extract_lines(doc)
            detection = chosen.detect(lines)
            if not detection.matched:
                raise IngestionError(
                    "template_mismatch", f"PDF 不符合選取的 {chosen.code} 模板：" + "；".join(detection.failed)
                )
            code = chosen.product_code(lines)
            record = load_inquiry(self.order, fmt)
            rows = [Condition("商品代號", record.product_code.value, record.product_code.source)]
            for label, name in fmt.columns.items():
                if name in record.fields:
                    value = record.fields[name]
                    rows.append(Condition(label, value.value, value.source))
                else:
                    rows.append(Condition(label, None, f"{fmt.sheet}（未找到欄名）"))
            warnings = [f"未找到欄名：{label}" for label in record.missing_columns]
            warnings.extend(f"未知欄名：{v.value}（{v.source}）" for v in record.unknown_columns)
            warnings.extend(f"重複欄名：{v.value}（{v.source}）" for v in record.duplicate_columns)
            if before != self._fingerprints():
                raise IngestionError("source_changed", "讀取期間來源已變更，請重新載入預覽。")
            self._preview = Preview(
                code.value if code.ok else None,
                code.note,
                tuple(code.evidence),
                tuple(rows),
                tuple(warnings),
            )
            self._hashes = before
            self.message = "預覽已載入，請確認 PDF 商品代號與 Excel 條件。"
            return self._preview
        except OSError as e:
            self.message = "來源檔案或設定無法讀取，請確認檔案存在且有讀取權限。"
            raise IngestionError("source_unreadable", self.message) from e
        except IngestionError as e:
            self.message = str(e)
            raise

    @property
    def outcome(self) -> PanelOutcome | None:
        return self._outcome if self.preview is not None else None

    def start_check(self) -> PanelOutcome:
        if self.preview is None:
            raise IngestionError("preview_required", "請先載入並確認當次預覽，來源變更後須重新載入。")
        self._outcome, self._review_hash = None, None
        try:
            hashes = self._hashes
            review_hash = sha256_of(self.review_standard)
            report = run_check(
                self.term_sheet, self.order, self.review_standard, self.order_format, stop_on_pairing_failure=True
            )
            if hashes != self._fingerprints() or review_hash != sha256_of(self.review_standard):
                self._preview, self._hashes = None, ()
                raise IngestionError("source_changed", "核對期間來源或審查標準已變更，請重新載入預覽。")
            pairing = next((r for r in report.results if r.rule_id == "field.product_code"), None)
            stopped = (
                report.template is None
                or report.status == CheckStatus.ERROR
                or pairing is None
                or pairing.status != CheckStatus.PASS
            )
            self._outcome = PanelOutcome(report, stopped)
            self._review_hash = review_hash
            self.message = self._outcome.headline
            return self._outcome
        except OSError as e:
            self.message = "核對已停止：來源或審查標準無法讀取，請確認檔案與權限後重新載入。"
            raise IngestionError("source_unreadable", self.message) from e
        except IngestionError as e:
            self.message = str(e)
            raise

    def save_report(self, out_dir: Path | None) -> SaveReceipt:
        if out_dir is None:
            return SaveReceipt(cancelled=True)
        outcome = self.outcome
        if outcome is None:
            raise IngestionError("result_required", "請先核對當次來源；來源變更後須重新載入與核對。")
        # 每次保存使用新名稱，exclusive create 也防止碰撞時覆蓋。
        stem = "FCN_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex
        saved: list[Path] = []
        error = ""
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            contents = (
                ("json", json.dumps(to_json(outcome.report), ensure_ascii=False, indent=2) + "\n"),
                ("md", to_markdown(outcome.report)),
            )
            for extension, content in contents:
                path = out_dir / f"{stem}.check.{extension}"
                created = False
                try:
                    with path.open("x", encoding="utf-8") as stream:
                        created = True
                        stream.write(content)
                except OSError:
                    if created:
                        try:
                            path.unlink()
                        except OSError as cleanup_error:
                            error = f"未完成的檔案無法移除：{path}（{cleanup_error}）；"
                    raise
                saved.append(path)
        except OSError as failure:
            error += str(failure)
        return SaveReceipt(tuple(saved), error, source_changed=self.outcome is not outcome)
