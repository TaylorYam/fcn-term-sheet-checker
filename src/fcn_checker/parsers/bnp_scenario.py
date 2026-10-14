"""BNP 第一章第 18 項(3) 情境分析：假設、重印標的表與各情境的算式（docs/templates/bnp-zh-product-description.md §4.7、§5.2）。

只擷取文件上寫的值與原文行；核對在 rules/bnp_scenario.py。抓不到的值為 None，由規則轉人工覆核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from ..schema import Line, ParsedField
from ..standard_fields import Money
from ..text import squash
from . import bnp_tables, money
from .common import NUM, SCENARIO_HEAD, Amount, Hit, dec, hits, one
from .layout import TextIndex

PCT = r"(-?\d+(?:\.\d+)?)%"
U = money.CURRENCY  # 金額後的幣別字（任一已知寫法；寫哪一個才對由共用規則核對）
ROUNDING = r"\((四捨五入至小數點以下第二位|四捨五入至整數位)\)"
KINDS = (("自動提前贖回", "autocall"), ("最佳", "best"), ("較差", "worse"), ("最差", "worst"), ("極端", "extreme"))
TABLE_TITLE = "連結標的相關價格如下："
DECIMALS = {"四捨五入至小數點以下第二位": 2, "四捨五入至整數位": 0}  # 四捨五入寫法 → 小數位數（第 15 項同）
REMAINDER_LABEL = "剩餘金額"  # 剩餘金額算式裡的收盤價跟著標的走，不是商品幣別的出處


class Assumed(NamedTuple):
    """情境一「假設於第k(j=N)個自動提前贖回評價日」。"""

    number: int
    j: int | None  # 「(j=N)」的 N；沒寫為 None


class CouponCalc(NamedTuple):
    """「第a~b 個月配息金額 = 1 單位商品 × 面額 × 月配息率 = 配息(四捨五入…)」。"""

    first: int
    last: int | None  # 只寫一期時為 None
    denomination: Decimal
    currency: str
    rate: Decimal
    amount: Decimal
    amount_currency: str
    decimals: int


class Redemption(NamedTuple):
    """「自動提前贖回金額／到期贖回金額 = 1 單位商品 × 面額 × 100% = 金額」。"""

    label: str
    denomination: Decimal
    currency: str
    amount: Decimal
    amount_currency: str


class TotalReturn(NamedTuple):
    """獲利情境「= [(面額 + 累積) ÷ 面額] − 1 = r%(年化報酬率：y%)」。"""

    denomination: Decimal
    cumulative: Decimal
    divisor: Decimal
    pct: Decimal
    annualized: Decimal


class StrikeAssumption(NamedTuple):
    """較差情境假設句「於期末評價日之收盤價格 C 小於其執行價格 K」。"""

    close: Decimal
    close_currency: str
    strike: Decimal
    strike_currency: str


class Shares(NamedTuple):
    """「股數計算 = 面額（× 匯率）÷ 執行價格 = s(四捨五入至第四位)無條件捨去至整數位 = S 股」。"""

    denomination: Decimal
    currency: str
    fx: Decimal | None  # 標的幣別 ≠ 商品幣別時的匯率
    strike: Decimal
    strike_currency: str
    shares: Decimal
    whole: int


class Remainder(NamedTuple):
    """「剩餘金額 = 收盤價（÷ 匯率）× 畸零股數 = R(四捨五入…)」。"""

    close: Decimal
    currency: str
    fx: Decimal | None
    fraction: Decimal
    amount: Decimal
    amount_currency: str
    decimals: int


class WorseReturn(NamedTuple):
    """較差情境「總報酬率 = [(S 股 × 收盤價（÷ 匯率）+ 現金交割 + 累積) ÷ 總投資金額] − 1 = r%」。"""

    whole: int
    close: Decimal
    close_currency: str
    divided_by_fx: bool
    cash: Decimal
    cash_currency: str
    cumulative: Decimal
    cumulative_currency: str
    total: Decimal
    total_currency: str
    pct: Decimal


class Principal(NamedTuple):
    """「投資本金 = 1 單位商品 × 商品面額 × X% = N」。"""

    pct: Decimal
    amount: Decimal
    currency: str


class Fx(NamedTuple):
    """「匯率(每一A兌B的市場中間匯率) = r」。"""

    base: str
    quote: str
    rate: Decimal


@dataclass
class Scenario:
    number: str  # 情境編號（一、二…）
    title: str  # 例：自動提前贖回情況、最佳情況(A)、較差情況
    kind: str  # autocall／best／worse／worst／extreme／unknown
    lines: list[Line]
    assumed: Hit[Assumed] | None = None  # 情境一假設的期別
    coupon: Hit[CouponCalc] | None = None  # 每期配息算式
    coupon_count: int = 0  # 「個月配息金額」出現次數（算式應剛好一個）
    cumulative: Hit[Amount] | None = None  # 累積總月配息金額
    redemption: Hit[Redemption] | None = None
    total_return: Hit[TotalReturn] | None = None  # 獲利情境
    strike: Hit[StrikeAssumption] | None = None  # 較差情境假設句
    worst_name: Hit[str] | None = None  # 較差情境假設句「(假設為X)」的表現最差標的名稱（單一標的時沒有）
    shares: Hit[Shares] | None = None
    remainder: Hit[Remainder] | None = None
    worse_return: Hit[WorseReturn] | None = None
    money: tuple[Money, ...] = ()  # 各金額旁的幣別字（商品幣別的出處）


@dataclass
class ScenarioSection:
    lines: list[Line]
    denomination: Hit[Amount] | None  # 商品面額 = 每單位商品為N 幣別
    issue_price: Hit[Decimal] | None  # 發行價格：X%
    principal: Hit[Principal] | None
    monthly: list[Hit[Decimal]]  # 每月配息率：X%
    fx: Hit[Fx] | None
    table: ParsedField  # 重印標的表
    scenarios: list[Scenario]
    assumptions: list[Line] = field(default_factory=list)


def _skip(text: str) -> bool:
    """跟著標的走的行：股價與實物交割算式（共用），加上剩餘金額算式（收盤價 × 畸零股數，收盤價為標的幣別）。"""
    return money.stock_price_line(text) or REMAINDER_LABEL in text


def _kind(title: str) -> str:
    return next((kind for word, kind in KINDS if word in title), "unknown")


def _fx(s: str | None) -> Decimal | None:
    return Decimal(s) if s else None


def _scenario(number: str, title: str, lines: list[Line]) -> Scenario:
    s = Scenario(number, title, _kind(title), lines)
    ti = TextIndex(lines, separate_digits=True)
    s.money = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2, skip=_skip)
    s.assumed = one(
        hits(
            ti,
            r"假設於第(\d+)(?:\(j=(\d+)\))?個自動提前贖回評價日",
            lambda m: Assumed(int(m[1]), int(m[2]) if m[2] else None),
        )
    )
    s.coupon_count = len(list(ti.finditer(r"個月配息金額")))
    s.coupon = one(
        hits(
            ti,
            rf"第(\d+)(?:~(\d+))?個月配息金額=1單位商品×{NUM}({U})×{PCT}={NUM}({U}){ROUNDING}",
            lambda m: CouponCalc(
                int(m[1]),
                int(m[2]) if m[2] else None,
                dec(m[3]),
                m[4],
                Decimal(m[5]),
                dec(m[6]),
                m[7],
                DECIMALS[m[8]],
            ),
        )
    )
    s.cumulative = one(hits(ti, rf"累積總月配息金額={NUM}({U})", lambda m: Amount(dec(m[1]), m[2])))
    s.redemption = one(
        hits(
            ti,
            rf"(自動提前贖回金額|到期贖回金額)=1單位商品×{NUM}({U})×100%={NUM}({U})",
            lambda m: Redemption(m[1], dec(m[2]), m[3], dec(m[4]), m[5]),
        )
    )
    s.total_return = one(
        hits(
            ti,
            rf"=\[\({NUM}({U})\+{NUM}({U})\)÷{NUM}({U})\]-1={PCT}\(年化報酬率：{PCT}\)",
            lambda m: TotalReturn(dec(m[1]), dec(m[3]), dec(m[5]), Decimal(m[7]), Decimal(m[8])),
        )
    )
    s.strike = one(
        hits(
            ti,
            rf"於期末評價日之收盤價格{NUM}({U})小於其執行價格([\d,]+\.\d{{4}})({U})",
            lambda m: StrikeAssumption(dec(m[1]), m[2], dec(m[3]), m[4]),
        )
    )
    s.worst_name = one(hits(ti, r"[(（]假設為(.+?)[)）]於期末評價日之收盤價格", lambda m: m[1]))
    s.shares = one(
        hits(
            ti,
            r"股數計算=(?:每單位面額|\(每單位面額×匯率\))÷(?:表現最差)?連結標的之?執行價格="
            rf"(?:{NUM}({U})|\({NUM}({U})×(\d+(?:\.\d+)?)\))÷([\d,]+\.\d{{4}})({U})=(\d+(?:\.\d+)?)"
            r"\(四捨五入至小數點以下第四位\)無條件捨去至整數位=(\d+)股",
            lambda m: Shares(dec(m[1] or m[3]), m[2] or m[4], _fx(m[5]), dec(m[6]), m[7], Decimal(m[8]), int(m[9])),
        )
    )
    s.remainder = one(
        hits(
            ti,
            rf"剩餘金額=(?:{NUM}({U})|\({NUM}({U})÷(\d+(?:\.\d+)?)\))×(\d+(?:\.\d+)?)={NUM}({U}){ROUNDING}",
            lambda m: Remainder(
                dec(m[1] or m[3]), m[2] or m[4], _fx(m[5]), Decimal(m[6]), dec(m[7]), m[8], DECIMALS[m[9]]
            ),
        )
    )
    s.worse_return = one(
        hits(
            ti,
            rf"總報酬率=\[\((\d+)股×.+?收盤價格{NUM}({U})(÷匯率)?\+現金交割{NUM}({U})\+累積總月配息金額{NUM}({U})\)"
            rf"÷總投資金額{NUM}({U})\]-1={PCT}",
            lambda m: WorseReturn(
                int(m[1]),
                dec(m[2]),
                m[3],
                bool(m[4]),
                dec(m[5]),
                m[6],
                dec(m[7]),
                m[8],
                dec(m[9]),
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
        denomination=one(hits(ti, rf"商品面額=每單位商品為{NUM}({U})", lambda m: Amount(dec(m[1]), m[2]))),
        issue_price=one(hits(ti, r"發行價格：(\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))),
        principal=one(
            hits(
                ti,
                rf"投資本金=1單位商品×商品面額×(\d+(?:\.\d+)?)%={NUM}({U})",
                lambda m: Principal(Decimal(m[1]), dec(m[2]), m[3]),
            )
        ),
        monthly=hits(ti, r"每月配息率：(\d+(?:\.\d+)?)%", lambda m: Decimal(m[1])),
        fx=one(
            hits(
                ti, rf"匯率\(每一({U})兌({U})的市場中間匯率\)=(\d+(?:\.\d+)?)", lambda m: Fx(m[1], m[2], Decimal(m[3]))
            )
        ),
        table=table,
        scenarios=scenarios,
        assumptions=assumptions,
    )
