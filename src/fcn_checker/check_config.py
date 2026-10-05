"""核對設定：審查標準、參考條件表格式、上手編號對照與上手註冊表，一次載入、之後只傳這一個值（Issue #92）。

設定檔的預設位置只在這裡定義；CLI 依參數、PANEL 依設定資料夾（缺檔時改用程式內建設定）建立 `ConfigPaths`，
再以 `ConfigPaths.load` 載入。載入前先取設定檔的 hash（`CheckConfig.files`），來源快照與核對紀錄都取自它。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config import ReferenceFormat, ReviewStandard, load_issuer_prefixes, load_reference_format, load_review_standard
from .ingestion import SourceSnapshot
from .issuers import REGISTRY, Issuer

CONFIG_DIR = Path("config")  # 預設設定資料夾（相對於執行目錄）
REVIEW_STANDARD_FILE = "review_standard.toml"
REFERENCE_FORMAT_FILE = "reference_sheet.toml"
ISSUER_PREFIXES_FILE = "issuer_prefixes.toml"
# 開發環境（editable 安裝）的 repo config；PANEL 安裝是非 editable，內建設定由雙擊入口另外指定（專案的 config）
BUILTIN_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _with_fallback(name: str, config_dir: Path | None, builtin_dir: Path | None) -> Path:
    """config_dir 有該設定檔就用它；沒有就用程式內建的同名設定（舊安裝的根目錄 config 沒有新增的設定檔）。"""
    if config_dir is not None and (Path(config_dir) / name).is_file():
        return (Path(config_dir) / name).resolve()
    return (Path(builtin_dir or BUILTIN_CONFIG) / name).resolve()


@dataclass(frozen=True)
class ConfigPaths:
    """三個設定檔的位置；預設為執行目錄的 config/（CLI 的預設值）。"""

    review_standard: Path = CONFIG_DIR / REVIEW_STANDARD_FILE
    reference_format: Path = CONFIG_DIR / REFERENCE_FORMAT_FILE
    issuer_prefixes: Path = CONFIG_DIR / ISSUER_PREFIXES_FILE

    @classmethod
    def with_fallback(
        cls,
        review_standard: Path = CONFIG_DIR / REVIEW_STANDARD_FILE,
        config_dir: Path | None = CONFIG_DIR,
        builtin_dir: Path | None = None,
    ) -> ConfigPaths:
        """PANEL：config_dir 有參考條件表格式與上手編號對照就用它的，沒有就用 builtin_dir（未指定時為開發環境的 repo config）。"""
        return cls(
            Path(review_standard).resolve(),
            _with_fallback(REFERENCE_FORMAT_FILE, config_dir, builtin_dir),
            _with_fallback(ISSUER_PREFIXES_FILE, config_dir, builtin_dir),
        )

    @property
    def labelled(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("審查標準", self.review_standard),
            ("參考條件表格式", self.reference_format),
            ("上手編號對照", self.issuer_prefixes),
        )

    def load(self, registry: Sequence[Issuer] = REGISTRY) -> CheckConfig:
        """載入全部設定；設定檔缺檔或格式錯誤時丟出 IngestionError（訊息寫出哪個設定檔、什麼問題）。"""
        paths = tuple(map(Path, (self.review_standard, self.reference_format, self.issuer_prefixes)))
        files = SourceSnapshot.take(paths)  # 讀取前先取 hash：之後設定檔被改過，來源快照就會失效
        return CheckConfig(
            review_standard=load_review_standard(paths[0]),
            reference_format=load_reference_format(paths[1]),
            issuer_prefixes=load_issuer_prefixes(paths[2]),
            registry=tuple(registry),
            paths=ConfigPaths(*paths),
            files=files,
        )


DEFAULTS = ConfigPaths()  # 預設位置：執行目錄的 config/（CLI 參數與 PANEL 的預設值）


@dataclass(frozen=True)
class CheckConfig:
    """一次核對使用的全部設定：已載入的設定檔內容、上手註冊表，以及設定檔的路徑與載入前取的 hash。"""

    review_standard: ReviewStandard
    reference_format: ReferenceFormat
    issuer_prefixes: dict[str, str]  # 上手編號（三碼）→ 上手代號
    registry: tuple[Issuer, ...]
    paths: ConfigPaths
    files: SourceSnapshot = field(repr=False)  # 設定檔的路徑與 hash（載入前取）

    def with_registry(self, registry: Sequence[Issuer]) -> CheckConfig:
        """換上手註冊表（測試放入假上手）；設定檔內容與檔案資訊不變。"""
        return replace(self, registry=tuple(registry))

    def record(self) -> dict[str, Any]:
        """核對紀錄的設定檔資訊：路徑、hash 與版本。"""
        p, files = self.paths, self.files
        return {
            "review_standard": {**files.meta(p.review_standard), "version": self.review_standard.version},
            "reference_format": {**files.meta(p.reference_format), "version": self.reference_format.version},
            "issuer_prefixes": files.meta(p.issuer_prefixes),
        }
