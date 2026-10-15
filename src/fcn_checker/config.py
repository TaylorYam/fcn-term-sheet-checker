"""載入審查標準、參考條件表格式與上手編號對照設定（TOML）。"""

from __future__ import annotations

import datetime as dt
import string
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .ingestion import IngestionError
from .standard_fields import AGENT_PLACES, GUARANTOR_PLACES
from .trading_calendar import Market, TradingCalendar

# 商品名稱樣板可用的佔位符：天期、幣別，以及依說明書欄位填入的 memory（記憶式）、maxi（標的數 ≥ 2）、daily（KO 每日觀察）、
# autocall（Non-Call < 天期，BNP）、quanto（承作幣別 ≠ 標的報價幣別，DBS）、ki（有 KI，DBS、UBS），與 underlying（標的寫法：
# 1 檔填 single_*、2 檔以上填 basket_*）、ko_observation（KO 觀察方式：每日填 daily_*、每期填 periodic_*，UBS）
NAME_FLAGS = ("memory", "maxi", "daily", "autocall", "quanto", "ki")
NAME_PLACEHOLDERS = frozenset(
    {"tenor", "ccy_zh", "ccy"}
    | {f"{f}_{lang}" for f in (*NAME_FLAGS, "underlying", "ko_observation") for lang in ("zh", "en")}
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
    autocall_zh: str = ""  # {autocall_zh}／{autocall_en}：Non-Call < 天期（會提前出場）時填入（BNP、SG）
    autocall_en: str = ""
    autocall_memory_zh: str = ""  # 會提前出場且記憶式時改填這個（有設定時；SG 中文「（記憶式自動提前出場）」）
    autocall_memory_en: str = ""
    ccy_aliases: dict[str, str] = field(
        default_factory=dict
    )  # {ccy} 的其他寫法：ISO 代碼 → 名稱用的代碼（SG：CNH → CNY）
    quanto_zh: str = ""  # {quanto_zh}／{quanto_en}：承作幣別和標的報價幣別不同時填入（DBS「Quanto」）
    quanto_en: str = ""
    ki_zh: str = ""  # {ki_zh}／{ki_en}：有 KI 時填入（DBS「及定價日觸發(歐式)」、UBS「（含觸及下限價事件）」）
    ki_en: str = ""
    # 標的 1 檔沒有樣本、名稱寫法未知：用了 {underlying_*} 而標的只有 1 檔時名稱轉人工覆核，single_* 可不設（DBS）
    single_unsupported: bool = False
    periodic_zh: str = ""  # {ko_observation_*}：KO 每期觀察時填入（每日時填 daily_*；UBS）
    periodic_en: str = ""

    def placeholders(self, lang: str) -> frozenset[str]:
        """樣板（zh／en）用到的佔位符名稱。"""
        return frozenset(name for _, name, _, _ in string.Formatter().parse(getattr(self, lang)) if name is not None)

    def flag_text(self, flag: str, lang: str) -> str:
        return getattr(self, f"{flag}_{lang}")

    def autocall_text(self, memory: bool, lang: str) -> str:
        """{autocall_<lang>}：會提前出場時的字樣；記憶式且有 autocall_memory_* 時用它。"""
        special = getattr(self, f"autocall_memory_{lang}")
        return special if memory and special else getattr(self, f"autocall_{lang}")

    def ccy_text(self, iso: str) -> str:
        return self.ccy_aliases.get(iso, iso)

    def ko_observation_text(self, daily: bool, lang: str) -> str:
        """{ko_observation_<lang>}：KO 每日觀察用 daily_*，每期觀察用 periodic_*。"""
        return getattr(self, f"{'daily' if daily else 'periodic'}_{lang}")

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
    distributor_address_equivalents: tuple[str, ...]  # 地址可接受的其他寫法（所有上手適用）
    iis_fixed_warning_occurrences: int | None  # 投資人須知的固定警語次數；None → 審查標準沒有該上手的值
    distributor_name_equivalents: tuple[str, ...] = ()  # 名稱可接受的其他寫法（所有上手適用；SG 第二章簡稱）
    # 下列只有審查標準有該上手分節時才核對（BNP）
    guarantor_names: dict[str, str] = field(default_factory=dict)  # 出處（cover／ch1／ch2）→ 保證機構名稱
    guarantor_name_equivalents: tuple[str, ...] = ()  # 投資人須知相關機構的保證機構名稱其他寫法（SG）
    agent_names: dict[str, str] = field(default_factory=dict)  # 出處（cover／ch2）→ 總代理人名稱（SG）
    product_type: str | None = None  # 封面商品種類固定值
    minimum_issue_size: str | None = None  # 第四章發行不成立門檻的金額寫法
    # 最低金額出處欄位 → 單位面額的倍數（沒列為 1）；依幣別不同時為 {ISO 代碼: 倍數}（SG 最低申購金額）
    min_amount_multiples: dict[str, int | dict[str, int]] = field(default_factory=dict)


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
    fixed_warning_occurrences: int  # 說明書的固定警語次數（沒有上手別設定的上手）
    forbidden: tuple[str, ...]
    allowed_phrases: tuple[str, ...]
    product_names: dict[str, ProductNameTemplate]  # 上手代號（小寫）→ 名稱樣板
    currency_zh_to_iso: dict[str, str]
    denomination: dict[str, int]
    iis_pages: int  # 投資人須知必須剛好的頁數
    iis_fixed_warning_occurrences: dict[str, int]  # 上手代號（小寫）→ 固定警語在投資人須知出現的次數
    fixed_warning_by_issuer: dict[str, str] = field(default_factory=dict)
    # 上手代號（小寫）→ 固定警語在說明書出現的次數（risk.fixed_warning_occurrences_by_issuer；沒列出的上手用 fixed_warning_occurrences）
    fixed_warning_occurrences_by_issuer: dict[str, int] = field(default_factory=dict)
    distributor_phone_equivalents: tuple[str, ...] = ()
    distributor_address_equivalents: tuple[str, ...] = ()
    distributor_name_equivalents: tuple[str, ...] = ()
    fixed_warning_openings: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 上手 → 警語開頭句的其他寫法
    risk_level_formats: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 上手 → 風險等級寫法
    issuer_name_ignore: dict[str, frozenset[str]] = field(default_factory=dict)  # 上手 → 全名比對另外忽略的差異
    guarantor_names: dict[str, dict[str, str]] = field(default_factory=dict)  # 上手 → 出處 → 保證機構名稱
    guarantor_name_equivalents: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 上手 → 其他寫法
    agent_names: dict[str, dict[str, str]] = field(default_factory=dict)  # 上手 → 出處 → 總代理人名稱
    product_types: dict[str, str] = field(default_factory=dict)  # 上手 → 封面商品種類固定值
    minimum_issue_sizes: dict[str, str] = field(default_factory=dict)  # 上手 → 發行不成立門檻寫法
    # 上手 → 最低金額出處 → 倍數（或 {ISO 代碼: 倍數}）
    min_amount_multiples: dict[str, dict[str, int | dict[str, int]]] = field(default_factory=dict)
    # 上手 → 固定警語的其他完整寫法（整段逐字；SG 第一章 §2 的風險等級寫 [RRn]），每一處都算一次
    fixed_warning_variants: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # 交易日（參考條件表）在這天（含）以前的文件出現禁用語時特例通過（已通知上手改版，Issue #247）；None 不特例
    forbidden_exempt_until: dt.date | None = None

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
            fixed_warnings=(
                warning,
                *(alt + rest for alt in self.fixed_warning_openings.get(key, ())),
                *self.fixed_warning_variants.get(key, ()),
            ),
            fixed_warning_occurrences=self.fixed_warning_occurrences_by_issuer.get(key, self.fixed_warning_occurrences),
            risk_level_formats=self.risk_level_formats.get(key, ("【" + RISK_LEVEL + "】",)),
            issuer_name=self.issuer_names.get(key),
            issuer_name_ignore=self.issuer_name_ignore.get(key, frozenset()),
            product_name=self.product_names.get(key),
            distributor_name=self.distributor_name,
            distributor_phone=self.distributor_phone,
            distributor_phone_equivalents=self.distributor_phone_equivalents,
            distributor_address=self.distributor_address,
            distributor_address_equivalents=self.distributor_address_equivalents,
            distributor_name_equivalents=self.distributor_name_equivalents,
            iis_fixed_warning_occurrences=self.iis_fixed_warning_occurrences.get(key),
            guarantor_names=dict(self.guarantor_names.get(key, {})),
            guarantor_name_equivalents=self.guarantor_name_equivalents.get(key, ()),
            agent_names=dict(self.agent_names.get(key, {})),
            product_type=self.product_types.get(key),
            minimum_issue_size=self.minimum_issue_sizes.get(key),
            min_amount_multiples=dict(self.min_amount_multiples.get(key, {})),
        )


