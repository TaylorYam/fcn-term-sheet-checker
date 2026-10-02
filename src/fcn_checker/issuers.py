"""上手註冊表：每家上手的範本辨識、擷取、規則入口與參考條件表需要的擷取能力。

新增上手時在 `REGISTRY` 登記一筆，並在 `config/issuer_prefixes.toml` 登記上手編號（docs/issuer-onboarding.md §6）；
批量入口、CLI 與 PANEL 都由這裡分派。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .config import ReferenceFormat, ReviewStandard
from .orders.reference import OrderRecord
from .parsers import barc as barc_parser
from .parsers import hsbc as hsbc_parser
from .rules import barc as barc_rules
from .rules import hsbc as hsbc_rules
from .schema import CheckResult, CheckStatus, DetectionResult, Evidence, Line, ParsedField


@dataclass(frozen=True)
class Issuer:
    code: str  # 上手代號（例：BARC），與上手編號對照、參考條件表「發行機構」設定相同
    template_id: str
    label: str
    parser_version: str
    detect: Callable[[Sequence[Line]], DetectionResult]
    parse: Callable[[Sequence[Line]], tuple[DetectionResult, Any]]
    product_code: Callable[[Sequence[Line]], ParsedField]
    context: Callable[[Any, OrderRecord, ReviewStandard, ReferenceFormat], Any]
    rules: Callable[[Any], list[CheckResult]]  # 表上事先填好的欄位＋說明書內部規則
    isin: Callable[[Any], ParsedField]  # 說明書 ISIN（含證據）
    autocall_schedule: Callable[[Any], ParsedField]  # 值為 rules.reference.AutocallSchedule
    not_covered: tuple[dict[str, str], ...]


BARC = Issuer(
    code=barc_rules.ISSUER,
    template_id=barc_parser.TEMPLATE_ID,
    label="BARC 中文產品說明書",
    parser_version=barc_parser.PARSER_VERSION,
    detect=lambda lines: barc_parser.detect(barc_parser.document(lines)),
    parse=barc_parser.parse,
    product_code=barc_parser.product_code,
    context=barc_rules.Context,
    rules=barc_rules.run_all,
    isin=lambda ts: ts.f("isin"),
    autocall_schedule=barc_rules.autocall_schedule,
    not_covered=tuple(barc_rules.NOT_COVERED),
)

HSBC = Issuer(
    code=hsbc_rules.ISSUER,
    template_id=hsbc_parser.TEMPLATE_ID,
    label="HSBC 中文產品說明書",
    parser_version=hsbc_parser.PARSER_VERSION,
    detect=hsbc_parser.detect,
    parse=hsbc_parser.parse,
    product_code=hsbc_parser.product_code,
    context=hsbc_rules.Context,
    rules=hsbc_rules.reference_rules,
    isin=lambda ts: ts.f("isin"),
    autocall_schedule=hsbc_rules.autocall_schedule,
    not_covered=tuple(hsbc_rules.NOT_COVERED),
)

REGISTRY: tuple[Issuer, ...] = (BARC, HSBC)


def by_code(code: str, registry: Sequence[Issuer] = REGISTRY) -> Issuer | None:
    return next((i for i in registry if i.code == code), None)


def detect(lines: Sequence[Line], registry: Sequence[Issuer] = REGISTRY) -> tuple[Issuer | None, CheckResult]:
    """以每家已註冊上手辨識範本；恰好一個命中才回傳該上手，零個或多個命中轉人工覆核。"""
    detections: list[tuple[Issuer, DetectionResult]] = [(i, i.detect(lines)) for i in registry]
    hits = [(i, d) for i, d in detections if d.matched]
    rid = "template.detect"
    if len(hits) == 1:
        issuer, det = hits[0]
        return issuer, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.PASS,
            actual=issuer.template_id,
            document_evidence=det.evidence,
            message=f"符合 {issuer.label} 範本",
        )
    if hits:
        return None, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.REVIEW_REQUIRED,
            actual=[i.template_id for i, _ in hits],
            reason_code="template_ambiguous",
            document_evidence=[e for _, d in hits for e in d.evidence],
            message="同時符合多個範本，無法確定上手，請人工處理：" + "、".join(i.label for i, _ in hits),
        )
    evidence: list[Evidence] = [e for _, d in detections for e in d.evidence]
    reasons = "；".join(f"{i.code}：" + "；".join(d.failed) for i, d in detections)
    return None, CheckResult(
        rule_id=rid,
        field="template",
        status=CheckStatus.REVIEW_REQUIRED,
        reason_code="template_unknown",
        document_evidence=evidence,
        message="不是已支援的說明書範本，請人工處理：" + reasons,
    )
