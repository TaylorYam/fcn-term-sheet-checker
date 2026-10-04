"""核對紀錄：每次儲存核對結果時，在根目錄 `runtime/核對紀錄/` 寫一份整批 JSON，供維護人員事後追查（Issue #73）。

根目錄和設定檔一致：CLI 是執行目錄，PANEL 是安裝根目錄。檔名 `<YYYYMMDD-HHMMSS>.json`，與同次核對結果檔的
時間戳相同；不覆蓋既有紀錄。核對結果檔沒寫成（例如同名檔已存在）時仍寫紀錄並記下錯誤；參考條件表核對後被改過時
兩者都不寫。內容：執行 metadata（程式版本與 commit、設定檔與審查標準的路徑與 hash、
參考條件表與每份 PDF 的 hash、執行時間）、核對結果檔路徑、整批錯誤，以及每份 PDF 的整體狀態、逐項結果、
證據（頁碼、原文、儲存格位置）與回填決策。作業人員不需要看這份紀錄。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ingestion import write_new
from .schema import CheckReport, CheckResult, CheckStatus
from .version import program_commit

if TYPE_CHECKING:
    from .backfill import CellDecision
    from .batch import BatchOutcome

RECORD_DIR = Path("runtime") / "核對紀錄"
RECORD_VERSION = 2


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return v


def _result_dict(r: CheckResult) -> dict[str, Any]:
    return {
        "rule_id": r.rule_id,
        "rule_version": r.rule_version,
        "field": r.field,
        "status": r.status.value,
        "expected": _plain(r.expected),
        "actual": _plain(r.actual),
        "tolerance": r.tolerance,
        "reason_code": r.reason_code,
        "message": r.message,
        "document_evidence": [e.to_dict() for e in r.document_evidence],
        "order_source": list(r.order_source),
        "column": "、".join(r.item.columns),
        "item": {"name": r.item.name, "source": r.item.source.value, "columns": list(r.item.columns)},
    }


def _backfill_dict(d: CellDecision) -> dict[str, Any]:
    return {
        "column": d.column,
        "cell": d.cell,
        "sheet_value": _plain(d.sheet_value),
        "expected": _plain(d.expected),
        "action": d.action.value,
    }


def to_json(report: CheckReport) -> dict[str, Any]:
    """一份說明書的核對結果。"""
    counts = {s.value: sum(1 for r in report.results if r.status == s) for s in CheckStatus}
    out = {
        "status": report.status.value,
        "template": report.template,
        "summary": counts,
        "results": [_result_dict(r) for r in report.results],
        "not_covered": report.not_covered,
        "metadata": _plain(report.metadata),
    }
    if report.backfill:
        out["backfill"] = [_backfill_dict(d) for d in report.backfill]
    return out


def save_record(outcome: BatchOutcome, root: Path, now: dt.datetime) -> Path:
    """寫出這次儲存的核對紀錄並回傳路徑；核對結果檔處理完後呼叫，才記得到核對結果檔路徑與是否回填。

    檔案已存在（不覆蓋）或無法寫入時丟出 IngestionError。
    """
    path = root / RECORD_DIR / f"{now:%Y%m%d-%H%M%S}.json"
    text = json.dumps(_record(outcome, root, now), ensure_ascii=False, indent=2, default=str) + "\n"
    write_new(path, text.encode("utf-8"), "核對紀錄")
    return path


def _record(outcome: BatchOutcome, root: Path, now: dt.datetime) -> dict[str, Any]:
    return {
        "record_version": RECORD_VERSION,
        "saved_at": now.astimezone().isoformat(timespec="seconds"),
        "status": outcome.status.value,
        "result_file": str(outcome.output) if outcome.output else None,
        "metadata": _plain({**outcome.metadata, "program_commit": program_commit(root)}),
        "errors": [_result_dict(e) for e in outcome.errors],
        "items": [
            {
                "pdf": i.term_sheet.name,
                "issuer": i.issuer,
                "product_code": i.product_code,
                "reference_row": i.reference_row,
                "filled": i.filled,
                "manual_release": i.released,
                **to_json(i.report),
            }
            for i in outcome.items
        ],
    }
