"""載入審查標準與上手詢價格式設定（TOML）。"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ingestion import IngestionError, sha256_of


@dataclass(frozen=True)
class ProductNameStandard:
    """`product_name.<上手>` 分節：名稱樣板與佔位符的填入文字（例 memory_zh）。"""

    zh: str
    en: str
    placeholders: dict[str, str]
    normalize_brackets: bool
    ignore_whitespace_en: bool


@dataclass(frozen=True)
class ReviewStandard:
    version: int
    effective_date: dt.date
    approval_date: dt.date
    chairman: str
    risk_level: str
    fixed_warning: str
    fixed_warning_occurrences: int
    forbidden: tuple[str, ...]
    allowed_phrases: tuple[str, ...]
    fixed_warning_by_issuer: dict[str, str]  # 鍵為上手代號小寫；未列出的上手用 fixed_warning
    product_names: dict[str, ProductNameStandard]  # 鍵為上手代號小寫
    currency_zh_to_iso: dict[str, str]
    denomination: dict[str, int]
    print_date_max_days_after_trade: int
    sha256: str

    def fixed_warning_for(self, issuer: str) -> str:
        return self.fixed_warning_by_issuer.get(issuer.lower(), self.fixed_warning)


@dataclass(frozen=True)
class OrderFormat:
    issuer: str
    version: int
    sheet: str
    product_code_cell: str
    header_row: int
    data_row: int
    columns: dict[str, str]  # Excel 欄名 → 標準欄位
    ignored: tuple[str, ...]
    ko_type_values: dict[str, dict[str, Any]]
    ki_type_values: dict[str, str]
    sha256: str


def _load(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise IngestionError("config_not_found", f"找不到{what}設定檔：{path}")
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise IngestionError("config_invalid", f"{what}設定檔格式錯誤：{e}") from e


def _product_name(d: dict[str, Any]) -> ProductNameStandard:
    flags = ("normalize_brackets", "ignore_whitespace_en")
    placeholders = {k: v for k, v in d.items() if k not in ("zh", "en", *flags)}
    if not all(isinstance(v, str) for v in placeholders.values()):
        raise TypeError("product_name 佔位符的值必須是文字")
    return ProductNameStandard(
        zh=d["zh"],
        en=d["en"],
        placeholders=placeholders,
        normalize_brackets=bool(d.get("normalize_brackets", True)),
        ignore_whitespace_en=bool(d.get("ignore_whitespace_en", False)),
    )


def load_review_standard(path: Path) -> ReviewStandard:
    d = _load(path, "審查標準")
    try:
        return ReviewStandard(
            version=int(d["version"]),
            effective_date=d["effective_date"],
            approval_date=d["distributor"]["approval_date"],
            chairman=d["distributor"]["chairman"],
            risk_level=d["risk"]["level"],
            fixed_warning=d["risk"]["fixed_warning"],
            fixed_warning_occurrences=int(d["risk"]["fixed_warning_occurrences"]),
            forbidden=tuple(d["wording"]["forbidden"]),
            allowed_phrases=tuple(d["wording"]["allowed_phrases"]),
            fixed_warning_by_issuer={k.lower(): v for k, v in d["risk"].get("fixed_warning_by_issuer", {}).items()},
            product_names={k.lower(): _product_name(v) for k, v in d["product_name"].items()},
            currency_zh_to_iso=dict(d["currency"]),
            denomination={k: int(v) for k, v in d["denomination"].items()},
            print_date_max_days_after_trade=int(d["dates"]["print_date_max_days_after_trade"]),
            sha256=sha256_of(path),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"審查標準設定檔缺少或格式錯誤的項目：{e}") from e


def load_order_format(path: Path) -> OrderFormat:
    d = _load(path, "詢價格式")
    try:
        cols = dict(d["columns"])
        ignored = tuple(cols.pop("ignored", ()))
        return OrderFormat(
            issuer=d["issuer"],
            version=int(d["version"]),
            sheet=d["layout"]["sheet"],
            product_code_cell=d["layout"]["product_code_cell"],
            header_row=int(d["layout"]["header_row"]),
            data_row=int(d["layout"]["data_row"]),
            columns=cols,
            ignored=ignored,
            ko_type_values=dict(d["values"]["ko_type"]),
            ki_type_values=dict(d["values"]["ki_type"]),
            sha256=sha256_of(path),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"詢價格式設定檔缺少或格式錯誤的項目：{e}") from e
