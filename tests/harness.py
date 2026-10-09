"""單份核對 harness（不分上手）：一份說明書＋參考條件表跑公開批量入口（預覽＋核對），再依 rule_id 取結果。

說明書旁有同商品投資人須知（`<商品代號>_IIS.pdf`，合成器預設會一起寫出）時一起核對：說明書與投資人須知要
一起選取（ADR 0007），只給說明書會因「這批缺投資人須知」轉人工覆核。

`CONFIG` 是以 repo config 載入一次的核對設定 fixture；要換某個設定檔或上手註冊表時用 `load_config`／
`CheckConfig.with_registry`。審查標準的原始內容 `STANDARD` 也只在這裡讀一次，三家合成器都從這裡取固定文字。

說明書與參考條件表列由各上手的合成器產生（商品規格與參考條件表列在 tests/reference_synth.py）；
這裡不引用任何上手的版面或預設值，也不 import 合成器。
"""

from __future__ import annotations

import json
import shutil
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fcn_checker.batch import BatchOutcome, check_batch, preview_batch
from fcn_checker.check_config import CheckConfig, ConfigPaths
from fcn_checker.issuers import REGISTRY, Issuer
from fcn_checker.schema import CheckReport, CheckStatus

ROOT = Path(__file__).resolve().parents[1]
REVIEW_STANDARD = ROOT / "config" / "review_standard.toml"
REFERENCE_FORMAT = ROOT / "config" / "reference_sheet.toml"
ISSUER_PREFIXES = ROOT / "config" / "issuer_prefixes.toml"
STANDARD: dict[str, Any] = tomllib.loads(REVIEW_STANDARD.read_text(encoding="utf-8"))  # 審查標準原文，只讀這一次
CURRENCY_ISO: dict[str, str] = dict(STANDARD["currency"])  # 幣別中文 → ISO 代碼


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


def iis_path(ts: Path) -> Path:
    """說明書 `<商品代號>_TS.pdf` 旁的同商品投資人須知 `<商品代號>_IIS.pdf`（各上手合成器共用）。"""
    return ts.with_name(ts.stem[: -len("_TS")] + "_IIS.pdf")


def with_iis(pdfs: Sequence[Path]) -> list[Path]:
    """每份 `<商品代號>_TS.pdf` 旁若有同商品 `<商品代號>_IIS.pdf` 且沒選到，就加在最後（說明書的順序不變）。"""
    out = [Path(p) for p in pdfs]
    for p in list(out):
        sibling = iis_path(p)
        if p.stem.endswith("_TS") and sibling.exists() and sibling not in out:
            out.append(sibling)
    return out


def check_all(sheet: Path, pdfs: Sequence[Path], config: CheckConfig = CONFIG) -> BatchOutcome:
    """預覽＋核對（不儲存），回傳批量核對結果；說明書旁的同商品投資人須知一起核對（`with_iis`）。"""
    return check_batch(preview_batch(config, sheet, with_iis(pdfs)))


def check_sheet(pdf: Path, sheet: Path, config: CheckConfig = CONFIG) -> CheckReport:
    """以既有的參考條件表核對一份說明書，回傳該份的 CheckReport。"""
    return check_all(sheet, [pdf], config).items[0].report


def problems(report: CheckReport) -> set[tuple[str, CheckStatus]]:
    return {(r.rule_id, r.status) for r in report.results if r.status not in (PASS, NA)}


def results(report: CheckReport, rule_id: str, field: str | None = None):
    out = [r for r in report.results if r.rule_id == rule_id and (field is None or r.field == field)]
    assert out, f"沒有 {rule_id} {field or ''} 的結果"
    return out
