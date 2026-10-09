"""上手註冊表：每家上手的識別資料、範本辨識、讀出、說明書內部規則與其可讀的參考條件表欄位（ADR 0005），
以及投資人須知範本（ADR 0007；沒有範本的上手，投資人須知是未支援上手）。

上手 adapter 只提供這五樣；參考條件表欄位、審查標準、Non-Call／ISIN／發行日／比價日與回填都是各上手共用的規則，
由單份核對（single_check.py）依序執行。新增上手時在 `REGISTRY` 登記一筆，並在 `config/issuer_prefixes.toml`
登記上手編號（docs/issuer-onboarding.md §6）；批量入口、CLI 與 PANEL 都由這裡分派。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .investor_sheet import IisSheet
from .parsers import barc as barc_parser
from .parsers import barc_iis, hsbc_iis, ms_iis
from .parsers import hsbc as hsbc_parser
from .parsers import ms as ms_parser
from .rules import barc as barc_rules
from .rules import hsbc as hsbc_rules
from .rules import ms as ms_rules
from .rules import ms_iis as ms_iis_rules
from .rules.iis import IisIssuerContext
from .rules.kit import IssuerContext
from .schema import CheckResult, CheckStatus, DetectionResult, Evidence, Item, Line
from .standard_fields import TermSheet


@dataclass(frozen=True)
class IisTemplate:
    """某上手的投資人須知範本：檢查點依範本實際有的欄位（`IisSheet.provides`），規則各上手共用（rules/iis.py）。

    範本另有共用規則表達不了的檢查（例：MS 商品種類依標的數）時，由 `rules` 提供；只拿到投資人須知與同商品說明書的
    讀出結果，不含參考條件表（同上手說明書內部規則）。
    """

    template_id: str
    label: str
    parser_version: str
    detect: Callable[[Sequence[Line]], DetectionResult]
    read: Callable[[Sequence[Line]], IisSheet]
    not_covered: tuple[dict[str, str], ...] = ()  # 範本未涵蓋的型態（PANEL「待處理」與核對紀錄）
    rules: Callable[[IisIssuerContext], list[CheckResult]] | None = None  # 範本專屬的投資人須知規則


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
    # 說明書內部規則可讀的參考條件表欄位（ADR 0005 的例外，須逐一宣告：BARC 月配息率推算的年利率與天期（Issue #54）、
    # MS 月配息率與年化報酬率的年利率（說明書沒有年利率，Issue #135））
    reference_fields: tuple[str, ...] = ()
    # 投資人須知範本；None → 這家上手的投資人須知是未支援上手
    iis: IisTemplate | None = None


_IIS_SAMPLES = {
    "rule_id": "iis.template_variants",
    "description": "投資人須知依各 8 份樣本建立（docs/templates/*-zh-iis.md 樣本總表；MS 只支援其中新版 6 份）："
    "其他型態的寫法未驗證，範本以外的欄位或寫法會轉人工覆核",
}

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
    iis=IisTemplate(
        template_id=barc_iis.TEMPLATE_ID,
        label="BARC 中文投資人須知",
        parser_version=barc_iis.PARSER_VERSION,
        detect=barc_iis.detect,
        read=barc_iis.read,
        not_covered=(_IIS_SAMPLES,),
    ),
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
    iis=IisTemplate(
        template_id=hsbc_iis.TEMPLATE_ID,
        label="HSBC 中文投資人須知",
        parser_version=hsbc_iis.PARSER_VERSION,
        detect=hsbc_iis.detect,
        read=hsbc_iis.read,
        not_covered=(_IIS_SAMPLES,),
    ),
)

MS = Issuer(
    code=ms_rules.ISSUER,
    template_id=ms_parser.TEMPLATE_ID,
    label="MS 中文產品說明書",
    parser_version=ms_parser.PARSER_VERSION,
    not_covered=tuple(ms_rules.NOT_COVERED),
    detect=ms_parser.detect,
    read=ms_parser.read,
    rules=ms_rules.run_all,
    reference_fields=ms_rules.REFERENCE_FIELDS,
    iis=IisTemplate(
        template_id=ms_iis.TEMPLATE_ID,
        label="MS 中文投資人須知",
        parser_version=ms_iis.PARSER_VERSION,
        detect=ms_iis.detect,
        read=ms_iis.read,
        not_covered=(_IIS_SAMPLES,),
        rules=ms_iis_rules.run_all,
    ),
)

REGISTRY: tuple[Issuer, ...] = (BARC, HSBC, MS)


def by_code(code: str, registry: Sequence[Issuer] = REGISTRY) -> Issuer | None:
    return next((i for i in registry if i.code == code), None)


TEMPLATE = Item.note("範本")


def detect(lines: Sequence[Line], registry: Sequence[Issuer] = REGISTRY) -> tuple[Issuer | None, CheckResult]:
    """以每家已註冊上手辨識說明書範本；恰好一個命中才回傳該上手，零個或多個命中轉人工覆核。"""
    return _detect(lines, [(i, i.detect, i.template_id, i.label) for i in registry], "說明書")


def detect_iis(lines: Sequence[Line], registry: Sequence[Issuer] = REGISTRY) -> tuple[Issuer | None, CheckResult]:
    """以每家有投資人須知範本的上手辨識投資人須知；恰好一個命中才回傳該上手，零個或多個命中轉人工覆核。"""
    templates = [(i, i.iis.detect, i.iis.template_id, i.iis.label) for i in registry if i.iis is not None]
    return _detect(lines, templates, "投資人須知")


def _detect(
    lines: Sequence[Line],
    templates: Sequence[tuple[Issuer, Callable[[Sequence[Line]], DetectionResult], str, str]],
    what: str,
) -> tuple[Issuer | None, CheckResult]:
    detections = [(i, d(lines), tid, label) for i, d, tid, label in templates]
    hits = [(i, d, tid, label) for i, d, tid, label in detections if d.matched]
    rid = "template.detect"
    if len(hits) == 1:
        issuer, det, tid, label = hits[0]
        return issuer, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.PASS,
            actual=tid,
            document_evidence=det.evidence,
            message=f"符合 {label} 範本",
            item=TEMPLATE,
        )
    if hits:
        return None, CheckResult(
            rule_id=rid,
            field="template",
            status=CheckStatus.REVIEW_REQUIRED,
            actual=[tid for _, _, tid, _ in hits],
            reason_code="template_ambiguous",
            document_evidence=[e for _, d, _, _ in hits for e in d.evidence],
            message="同時符合多個範本，無法確定上手，請人工處理：" + "、".join(label for _, _, _, label in hits),
            item=TEMPLATE,
        )
    evidence: list[Evidence] = [e for _, d, _, _ in detections for e in d.evidence]
    reasons = "；".join(f"{i.code}：" + "；".join(d.failed) for i, d, _, _ in detections)
    return None, CheckResult(
        rule_id=rid,
        field="template",
        status=CheckStatus.REVIEW_REQUIRED,
        reason_code="template_unknown",
        document_evidence=evidence,
        message=f"不是已支援的{what}範本，請人工處理：" + reasons,
        item=TEMPLATE,
    )
