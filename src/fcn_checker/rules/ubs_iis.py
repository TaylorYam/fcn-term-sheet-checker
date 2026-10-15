"""UBS 投資人須知專屬規則（docs/rules/iis-check-rules.md §3 C25）：發行機構的發行分行要是倫敦分行（同說明書）。

發行機構名稱本身（瑞士銀行）由共用規則和審查標準 `issuer_name.ubs` 比；發行分行是 UBS 才有的寫法，放這裡。只用投資人須知
的讀出結果，不碰參考條件表；其他檢查點都是各上手共用的投資人須知規則（rules/iis.py、review_standard.iis_review_standard_rules）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from ..text import squash
from .iis import IisIssuerContext
from .kit import Check
from .ubs import BRANCH_PHRASES


def issuing_branch(ctx: IisIssuerContext) -> CheckResult:
    """相關機構「發行機構：瑞士銀行，經由倫敦分行發行」：發行分行是倫敦分行（三種寫法等價）；其他分行轉人工覆核。"""
    pf = read_iis(ctx.sheet, "issuing_branch")
    check = Check("iis.issuing_branch", "issuing_branch", Item.expected("相關機構發行機構的發行分行"), ctx.document)
    return check.needs(pf).compare(
        "倫敦分行",
        pf.value,
        ok=pf.ok and any(p in squash(pf.value) for p in BRANCH_PHRASES),
        fail=S.REVIEW_REQUIRED,
        reason="variant_not_covered",
        tolerance="「經由瑞士境外之倫敦分行」「經由倫敦分行」「透過其倫敦分行」視為相同",
        fail_message="發行分行不是倫敦分行（目前只支援瑞士銀行經由倫敦分行發行），請人工確認",
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [issuing_branch(ctx)]
