"""MS 第一章第 18 項情境分析：假設、重印價格表與各情境的算式（docs/templates/ms-zh-product-description.md §6、§7）。

只擷取文件上寫的值與原文行；核對在 rules/ms_scenario.py。抓不到的值為 None，由規則轉人工覆核。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..schema import Line, ParsedField
from ..text import squash
from . import ms_tables
from .layout import TextIndex

N = r"([\d,]+\.\d{2})"  # 金額（2 位小數）
SCENARIO_HEAD = re.compile(r"^情境([一二三四五六七八九十]+)[:：](.*)$")


class ScenarioText(TextIndex):
    """保留相鄰數字行的邊界（插入 |），避免上一行結尾與下一行開頭的數字相黏。"""

    def __init__(self, lines):
        self.lines = list(lines)
        self._starts = []
        text = ""
        for line in self.lines:
            part = squash(line.text)
            if text and part and text[-1].isdigit() and part[0].isdigit():
                text += "|"
            self._starts.append(len(text))
            text += part
        self.text = text


@dataclass(frozen=True)
class Hit:
    """文件上的一個值（或一組值）與原文行。"""

    values: tuple
    lines: tuple[Line, ...]


@dataclass
class Scenario:
    number: str  # 情境編號（一、二…）
    title: str  # 例：較佳情況 A、一般情況、較差情況
    kind: str  # profit 獲利情境／worse 較差情境／default 發行機構無法履約（不核對）
    lines: list[Line]
    assumed: Hit | None = None  # 假設的配息次數：(k,)；「所有配息觀察期間」為 (None,)
    coupons: list[Hit] = field(default_factory=list)  # 每期配息算式：(面額, 月配息率, 每期配息)
    rates: list[Hit] = field(default_factory=list)  # 算式中的每個 ×X%：(X,)
    # 損益算式；獲利：(面額, 每期配息, k, 面額, 損益)；較差：(每期配息, k, 贖回價值, 面額, 損益)
    pnl: list[Hit] = field(default_factory=list)
    annualized: list[Hit] = field(default_factory=list)  # 年化報酬率：(Y,)
    strike: list[Hit] = field(default_factory=list)  # 較差情境：(彭博代碼去空白, 執行價)


@dataclass
class ScenarioSection:
    lines: list[Line]
    denomination: Hit | None  # 總投資金額 = 1 單位商品面額 = N
    tenor: Hit | None  # 年期：N 個月
    monthly: list[Hit]  # 月配息率=X%
    table: ParsedField  # 重印價格表
    scenarios: list[Scenario]


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _hits(ti: TextIndex, pattern: str, convert) -> list[Hit]:
    return [Hit(tuple(convert(m)), tuple(ti.lines_for(m.start(), m.end()))) for m in ti.finditer(pattern)]


def _one(hits: list[Hit]) -> Hit | None:
    return hits[0] if len(hits) == 1 else None


def _scenario(number: str, title: str, lines: list[Line], unit: str) -> Scenario:
    kind = "default" if "無法履約" in title else "worse" if "較差" in title else "profit"
    s = Scenario(number, title, kind, lines)
    ti = ScenarioText(lines)
    assumed = [
        *_hits(ti, r"假設在第(\d+)個(?:配息週期終止日|定價日)", lambda m: (int(m[1]),)),
        *_hits(ti, r"假設在第1至第(\d+)個(?:配息觀察期間|定價日)", lambda m: (int(m[1]),)),
        *_hits(ti, r"假設在所有配息觀察期間內", lambda m: (None,)),
    ]
    s.assumed = _one(assumed)
    s.coupons = _hits(
        ti, rf"{N}{unit}×(\d+(?:\.\d+)?)%(?:×100%)?={N}{unit}", lambda m: (_dec(m[1]), Decimal(m[2]), _dec(m[3]))
    )
    s.rates = _hits(ti, r"(?<!%)×(\d+(?:\.\d+)?)%", lambda m: (Decimal(m[1]),))
    if kind == "profit":
        s.pnl = _hits(
            ti,
            rf"={N}{unit}\+{N}{unit}(?:×(\d+))?-{N}{unit}=(-?[\d,]+\.\d{{2}}){unit}",
            lambda m: (_dec(m[1]), _dec(m[2]), int(m[3] or 1), _dec(m[4]), _dec(m[5])),
        )
    elif kind == "worse":
        s.pnl = _hits(
            ti,
            rf"={N}{unit}×(\d+)\+([\d,]+(?:\.\d+)?){unit}-{N}{unit}=(-?[\d,]+\.\d{{2}}){unit}",
            lambda m: (_dec(m[1]), int(m[2]), _dec(m[3]), _dec(m[4]), _dec(m[5])),
        )
        s.strike = _hits(
            ti,
            rf"連結標的[（(]([A-Z0-9./-]+)[）)]的收盤價=[\d,]+(?:\.\d+)?{unit}小於其執行價=([\d,]+\.\d{{4}}){unit}",
            lambda m: (m[1], _dec(m[2])),
        )
    s.annualized = _hits(ti, r"年化報酬率為(-?\d+(?:\.\d+)?)%", lambda m: (Decimal(m[1]),))
    return s


def section(lines: list[Line], unit: str) -> ScenarioSection:
    """第 18 項：假設（到第一個情境前）、重印價格表（月配息率之後到情境一）、各情境（依「情境X：」切開）。"""
    unit = re.escape(unit)
    heads = [i for i, ln in enumerate(lines) if SCENARIO_HEAD.match(squash(ln.text))]
    head_end = heads[0] if heads else len(lines)
    ti = TextIndex(lines[:head_end])
    monthly = _hits(ti, r"月配息率=(\d+(?:\.\d+)?)%", lambda m: (Decimal(m[1]),))
    if len(monthly) == 1 and heads:
        last = monthly[0].lines[-1]
        region = lines[lines.index(last) + 1 : head_end]
        table = ms_tables.price_table("scenario_table", region, None)
    else:
        table = ParsedField.missing("scenario_table", "找不到第 18 項重印價格表（月配息率之後、情境一之前）")
    scenarios = []
    for k, i in enumerate(heads):
        m = SCENARIO_HEAD.match(squash(lines[i].text))
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        scenarios.append(_scenario(m[1], m[2], lines[i:end], unit))
    return ScenarioSection(
        lines=lines,
        denomination=_one(_hits(ti, rf"總投資金額=1單位商品面額=([\d,]+(?:\.\d+)?){unit}", lambda m: (_dec(m[1]),))),
        tenor=_one(_hits(ti, r"年期[:：](\d+)個月", lambda m: (int(m[1]),))),
        monthly=monthly,
        table=table,
        scenarios=scenarios,
    )
