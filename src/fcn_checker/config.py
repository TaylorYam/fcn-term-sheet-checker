"""載入審查標準、參考條件表格式與上手編號對照設定（TOML）。"""

from __future__ import annotations

import datetime as dt
import string
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .ingestion import IngestionError

# 商品名稱樣板可用的佔位符：天期、幣別，以及依說明書欄位填入的 memory（記憶式）、maxi（標的數 ≥ 2）、daily（KO 每日觀察），
# 與 underlying（標的寫法：1 檔填 single_*、2 檔以上填 basket_*）
NAME_FLAGS = ("memory", "maxi", "daily")
NAME_PLACEHOLDERS = frozenset(
    {"tenor", "ccy_zh", "ccy"} | {f"{f}_{lang}" for f in (*NAME_FLAGS, "underlying") for lang in ("zh", "en")}
)
# 發行機構全名比對時可另外忽略的寫法差異（審查標準 issuer_name_ignore）→ 結果上的說明
ISSUER_NAME_IGNORES = {"brackets": "括號（全形／半形）", "trailing_period": "英文名結尾的句點"}
RISK_LEVEL = "{level}"  # 風險等級寫法（審查標準 risk.level_formats）中填入 RRn 的位置


@dataclass(frozen=True)
class ProductNameTemplate:
    zh: str
    en: str
    memory_zh: str
    memory_en: str
    normalize_brackets: bool
    maxi_zh: str = ""
    maxi_en: str = ""
    daily_zh: str = ""
    daily_en: str = ""
    ignore_whitespace_en: bool = False
    single_zh: str = ""  # {underlying_zh}／{underlying_en}：標的 1 檔時填入
    single_en: str = ""
    basket_zh: str = ""  # 標的 2 檔以上時填入
    basket_en: str = ""

    def placeholders(self, lang: str) -> frozenset[str]:
        """樣板（zh／en）用到的佔位符名稱。"""
        return frozenset(name for _, name, _, _ in string.Formatter().parse(getattr(self, lang)) if name is not None)

    def flag_text(self, flag: str, lang: str) -> str:
        return getattr(self, f"{flag}_{lang}")

    def underlying_text(self, basket: bool, lang: str) -> str:
        """{underlying_<lang>} 的標的寫法：2 檔以上用 basket_*，1 檔用 single_*。"""
        return getattr(self, f"{'basket' if basket else 'single'}_{lang}")


@dataclass(frozen=True)
class IssuerStandard:
    """某上手適用的審查標準：上手專屬值已解析（固定警語有上手版本就用，否則用預設）。"""

    issuer: str
    fixed_warning: str
    # 固定警語的允許寫法：fixed_warning 與只換開頭句的版本（risk.fixed_warning_openings）
    fixed_warnings: tuple[str, ...]
    fixed_warning_occurrences: int
    risk_level_formats: tuple[str, ...]  # 風險等級的寫法，{level} 處為 RRn（risk.level_formats；沒列出的上手為【RRn】）
    issuer_name: str | None  # None → 審查標準沒有該上手的值，規則轉人工覆核
    issuer_name_ignore: frozenset[str]  # 發行機構全名比對時另外忽略的寫法差異（ISSUER_NAME_IGNORES 的子集）
    product_name: ProductNameTemplate | None
    distributor_name: str
    distributor_phone: str
    distributor_phone_equivalents: tuple[str, ...]  # 電話可接受的其他寫法（所有上手適用）
    distributor_address: str
    iis_fixed_warning_occurrences: int | None  # 投資人須知的固定警語次數；None → 審查標準沒有該上手的值


