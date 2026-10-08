"""標準化資料型別：證據、擷取欄位、下單欄位、核對結果。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .backfill import CellDecision


class FieldStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"
    NOT_APPLICABLE = "not_applicable"


class CheckStatus(StrEnum):
    PASS = "PASS"
    MISMATCH = "MISMATCH"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"

    @property
    def is_problem(self) -> bool:
        """需作業人員處理：ERROR、MISMATCH、REVIEW_REQUIRED。"""
        return self in _PROBLEMS

    @property
    def display_rank(self) -> int:
        """PANEL 的顯示順序：ERROR、MISMATCH、REVIEW_REQUIRED、PASS、NOT_APPLICABLE。"""
        return _DISPLAY_ORDER.index(self)


# 整體狀態優先順序：ERROR > REVIEW_REQUIRED > MISMATCH > PASS
_SEVERITY = {
    CheckStatus.NOT_APPLICABLE: 0,
    CheckStatus.PASS: 0,
    CheckStatus.MISMATCH: 1,
    CheckStatus.REVIEW_REQUIRED: 2,
    CheckStatus.ERROR: 3,
}
_PROBLEMS = frozenset({CheckStatus.ERROR, CheckStatus.MISMATCH, CheckStatus.REVIEW_REQUIRED})
_DISPLAY_ORDER = (
    CheckStatus.ERROR,
    CheckStatus.MISMATCH,
    CheckStatus.REVIEW_REQUIRED,
    CheckStatus.PASS,
    CheckStatus.NOT_APPLICABLE,
)


def overall_status(statuses: list[CheckStatus]) -> CheckStatus:
    worst = CheckStatus.PASS
    for s in statuses:
        if _SEVERITY[s] > _SEVERITY[worst]:
            worst = s
    return worst


@dataclass(frozen=True)
class Line:
    """擷取出的一行文字。頁碼從 1 起；bbox 為 PyMuPDF 座標（左上原點，pt）。"""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class Evidence:
    page: int
    bbox: tuple[float, float, float, float]
    text: str

    @classmethod
    def of(cls, line: Line) -> Evidence:
        return cls(line.page, tuple(round(v, 1) for v in (line.x0, line.y0, line.x1, line.y1)), line.text)

    def to_dict(self) -> dict[str, Any]:
        return {"page": self.page, "bbox": list(self.bbox), "text": self.text}


@dataclass
class ParsedField:
    """說明書擷取欄位。缺漏、歧義、不合法、不適用分開表示；不猜值。"""

    name: str
    status: FieldStatus
    value: Any = None
    evidence: list[Evidence] = field(default_factory=list)
    candidates: list[Any] = field(default_factory=list)
    note: str = ""

    @classmethod
    def present(cls, name: str, value: Any, lines: list[Line]) -> ParsedField:
        return cls(name, FieldStatus.PRESENT, value, [Evidence.of(x) for x in lines])

    @classmethod
    def missing(cls, name: str, note: str = "") -> ParsedField:
        return cls(name, FieldStatus.MISSING, note=note)

    @classmethod
    def ambiguous(cls, name: str, candidates: list[Any], lines: list[Line], note: str = "") -> ParsedField:
        return cls(name, FieldStatus.AMBIGUOUS, None, [Evidence.of(x) for x in lines], candidates, note)

    @classmethod
    def invalid(cls, name: str, lines: list[Line], note: str = "") -> ParsedField:
        return cls(name, FieldStatus.INVALID, None, [Evidence.of(x) for x in lines], note=note)

    @classmethod
    def from_hits(
        cls,
        name: str,
        hits: Sequence[tuple[Any, Sequence[Line]]],
        *,
        missing_note: str = "",
        ambiguous_note: str = "",
    ) -> ParsedField:
        """說明書多處取到的值（值, 原文行）合併：沒有 → 缺漏；不同的值 → 歧義（依出現順序列出）；都相同 → 存在。"""
        if not hits:
            return cls.missing(name, missing_note)
        values: list[Any] = []
        for v, _ in hits:
            if v not in values:
                values.append(v)
        lines = [ln for _, lns in hits for ln in lns]
        if len(values) > 1:
            return cls.ambiguous(name, values, lines, ambiguous_note)
        return cls.present(name, values[0], lines)

    @property
    def ok(self) -> bool:
        return self.status == FieldStatus.PRESENT


@dataclass
class OrderValue:
    """下單資料欄位值與來源儲存格。"""

    value: Any
    source: str  # 例：詢價表格!J5
    column: str = ""  # 來源 Excel 欄名（例：UL_2_進場價）；核對結果的項目用


class ItemSource(StrEnum):
    """核對結果的預期值從哪裡來；錯訊依此決定句型。"""

    REFERENCE = "reference"  # 直接比對參考條件表的值（錯訊寫「<項目>對不起來：參考條件表 …／說明書 …」）
    REFERENCE_DERIVED = "reference_derived"  # 由參考條件表的值推算（例：月配息率；錯訊附「參考條件表」值與推算說明）
    STANDARD = "standard"  # 審查標準
    EXPECTED = "expected"  # 說明書其他位置或由說明書推算（錯訊寫「預期」）
    TERM_SHEET = "term_sheet"  # 同商品說明書讀出的值（投資人須知用；錯訊寫「<項目>對不起來：說明書 …／投資人須知 …」）
    NONE = "none"  # 不比對值：配對、範本、讀檔、寫檔、參考條件表表頭


def column_label(column: str) -> str:
    """Excel 欄名的顯示：`UL_2_進場價` → `UL_2 進場價`，其他照原欄名。"""
    return re.sub(r"^(UL_\d+)_", r"\1 ", column)


def _columns(ovs: list[OrderValue | None] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(o.column for o in ovs or [] if o is not None and o.column))


@dataclass(frozen=True)
class Item:
    """一條核對結果在講哪一項：作業人員看得懂的中文名稱與預期值出處，由規則建立結果時給（Issue #91）。"""

    name: str  # 例：UL_2 KO價、情境 3 損益金額、受託機構負責人
    source: ItemSource
    columns: tuple[str, ...] = ()  # 讀到的參考條件表 Excel 欄名
    grouped: bool = False  # 多欄合起來核對（例：標的、比價日），核對紀錄的 column 寫項目名稱

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("核對結果的項目名稱不可空白")

    @property
    def record_column(self) -> str:
        """核對紀錄的 `column`：多欄合起來核對時為項目名稱，否則為讀到的 Excel 欄名。"""
        return self.name if self.grouped else "、".join(self.columns)

    @classmethod
    def sheet(cls, name: str, ovs: list[OrderValue | None] | None = None) -> Item:
        """直接比對參考條件表的值，名稱固定（例：最低申購金額與「單位面額」比對）。"""
        return cls(name, ItemSource.REFERENCE, _columns(ovs))

    @classmethod
    def group(cls, name: str, ovs: list[OrderValue | None] | None = None) -> Item:
        """多欄參考條件表合起來核對（例：UL_1～UL_5 為「標的」、比價日_1～12 為「比價日」）。"""
        return cls(name, ItemSource.REFERENCE, _columns(ovs), grouped=True)

    @classmethod
    def column(cls, fallback: str, ovs: list[OrderValue | None] | None = None) -> Item:
        """參考條件表欄位：只讀到一欄時以 Excel 欄名為名稱（UL_2_進場價 → UL_2 進場價），否則用 fallback。"""
        item = cls.sheet(fallback, ovs)
        if len(item.columns) == 1:
            return cls(column_label(item.columns[0]), ItemSource.REFERENCE, item.columns)
        return item

    @classmethod
    def derived(cls, name: str, ovs: list[OrderValue | None]) -> Item:
        """由參考條件表的值推算的預期值（例：月配息率由年利率與天期推算）。"""
        return cls(name, ItemSource.REFERENCE_DERIVED, _columns(ovs))

    @classmethod
    def standard(cls, name: str) -> Item:
        return cls(name, ItemSource.STANDARD)

    @classmethod
    def expected(cls, name: str) -> Item:
        return cls(name, ItemSource.EXPECTED)

    @classmethod
    def term_sheet(cls, name: str) -> Item:
        """預期值是同商品說明書讀出的值（投資人須知上參考條件表沒有的欄位，ADR 0007）。"""
        return cls(name, ItemSource.TERM_SHEET)

    @classmethod
    def note(cls, name: str) -> Item:
        """不比對值的項目（配對、範本、讀檔、寫檔、參考條件表表頭）：錯訊只寫說明。"""
        return cls(name, ItemSource.NONE)


@dataclass
class CheckResult:
    rule_id: str
    field: str
    status: CheckStatus
    expected: Any = None  # 下單資料或審查標準的值
    actual: Any = None  # 說明書的值
    tolerance: str | None = None
    reason_code: str = ""
    message: str = ""
    document_evidence: list[Evidence] = field(default_factory=list)
    order_source: list[str] = field(default_factory=list)
    rule_version: str = "1"
    item: Item = field(kw_only=True)  # 必填：沒給項目就建立不了結果


@dataclass
class CheckReport:
    """一份說明書的完整核對結果。"""

    status: CheckStatus
    template: str | None
    results: list[CheckResult]
    not_covered: list[dict[str, str]]
    metadata: dict[str, Any] = field(default_factory=dict)
    backfill: list[CellDecision] = field(default_factory=list)  # 回填決策


@dataclass
class DetectionResult:
    """範本辨識結果：全部條件成立才 matched；failed 為不成立的條件說明。"""

    matched: bool
    failed: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
