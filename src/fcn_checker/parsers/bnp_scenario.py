"""BNP 第一章第 18 項(3) 情境分析：假設、重印標的表與各情境的算式（docs/templates/bnp-zh-product-description.md §4.7、§5.2）。

只擷取文件上寫的值與原文行；核對在 rules/bnp_scenario.py。抓不到的值為 None，由規則轉人工覆核。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..schema import Line, ParsedField
from ..standard_fields import Money
from ..text import squash
from . import bnp_tables, money
from .layout import TextIndex

NUM = r"([\d,]+(?:\.\d+)?)"
PCT = r"(-?\d+(?:\.\d+)?)%"
U = money.CURRENCY  # 金額後的幣別字（任一已知寫法；寫哪一個才對由共用規則核對）
ROUNDING = r"\((四捨五入至小數點以下第二位|四捨五入至整數位)\)"
SCENARIO_HEAD = re.compile(r"^情境([一二三四五六七八九十]+)[:：](.*)$")
KINDS = (("自動提前贖回", "autocall"), ("最佳", "best"), ("較差", "worse"), ("最差", "worst"), ("極端", "extreme"))
TABLE_TITLE = "連結標的相關價格如下："
DECIMALS = {"四捨五入至小數點以下第二位": 2, "四捨五入至整數位": 0}
REMAINDER_LABEL = "剩餘金額"  # 剩餘金額算式裡的收盤價跟著標的走，不是商品幣別的出處


@dataclass(frozen=True)
class Hit:
    """文件上的一組值與原文行。"""

    values: tuple
    lines: tuple[Line, ...]


@dataclass
class Scenario:
    number: str  # 情境編號（一、二…）
    title: str  # 例：自動提前贖回情況、最佳情況(A)、較差情況
    kind: str  # autocall／best／worse／worst／extreme／unknown
    lines: list[Line]
    assumed: Hit | None = None  # 情境一假設的期別：(k, N 或 None)（「第1(j=6)個」的 j=N）
    coupon: Hit | None = None  # 每期配息算式：(a, b 或 None, 面額, 幣別, 月配息率, 配息, 幣別, 四捨五入位數)
    coupon_count: int = 0  # 「個月配息金額」出現次數（算式應剛好一個）
    cumulative: Hit | None = None  # 累積總月配息金額：(金額, 幣別)
    redemption: Hit | None = None  # 贖回金額算式：(標籤, 面額, 幣別, 金額, 幣別)
    total_return: Hit | None = None  # 獲利情境總報酬率：(面額, 累積, 面額, 總報酬率, 年化報酬率)
    strike: Hit | None = None  # 較差情境假設句：(收盤價, 幣別, 執行價, 幣別)
    shares: Hit | None = None  # 股數：(面額, 幣別, 匯率 或 None, 執行價, 幣別, 股數, 整數股數)
    remainder: Hit | None = None  # 剩餘金額：(收盤價, 幣別, 匯率 或 None, 畸零股數, 金額, 幣別, 四捨五入位數)
    worse_return: Hit | None = (
        None  # 較差情境總報酬率：(整數股數, 收盤價, 幣別, 是否÷匯率, 現金, 幣別, 累積, 幣別, 面額, 幣別, 總報酬率)
    )
    money: tuple[Money, ...] = ()  # 各金額旁的幣別字（商品幣別的出處）


@dataclass
class ScenarioSection:
    lines: list[Line]
    denomination: Hit | None  # 商品面額 = 每單位商品為N 幣別：(N, 幣別)
    issue_price: Hit | None  # 發行價格：X%：(X,)
    principal: Hit | None  # 投資本金 = 1 單位商品 × 商品面額 × X% = N 幣別：(X, N, 幣別)
    monthly: list[Hit]  # 每月配息率：X%
    fx: Hit | None  # 匯率(每一A兌B的市場中間匯率) = r：(A, B, r)
    table: ParsedField  # 重印標的表
    scenarios: list[Scenario]
    assumptions: list[Line] = field(default_factory=list)


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _hits(ti: TextIndex, pattern: str, convert) -> list[Hit]:
    return [Hit(tuple(convert(m)), tuple(ti.lines_for(m.start(), m.end()))) for m in ti.finditer(pattern)]


def _one(hits: list[Hit]) -> Hit | None:
    return hits[0] if len(hits) == 1 else None


def _skip(text: str) -> bool:
    """跟著標的走的行：股價與實物交割算式（共用），加上剩餘金額算式（收盤價 × 畸零股數，收盤價為標的幣別）。"""
    return money.stock_price_line(text) or REMAINDER_LABEL in text


def _kind(title: str) -> str:
    return next((kind for word, kind in KINDS if word in title), "unknown")


def _scenario(number: str, title: str, lines: list[Line]) -> Scenario:
    s = Scenario(number, title, _kind(title), lines)
    ti = TextIndex(lines, separate_digits=True)
    s.money = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, skip=_skip)
    s.assumed = _one(
        _hits(
            ti, r"假設於第(\d+)(?:\(j=(\d+)\))?個自動提前贖回評價日", lambda m: (int(m[1]), int(m[2]) if m[2] else None)
        )
    )
    s.coupon_count = len(list(ti.finditer(r"個月配息金額")))
    s.coupon = _one(
        _hits(
            ti,
            rf"第(\d+)(?:~(\d+))?個月配息金額=1單位商品×{NUM}({U})×{PCT}={NUM}({U}){ROUNDING}",
            lambda m: (
                int(m[1]),
                int(m[2]) if m[2] else None,
                _dec(m[3]),
                m[4],
                Decimal(m[5]),
                _dec(m[6]),
                m[7],
                DECIMALS[m[8]],
            ),
        )
    )
    s.cumulative = _one(_hits(ti, rf"累積總月配息金額={NUM}({U})", lambda m: (_dec(m[1]), m[2])))
    s.redemption = _one(
        _hits(
            ti,
            rf"(自動提前贖回金額|到期贖回金額)=1單位商品×{NUM}({U})×100%={NUM}({U})",
            lambda m: (m[1], _dec(m[2]), m[3], _dec(m[4]), m[5]),
        )
    )
    s.total_return = _one(
        _hits(
            ti,
            rf"=\[\({NUM}({U})\+{NUM}({U})\)÷{NUM}({U})\]-1={PCT}\(年化報酬率：{PCT}\)",
            lambda m: (_dec(m[1]), _dec(m[3]), _dec(m[5]), Decimal(m[7]), Decimal(m[8])),
        )
    )
    s.strike = _one(
        _hits(
            ti,
            rf"於期末評價日之收盤價格{NUM}({U})小於其執行價格([\d,]+\.\d{{4}})({U})",
            lambda m: (_dec(m[1]), m[2], _dec(m[3]), m[4]),
        )
    )
    s.shares = _one(
        _hits(
            ti,
            r"股數計算=(?:每單位面額|\(每單位面額×匯率\))÷(?:表現最差)?連結標的之?執行價格="
            rf"(?:{NUM}({U})|\({NUM}({U})×(\d+(?:\.\d+)?)\))÷([\d,]+\.\d{{4}})({U})=(\d+(?:\.\d+)?)"
            r"\(四捨五入至小數點以下第四位\)無條件捨去至整數位=(\d+)股",
            lambda m: (
                _dec(m[1] or m[3]),
                m[2] or m[4],
                Decimal(m[5]) if m[5] else None,
                _dec(m[6]),
                m[7],
                Decimal(m[8]),
                int(m[9]),
            ),
        )
    )
    s.remainder = _one(
        _hits(
            ti,
            rf"剩餘金額=(?:{NUM}({U})|\({NUM}({U})÷(\d+(?:\.\d+)?)\))×(\d+(?:\.\d+)?)={NUM}({U}){ROUNDING}",
            lambda m: (
                _dec(m[1] or m[3]),
                m[2] or m[4],
                Decimal(m[5]) if m[5] else None,
                Decimal(m[6]),
                _dec(m[7]),
                m[8],
                DECIMALS[m[9]],
            ),
        )
    )
    s.worse_return = _one(
        _hits(
            ti,
            rf"總報酬率=\[\((\d+)股×.+?收盤價格{NUM}({U})(÷匯率)?\+現金交割{NUM}({U})\+累積總月配息金額{NUM}({U})\)"
            rf"÷總投資金額{NUM}({U})\]-1={PCT}",
            lambda m: (
                int(m[1]),
                _dec(m[2]),
                m[3],
                bool(m[4]),
                _dec(m[5]),
                m[6],
                _dec(m[7]),
                m[8],
                _dec(m[9]),
                m[10],
                Decimal(m[11]),
            ),
        )
    )
    return s


def section(lines: list[Line]) -> ScenarioSection:
    """第 18 項(3)：假設（到重印表之前）、重印標的表（「連結標的相關價格如下：」到情境一之前）、各情境（依「情境X：」切開）。"""
    heads = [i for i, ln in enumerate(lines) if SCENARIO_HEAD.match(squash(ln.text))]
    head_end = heads[0] if heads else len(lines)
    titles = [i for i, ln in enumerate(lines[:head_end]) if squash(ln.text) == TABLE_TITLE]
    assumptions = lines[: titles[0]] if titles else lines[:head_end]
    ti = TextIndex(assumptions)
    if len(titles) == 1 and heads:
        table = bnp_tables.price_table("scenario_table", lines[titles[0] + 1 : head_end], with_tickers=False)
    else:
        table = ParsedField.missing(
            "scenario_table", "找不到第 18 項重印標的表（「連結標的相關價格如下：」到情境一之前）"
        )
    scenarios = []
    for k, i in enumerate(heads):
        m = SCENARIO_HEAD.match(squash(lines[i].text))
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        scenarios.append(_scenario(m[1], m[2], lines[i:end]))
    return ScenarioSection(
        lines=lines,
        denomination=_one(_hits(ti, rf"商品面額=每單位商品為{NUM}({U})", lambda m: (_dec(m[1]), m[2]))),
        issue_price=_one(_hits(ti, r"發行價格：(\d+(?:\.\d+)?)%", lambda m: (Decimal(m[1]),))),
        principal=_one(
            _hits(
                ti,
                rf"投資本金=1單位商品×商品面額×(\d+(?:\.\d+)?)%={NUM}({U})",
                lambda m: (Decimal(m[1]), _dec(m[2]), m[3]),
            )
        ),
        monthly=_hits(ti, r"每月配息率：(\d+(?:\.\d+)?)%", lambda m: (Decimal(m[1]),)),
        fx=_one(
            _hits(ti, rf"匯率\(每一({U})兌({U})的市場中間匯率\)=(\d+(?:\.\d+)?)", lambda m: (m[1], m[2], Decimal(m[3])))
        ),
        table=table,
        scenarios=scenarios,
        assumptions=assumptions,
    )