@dataclass(frozen=True)
class ImportFormat:
    """匯入格式：核對結果檔「回填後」固定的版面，比照公司原始待建檔表（Issue #203）。"""

    title: str  # 第 1 列 A1；B1 為留下的列裡最晚的交易日
    header_row: int
    checked_column: str  # 一律寫打勾寫法
    fallbacks: dict[str, str]  # 欄名 → 參考條件表沒有這欄時改取的欄
    columns: tuple[str, ...]  # 欄位與左右順序


@dataclass(frozen=True)
class ReferenceFormat:
    """參考條件表（多列表格）的版面與欄位對照，所有上手共用。"""

    version: int
    header_search_rows: int  # 表頭在第一張工作表的前幾列裡找（有配對欄 key_column 的那一列）
    key_column: str
    issuer_column: str  # 不是必要欄：表上有才確認列屬於哪家上手
    empty_value: str
    checked_value: str  # TS、IIS 欄的打勾寫法（核對沒問題）
    issuer_values: dict[str, str]  # 上手代號 → 發行機構欄的寫法
    columns: dict[str, str]  # Excel 欄名 → 標準欄位（要核對或回填的欄；同一標準欄位可有多個欄名）
    optional_columns: tuple[str, ...]  # columns 裡不是必要欄的欄名（表上沒有也照常核對）
    import_format: ImportFormat  # 核對結果檔「回填後」的版面（Issue #203）
    ko_observation_values: dict[str, str]
    ko_memory_values: dict[str, bool]
    ki_type_values: dict[str, str]
    initial_pricing_values: dict[str, str]  # 期初定價：開盤價／收盤價／VWAP → open／close／vwap
    # 標的不看順序的上手代號：表上 UL_n 與說明書同一組代號就相符，價格與 VWAP 回填依代號配對（Citi，Issue #220）
    underlyings_any_order: tuple[str, ...] = ()


