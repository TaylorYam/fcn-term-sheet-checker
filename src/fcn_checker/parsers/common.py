"""各上手說明書 parser 共用的小工具：數字、推得欄位、文件上的一組值（含原文行）、月配息率出處、沒有 KO 價時的 KO 100%，
以及保留空白的原文合併文字 `RawText`、同一句另一組的 `capture_group`（Nomura、GS），
費用表依座標讀費率的 `fee_rates`（GS、Natixis、CA）。

MS 與 BNP（之後的上手同樣）的 `read` 與情境分析解析都用這裡的版本，不各自複製一份（Issue #196）。
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Generic, NamedTuple, TypeVar

from .. import standard_fields
from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from .layout import TextIndex

NUM = r"([\d,]+(?:\.\d+)?)"  # 數字（可有千分位與小數）
SCENARIO_HEAD = re.compile(r"^情境([一二三四五六七八九十]+)[:：](.*)$")  # 情境標題「情境X：標題」（去空白後）
# 說明書沒有自動提前出場價（Non-Call = 天期）：機制仍在、只是不會觸發，KO 視為期初價格的 100%（參考條件表填 100／進場價）
NO_AUTOCALL_KO_PCT = Decimal(100)

T = TypeVar("T")


def dec(s: str) -> Decimal:
    """去掉千分位逗號的 Decimal。"""
    return Decimal(s.replace(",", ""))


def number(s: str) -> int | Decimal:
    """金額：整數值回傳 int，否則 Decimal。"""
    d = dec(s)
    return int(d) if d == d.to_integral_value() else d


def ko_at_initial(prices: dict) -> dict:
    """一列標的價格補上 KO 價 = 期初價格（說明書沒有 KO 價時，見 `NO_AUTOCALL_KO_PCT`）。"""
    out = dict(prices)
    if "ko" not in out and "initial" in out:
        out["ko"] = out["initial"]
    return out


def by_value(hits: Iterable[tuple[T, Sequence[Line]]]) -> dict[T, list[Line]]:
    """重複寫出的值：同一個值的各處合成一筆（依第一次出現的順序），原文行為每一處的行（情境假設值，Issue #294）。"""
    out: dict[T, list[Line]] = {}
    for value, lines in hits:
        out.setdefault(value, []).extend(lines)
    return out


def derived(name: str, src: ParsedField, value=None) -> ParsedField:
    """由另一個欄位推得（證據沿用來源）：來源有問題時沿用來源的狀態與說明。"""
    if not src.ok:
        return ParsedField(name, src.status, None, list(src.evidence), list(src.candidates), src.note)
    return ParsedField(name, FieldStatus.PRESENT, value, list(src.evidence))


class Amount(NamedTuple):
    """金額與旁邊的幣別字（例：累積總配息金額）。"""

    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Hit(Generic[T]):
    """文件上的一個值（或一組具名的值，`typing.NamedTuple`）與原文行。"""

    value: T
    lines: tuple[Line, ...]


def hits(ti: TextIndex, pattern: str, convert: Callable[[re.Match[str]], T]) -> list[Hit[T]]:
    """`pattern` 在 `ti` 的每一處，`convert(match)` 為值。"""
    return [Hit(convert(m), tuple(ti.lines_for(m.start(), m.end()))) for m in ti.finditer(pattern)]


