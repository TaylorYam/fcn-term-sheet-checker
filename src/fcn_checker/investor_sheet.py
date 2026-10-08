"""投資人須知讀出結果：上手投資人須知 parser 與共用規則之間的 seam（ADR 0007、docs/rules/iis-check-rules.md）。

投資人須知只摘錄部分條件，各上手範本有的項目不同：讀出結果以 `provides` 宣告這份有哪些欄位，
範本沒有的欄位不核對（不推定通過，也不報缺漏）；範本有但讀不到的欄位照常是缺漏，規則轉人工覆核。

欄位名稱：和說明書意義相同的沿用標準欄位（standard_fields.STANDARD_FIELDS，例：`currency_zh`、
`underlyings`、`issue_date`、`print_dates`、`fee_<費用項目>`），投資人須知才有的欄位見 `IIS_FIELDS`。
共用規則一律經 `read_iis` 讀投資人須知的欄位（說明書標準欄位仍經 `rules/kit.read_standard`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import standard_fields
from .parsers.layout import TextIndex
from .schema import ParsedField

# 投資人須知才有的欄位：名稱 → 值的形狀
IIS_FIELDS: dict[str, str] = {
    "product_codes": "tuple[Occurrence, ...]：封面各處商品代號，須等於檔名前 12 碼",
    "page_totals": "tuple[int, ...]：每頁頁首「第 N 頁，共 M 頁」的 M",
    "risk_level_summary": "str：商品簡介「本商品風險程度」的風險等級（例：RR4，沒有【】）",
    "underlying_names": "list[str]：標的中文名稱，依說明書順序",
    "min_subscription": "int：最低申購金額",
    "monthly_coupon_pct": "Decimal：每月配息率 %，保留顯示位數",
    "issuer_names": "tuple[Occurrence, ...]：各處發行機構名稱",
    "distributor_names": "tuple[Occurrence, ...]：各處受託或銷售機構名稱",
    "distributor_addresses": "tuple[Occurrence, ...]：各處受託或銷售機構地址",
    "distributor_phones": "tuple[Occurrence, ...]：各處受託或銷售機構電話",
}


@dataclass
class IisSheet:
    """一份投資人須知的讀出結果：`f(name)` 交出欄位，`full_text` 為全文索引（固定警語、風險等級、禁用語用）。"""

    fields: dict[str, ParsedField]
    full_text: TextIndex
    provided: frozenset[str]  # 這份範本有的欄位；不在其中的不核對
    issuer_name_zh_only: bool = False  # 範本的發行機構名稱只寫中文（HSBC）

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)

    def provides(self, name: str) -> bool:
        return name in self.provided


def read_iis(sheet: IisSheet, name: str) -> ParsedField:
    """共用規則讀投資人須知欄位的唯一方式：只能讀標準欄位或 `IIS_FIELDS`；沒交出時是缺漏。"""
    if name not in IIS_FIELDS and not standard_fields.is_standard(name):
        raise ValueError(f"{name} 不是投資人須知欄位；要先加進 investor_sheet.IIS_FIELDS")
    return sheet.f(name)
