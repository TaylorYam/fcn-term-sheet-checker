"""載入審查標準、參考條件表格式與上手編號對照設定（TOML）。"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .ingestion import IngestionError, sha256_of


@dataclass(frozen=True)
class ProductNameTemplate:
    zh: str
    en: str
    memory_zh: str
    memory_en: str
    normalize_brackets: bool
    maxi_en: str = ""
    daily_en: str = ""
    ignore_whitespace_en: bool = False


@dataclass(frozen=True)
class ReviewStandard:
    version: int
    effective_date: dt.date
    approval_date: dt.date
    chairman: str
    distributor_name: str
    distributor_phone: str
    distributor_address: str
    issuer_names: dict[str, str]  # 上手代號（小寫）→ 發行機構中英文法人全名
    fees: dict[str, str]  # 第四章費用項目 → 費率區間
    issue_price_pct: Decimal
    risk_level: str
    fixed_warning: str
    fixed_warning_occurrences: int
    forbidden: tuple[str, ...]
    allowed_phrases: tuple[str, ...]
    product_names: dict[str, ProductNameTemplate]  # 上手代號（小寫）→ 名稱樣板
    currency_zh_to_iso: dict[str, str]
    denomination: dict[str, int]
    print_date_max_days_after_trade: int
    sha256: str
    fixed_warning_by_issuer: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReferenceFormat:
    """參考條件表（多列表格）的版面與欄位對照，所有上手共用。"""

    version: int
    sheet: str
    header_row: int
    first_data_row: int
    key_column: str
    issuer_column: str
    empty_value: str
    issuer_values: dict[str, str]  # 上手代號 → 發行機構欄的寫法
    columns: dict[str, str]  # Excel 欄名 → 標準欄位
    ignored: tuple[str, ...]
    ko_observation_values: dict[str, str]
    ko_memory_values: dict[str, bool]
    ki_type_values: dict[str, str]
    sha256: str


# 開發環境（editable 安裝）的 repo config；PANEL 安裝是非 editable，內建設定由啟動的版本資料夾另外指定
BUILTIN_CONFIG = Path(__file__).resolve().parents[2] / "config"


def resolve_config(name: str, config_dir: Path | None, builtin_dir: Path | None = None) -> Path:
    """config_dir 有該設定檔就用它；沒有就用程式內建的同名設定（舊安裝的根目錄 config 沒有新增的設定檔）。"""
    if config_dir is not None and (Path(config_dir) / name).is_file():
        return (Path(config_dir) / name).resolve()
    return (Path(builtin_dir or BUILTIN_CONFIG) / name).resolve()


def _load(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise IngestionError("config_not_found", f"找不到{what}設定檔：{path}")
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise IngestionError("config_invalid", f"{what}設定檔格式錯誤：{e}") from e


def load_review_standard(path: Path) -> ReviewStandard:
    d = _load(path, "審查標準")
    try:
        names = {
            issuer: ProductNameTemplate(
                zh=n["zh"],
                en=n["en"],
                memory_zh=n["memory_zh"],
                memory_en=n["memory_en"],
                normalize_brackets=bool(n.get("normalize_brackets", True)),
                maxi_en=n.get("maxi_en", ""),
                daily_en=n.get("daily_en", ""),
                ignore_whitespace_en=bool(n.get("ignore_whitespace_en", False)),
            )
            for issuer, n in d["product_name"].items()
        }
        return ReviewStandard(
            version=int(d["version"]),
            effective_date=d["effective_date"],
            approval_date=d["distributor"]["approval_date"],
            chairman=d["distributor"]["chairman"],
            distributor_name=d["distributor"]["name"],
            distributor_phone=d["distributor"]["phone"],
            distributor_address=d["distributor"]["address"],
            issuer_names=dict(d["issuer_name"]),
            fees=dict(d["fees"]),
            issue_price_pct=Decimal(str(d["issue_price"]["pct"])),
            risk_level=d["risk"]["level"],
            fixed_warning=d["risk"]["fixed_warning"],
            fixed_warning_by_issuer=dict(d["risk"].get("fixed_warning_by_issuer", {})),
            fixed_warning_occurrences=int(d["risk"]["fixed_warning_occurrences"]),
            forbidden=tuple(d["wording"]["forbidden"]),
            allowed_phrases=tuple(d["wording"]["allowed_phrases"]),
            product_names=names,
            currency_zh_to_iso=dict(d["currency"]),
            denomination={k: int(v) for k, v in d["denomination"].items()},
            print_date_max_days_after_trade=int(d["dates"]["print_date_max_days_after_trade"]),
            sha256=sha256_of(path),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"審查標準設定檔缺少或格式錯誤的項目：{e}") from e


def load_reference_format(path: Path) -> ReferenceFormat:
    d = _load(path, "參考條件表格式")
    try:
        cols = dict(d["columns"])
        ignored = tuple(cols.pop("ignored", ()))
        layout = d["layout"]
        return ReferenceFormat(
            version=int(d["version"]),
            sheet=layout["sheet"],
            header_row=int(layout["header_row"]),
            first_data_row=int(layout["first_data_row"]),
            key_column=layout["key_column"],
            issuer_column=layout["issuer_column"],
            empty_value=layout["empty_value"],
            issuer_values=dict(d["issuer_values"]),
            columns=cols,
            ignored=ignored,
            ko_observation_values=dict(d["values"]["ko_observation"]),
            ko_memory_values={k: bool(v) for k, v in d["values"]["ko_memory"].items()},
            ki_type_values=dict(d["values"]["ki_type"]),
            sha256=sha256_of(path),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"參考條件表格式設定檔缺少或格式錯誤的項目：{e}") from e


def load_issuer_prefixes(path: Path) -> dict[str, str]:
    """上手編號（三碼）→ 上手代號。"""
    d = _load(path, "上手編號對照")
    try:
        prefixes = {str(k): str(v) for k, v in d["prefixes"].items()}
    except (KeyError, TypeError, AttributeError) as e:
        raise IngestionError("config_invalid", f"上手編號對照設定檔缺少或格式錯誤的項目：{e}") from e
    bad = [k for k in prefixes if not (len(k) == 3 and k.isdigit())]
    if bad:
        raise IngestionError("config_invalid", f"上手編號必須是三位數字：{'、'.join(bad)}")
    return prefixes
