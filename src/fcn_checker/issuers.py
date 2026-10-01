"""上手註冊表：每家上手的範本辨識、擷取、規則入口與預設詢價格式設定。

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
from .rules import barc as barc_rules
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


BARC = Issuer(
    code=barc_rules.ISSUER,
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
)

REGISTRY: tuple[Issuer, ...] = (BARC,)


def by_code(code: str, registry: Sequence[Issuer] = REGISTRY) -> Issuer | None:
    return next((i for i in registry if i.code == code), None)