def one(found: list[Hit[T]]) -> Hit[T] | None:
    """剛好一處時回傳那一處；沒有或多處為 None（由規則轉人工覆核）。"""
    return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class Mention:
    """重複寫出的值的一個出處（月配息率、年化報酬率；GS 另有面額、單位數、天期等，Issue #317）；值讀不到時為 None。"""

    where: str
    value: Decimal | int | None
    evidence: tuple[Evidence, ...]

    def parsed(self, name: str) -> ParsedField:
        """當成欄位 `name` 交給規則：讀不到時為缺漏（證據保留出處附近的原文行）。"""
        if self.value is None:
            return ParsedField(name, FieldStatus.MISSING, evidence=list(self.evidence), note=f"找不到{self.where}")
        return ParsedField(name, FieldStatus.PRESENT, self.value, list(self.evidence))

    @classmethod
    def of_field(cls, where: str, pf: ParsedField) -> Mention:
        return cls(where, pf.value if pf.ok else None, tuple(pf.evidence))

    @classmethod
    def of_hit(cls, where: str, value: Decimal, hit: Hit) -> Mention:
        return cls(where, value, tuple(Evidence.of(ln) for ln in hit.lines))

    @classmethod
    def missing(cls, where: str, lines) -> Mention:
        return cls(where, None, tuple(Evidence.of(ln) for ln in lines))

    @classmethod
    def of_optional_hit(cls, where: str, hit: Hit | None, pick: Callable, fallback) -> Mention:
        """`hit` 找不到時為讀不到（證據為 `fallback` 行）；找到時值為 `pick(hit.value)`。"""
        return cls.missing(where, fallback) if hit is None else cls.of_hit(where, pick(hit.value), hit)


# ---------------------------------------------------------------- 原文合併文字（Nomura、GS）


class RawText:
    """多行原文合併（同 `layout.join_text`：英數字跨行補一個空白），並可由字元位置找回原始行（作為證據）。

    名稱、地址、電話等要保留空白的值在這裡讀；數字、日期與句型在去空白的 `TextIndex` 上讀。
    """

    def __init__(self, lines: Sequence[Line]):
        self.lines = list(lines)
        out, self._starts = "", []
        for ln in self.lines:
            t = ln.text.strip()
            if out and t and re.search(r"[A-Za-z0-9.,)]$", out) and re.match(r"[A-Za-z0-9(]", t):
                out += " "
            self._starts.append(len(out))
            out += t
        self.text = out

    def lines_for(self, start: int, end: int) -> list[Line]:
        if not self.lines:
            return []
        i = max(0, bisect.bisect_right(self._starts, start) - 1)
        j = max(i, bisect.bisect_right(self._starts, max(start, end - 1)) - 1)
        return self.lines[i : j + 1]

    def capture(self, name: str, pattern: str, convert: Callable = lambda x: x.strip(), group: int = 1) -> ParsedField:
        """`pattern` 第 `group` 組的每一處（證據為整個比對所在的行）；不同值 → 歧義，沒有 → 缺漏，轉不成值 → 不合法。"""
        found = []
        for m in re.finditer(pattern, self.text, re.S):
            lns = self.lines_for(m.start(), m.end())
            try:
                value = convert(m[group])
            except (ValueError, TypeError, InvalidOperation):
                value = None
            if value is None:
                return ParsedField.invalid(name, lns, f"「{m[group]}」無法辨識")
            found.append((value, lns))
        return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")


ZH_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def zh_number(s: str) -> int | None:
    """中文數字（一～九十九）或阿拉伯數字 → int（Citi 自動提前出場起始期、UBS 項號「二十六、」）。"""
    if s.isdigit():
        return int(s)
    if s in ZH_DIGITS:
        return ZH_DIGITS[s]
    m = re.fullmatch(r"([一二三四五六七八九]?)十([一二三四五六七八九]?)", s)
    if not m:
        return None
    return (ZH_DIGITS.get(m[1], 1) if m[1] else 1) * 10 + (ZH_DIGITS[m[2]] if m[2] else 0)


def compat_standard(text: str) -> str:
    """CJK 相容字（U+F900–U+FAFF）轉成標準字（例：U+F9DD → 利、U+F98E → 年）；其他字元不動，字數不變。"""
    return "".join(unicodedata.normalize("NFKC", c) if 0xF900 <= ord(c) <= 0xFAFF else c for c in text)


def compat_normalized(lines: Sequence[Line]) -> list[Line]:
    """每行的 CJK 相容字轉成標準字（GS 投資人須知、CA 兩種文件）；字數不變，字座標照舊。"""
    return [replace(ln, text=t) if (t := compat_standard(ln.text)) != ln.text else ln for ln in lines]


