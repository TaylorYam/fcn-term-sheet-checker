"""SG 第一章 §15(3) 情境分析：假設、重印標的表與各情境的算式（docs/templates/sg-zh-product-description.md §4.7）。

只擷取文件上寫的值與原文行；核對在 rules/sg_scenario.py。抓不到的值為 None，由規則轉人工覆核。
兩種版面在去空白後的寫法相同（舊版面只是數字與單位間多了空白），所有句型都在去空白的全文上比對。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from ..schema import Line, ParsedField
from ..standard_fields import Money
from ..text import squash
from . import money, sg_tables
from .common import NUM, SCENARIO_HEAD, Amount, Hit, dec, hits, one
from .layout import TextIndex

PCT = r"(-?\d+(?:\.\d+)?)%"
U = money.CURRENCY  # 金額後的幣別字（任一已知寫法；寫哪一個才對由共用規則核對）
KINDS = (("較佳", "best"), ("中間", "middle"), ("較差", "worse"), ("最差", "worst"))
# 不算商品幣別出處的行：股價（共用）、實體交付算式（數額、現金交割、持有單位）、現金結算總報酬率算式、匯率
SKIP_WORDS = ("數額", "現金交割", "加上現金", "{[", "最終匯率")


class Assumed(NamedTuple):
    """情境一假設句：D 型「於…觀察期間起始日」（period 為 None）／P 型「於第 k 個評價日」。"""

    observation: str  # "D"／"P"
    period: int | None


class CouponCalc(NamedTuple):
    """「第a~b 個配息金額 = U 單位面額 × 面額 × X% = 總投資金額 × X% = 配息」。"""

    first: int
    last: int | None  # 只寫一期時為 None
    units: int
    denomination: Decimal
    currency: str
    rate: Decimal
    total: Decimal
    total_currency: str
    rate_again: Decimal  # 第二個 × X%
    amount: Decimal
    amount_currency: str


class Redemption(NamedTuple):
    """「自動提前出場贖回金額／到期贖回金額 = U 單位面額 × 面額 × 100% = 總投資金額 × 100% = 金額」。"""

    label: str
    units: int
    denomination: Decimal
    currency: str
    total: Decimal
    total_currency: str
    amount: Decimal
    amount_currency: str


class TotalReturn(NamedTuple):
    """獲利情境「=(合計/總投資金額)-1 = r%(平均年化報酬率：y%)」。"""

    total_sum: Decimal
    sum_currency: str
    total: Decimal
    total_currency: str
    pct: Decimal
    annualized: Decimal


class CloseAssumption(NamedTuple):
    """較差情境假設句「連結標的(k=n)收盤價 = C 小於下限價／執行價」。"""

    k: int | None  # 沒寫 (k=n) 時為 None（單一標的）
    close: Decimal
    currency: str
    barrier: str  # 下限價／執行價


class Quantity(NamedTuple):
    """「數額計算 = 每單位面額 / … 執行價 = 面額 / (執行價（× 匯率）) = q」。"""

    denomination: Decimal
    currency: str
    strike: Decimal
    strike_currency: str
    fx: Decimal | None  # quanto 的匯率
    quantity: Decimal


class CashSettlement(NamedTuple):
    """「加上現金交割 = 收盤價 × 畸零數（× 匯率…）= 金額」。"""

    close: Decimal
    currency: str
    fraction: Decimal
    fx: Decimal | None
    amount: Decimal
    amount_currency: str


class Holdings(NamedTuple):
    """「總投資金額 = U 單位面額，因此，到期日將獲得…連結標的 N 單位加上現金 C」。"""

    units: int
    total_units: int
    total_cash: Decimal
    currency: str


class WorseReturn(NamedTuple):
    """現金結算「總報酬率 = {[總投資 × (收盤價 / 執行價) + 累積] / 總投資} − 1 = r%(平均年化報酬率：y%)」。"""

    total: Decimal
    total_currency: str
    close: Decimal
    close_currency: str
    strike: Decimal
    strike_currency: str
    cumulative: Decimal
    cumulative_currency: str
    divisor: Decimal
    divisor_currency: str
    pct: Decimal
    annualized: Decimal


class Total(NamedTuple):
    """「總投資金額 = U 單位面額 = N 幣別」。"""

    units: int
    amount: Decimal
    currency: str


@dataclass
class Scenario:
    number: str  # 情境編號（一、二…）
    title: str  # 例：較佳情況、中間情況
    kind: str  # best／middle／worse／worst／unknown
    lines: list[Line]
    assumed: Hit[Assumed] | None = None  # 情境一假設句
    coupon: Hit[CouponCalc] | None = None  # 每期配息算式
    coupon_count: int = 0  # 「個配息金額」出現次數（算式應剛好一個）
    cumulative: Hit[Amount] | None = None  # 累積總配息金額
    redemption: Hit[Redemption] | None = None
    total_return: Hit[TotalReturn] | None = None  # 獲利情境
    close: Hit[CloseAssumption] | None = None  # 較差情境假設句
    fx: Hit[Decimal] | None = None  # quanto：最終匯率定價
    shares: Hit[Quantity] | None = None  # 數額
    whole: Hit[int] | None = None  # 應實際交付數（整數單位）
    cash: Hit[CashSettlement] | None = None
    holdings: Hit[Holdings] | None = None  # 總投資金額…將獲得
    worse_return: Hit[WorseReturn] | None = None  # 現金結算總報酬率
    money: tuple[Money, ...] = ()  # 各金額旁的幣別字（商品幣別的出處）


@dataclass
class ScenarioSection:
    lines: list[Line]
    total: Hit[Total] | None  # 總投資金額 = U單位面額 = N 幣別
    tenor: Hit[int] | None  # 天期：N個月
    rate: Hit[Decimal] | None  # 配息率 = X %
    table: ParsedField  # 重印價格表（sg_tables.price_table，含表頭百分比）
    scenarios: list[Scenario]
    assumptions: list[Line] = field(default_factory=list)


def _skip(text: str) -> bool:
    return money.stock_price_line(text) or any(w in text for w in SKIP_WORDS)


def _kind(title: str) -> str:
    return next((kind for word, kind in KINDS if word in title), "unknown")


def _scenario(number: str, title: str, lines: list[Line]) -> Scenario:
    s = Scenario(number, title, _kind(title), lines)
    ti = TextIndex(lines)
    s.money = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, skip=_skip)
    s.assumed = one(
        hits(
            ti,
            r"假設於(?:(記憶事件|自動提前出場事件)觀察期間起始日|第(\d+)個評價日)，發生自動提前出場事件",
            lambda m: Assumed("D", None) if m[1] else Assumed("P", int(m[2])),
        )
    )
    s.coupon_count = len(list(ti.finditer(r"個配息金額")))
    s.coupon = one(
        hits(
            ti,
            rf"第(\d+)(?:~(\d+))?個配息金額=(\d+)單位面額[xX×]{NUM}({U})[xX×]{PCT}={NUM}({U})[xX×]{PCT}={NUM}({U})",
            lambda m: CouponCalc(
                int(m[1]),
                int(m[2]) if m[2] else None,
                int(m[3]),
                dec(m[4]),
                m[5],
                Decimal(m[6]),
                dec(m[7]),
                m[8],
                Decimal(m[9]),
                dec(m[10]),
                m[11],
            ),
        )
    )
    s.cumulative = one(hits(ti, rf"累積總配息金額={NUM}({U})", lambda m: Amount(dec(m[1]), m[2])))
    s.redemption = one(
        hits(
            ti,
            rf"(自動提前出場贖回金額|到期贖回金額)=(\d+)單位面額[xX×]{NUM}({U})[xX×]100%={NUM}({U})[xX×]100%={NUM}({U})",
            lambda m: Redemption(m[1], int(m[2]), dec(m[3]), m[4], dec(m[5]), m[6], dec(m[7]), m[8]),
        )
    )
    s.total_return = one(
        hits(
            ti,
            rf"=[(（]{NUM}({U})/{NUM}({U})[)）]-1={PCT}[(（]平均年化報酬率：{PCT}[)）]",
            lambda m: TotalReturn(dec(m[1]), m[2], dec(m[3]), m[4], Decimal(m[5]), Decimal(m[6])),
        )
    )
    s.close = one(
        hits(
            ti,
            rf"連結標的(?:\(k=(\d+)\))?收盤價={NUM}({U})小於(下限價|執行價)",
            lambda m: CloseAssumption(int(m[1]) if m[1] else None, dec(m[2]), m[3], m[4]),
        )
    )
    s.fx = one(hits(ti, r"最終匯率定價=(\d+(?:\.\d+)?)", lambda m: Decimal(m[1])))
    s.shares = one(
        hits(
            ti,
            rf"數額計算=每單位面額/.*?執行價={NUM}({U})/\(?{NUM}({U})(?:[xX×](\d+(?:\.\d+)?)\))?=([\d,]+\.\d{{4}})",
            lambda m: Quantity(dec(m[1]), m[2], dec(m[3]), m[4], Decimal(m[5]) if m[5] else None, dec(m[6])),
        )
    )
    s.whole = one(
        hits(ti, r"應實際交付數=數額向下調整至最接近的整數位(?:或交易單位)?=([\d,]+)單位", lambda m: int(dec(m[1])))
    )
    s.cash = one(
        hits(
            ti,
            rf"加上現金交割={NUM}({U})[xX×](\d+\.\d{{4}})(?:[xX×](\d+(?:\.\d+)?)=[\d,]+(?:\.\d+)?{U}[xX×]\d+(?:\.\d+)?)?={NUM}({U})",
            lambda m: CashSettlement(dec(m[1]), m[2], Decimal(m[3]), Decimal(m[4]) if m[4] else None, dec(m[5]), m[6]),
        )
    )
    s.holdings = one(
        hits(
            ti,
            rf"總投資金額=(\d+)單位面額，因此，到期日將獲得(?:最終評價日)?(?:最差表現之)?連結標的([\d,]+)單位加上現金{NUM}({U})",
            lambda m: Holdings(int(m[1]), int(dec(m[2])), dec(m[3]), m[4]),
        )
    )
    s.worse_return = one(
        hits(
            ti,
            rf"總報酬率=\{{\[{NUM}({U})[xX×]\({NUM}({U})/{NUM}({U})\)\+{NUM}({U})\]/{NUM}({U})\}}-1={PCT}[(（]平均年化報酬率：{PCT}[)）]",
            lambda m: WorseReturn(
                dec(m[1]),
                m[2],
                dec(m[3]),
                m[4],
                dec(m[5]),
                m[6],
                dec(m[7]),
                m[8],
                dec(m[9]),
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
        table = sg_tables.price_table("scenario_table", lines[rate_at + 1 : head_end], sg_tables.REPRINT_TABLE, count)
    else:
        table = ParsedField.missing("scenario_table", "找不到第 15 項重印標的表（「配息率 = X %」到情境一之前）")
    scenarios = []
    for k, i in enumerate(heads):
        m = SCENARIO_HEAD.match(squash(lines[i].text))
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        scenarios.append(_scenario(m[1], m[2], lines[i:end]))
    return ScenarioSection(
        lines=lines,
        total=one(hits(ti, rf"總投資金額=(\d+)單位面額={NUM}({U})", lambda m: Total(int(m[1]), dec(m[2]), m[3]))),
        tenor=one(hits(ti, r"天期：(\d+)個月", lambda m: int(m[1]))),
        rate=one(hits(ti, r"配息率=(\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))),
        table=table,
        scenarios=scenarios,
        assumptions=assumptions,
    )