@dataclass(frozen=True)
class ReviewStandard:
    version: int
    effective_date: dt.date
    approval_dates: tuple[dt.date, ...]  # 歷次審查通過日期，由舊到新
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
    iis_pages: int  # 投資人須知必須剛好的頁數
    iis_fixed_warning_occurrences: dict[str, int]  # 上手代號（小寫）→ 固定警語在投資人須知出現的次數
    fixed_warning_by_issuer: dict[str, str] = field(default_factory=dict)
    distributor_phone_equivalents: tuple[str, ...] = ()
    fixed_warning_openings: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 上手 → 警語開頭句的其他寫法
    risk_level_formats: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 上手 → 風險等級寫法
    issuer_name_ignore: dict[str, frozenset[str]] = field(default_factory=dict)  # 上手 → 全名比對另外忽略的差異

    def approval_date_on(self, trade_date: dt.date) -> dt.date | None:
        """交易日當天或之前最近一次的審查通過日期；交易日早於最早的日期時為 None。"""
        return next((d for d in reversed(self.approval_dates) if d <= trade_date), None)

    def for_issuer(self, issuer: str) -> IssuerStandard:
        key = issuer.lower()
        warning = self.fixed_warning_by_issuer.get(key, self.fixed_warning)
        rest = warning[warning.find("。") + 1 :]  # 開頭句以後；有其他開頭寫法時載入已確認有句號
        return IssuerStandard(
            issuer=issuer,
            fixed_warning=warning,
            fixed_warnings=(warning, *(alt + rest for alt in self.fixed_warning_openings.get(key, ()))),
            fixed_warning_occurrences=self.fixed_warning_occurrences,
            risk_level_formats=self.risk_level_formats.get(key, ("【" + RISK_LEVEL + "】",)),
            issuer_name=self.issuer_names.get(key),
            issuer_name_ignore=self.issuer_name_ignore.get(key, frozenset()),
            product_name=self.product_names.get(key),
            distributor_name=self.distributor_name,
            distributor_phone=self.distributor_phone,
            distributor_phone_equivalents=self.distributor_phone_equivalents,
            distributor_address=self.distributor_address,
            iis_fixed_warning_occurrences=self.iis_fixed_warning_occurrences.get(key),
        )


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
    checked_value: str  # TS、IIS 欄的打勾寫法（核對沒問題）
    issuer_values: dict[str, str]  # 上手代號 → 發行機構欄的寫法
    columns: dict[str, str]  # Excel 欄名 → 標準欄位
    ignored: tuple[str, ...]
    ko_observation_values: dict[str, str]
    ko_memory_values: dict[str, bool]
    ki_type_values: dict[str, str]
    initial_pricing_values: dict[str, str]  # 期初定價：開盤價／收盤價／VWAP → open／close／vwap