def capture_group(lns: list[Line], pattern: str, group: int, name: str, convert: Callable = lambda x: x) -> ParsedField:
    """同一句的另一組（例：最低加價申購金額接在最低申購金額後面）。"""
    ti = TextIndex(lns)
    found = []
    for m in ti.finditer(pattern):
        try:
            found.append((convert(m[group]), ti.lines_for(m.start(), m.end())))
        except (ValueError, TypeError, InvalidOperation):
            return ParsedField.invalid(name, ti.lines_for(m.start(), m.end()), f"「{m[group]}」無法辨識")
    return ParsedField.from_hits(name, found, missing_note="找不到欄位標籤或已知寫法")


# ---------------------------------------------------------------- 費用表（GS、Natixis、CA）

FEE_RATE = r"(\d+(?:\.\d+)?%[~～]\d+(?:\.\d+)?%)"
# 費用表：左欄項目名稱的開頭 → 審查標準 [fees] 的鍵（投資人須知寫「贖回費用」）
FEE_ITEMS = {
    "申購費用": "申購費用",
    "提前贖回費用": "提前贖回費用",
    "贖回費用": "提前贖回費用",
    "管理費用": None,
    "分銷費": "分銷費用",
    "保費費用": None,
    "解約費用": None,
    "其他費用": None,
}


def fee_rates(lines: list[Line]) -> dict[str, ParsedField]:
    """費用表（說明書第四章 2.、投資人須知四.）各費用項目的費率區間（標準欄位 `fee_<費用項目>`）。

    依座標讀：費率欄從表頭「費率（百分比）」的 x 起、到下一欄「收取時點」之前（分銷費用的子列標籤「報酬／費用／折讓」也在
    這個範圍）；每個費用項目一列，從左欄項目名稱那一行到下一個項目名稱之前。一列的費率欄文字接起來須剛好有一個
    「X%~Y%」（分銷費用只有「費用」子列有費率；擷取時欄位文字可能交錯，不要求前綴寫法）。"""
    out = {standard_fields.fee_field(k): k for k in dict.fromkeys(v for v in FEE_ITEMS.values() if v)}
    head = next((ln for ln in lines if squash(ln.text).startswith("費率")), None)
    if head is None:
        return {name: ParsedField.missing(name, "找不到費用表表頭「費率（百分比）」") for name in out}
    # 下一欄表頭：和「費率」那一行垂直重疊（CA 表頭「費率（百分」／「比）」兩行，其他欄置中在兩行之間）
    right = min(
        (
            ln.x0
            for ln in lines
            if ln.page == head.page and ln.y0 < head.y1 and ln.y1 > head.y0 and ln.x0 > head.x0 + 10
        ),
        default=None,
    )
    lo = head.x0 - 40
    starts = []
    for ln in lines:
        if ln.x0 < lo and (ln.page, ln.y0) > (head.page, head.y0):
            label = next((k for k in FEE_ITEMS if squash(ln.text).startswith(k)), None)
            if label is not None:
                starts.append((ln, FEE_ITEMS[label]))
    fields: dict[str, ParsedField] = {}
    for name, label in out.items():
        rows = [k for k, (_, key) in enumerate(starts) if key == label]
        if len(rows) != 1 or right is None:
            fields[name] = ParsedField.missing(name, f"費用表找不到「{label}」這一列或出現多次")
            continue
        k = rows[0]
        top = starts[k][0]
        bottom = starts[k + 1][0] if k + 1 < len(starts) else None
        cells = sorted(
            (
                ln
                for ln in lines
                if lo <= ln.x0 < right - 5
                and (ln.page, ln.y0 + 3) >= (top.page, top.y0)
                and (bottom is None or (ln.page, ln.y0 + 3) < (bottom.page, bottom.y0))
            ),
            key=lambda x: (x.page, x.y0, x.x0),
        )
        rates = re.findall(FEE_RATE, "".join(squash(ln.text) for ln in cells))
        fields[name] = (
            ParsedField.present(name, rates[0], [top, *cells])
            if len(rates) == 1
            else ParsedField.invalid(name, [top, *cells], f"費用表「{label}」的費率欄沒有剛好一個「X%~Y%」")
        )
    return fields
