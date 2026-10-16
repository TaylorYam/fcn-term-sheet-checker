"""MS 第一章第 18 項情境分析：假設、重印價格表與各情境的算式（docs/templates/ms-zh-product-description.md §6、§7）。

只擷取文件上寫的值與原文行；核對在 rules/ms_scenario.py。抓不到的值為 None，由規則轉人工覆核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from ..schema import Line, ParsedField
from ..standard_fields import Money
from ..text import squash
from . import money, ms_tables
from .common import SCENARIO_HEAD, Hit, dec, hits, one
from .layout import TextIndex

N = r"([\d,]+(?:\.\d{2})?)"  # 金額：2 位小數，或整數（日圓樣本寫到整數位，Issue #181）
PNL = r"(-?[\d,]+(?:\.\d{2})?)"  # 損益（可為負）
TIMES = "[×xX]"  # 乘號：全形 ×，也有樣本寫半形 x（Issue #181）
UNIT = money.CURRENCY  # 金額後的幣別字（任一已知寫法；寫哪一個才對由共用規則核對，Issue #170）


class CouponCalc(NamedTuple):
    """每期配息算式「面額 × 月配息率 = 每期配息」。"""

    denomination: Decimal
    rate: Decimal
    amount: Decimal


class ProfitPnl(NamedTuple):
    """獲利情境損益算式「= 面額 + 每期配息 × k − 面額 = 損益」。"""

    denomination: Decimal
    coupon: Decimal
    periods: int
    principal: Decimal
    pnl: Decimal


class WorsePnl(NamedTuple):
    """較差情境損益算式「= 每期配息 × k + 到期贖回價值 − 面額 = 損益」。"""

    coupon: Decimal
    periods: int
    redemption: Decimal
    denomination: Decimal
    pnl: Decimal


class WorseStrike(NamedTuple):
    """較差情境「連結標的(代碼)的收盤價 = … 小於其執行價 = N」。"""

    ticker: str  # 彭博代碼去空白
    strike: Decimal


@dataclass
class Scenario:
    number: str  # 情境編號（一、二…）
    title: str  # 例：較佳情況 A、一般情況、較差情況
    kind: str  # profit 獲利情境／worse 較差情境／default 發行機構無法履約（不核對）
    lines: list[Line]
    assumed: Hit[int | None] | None = None  # 假設的配息次數 k；「所有配息觀察期間」為 None
    coupons: list[Hit[CouponCalc]] = field(default_factory=list)
    coupon_formulas: int = 0  # 文件上「N 美元×X%」的處數（讀得出的每期配息算式應與它一樣多）
    rates: list[Hit[Decimal]] = field(default_factory=list)  # 算式中的每個 ×X%
    pnl: list[Hit[ProfitPnl | WorsePnl]] = field(default_factory=list)  # 損益算式（依情境種類）
    annualized: list[Hit[Decimal]] = field(default_factory=list)  # 年化報酬率 Y%
    strike: list[Hit[WorseStrike]] = field(default_factory=list)
    # 各金額旁的幣別字（商品幣別的出處）；股價與實物交割算式所在的行跟著標的走，不列入（money.stock_price_line）
    money: tuple[Money, ...] = ()


@dataclass
class ScenarioSection:
    lines: list[Line]
    denomination: Hit[Decimal] | None  # 總投資金額 = 1 單位商品面額 = N
    denomination_currency: Hit[str] | None  # 總投資金額旁的幣別字
    tenor: Hit[int] | None  # 年期：N 個月
    monthly: list[Hit[Decimal]]  # 月配息率=X%
    table: ParsedField  # 重印價格表
    scenarios: list[Scenario]


def _scenario(number: str, title: str, lines: list[Line]) -> Scenario:
    unit = UNIT
    kind = "default" if "無法履約" in title else "worse" if "較差" in title else "profit"
    s = Scenario(number, title, kind, lines)
    ti = TextIndex(lines, separate_digits=True)
    s.money = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2)
    assumed = [
        *hits(ti, r"假設在第(\d+)個(?:配息週期終止日|定價日)", lambda m: int(m[1])),
        *hits(ti, r"假設在第1至第(\d+)個(?:配息觀察期間|定價日)", lambda m: int(m[1])),
        *hits(ti, r"假設在所有配息觀察期間內", lambda m: None),
    ]
    s.assumed = one(assumed)
    s.coupons = hits(
        ti,
        rf"{N}{unit}{TIMES}(\d+(?:\.\d+)?)%(?:{TIMES}100%)?={N}{unit}",
        lambda m: CouponCalc(dec(m[1]), Decimal(m[2]), dec(m[3])),
    )
    s.coupon_formulas = len(list(ti.finditer(rf"\d{unit}{TIMES}\d+(?:\.\d+)?%")))
    s.rates = hits(ti, rf"(?<!%){TIMES}(\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))
    if kind == "profit":
        s.pnl = hits(
            ti,
            rf"={N}{unit}\+{N}{unit}(?:{TIMES}(\d+))?-{N}{unit}={PNL}{unit}",
            lambda m: ProfitPnl(dec(m[1]), dec(m[2]), int(m[3] or 1), dec(m[4]), dec(m[5])),
        )
    elif kind == "worse":
        s.pnl = hits(
            ti,
            rf"={N}{unit}{TIMES}(\d+)\+([\d,]+(?:\.\d+)?){unit}-{N}{unit}={PNL}{unit}",
            lambda m: WorsePnl(dec(m[1]), int(m[2]), dec(m[3]), dec(m[4]), dec(m[5])),
        )
        s.strike = hits(
            ti,
            rf"連結標的[（(]([A-Z0-9./-]+)[）)]的收盤價=[\d,]+(?:\.\d+)?{unit}小於其執行價=([\d,]+\.\d{{4}}){unit}",
            lambda m: WorseStrike(m[1], dec(m[2])),
        )
    s.annualized = hits(ti, r"年化報酬率為(-?\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))
    return s


def section(lines: list[Line]) -> ScenarioSection:
    """第 18 項：假設（到第一個情境前）、重印價格表（月配息率之後到情境一）、各情境（依「情境X：」切開）。"""
    unit = UNIT
    heads = [i for i, ln in enumerate(lines) if SCENARIO_HEAD.match(squash(ln.text))]
    head_end = heads[0] if heads else len(lines)
    ti = TextIndex(lines[:head_end])
    monthly = hits(ti, r"月配息率=(\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))
    if len(monthly) == 1 and heads:
        last = monthly[0].lines[-1]
        region = lines[lines.index(last) + 1 : head_end]
        table = ms_tables.price_table("scenario_table", region, ms_tables.SCENARIO_TABLE)
    else:
        table = ParsedField.missing("scenario_table", "找不到第 18 項重印價格表（月配息率之後、情境一之前）")
    scenarios = []
    for k, i in enumerate(heads):
        m = SCENARIO_HEAD.match(squash(lines[i].text))
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        scenarios.append(_scenario(m[1], m[2], lines[i:end]))
    notional = rf"總投資金額=1單位商品面額=([\d,]+(?:\.\d+)?)({unit})"
    return ScenarioSection(
        lines=lines,
        denomination=one(hits(ti, notional, lambda m: dec(m[1]))),
        denomination_currency=one(hits(ti, notional, lambda m: m[2])),
        tenor=one(hits(ti, r"年期[:：](\d+)個月", lambda m: int(m[1]))),
        monthly=monthly,
        table=table,
        scenarios=scenarios,
    )
