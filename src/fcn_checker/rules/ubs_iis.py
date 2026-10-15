"""UBS 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 C25）：發行機構的發行分行要是倫敦分行（同說明書）。

發行機構名稱本身（瑞士銀行）由共用規則和審查標準 `issuer_name.ubs` 比；發行分行是 UBS 才有的寫法，放這裡（判定與說明書
`doc.issuing_branch` 共用 `rules.ubs.issuing_branch`）。只用投資人須知的讀出結果，不碰參考條件表；其他檢查點都是各上手共用的
投資人須知規則（rules/iis.py、review_standard.iis_review_standard_rules）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from . import ubs
from .iis import IisIssuerContext
from .kit import Check


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    check = Check("iis.issuing_branch", "issuing_branch", Item.expected("相關機構發行機構的發行分行"), ctx.document)
    return [ubs.issuing_branch(check, read_iis(ctx.sheet, "issuing_branch"))]
