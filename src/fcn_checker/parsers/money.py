"""金額旁的幣別字：三家 parser 把「面額、最低金額、情境試算金額」旁邊寫的幣別交給共用規則（Issue #170）。

parser 只認得這裡列的幣別寫法（中文幣別或 3 碼 ISO 代碼），不知道哪一個才對：中文幣別對 ISO 代碼的對照在審查標準
（`[currency]`），比對在 rules/reference.py `currency_others`。新幣別要兩邊都加：這裡讓 parser 讀得到，審查標準讓規則對得上。
寫法分兩種：BARC 金額在前（`10,000 美元`）、MS 與 HSBC 幣別在前（`美元10,000`）。

商品幣別不必等於標的幣別（FCN 高度客製化，例：連結日股、美元計價）：承作幣別影響的是面額、配息、本金這類金額，
執行價、收盤價等股價跟著標的走，所以股價與實物交割算式（`stock_price_line`）不當成商品幣別的出處；匯率寫法
（`1 日圓 = 美元 0.006`）是兩種幣別的換算，也不算（Issue #179）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Money, Occurrence
from ..text import squash
from .layout import TextIndex

CURRENCY_WORDS = (
    "美元",
    "日幣",
    "日圓",
    "人民幣",
    "境外人民幣",
    "港幣",
    "港元",
    "澳幣",
    "澳元",
    "歐元",
    "英鎊",
    "南非幣",
    "南非蘭特",
    "新台幣",
    "加幣",
    "紐幣",
    "瑞郎",
    "星幣",
    "新加坡幣",
)
NUMBER = r"\d[\d,]*(?:\.\d+)?"  # 至少一個數字（單獨一個逗號不是金額）
# 幣別字：長的中文寫法先比（人民幣不會被當成「民幣」），或前面不接英文字母的 3 碼 ISO 代碼（交易所名稱的尾巴不算）。
# 全文索引去掉了換行，幣別字後面可能直接接下一行的字，所以不看後面。
CURRENCY = "(?:" + "|".join(sorted(CURRENCY_WORDS, key=len, reverse=True)) + "|(?<![A-Za-z])[A-Z]{3})"
AMOUNT_THEN_CURRENCY = rf"({NUMBER})\s*({CURRENCY})"  # BARC：10,000 美元
CURRENCY_THEN_AMOUNT = rf"({CURRENCY})\s*({NUMBER})"  # MS、HSBC：美元10,000


STOCK_PRICE_WORDS = ("股", "收盤價", "執行價", "觸及不保本價格", "下限價")
# 匯率：1 單位某幣別換成另一幣別多少（`1日圓=美元0.006`、`1日圓=0.006美元`），兩邊都不是商品幣別的出處
FX_RATE = rf"(?<![\d.,])1{CURRENCY}=(?:{CURRENCY}{NUMBER}|{NUMBER}{CURRENCY})"
CLAUSE_ENDS = "。，；：;"  # 子句的界線（半形逗號、冒號會出現在金額與時間裡，不算）
OPERATORS = "×xX*/÷+-"


def stock_price_line(text: str) -> bool:
    """跟著標的走的價格所在的行：股價（收盤價、執行價、觸及不保本價格、下限價）與含「股」的實物交割算式。

    承作幣別影響的是面額、配息、本金這類金額；這些行裡的金額不當成商品幣別的出處（2026-10-10 使用者決定）。
    """
    return any(w in text for w in STOCK_PRICE_WORDS)


def amounts(
    ti: TextIndex,
    pattern: str,
    *,
    currency_group: int,
    skip: Callable[[str], bool] = stock_price_line,
    start: int = 0,
    end: int | None = None,
) -> tuple[Money, ...]:
    """`ti.text[start:end]` 內每一處金額旁的幣別字；匯率裡的金額不算，`skip`（預設：股價與實物交割算式）成立的也不算。

    `skip` 看兩處（Issue #179）：
    - 金額所在的行：成立就不算。
    - 同一句裡金額前面的文字（`_lead_in`，可跨行）：關鍵字落在上一行（算式說明在上一行、數字在下一行）或被換行
      拆開（`收盤價`／`格小於…`）時靠這裡認出來。但算式的結果（`=` 後面、同一行裡後面不再接運算，例：
      `…期末股價×零股數/期末匯率` ／ `=美元83.30×0.0166/0.006` ／ `=日圓230.45` 的 `日圓230.45`）是以承作幣別
      支付的金額，仍算。
    """
    end = len(ti.text) if end is None else end
    fx = [m.span() for m in ti.finditer(FX_RATE)]
    out = []
    for m in ti.finditer(pattern):
        if m.start() < start or m.start() >= end or any(a <= m.start() < b for a, b in fx):
            continue
        lines = ti.lines_for(m.start(), m.end())
        if any(skip(ln.text) for ln in lines):
            continue
        if skip(_lead_in(ti, m.start(), start)) and not _result(ti, m.start(), m.end()):
            continue
        out.append(Money(m[0], m[currency_group], tuple(Evidence.of(ln) for ln in lines)))
    return tuple(out)


def _lead_in(ti: TextIndex, pos: int, floor: int = 0) -> str:
    """`ti.text[pos]` 前面同一句的文字（全文已去空白並串接各行；不超出 `floor`，即這一段情境的起點）。

    往前到上一個子句界線（`CLAUSE_ENDS`），或上一個算式結束、下一句開始的地方（`_new_statement`；例：BARC 情境
    (iii) 的 `= 6,323.00 日幣` ／ `總報酬率 = …`，兩個算式之間沒有標點）。
    """
    start = max(floor, *(ti.text.rfind(c, 0, pos) + 1 for c in CLAUSE_ENDS))
    i = ti.line_at(pos)
    while i > 0 and ti.line_start(i) > start:
        if _new_statement(squash(ti.lines[i - 1].text), squash(ti.lines[i].text)):
            start = ti.line_start(i)
            break
        i -= 1
    return ti.text[start:pos]


def _new_statement(prev: str, cur: str) -> bool:
    """`cur` 這一行開始新的一句：上一行是以 `=` 開頭的算式接續行，而且這一行不再以 `=` 接續，或上一行本身已經
    算完（中間還有 `=`，例：`=42.00美元×142股/1+36.00美元=6,000.00美元`）。

    算式說明在上一行、數字在下一行的寫法（`…期末股價×零股數/期末匯率` ／ `=美元83.30×0.0166/0.006` ／
    `=日圓230.45`）整段是同一句。
    """
    return prev.startswith("=") and (not cur.startswith("=") or "=" in prev[1:])


def _result(ti: TextIndex, start: int, end: int) -> bool:
    """`ti.text[start:end]` 是算式的結果：緊接在 `=`（或 `=-`）後面，同一行裡後面不再接運算。"""
    if not ti.text[max(0, start - 2) : start].endswith(("=", "=-")):
        return False
    after = ti.text[end : end + 1]
    same_line = ti.line_at(end) == ti.line_at(end - 1)
    return not (after and same_line and after in OPERATORS)


def scenario_occurrence(
    field: str, name: str, where: str, found: Sequence[Money], fallback: Sequence[Line]
) -> Occurrence:
    """一個情境的金額幣別出處：值是該情境裡每一處金額旁的幣別；一處金額都沒有時為缺漏（證據引情境開頭）。"""
    if not found:
        pf = ParsedField(field, FieldStatus.MISSING, evidence=[Evidence.of(x) for x in fallback], note="找不到金額")
    else:
        pf = ParsedField(field, FieldStatus.PRESENT, tuple(found), [e for money in found for e in money.evidence])
    return Occurrence(field, name, where, pf)


def currency_words(lns: list[Line], pattern: str, field: str, name: str, where: str) -> list[Occurrence]:
    """說明文字裡每一處幣別字（`pattern` 第 1 組，例：`以美元為計價單位`；BBVA、Nomura，Issue #305、#316）：每處一筆
    （欄位 `<field>_<n>`，只有一處時為 `<field>`），沒有任何一處時一筆缺漏。"""
    ti = TextIndex(lns)
    hits = list(ti.finditer(pattern))
    if not hits:
        return [Occurrence(field, name, where, ParsedField.missing(field, f"{where}找不到幣別說明文字"))]
    return [
        Occurrence(
            f"{field}_{n}" if len(hits) > 1 else field,
            name,
            where,
            ParsedField.present(field, m[1], ti.lines_for(m.start(), m.end())),
        )
        for n, m in enumerate(hits, start=1)
    ]
