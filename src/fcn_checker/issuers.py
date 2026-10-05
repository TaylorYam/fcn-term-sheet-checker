"""上手註冊表：每家上手的識別資料、範本辨識、讀出、說明書內部規則與其可讀的參考條件表欄位（ADR 0005）。

上手 adapter 只提供這五樣；參考條件表欄位、審查標準、Non-Call／ISIN／發行日／比價日與回填都是各上手共用的規則，
由單份核對（single_check.py）依序執行。新增上手時在 `REGISTRY` 登記一筆，並在 `config/issuer_prefixes.toml`
登記上手編號（docs/issuer-onboarding.md §6）；批量入口、CLI 與 PANEL 都由這裡分派。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .parsers import barc as barc_parser
from .parsers import hsbc as hsbc_parser
from .rules import barc as barc_rules
from .rules import hsbc as hsbc_rules
from .rules.kit import IssuerContext
from .schema import CheckResult, CheckStatus, DetectionResult, Evidence, Item, Line
from .standard_fields import TermSheet


@dataclass(frozen=True)
class Issuer:
    # 識別資料
    code: str  # 上手代號（例：BARC），與上手編號對照、參考條件表「發行機構」設定相同
    template_id: str
    label: str
    parser_version: str
    not_covered: tuple[dict[str, str], ...]  # 「未涵蓋」的固定清單（PANEL「待處理」分頁與核對紀錄）
    # 辨識：文字行 → 是否為這家上手的範本（含證據）；一次核對中每份說明書只呼叫一次
    detect: Callable[[Sequence[Line]], DetectionResult]
    # 讀出：文字行 → 標準欄位（standard_fields.STANDARD_FIELDS）＋該上手規則需要的專屬資料；每份只呼叫一次
    read: Callable[[Sequence[Line]], TermSheet]
    # 說明書內部規則：只用讀出結果與審查標準，拿不到參考條件表的列與格式設定
    rules: Callable[[IssuerContext], list[CheckResult]]
    # 說明書內部規則可讀的參考條件表欄位（ADR 0005 的例外，須逐一宣告；目前只有 BARC 月配息率推算的年利率與天期，Issue #54）
    reference_fields: tuple[str, ...] = ()


BARC = Issuer(
    code=barc_rules.ISSUER,
    template_id=barc_parser.TEMPLATE_ID,
    label="BARC 中文產品說明書",
    parser_version=barc_parser.PARSER_VERSION,
    not_covered=tuple(barc_rules.NOT_COVERED),
    detect=lambda lines: barc_parser.detect(barc_parser.document(lines)),
    read=barc_parser.read,
    rules=barc_rules.run_all,
    reference_fields=barc_rules.REFERENCE_FIELDS,
)

HSBC = Issuer(
    code=hsbc_rules.ISSUER,
    template_id=hsbc_parser.TEMPLATE_ID,
    label="HSBC 中文產品說明書",
    parser_version=hsbc_parser.PARSER_VERSION,
    not_covered=tuple(hsbc_rules.NOT_COVERED),
    detect=hsbc_parser.detect,
    read=hsbc_parser.read,
    rules=hsbc_rules.run_all,
)

REGISTRY: tuple[Issuer, ...] = (BARC, HSBC)


def by_code(code: str, registry: Sequence[Issuer] = REGISTRY) -> Issuer | None:
    return next((i for i in registry if i.code == code), None)


TEMPLATE = Item.note("範本")


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
            item=TEMPLATE,
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
            item=TEMPLATE,
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
        item=TEMPLATE,
    )