def load_toml(path: Path, what: str) -> dict[str, Any]:
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
        autocall_zh=n.get("autocall_zh", ""),
        autocall_en=n.get("autocall_en", ""),
        autocall_memory_zh=n.get("autocall_memory_zh", ""),
        autocall_memory_en=n.get("autocall_memory_en", ""),
        ccy_aliases=dict(n.get("ccy_aliases", {})),
        quanto_zh=n.get("quanto_zh", ""),
        quanto_en=n.get("quanto_en", ""),
        ki_zh=n.get("ki_zh", ""),
        ki_en=n.get("ki_en", ""),
        single_unsupported=bool(n.get("single_unsupported", False)),
        periodic_zh=n.get("periodic_zh", ""),
        periodic_en=n.get("periodic_en", ""),
    )
    if any(not (isinstance(k, str) and isinstance(v, str) and v) for k, v in tpl.ccy_aliases.items()):
        raise IngestionError(
            "config_invalid",
            f"審查標準商品名稱樣板 product_name.{issuer}.ccy_aliases 須是 ISO 代碼 → 名稱用代碼的文字表",
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
        need = [k for k in (f"daily_{lang}", f"periodic_{lang}") if not getattr(tpl, k)]
        if f"ko_observation_{lang}" in tpl.placeholders(lang) and need:
            raise IngestionError(
                "config_invalid",
                f"審查標準商品名稱樣板 product_name.{issuer}.{lang} 用了 {{ko_observation_{lang}}}，"
                f"但沒有設定 {'、'.join(need)}（KO 每日與每期觀察的寫法）",
            )
        needed = (f"basket_{lang}",) if tpl.single_unsupported else (f"single_{lang}", f"basket_{lang}")
        missing = [k for k in needed if not getattr(tpl, k)]
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
    variants = _per_issuer_lists(risk.get("fixed_warning_variants", {}), "risk.fixed_warning_variants")
    for issuer, values in variants.items():
        warning = warnings.get(issuer, risk["fixed_warning"])
        if any(v.replace(" ", "") == warning.replace(" ", "") for v in values) or len(set(values)) != len(values):
            raise IngestionError(
                "config_invalid", f"審查標準 risk.fixed_warning_variants.{issuer} 的寫法重複或與該上手的固定警語相同"
            )
    return {"fixed_warning_openings": openings, "risk_level_formats": formats, "fixed_warning_variants": variants}


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


def _text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _per_issuer(d: Any, where: str, valid: Callable[[Any], bool], what: str) -> dict[str, Any]:
    """`[<表>]` 裡每個上手一個值，都要 `valid`（`what` 為錯訊中值的說明）。"""
    if not isinstance(d, dict):
        raise IngestionError("config_invalid", f"審查標準 {where} 須是一個表（[{where}]）")
    bad = [k for k, v in d.items() if not valid(v)]
    if bad:
        raise IngestionError("config_invalid", f"審查標準 {where}.{bad[0]} 須是{what}")
    return dict(d)


def _per_issuer_text(d: Any, where: str) -> dict[str, str]:
    """`[<表>]` 裡每個上手一段不空白的文字。"""
    return _per_issuer(d, where, _text, "一段不空白的文字")


def _per_issuer_tables(
    d: Any,
    where: str,
    valid: Callable[[Any], bool],
    table: str,
    problem: Callable[[str, list[str], list[str]], str],
    keys: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """`[<表>.<上手>]`：每個上手一個不空的表（`table` 為錯訊中表的說明），表內每個值都要 `valid`；
    `keys` 給了就只允許這些鍵。有不合法的值或未知的鍵時，`problem(上手, 不合法的鍵, 未知的鍵)` 為錯訊。"""
    allowed = tuple(keys) if keys is not None else ()
    tables = _per_issuer(d, where, lambda v: isinstance(v, dict) and bool(v), table)
    for issuer, values in tables.items():
        bad = [k for k, v in values.items() if not valid(v)]
        unknown = sorted(set(values) - set(allowed)) if allowed else []
        if bad or unknown:
            raise IngestionError("config_invalid", problem(issuer, bad, unknown))
    return {issuer: dict(values) for issuer, values in tables.items()}


def _place_texts(d: Any, where: str, places: Iterable[str]) -> dict[str, dict[str, str]]:
    """`[<表>.<上手>]`：機構名稱的出處（例：保證機構 cover／ch1／ch2、總代理人 cover／ch2）→ 不空白的文字。"""
    keys = tuple(places)
    return _per_issuer_tables(
        d,
        where,
        _text,
        "一個表，每個鍵一段文字",
        lambda issuer, bad, unknown: (
            f"審查標準 {where}.{issuer} 的鍵須為 {'、'.join(keys)}，值為不空白的文字（有問題：{'、'.join(bad + unknown)}）"
        ),
        keys,
    )


def _multiple_ok(v: Any) -> bool:
    """倍數：正整數，或依幣別的表 {ISO 代碼: 正整數}。"""
    if type(v) is int:
        return v >= 1
    return (
        isinstance(v, dict)
        and bool(v)
        and all(isinstance(k, str) and len(k) == 3 and k.isupper() and type(x) is int and x >= 1 for k, x in v.items())
    )


def _min_amount_multiples(d: Any) -> dict[str, dict[str, int | dict[str, int]]]:
    """`[min_amount_multiples.<上手>]`：最低金額出處欄位 → 正整數倍數，或依承作幣別的 {ISO 代碼: 倍數}（SG 最低申購金額）。"""
    where = "min_amount_multiples"
    return _per_issuer_tables(
        d,
        where,
        _multiple_ok,
        "一個表，每個最低金額出處一個倍數",
        lambda issuer, bad, unknown: (
            f"審查標準 {where}.{issuer}.{bad[0]} 須是正整數（單位面額的倍數）或依幣別的表（例：{{ USD = 10, JPY = 1 }}）"
        ),
    )


def _occurrence_counts(d: Any, where: str) -> dict[str, int]:
    """上手代號（小寫）→ 固定警語次數：須是正整數。"""
    return _per_issuer(d, where, lambda v: type(v) is int and v > 0, "正整數")


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
    d = load_toml(path, "審查標準")
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
            distributor_address_equivalents=tuple(d["distributor"].get("address_equivalents", ())),
            distributor_name_equivalents=tuple(d["distributor"].get("name_equivalents", ())),
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
            fixed_warning_occurrences_by_issuer=_occurrence_counts(
                d["risk"].get("fixed_warning_occurrences_by_issuer", {}), "risk.fixed_warning_occurrences_by_issuer"
            ),
            forbidden=tuple(d["wording"]["forbidden"]),
            allowed_phrases=tuple(d["wording"]["allowed_phrases"]),
            forbidden_exempt_until=d["wording"].get("exempt_until"),
            product_names=names,
            currency_zh_to_iso=dict(d["currency"]),
            denomination={k: int(v) for k, v in d["denomination"].items()},
            iis_pages=int(d["iis"]["pages"]),
            iis_fixed_warning_occurrences={k: int(v) for k, v in d["iis"]["fixed_warning_occurrences"].items()},
            guarantor_names=_place_texts(d.get("guarantor_name", {}), "guarantor_name", GUARANTOR_PLACES),
            guarantor_name_equivalents=_per_issuer_lists(
                d.get("guarantor_name_equivalents", {}), "guarantor_name_equivalents"
            ),
            agent_names=_place_texts(d.get("agent_name", {}), "agent_name", AGENT_PLACES),
            product_types=_per_issuer_text(d.get("product_type", {}), "product_type"),
            minimum_issue_sizes=_per_issuer_text(d.get("minimum_issue_size", {}), "minimum_issue_size"),
            min_amount_multiples=_min_amount_multiples(d.get("min_amount_multiples", {})),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"審查標準設定檔缺少或格式錯誤的項目：{e}") from e


def load_reference_format(path: Path) -> ReferenceFormat:
    d = load_toml(path, "參考條件表格式")
    try:
        cols = dict(d["columns"])
        layout = d["layout"]
        fmt = ReferenceFormat(
            version=int(d["version"]),
            header_search_rows=int(layout["header_search_rows"]),
            key_column=layout["key_column"],
            issuer_column=layout["issuer_column"],
            empty_value=layout["empty_value"],
            checked_value=layout["checked_value"],
            issuer_values=dict(d["issuer_values"]),
            columns=cols,
            optional_columns=tuple(str(h) for h in layout["optional_columns"]),
            import_format=_import_format(d["import_format"]),
            ko_observation_values=dict(d["values"]["ko_observation"]),
            ko_memory_values={k: bool(v) for k, v in d["values"]["ko_memory"].items()},
            ki_type_values=dict(d["values"]["ki_type"]),
            initial_pricing_values=dict(d["values"]["initial_pricing"]),
            underlyings_any_order=tuple(str(c) for c in d.get("underlyings", {}).get("any_order_issuers", [])),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"參考條件表格式設定檔缺少或格式錯誤的項目：{e}") from e
    _check_column_settings(fmt)
    return fmt


def _import_format(d: dict[str, Any]) -> ImportFormat:
    return ImportFormat(
        title=str(d["title"]),
        header_row=int(d["header_row"]),
        checked_column=str(d["checked_column"]),
        fallbacks={str(k): str(v) for k, v in d["fallbacks"].items()},
        columns=tuple(str(h) for h in d["columns"]),
    )


def _check_column_settings(fmt: ReferenceFormat) -> None:
    """optional_columns 要是 [columns] 的欄名；匯入格式的欄名不重複、表頭在第 1 列以下、涵蓋 checked_column 與
    fallbacks，且每個必要的標準欄位至少有一個欄名在匯入格式裡（核對與回填的值都會進「回填後」）。"""
    unknown = [h for h in fmt.optional_columns if h not in fmt.columns]
    if unknown:
        raise IngestionError(
            "config_invalid", f"參考條件表格式設定檔 optional_columns 有 [columns] 沒有的欄名：{'、'.join(unknown)}"
        )
    imp = fmt.import_format
    cols = imp.columns
    named = {fmt.columns[h] for h in cols if h in fmt.columns}
    optional = {fmt.columns[h] for h in fmt.optional_columns}
    absent = [h for h, std in fmt.columns.items() if std not in named and std not in optional]
    absent += [h for h in (imp.checked_column, *imp.fallbacks) if h not in cols]
    repeated = sorted({h for h in cols if cols.count(h) > 1}, key=cols.index)
    problems = [f"缺少「{h}」" for h in dict.fromkeys(absent)] + [f"「{h}」重複" for h in repeated]
    if imp.header_row < 2:
        problems.append("header_row 須在第 2 列以下（第 1 列是標題）")
    if problems:
        raise IngestionError("config_invalid", f"參考條件表格式設定檔的匯入格式{'、'.join(problems)}")


def load_issuer_prefixes(path: Path) -> dict[str, str]:
    """上手編號（三碼）→ 上手代號。"""
    d = load_toml(path, "上手編號對照")
    try:
        prefixes = {str(k): str(v) for k, v in d["prefixes"].items()}
    except (KeyError, TypeError, AttributeError) as e:
        raise IngestionError("config_invalid", f"上手編號對照設定檔缺少或格式錯誤的項目：{e}") from e
    bad = [k for k in prefixes if not (len(k) == 3 and k.isdigit())]
    if bad:
        raise IngestionError("config_invalid", f"上手編號必須是三位數字：{'、'.join(bad)}")
    return prefixes


def _holiday_dates(values: Iterable[object], what: str) -> frozenset[dt.date]:
    out = set()
    for v in values:
        if not isinstance(v, dt.date) or isinstance(v, dt.datetime):
            raise IngestionError("config_invalid", f"交易所假日表的{what}必須是日期（YYYY-MM-DD）：{v}")
        out.add(v)
    return frozenset(out)


def _holiday_date(value: object, what: str) -> dt.date:
    (d,) = _holiday_dates([value], what)
    return d


def load_exchange_holidays(path: Path) -> TradingCalendar:
    """載入交易所假日表；缺檔、格式錯誤或同一個交易所碼對到兩個市場時丟出 IngestionError。"""
    d = load_toml(path, "交易所假日表")
    try:
        version = int(d["version"])
        markets = tuple(
            Market(
                code=str(code),
                name=str(m["name"]),
                bloomberg_codes=frozenset(str(c).upper() for c in m["bloomberg_codes"]),
                covered_from=_holiday_date(m["covered_from"], f"{code} 涵蓋起日"),
                covered_through=_holiday_date(m["covered_through"], f"{code} 涵蓋迄日"),
                holidays=_holiday_dates(m["holidays"], f"{code} 休市日"),
            )
            for code, m in d["markets"].items()
        )
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise IngestionError("config_invalid", f"交易所假日表設定檔缺少或格式錯誤的項目：{e}") from e
    seen: dict[str, str] = {}
    for m in markets:
        if m.covered_through < m.covered_from:
            raise IngestionError("config_invalid", f"交易所假日表 {m.code} 的涵蓋迄日早於起日")
        outside = sorted(h for h in m.holidays if not m.covers(h, h))
        if outside:
            raise IngestionError(
                "config_invalid", f"交易所假日表 {m.code} 有休市日不在涵蓋範圍內：{'、'.join(map(str, outside))}"
            )
        for c in m.bloomberg_codes:
            if c in seen:
                raise IngestionError("config_invalid", f"交易所碼 {c} 同時出現在 {seen[c]} 與 {m.code}")
            seen[c] = m.code
    return TradingCalendar(version, markets)
