"""SG 第一章 §15(3) 情境分析：假設、重印標的表與各情境的算式（docs/templates/sg-zh-product-description.md §4.7）。

只擷取文件上寫的值與原文行；核對在 rules/sg_scenario.py。抓不到的值為 None，由規則轉人工覆核。
兩種版面在去空白後的寫法相同（舊版面只是數字與單位間多了空白），所有句型都在去空白的全文上比對。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..schema import Line, ParsedField
from ..standard_fields import Money
from ..text import squash
from . import money, sg_tables
from .layout import TextIndex

NUM = r"([\d,]+(?:\.\d+)?)"
PCT = r"(-?\d+(?:\.\d+)?)%"
U = money.CURRENCY  # 金額後的幣別字（任一已知寫法；寫哪一個才對由共用規則核對）
SCENARIO_HEAD = re.compile(r"^情境([一二三四五六七八九十]+)[:：](.*)$")
KINDS = (("較佳", "best"), ("中間", "middle"), ("較差", "worse"), ("最差", "worst"))
# 不算商品幣別出處的行：股價（共用）、實體交付算式（數額、現金交割、持有單位）、現金結算總報酬率算式、匯率
SKIP_WORDS = ("數額", "現金交割", "加上現金", "{[", "最終匯率")


@dataclass(frozen=True)
class Hit:
    """文件上的一組值與原文行。"""

    values: tuple
    lines: tuple[Line, ...]


@dataclass
class Scenario:
    number: str  # 情境編號（一、二…）
    title: str  # 例：較佳情況、中間情況
    kind: str  # best／middle／worse／worst／unknown
    lines: list[Line]
    assumed: Hit | None = None  # 情境一假設句：("D", None) 觀察期間起始日／("P", k) 第 k 個評價日
    coupon: Hit | None = None  # (a, b 或 None, 單位數, 面額, 幣別, 配息率, 總投資金額, 幣別, 配息率, 配息, 幣別)
    coupon_count: int = 0  # 「個配息金額」出現次數（算式應剛好一個）
    cumulative: Hit | None = None  # 累積總配息金額：(金額, 幣別)
    redemption: Hit | None = None  # 贖回金額：(標籤, 單位數, 面額, 幣別, 總投資金額, 幣別, 金額, 幣別)
    total_return: Hit | None = None  # 獲利情境總報酬率：(合計, 幣別, 總投資金額, 幣別, 總報酬率, 平均年化報酬率)
    close: Hit | None = None  # 較差情境假設句：(k 或 None, 收盤價, 幣別, 下限價／執行價)
    fx: Hit | None = None  # quanto：最終匯率定價 (匯率,)
    shares: Hit | None = None  # 數額：(面額, 幣別, 執行價, 幣別, 匯率 或 None, 數額)
    whole: Hit | None = None  # 應實際交付數：(整數單位,)
    cash: Hit | None = None  # 現金交割：(收盤價, 幣別, 畸零數, 匯率 或 None, 金額, 幣別)
    holdings: Hit | None = None  # 總投資金額…將獲得：(單位數, 總單位, 總現金, 幣別)
    worse_return: Hit | None = (
        None  # 現金結算總報酬率：(總投資, 幣別, 收盤價, 幣別, 執行價, 幣別, 累積, 幣別, 總投資, 幣別, 總報酬率, 年化)
    )
    money: tuple[Money, ...] = ()  # 各金額旁的幣別字（商品幣別的出處）


@dataclass
class ScenarioSection:
    lines: list[Line]
    total: Hit | None  # 總投資金額 = U單位面額 = N 幣別：(U, N, 幣別)
    tenor: Hit | None  # 天期：N個月：(N,)
    rate: Hit | None  # 配息率 = X %：(X,)
    table: ParsedField  # 重印標的表（sg_tables.price_table，含表頭百分比）
    scenarios: list[Scenario]
    assumptions: list[Line] = field(default_factory=list)


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _hits(ti: TextIndex, pattern: str, convert) -> list[Hit]:
    return [Hit(tuple(convert(m)), tuple(ti.lines_for(m.start(), m.end()))) for m in ti.finditer(pattern)]


def _one(hits: list[Hit]) -> Hit | None:
    return hits[0] if len(hits) == 1 else None


def _skip(text: str) -> bool:
    return money.stock_price_line(text) or any(w in text for w in SKIP_WORDS)


def _kind(title: str) -> str:
    return next((kind for word, kind in KINDS if word in title), "unknown")


def _scenario(number: str, title: str, lines: list[Line]) -> Scenario:
    s = Scenario(number, title, _kind(title), lines)
    ti = TextIndex(lines)
    s.money = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, skip=_skip)
    s.assumed = _one(
        _hits(
            ti,
            r"假設於(?:(記憶事件|自動提前出場事件)觀察期間起始日|第(\d+)個評價日)，發生自動提前出場事件",
            lambda m: ("D", None) if m[1] else ("P", int(m[2])),
        )
    )
    s.coupon_count = len(list(ti.finditer(r"個配息金額")))
    s.coupon = _one(
        _hits(
            ti,
            rf"第(\d+)(?:~(\d+))?個配息金額=(\d+)單位面額[xX×]{NUM}({U})[xX×]{PCT}={NUM}({U})[xX×]{PCT}={NUM}({U})",
            lambda m: (
                int(m[1]),
                int(m[2]) if m[2] else None,
                int(m[3]),
                _dec(m[4]),
                m[5],
                Decimal(m[6]),
                _dec(m[7]),
                m[8],
                Decimal(m[9]),
                _dec(m[10]),
                m[11],
            ),
        )
    )
    s.cumulative = _one(_hits(ti, rf"累積總配息金額={NUM}({U})", lambda m: (_dec(m[1]), m[2])))
    s.redemption = _one(
        _hits(
            ti,
            rf"(自動提前出場贖回金額|到期贖回金額)=(\d+)單位面額[xX×]{NUM}({U})[xX×]100%={NUM}({U})[xX×]100%={NUM}({U})",
            lambda m: (m[1], int(m[2]), _dec(m[3]), m[4], _dec(m[5]), m[6], _dec(m[7]), m[8]),
        )
    )
    s.total_return = _one(
        _hits(
            ti,
            rf"=[(（]{NUM}({U})/{NUM}({U})[)）]-1={PCT}[(（]平均年化報酬率：{PCT}[)）]",
            lambda m: (_dec(m[1]), m[2], _dec(m[3]), m[4], Decimal(m[5]), Decimal(m[6])),
        )
    )
    s.close = _one(
        _hits(
            ti,
            rf"連結標的(?:\(k=(\d+)\))?收盤價={NUM}({U})小於(下限價|執行價)",
            lambda m: (int(m[1]) if m[1] else None, _dec(m[2]), m[3], m[4]),
        )
    )
    s.fx = _one(_hits(ti, r"最終匯率定價=(\d+(?:\.\d+)?)", lambda m: (Decimal(m[1]),)))
    s.shares = _one(
        _hits(
            ti,
            rf"數額計算=每單位面額/.*?執行價={NUM}({U})/\(?{NUM}({U})(?:[xX×](\d+(?:\.\d+)?)\))?=([\d,]+\.\d{{4}})",
            lambda m: (_dec(m[1]), m[2], _dec(m[3]), m[4], Decimal(m[5]) if m[5] else None, _dec(m[6])),
        )
    )
    s.whole = _one(
        _hits(
            ti, r"應實際交付數=數額向下調整至最接近的整數位(?:或交易單位)?=([\d,]+)單位", lambda m: (int(_dec(m[1])),)
        )
    )
    s.cash = _one(
        _hits(
            ti,
            rf"加上現金交割={NUM}({U})[xX×](\d+\.\d{{4}})(?:[xX×](\d+(?:\.\d+)?)=[\d,]+(?:\.\d+)?{U}[xX×]\d+(?:\.\d+)?)?={NUM}({U})",
            lambda m: (_dec(m[1]), m[2], Decimal(m[3]), Decimal(m[4]) if m[4] else None, _dec(m[5]), m[6]),
        )
    )
    s.holdings = _one(
        _hits(
            ti,
            rf"總投資金額=(\d+)單位面額，因此，到期日將獲得(?:最終評價日)?(?:最差表現之)?連結標的([\d,]+)單位加上現金{NUM}({U})",
            lambda m: (int(m[1]), int(_dec(m[2])), _dec(m[3]), m[4]),
        )
    )
    s.worse_return = _one(
        _hits(
            ti,
            rf"總報酬率=\{{\[{NUM}({U})[xX×]\({NUM}({U})/{NUM}({U})\)\+{NUM}({U})\]/{NUM}({U})\}}-1={PCT}[(（]平均年化報酬率：{PCT}[)）]",
            lambda m: (
                _dec(m[1]),
                m[2],
                _dec(m[3]),
                m[4],
                _dec(m[5]),
                m[6],
                _dec(m[7]),
                m[8],
                _dec(m[9]),
                m[10],
                Decimal(m[11]),
                Decimal(m[12]),
            ),
        )
    )
    return s


def section(lines: list[Line], count: int | None) -> ScenarioSection:
    """§15(3)：假設（到重印表之前）、重印標的表（「配息率 = X %」之後到情境一之前）、各情境（依「情境X：」切開）。"""
    heads = [i for i, ln in enumerate(lines) if SCENARIO_HEAD.match(squash(ln.text))]
    head_end = heads[0] if heads else len(lines)
    rate_at = next((i for i, ln in enumerate(lines[:head_end]) if squash(ln.text).startswith("配息率=")), None)
    assumptions = lines[: rate_at + 1] if rate_at is not None else lines[:head_end]
    ti = TextIndex(assumptions)
    if rate_at is not None and heads:
        table = sg_tables.price_table("scenario_table", lines[rate_at + 1 : head_end], count, reprint=True)
    else:
        table = ParsedField.missing("scenario_table", "找不到第 15 項重印標的表（「配息率 = X %」到情境一之前）")
    scenarios = []
    for k, i in enumerate(heads):
        m = SCENARIO_HEAD.match(squash(lines[i].text))
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        scenarios.append(_scenario(m[1], m[2], lines[i:end]))
    return ScenarioSection(
        lines=lines,
        total=_one(_hits(ti, rf"總投資金額=(\d+)單位面額={NUM}({U})", lambda m: (int(m[1]), _dec(m[2]), m[3]))),
        tenor=_one(_hits(ti, r"天期：(\d+)個月", lambda m: (int(m[1]),))),
        rate=_one(_hits(ti, r"配息率=(\d+(?:\.\d+)?)%", lambda m: (Decimal(m[1]),))),
        table=table,
        scenarios=scenarios,
        assumptions=assumptions,
    )
