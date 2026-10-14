"""樣態：一檔商品的結構類型，與上手支援樣態表標為不支援的樣態（Issue #207，見 CONTEXT.md「樣態」「不支援樣態」）。

上手支援樣態表在 `config/issuer_prefixes.toml` 的 `[unsupported_variants]`，採黑名單：只有表上列出的樣態不支援，
沒列的組合（例：KO 每日觀察＋記憶式、KI 到期觀察、無 KI）一律算支援。

辨識階段對到參考條件表列之後，分別從參考條件表該列、這份說明書或投資人須知明確讀出的值判斷樣態；
任一邊是該上手的不支援樣態，這份就是配對問題「不支援樣態」（identification.py）。讀不出或值不在允許值內的條件
不判斷（照參考條件表欄位規則處理）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import ReferenceFormat, load_toml
from .ingestion import IngestionError
from .investor_sheet import IisSheet, read_iis
from .orders.reference import OrderRecord
from .rules.kit import read_standard, to_int
from .standard_fields import AutocallSchedule, TermSheet

VWAP = "vwap"  # 期初定價允許值對照後的 VWAP（config/reference_sheet.toml [values.initial_pricing]）


class Variant(StrEnum):
    """上手支援樣態表的七個樣態；值是設定檔 `[unsupported_variants]` 的寫法。"""

    VWAP = "vwap"
    KO_DAILY = "ko_daily"  # KO 每日觀察、非記憶式
    KO_PERIODIC_MEMORY = "ko_periodic_memory"
    KO_PERIODIC = "ko_periodic"  # KO 每期觀察、非記憶式
    KI_DAILY = "ki_daily"
    KI_PERIODIC = "ki_periodic"
    NON_CALL_EQUALS_TENOR = "non_call_equals_tenor"  # Non-Call = 天期：整段期間都不會提前出場

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    Variant.VWAP: "期初定價 VWAP",
    Variant.KO_DAILY: "KO 每日觀察（非記憶式）",
    Variant.KO_PERIODIC_MEMORY: "KO 每期觀察（記憶式）",
    Variant.KO_PERIODIC: "KO 每期觀察（非記憶式）",
    Variant.KI_DAILY: "KI 每日觀察",
    Variant.KI_PERIODIC: "KI 每期觀察",
    Variant.NON_CALL_EQUALS_TENOR: "Non-Call = 天期",
}


@dataclass(frozen=True)
class Terms:
    """判斷樣態用的條件（一個出處）；None 表示這個出處沒有或讀不出明確的值。"""

    vwap: bool | None = None
    ko_observation: str | None = None  # D／P
    ko_memory: bool | None = None
    ki_type: str | None = None  # none／AM／D／P／M
    first_callable: int | None = None  # Non-Call(月)
    tenor: int | None = None  # 天期(月)

    def variants(self) -> tuple[Variant, ...]:
        """這組條件屬於哪些樣態（依 `Variant` 順序）；條件不足的樣態不判斷。"""
        found = []
        if self.vwap:
            found.append(Variant.VWAP)
        if self.ko_memory is not None:
            if self.ko_observation == "D" and not self.ko_memory:
                found.append(Variant.KO_DAILY)
            elif self.ko_observation == "P":
                found.append(Variant.KO_PERIODIC_MEMORY if self.ko_memory else Variant.KO_PERIODIC)
        found += {"D": [Variant.KI_DAILY], "P": [Variant.KI_PERIODIC]}.get(self.ki_type or "", [])
        if self.first_callable is not None and self.first_callable == self.tenor:
            found.append(Variant.NON_CALL_EQUALS_TENOR)
        return tuple(found)


def row_terms(row: OrderRecord, fmt: ReferenceFormat) -> Terms:
    """參考條件表該列的條件：文字欄依格式設定的允許值對照，不在允許值內的不判斷。"""

    def raw(key: str) -> Any:
        ov = row.get(key)
        return ov.value if ov is not None else None

    def mapped(key: str, values: dict[str, Any]) -> Any:
        v = raw(key)
        return values.get(v) if isinstance(v, str) else None

    pricing = mapped("initial_pricing", fmt.initial_pricing_values)
    return Terms(
        vwap=None if pricing is None else pricing == VWAP,
        ko_observation=mapped("ko_observation", fmt.ko_observation_values),
        ko_memory=mapped("ko_memory", fmt.ko_memory_values),
        ki_type=mapped("ki_type", fmt.ki_type_values),
        first_callable=to_int(raw("first_callable_period")),
        tenor=to_int(raw("tenor_months")),
    )


def term_sheet_terms(ts: TermSheet) -> Terms:
    """說明書明確讀出的條件（期初定價說明書不寫）；Non-Call 取提前出場排程的第一個可提前出場期。"""

    def value(name: str) -> Any:
        pf = read_standard(ts, name)
        return pf.value if pf.ok else None

    schedule = value("autocall_schedule")
    return Terms(
        ko_observation=value("ko_observation"),
        ko_memory=value("ko_memory"),
        ki_type=value("ki_type"),
        first_callable=schedule.first_callable if isinstance(schedule, AutocallSchedule) else None,
        tenor=value("tenor_months"),
    )


def investor_sheet_terms(sheet: IisSheet) -> Terms:
    """投資人須知明確讀出的條件：只看這份範本有的欄位。"""

    def value(name: str) -> Any:
        if not sheet.provides(name):
            return None
        pf = read_iis(sheet, name)
        return pf.value if pf.ok else None

    return Terms(
        ko_observation=value("ko_observation"),
        ko_memory=value("ko_memory"),
        ki_type=value("ki_type"),
        first_callable=value("first_callable_period"),
        tenor=value("tenor_months"),
    )


def unsupported(sources: Sequence[tuple[str, Terms]], banned: frozenset[Variant]) -> dict[Variant, tuple[str, ...]]:
    """各出處（名稱, 條件）裡屬於不支援樣態的：樣態 → 出處名稱（依 `Variant` 順序；沒有時為空）。"""
    found = {v: tuple(name for name, terms in sources if v in terms.variants()) for v in Variant if v in banned}
    return {v: names for v, names in found.items() if names}


def load_unsupported_variants(path: Path, issuers: Iterable[str]) -> dict[str, frozenset[Variant]]:
    """上手支援樣態表（上手編號對照設定檔的 `[unsupported_variants]`）：上手代號 → 不支援的樣態。

    上手編號對照的每家上手都要登記（沒有不支援樣態時寫空清單），也不能有對照表沒有的上手；樣態名稱要是 `Variant` 的值。
    """
    what = "上手編號對照設定檔的上手支援樣態表 [unsupported_variants]"
    table = load_toml(path, "上手編號對照").get("unsupported_variants")
    if not isinstance(table, dict):
        raise IngestionError("config_invalid", f"{what}不存在或格式錯誤")
    allowed = [v.value for v in Variant]
    out: dict[str, frozenset[Variant]] = {}
    for code, names in table.items():
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise IngestionError("config_invalid", f"{what}的 {code} 須是樣態名稱的清單")
        unknown = [n for n in names if n not in allowed]
        if unknown:
            raise IngestionError(
                "config_invalid", f"{what}的 {code} 有未知的樣態：{'、'.join(unknown)}（可用：{'、'.join(allowed)}）"
            )
        out[str(code)] = frozenset(map(Variant, names))
    issuers = set(issuers)
    if missing := sorted(issuers - out.keys()):
        raise IngestionError(
            "config_invalid", f"{what}沒有登記這些上手：{'、'.join(missing)}（沒有不支援樣態時寫空清單）"
        )
    if extra := sorted(out.keys() - issuers):
        raise IngestionError("config_invalid", f"{what}有上手編號對照沒有的上手：{'、'.join(extra)}")
    return out
