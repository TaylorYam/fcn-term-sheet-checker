"""單份核對 harness（不分上手）：一份說明書＋參考條件表跑公開批量入口（預覽＋核對），再依 rule_id 取結果。

說明書旁有同商品投資人須知（`<商品代號>_IIS.pdf`，合成器預設會一起寫出）時一起核對：說明書與投資人須知要
一起選取（ADR 0007），只給說明書會因「這批缺投資人須知」轉人工覆核。

`CONFIG` 是以 repo config 載入一次的核對設定 fixture；要換某個設定檔或上手註冊表時用 `load_config`／
`CheckConfig.with_registry`。

說明書與參考條件表列由各上手的合成器產生；這裡不引用任何上手的版面或預設值。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fcn_checker.batch import BatchOutcome, check_batch, preview_batch
from fcn_checker.check_config import CheckConfig, ConfigPaths
from fcn_checker.issuers import REGISTRY, Issuer
from fcn_checker.schema import CheckReport, CheckStatus
from reference_synth import REFERENCE_FORMAT, build_reference_sheet

ROOT = Path(__file__).resolve().parents[1]
REVIEW_STANDARD = ROOT / "config" / "review_standard.toml"
ISSUER_PREFIXES = ROOT / "config" / "issuer_prefixes.toml"


def load_config(
    *,
    review_standard: Path = REVIEW_STANDARD,
    reference_format: Path = REFERENCE_FORMAT,
    issuer_prefixes: Path = ISSUER_PREFIXES,
    registry: Sequence[Issuer] = REGISTRY,
) -> CheckConfig:
    """載入核對設定；預設全部用 repo config。"""
    return ConfigPaths(review_standard, reference_format, issuer_prefixes).load(registry)


CONFIG = load_config()  # 核對設定 fixture：repo config 只載入一次

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


def with_iis(pdfs: Sequence[Path]) -> list[Path]:
    """每份 `<商品代號>_TS.pdf` 旁若有同商品 `<商品代號>_IIS.pdf` 且沒選到，就加在最後（說明書的順序不變）。"""
    out = [Path(p) for p in pdfs]
    for p in list(out):
        sibling = p.with_name(p.stem[: -len("_TS")] + "_IIS.pdf")
        if p.stem.endswith("_TS") and sibling.exists() and sibling not in out:
            out.append(sibling)
    return out


def check_all(sheet: Path, pdfs: Sequence[Path], config: CheckConfig = CONFIG) -> BatchOutcome:
    """預覽＋核對（不儲存），回傳批量核對結果；說明書旁的同商品投資人須知一起核對（`with_iis`）。"""
    return check_batch(preview_batch(config, sheet, with_iis(pdfs)))


def check_sheet(pdf: Path, sheet: Path, config: CheckConfig = CONFIG) -> CheckReport:
    """以既有的參考條件表核對一份說明書，回傳該份的 CheckReport。"""
    return check_all(sheet, [pdf], config).items[0].report


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
