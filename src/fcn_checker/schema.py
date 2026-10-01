"""標準化資料型別：證據、擷取欄位、下單欄位、核對結果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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


# 整體狀態優先順序：ERROR > REVIEW_REQUIRED > MISMATCH > PASS
_SEVERITY = {
    CheckStatus.NOT_APPLICABLE: 0,
    CheckStatus.PASS: 0,
    CheckStatus.MISMATCH: 1,
    CheckStatus.REVIEW_REQUIRED: 2,
    CheckStatus.ERROR: 3,
}


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

    @property
    def ok(self) -> bool:
        return self.status == FieldStatus.PRESENT


@dataclass
class OrderValue:
    """下單資料欄位值與來源儲存格。"""

    value: Any
    source: str  # 例：詢價表格!J5


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
