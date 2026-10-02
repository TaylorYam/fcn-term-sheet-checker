"""上手註冊表：每家上手的範本辨識、擷取、規則入口、參考條件表需要的擷取能力與預設詢價格式設定。

新增上手時在 `REGISTRY` 登記一筆（docs/issuer-onboarding.md §6）；核對入口、CLI 與 PANEL 都由這裡分派。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import OrderFormat, ReviewStandard
from .orders.inquiry import OrderRecord
from .parsers import barc as barc_parser
from .parsers import hsbc as hsbc_parser
from .rules import barc as barc_rules
from .rules import hsbc as hsbc_rules
from .schema import CheckResult, DetectionResult, Line, ParsedField


@dataclass(frozen=True)
class Issuer:
    code: str  # 上手代號，與詢價格式設定的 issuer 相同（例：BARC）
    template_id: str
    label: str
    parser_version: str
    detect: Callable[[Sequence[Line]], DetectionResult]
    parse: Callable[[Sequence[Line]], tuple[DetectionResult, Any]]
    product_code: Callable[[Sequence[Line]], ParsedField]
    context: Callable[[Any, OrderRecord, ReviewStandard, OrderFormat], Any]
    pairing: Callable[[Any], CheckResult]  # 商品代號配對（PANEL 配對失敗即停止）
    run_rules: Callable[[Any], list[CheckResult]]
    not_covered: tuple[dict[str, str], ...]
    order_format: Path  # 預設詢價格式設定檔（相對於工作目錄）
    # ---- 參考條件表流程（ADR 0004）----
    isin: Callable[[Any], ParsedField]  # 說明書 ISIN（含證據）
    autocall_schedule: Callable[[Any], ParsedField]  # 值為 rules.reference.AutocallSchedule
    reference_rules: Callable[[Any], list[CheckResult]]  # 表上事先填好的欄位＋說明書內部規則
    reference_not_covered: tuple[dict[str, str], ...]
    product_prefix: str = ""


BARC = Issuer(
    code=barc_rules.ISSUER,
    product_prefix="029",
    template_id=barc_parser.TEMPLATE_ID,
    label="BARC 中文產品說明書",
    parser_version=barc_parser.PARSER_VERSION,
    detect=lambda lines: barc_parser.detect(barc_parser.document(lines)),
    parse=barc_parser.parse,
    product_code=barc_parser.product_code,
    context=barc_rules.Context,
    pairing=barc_rules.product_code,
    run_rules=barc_rules.run_all,
    not_covered=tuple(barc_rules.NOT_COVERED),
    order_format=Path("config/order_formats/barc.toml"),
    isin=lambda ts: ts.f("isin"),
    autocall_schedule=barc_rules.autocall_schedule,
    reference_rules=barc_rules.reference_rules,
    reference_not_covered=tuple(barc_rules.REFERENCE_NOT_COVERED),
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
    pairing=hsbc_rules.common.product_code,
    run_rules=hsbc_rules.run_all,
    not_covered=tuple(hsbc_rules.NOT_COVERED),
    order_format=Path("config/order_formats/hsbc.toml"),
    product_prefix="325",
    isin=lambda ts: ts.f("isin"),
    autocall_schedule=hsbc_rules.autocall_schedule,
    reference_rules=hsbc_rules.reference_rules,
    reference_not_covered=tuple(hsbc_rules.NOT_COVERED),
)

REGISTRY: tuple[Issuer, ...] = (BARC, HSBC)


def by_code(code: str, registry: Sequence[Issuer] = REGISTRY) -> Issuer | None:
    return next((i for i in registry if i.code == code), None)
