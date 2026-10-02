"""單份核對 harness（不分上手）：一份說明書＋參考條件表跑公開批量入口 check_batch，再依 rule_id 取結果。

說明書與參考條件表列由各上手的合成器產生；這裡不引用任何上手的版面或預設值。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fcn_checker.batch import check_batch
from fcn_checker.schema import CheckReport, CheckStatus
from reference_synth import REFERENCE_FORMAT, build_reference_sheet

ROOT = Path(__file__).resolve().parents[1]
REVIEW_STANDARD = ROOT / "config" / "review_standard.toml"
ISSUER_PREFIXES = ROOT / "config" / "issuer_prefixes.toml"

PASS, MISMATCH, REVIEW, NA, ERROR = (
    CheckStatus.PASS,
    CheckStatus.MISMATCH,
    CheckStatus.REVIEW_REQUIRED,
    CheckStatus.NOT_APPLICABLE,
    CheckStatus.ERROR,
)


def cli_root(tmp_path: Path, monkeypatch) -> Path:
    """CLI 的根目錄是執行目錄：把設定檔複製到 tmp_path/config 再切換過去，核對紀錄就寫在 tmp_path/runtime/。"""
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def load_record(root: Path) -> dict[str, Any]:
    """根目錄 runtime/核對紀錄/ 下唯一的一份核對紀錄。"""
    [path] = (root / "runtime" / "核對紀錄").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def check_sheet(pdf: Path, sheet: Path, review_standard: Path = REVIEW_STANDARD) -> CheckReport:
    """以既有的參考條件表核對一份說明書，回傳該份的 CheckReport。"""
    outcome = check_batch(
        [pdf], sheet, review_standard, reference_format=REFERENCE_FORMAT, issuer_prefixes=ISSUER_PREFIXES
    )
    return outcome.items[0].report


def check_rows(
    tmp_path: Path, pdf: Path, rows: list[dict[str, Any]], *, headers: list[str] | None = None
) -> CheckReport:
    """以合成參考條件表（rows）核對一份說明書，回傳該份的 CheckReport。"""
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows, headers)
    return check_sheet(pdf, sheet)


def problems(report: CheckReport) -> set[tuple[str, CheckStatus]]:
    return {(r.rule_id, r.status) for r in report.results if r.status not in (PASS, NA)}


def results(report: CheckReport, rule_id: str, field: str | None = None):
    out = [r for r in report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert out, f"沒有 {rule_id} {field or ''} 的結果"
    return out
