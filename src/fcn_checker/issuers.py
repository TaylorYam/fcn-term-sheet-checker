"""上手註冊表：每家上手的代號、範本、parser、規則入口與預設詢價格式設定。

核對入口、CLI 與 PANEL 都由這裡取得上手清單；新增上手時在 `ISSUERS` 登記一筆
（docs/issuer-onboarding.md §5、§6），不需改核對流程。
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

ORDER_FORMAT_DIR = Path("config/order_formats")


@dataclass(frozen=True)
class Issuer:
    code: str  # 上手代號，與詢價格式設定的 issuer 相同，例 BARC
    template_id: str  # 範本代號，例 barc-zh-pd
    label: str  # PANEL 顯示名稱
    parser_version: str
    detect: Callable[[Sequence[Line]], DetectionResult]  # 只做範本辨識
    product_code: Callable[[Sequence[Line]], ParsedField]  # PANEL 預覽用的說明書商品代號
    parse: Callable[[Sequence[Line]], tuple[DetectionResult, Any]]  # 範本辨識＋欄位擷取
    context: Callable[[Any, OrderRecord, ReviewStandard, OrderFormat], Any]  # 建立規則用的 Context
    pairing: Callable[[Any], CheckResult]  # 商品代號配對規則
    run_rules: Callable[[Any], list[CheckResult]]
    not_covered: tuple[dict[str, str], ...]

    @property
    def default_order_format(self) -> Path:
        return ORDER_FORMAT_DIR / f"{self.code.lower()}.toml"


ISSUERS: tuple[Issuer, ...] = (
    Issuer(
        code=barc_rules.ISSUER,
        template_id=barc_parser.TEMPLATE_ID,
        label="BARC 中文產品說明書",
        parser_version=barc_parser.PARSER_VERSION,
        detect=barc_parser.detect_lines,
        product_code=barc_parser.product_code,
        parse=barc_parser.parse,
        context=lambda ts, order, std, fmt: barc_rules.Context(ts, order, std, fmt, barc_rules.ISSUER),
        pairing=barc_rules.product_code,
        run_rules=barc_rules.run_all,
        not_covered=tuple(barc_rules.NOT_COVERED),
    ),
)


def find(code: str, issuers: Sequence[Issuer] = ISSUERS) -> Issuer | None:
    return next((i for i in issuers if i.code == code), None)
