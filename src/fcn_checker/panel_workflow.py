"""PANEL 公開工作流程：選取來源、唯讀預覽及輸入有效性；不執行核對或保存。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .config import load_order_format
from .extraction import extract_lines
from .ingestion import IngestionError, open_pdf, sha256_of
from .orders.inquiry import load_inquiry
from .parsers import barc
from .parsers.layout import Document
from .schema import Evidence


@dataclass(frozen=True)
class TemplateChoice:
    issuer: str
    template: str
    label: str


SUPPORTED_TEMPLATES = (TemplateChoice("BARC", barc.TEMPLATE_ID, "BARC 中文產品說明書"),)


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


class PanelSession:
    """UI 與測試共用的單筆工作階段；任何來源變更都使預覽失效。"""

    def __init__(self, order_format: Path):
        self.order_format = Path(order_format).resolve()
        self.term_sheet: Path | None = None
        self.order: Path | None = None
        self.issuer = "BARC"
        self.template = barc.TEMPLATE_ID
        self.message = "請選取 TS PDF 與 Excel 詢價表。"
        self._preview: Preview | None = None
        self._hashes: tuple[str, ...] = ()

    def select(self, term_sheet: Path | None, order: Path | None, *, issuer="BARC", template=barc.TEMPLATE_ID):
        self.term_sheet = Path(term_sheet).resolve() if term_sheet is not None else None
        self.order = Path(order).resolve() if order is not None else None
        self.issuer, self.template = issuer, template
        self._preview, self._hashes = None, ()
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
            except OSError:
                unchanged = False
            if not unchanged:
                self._preview, self._hashes = None, ()
                self.message = "來源檔案或格式設定已變更／無法讀取，請重新載入預覽。"
        return self._preview

    def load_preview(self) -> Preview:
        self._preview, self._hashes = None, ()
        try:
            if (self.issuer, self.template) not in {(c.issuer, c.template) for c in SUPPORTED_TEMPLATES}:
                raise IngestionError(
                    "template_unsupported", "目前只支援 BARC 中文產品說明書，請重新選取 issuer 與模板。"
                )
            if self.term_sheet is None or self.order is None:
                raise IngestionError("selection_missing", "請先選取 TS PDF 與 Excel 詢價表。")
            before = self._fingerprints()
            fmt = load_order_format(self.order_format)
            if fmt.issuer != self.issuer:
                raise IngestionError("issuer_mismatch", "Excel 格式設定的 issuer 與目前選取不符。")
            with open_pdf(self.term_sheet) as doc:
                lines = extract_lines(doc)
            detection = barc.detect(Document(lines))
            if not detection.matched:
                raise IngestionError("template_mismatch", "PDF 不符合選取的 BARC 模板：" + "；".join(detection.failed))
            code = barc.product_code(lines)
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