def _load(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise IngestionError("config_not_found", f"找不到{what}設定檔：{path}")
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise IngestionError("config_invalid", f"{what}設定檔格式錯誤：{e}") from e


def _product_name(issuer: str, n: dict[str, Any]) -> ProductNameTemplate:
    tpl = ProductNameTemplate(
        zh=n["zh"],
        en=n["en"],
        memory_zh=n["memory_zh"],
        memory_en=n["memory_en"],
        normalize_brackets=bool(n.get("normalize_brackets", True)),
        maxi_zh=n.get("maxi_zh", ""),
        maxi_en=n.get("maxi_en", ""),
        daily_zh=n.get("daily_zh", ""),
        daily_en=n.get("daily_en", ""),
        ignore_whitespace_en=bool(n.get("ignore_whitespace_en", False)),
        single_zh=n.get("single_zh", ""),
        single_en=n.get("single_en", ""),
        basket_zh=n.get("basket_zh", ""),
        basket_en=n.get("basket_en", ""),
    )
    for lang in ("zh", "en"):
        unknown = sorted(tpl.placeholders(lang) - NAME_PLACEHOLDERS)
        if unknown:
            raise IngestionError(
                "config_invalid",
                f"審查標準商品名稱樣板 product_name.{issuer}.{lang} 有未知的佔位符："
                + "、".join(f"{{{u}}}" for u in unknown)
                + "（可用："
                + "、".join(f"{{{p}}}" for p in sorted(NAME_PLACEHOLDERS))
                + "）",
            )
        missing = [k for k in (f"single_{lang}", f"basket_{lang}") if not getattr(tpl, k)]
        if f"underlying_{lang}" in tpl.placeholders(lang) and missing:
            raise IngestionError(
                "config_invalid",
                f"審查標準商品名稱樣板 product_name.{issuer}.{lang} 用了 {{underlying_{lang}}}，"
                f"但沒有設定 {'、'.join(missing)}（1 檔與 2 檔以上的標的寫法）",
            )
    return tpl


def _per_issuer_lists(d: dict[str, Any], where: str) -> dict[str, tuple[str, ...]]:
    """`[<表>]` 裡每個上手（小寫）一串不空白的文字。"""
    if not isinstance(d, dict):
        raise IngestionError("config_invalid", f"審查標準 {where} 須是一個表（[{where}]）")
    out = {}
    for issuer, values in d.items():
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
            raise IngestionError("config_invalid", f"審查標準 {where}.{issuer} 須是一串不空白的文字")
        out[issuer] = tuple(values)
    return out


def _risk_extras(risk: dict[str, Any], warnings: dict[str, str]) -> dict[str, Any]:
    """依上手的固定警語開頭句其他寫法與風險等級寫法；寫法有誤時載入就回報。"""
    openings = _per_issuer_lists(risk.get("fixed_warning_openings", {}), "risk.fixed_warning_openings")
    for issuer, values in openings.items():
        warning = warnings.get(issuer, risk["fixed_warning"])
        if "。" not in warning or any(not v.endswith("。") for v in values):
            raise IngestionError(
                "config_invalid",
                f"審查標準 risk.fixed_warning_openings.{issuer}：固定警語與每個開頭寫法都要有以「。」結尾的開頭句",
            )
        own = warning[: warning.index("。") + 1]
        if len({own, *values}) != len(values) + 1:  # 重複的寫法會讓同一段警語算兩次
            raise IngestionError(
                "config_invalid", f"審查標準 risk.fixed_warning_openings.{issuer} 的開頭寫法重複或與原本的開頭相同"
            )
    formats = _per_issuer_lists(risk.get("level_formats", {}), "risk.level_formats")
    for issuer, values in formats.items():
        if any(v.count(RISK_LEVEL) != 1 for v in values):
            raise IngestionError(
                "config_invalid", f"審查標準 risk.level_formats.{issuer} 的每個寫法都要剛好有一個 {RISK_LEVEL}"
            )
    return {"fixed_warning_openings": openings, "risk_level_formats": formats}


def _issuer_name_ignore(d: dict[str, Any]) -> dict[str, frozenset[str]]:
    lists = _per_issuer_lists(d, "issuer_name_ignore")
    for issuer, values in lists.items():
        unknown = sorted(set(values) - set(ISSUER_NAME_IGNORES))
        if unknown:
            raise IngestionError(
                "config_invalid",
                f"審查標準 issuer_name_ignore.{issuer} 有未知的項目：{'、'.join(unknown)}"
                f"（可用：{'、'.join(sorted(ISSUER_NAME_IGNORES))}）",
            )
    return {k: frozenset(v) for k, v in lists.items()}


def _approval_dates(value: Any) -> tuple[dt.date, ...]:
    """distributor.approval_dates：至少一筆、都是日期、不重複；回傳由舊到新。"""
    where = "審查標準 distributor.approval_dates"
    if not isinstance(value, list) or not value:
        raise IngestionError("config_invalid", f"{where} 須列出至少一個審查通過日期，例如 [2025-12-18, 2026-06-11]")
    bad = [v for v in value if type(v) is not dt.date]
    if bad:
        raise IngestionError("config_invalid", f"{where} 只能放日期（YYYY-MM-DD），不能是：{bad[0]!r}")
    dup = sorted({d for d in value if value.count(d) > 1})
    if dup:
        raise IngestionError("config_invalid", f"{where} 有重複的日期：" + "、".join(d.isoformat() for d in dup))
    return tuple(sorted(value))


def load_review_standard(path: Path) -> ReviewStandard:
    d = _load(path, "審查標準")
    try:
        names = {issuer: _product_name(issuer, n) for issuer, n in d["product_name"].items()}
        warnings = dict(d["risk"].get("fixed_warning_by_issuer", {}))
        return ReviewStandard(
            version=int(d["version"]),
            effective_date=d["effective_date"],
            approval_dates=_approval_dates(d["distributor"]["approval_dates"]),
            chairman=d["distributor"]["chairman"],
            distributor_name=d["distributor"]["name"],
            distributor_phone=d["distributor"]["phone"],
            distributor_phone_equivalents=tuple(d["distributor"].get("phone_equivalents", ())),
            distributor_address=d["distributor"]["address"],
            issuer_names=dict(d["issuer_name"]),
            fees=dict(d["fees"]),
            issue_price_pct=Decimal(str(d["issue_price"]["pct"])),
            risk_level=d["risk"]["level"],
            fixed_warning=d["risk"]["fixed_warning"],
            fixed_warning_by_issuer=warnings,
            **_risk_extras(d["risk"], warnings),
            issuer_name_ignore=_issuer_name_ignore(d.get("issuer_name_ignore", {})),
            fixed_warning_occurrences=int(d["risk"]["fixed_warning_occurrences"]),
            forbidden=tuple(d["wording"]["forbidden"]),
            allowed_phrases=tuple(d["wording"]["allowed_phrases"]),
            product_names=names,
            currency_zh_to_iso=dict(d["currency"]),
            denomination={k: int(v) for k, v in d["denomination"].items()},
            print_date_max_days_after_trade=int(d["dates"]["print_date_max_days_after_trade"]),
            iis_pages=int(d["iis"]["pages"]),
            iis_fixed_warning_occurrences={k: int(v) for k, v in d["iis"]["fixed_warning_occurrences"].items()},
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
            checked_value=layout["checked_value"],
            issuer_values=dict(d["issuer_values"]),
            columns=cols,
            ignored=ignored,
            ko_observation_values=dict(d["values"]["ko_observation"]),
            ko_memory_values={k: bool(v) for k, v in d["values"]["ko_memory"].items()},
            ki_type_values=dict(d["values"]["ki_type"]),
            initial_pricing_values=dict(d["values"]["initial_pricing"]),
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
