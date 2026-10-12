"""測試切點：三家 parser 共用的情境金額幣別擷取 `money.amounts`（Issue #170、#179）。

股價、執行價、收盤價與實物交割算式跟著標的走，匯率是兩種幣別的換算，都不是商品幣別的出處；
面額、配息、本金、剩餘金額（以承作幣別支付）仍逐處列出。手刻幾行文字（寫法同真實樣本、數字虛構），不讀 PDF。
"""

from __future__ import annotations

from fcn_checker.parsers import money
from fcn_checker.parsers.hsbc import ScenarioIndex
from fcn_checker.parsers.layout import TextIndex
from fcn_checker.schema import Line


def lines(*texts: str) -> list[Line]:
    return [Line(1, 50.0, 100.0 + 12 * i, 300.0, 110.0 + 12 * i, t) for i, t in enumerate(texts)]


def currency_first(*texts: str) -> list[str]:
    """HSBC 寫法（幣別在前，HSBC 情境全文保留相鄰數字行的邊界）：每一處金額的原文。"""
    return [m.text for m in money.amounts(ScenarioIndex(lines(*texts)), money.CURRENCY_THEN_AMOUNT, currency_group=1)]


def amount_first(*texts: str) -> list[str]:
    """BARC、MS 寫法（金額在前，BARC 情境全文）：每一處金額的原文。"""
    return [m.text for m in money.amounts(TextIndex(lines(*texts)), money.AMOUNT_THEN_CURRENCY, currency_group=2)]


def test_exchange_rates_are_not_the_product_currency():
    assert currency_first("每單位面額為日圓 1,000,000 元", "期末匯率為1 日圓 =美元 0.006") == ["日圓1,000,000"]
    assert amount_first("期末匯率為1 歐元 =1.16 美元", "每單位商品面額=10,000 歐元") == ["10,000歐元"]


def test_a_physical_settlement_formula_explained_on_the_line_above_is_skipped_but_its_result_is_kept():
    """算式說明在上一行、數字在下一行：股價與實物交割那幾行不算；以承作幣別支付的剩餘金額與到期贖回價值仍算。"""
    found = currency_first(
        "5)零股數則以日圓支付(「剩餘金額」)，計算方式為：",
        "最後評價日籃子中最差表現連結標的期末股價×零股數/期末匯率",
        "=美元 83.30 × 0.0166 / 0.006",
        "=日圓 230.45",
        "6)每單位面額到期贖回價值計算方式為：",
        "籃子中最差表現連結標的期末股價× 籃子中最差表現連結標的交割股數/期末匯率+以日圓支",
        "付的剩餘金額",
        "=(美元 83.30 × 36/ 0.006) + 日圓 230.45",
        "=日圓 500,000.45",
    )
    assert found == ["日圓230.45", "日圓500,000.45"]


def test_a_closing_price_split_across_lines_is_skipped():
    for head, tail in [("收盤價", "格小於1,364.52日圓"), ("收盤", "價格小於78.93美元")]:
        found = amount_first(
            f"*請注意，若在期末定價日籃子中表現最差的連結標的（7011 JT）{head}",
            f"{tail}，投資人的本金損失可能更大。在最差情況下，當籃子中表現最差的連結",
            "標的市場價值為0 時，每單位商品面額到期贖回價值為0 澳幣。",
        )
        assert found == ["0澳幣"], head


def test_a_new_formula_after_a_physical_settlement_formula_is_kept():
    """兩個算式之間沒有標點時，前一個算式的「股」不帶到下一個算式（BARC 情境 (iii)、MS 較差情境）。"""
    found = amount_first(
        "每單位最終實物贖回權益等值 = 表現最差之標的資產於最終評價日之相關價格 × 每單位交割股數 ÷ 交割匯",
        "率 + 剩餘金額",
        "= 1.00 美元 × 39 股 ÷ 0.0063 + 133 日幣",
        "= 6,323.00 日幣 (四捨五入至整數位)",
        "總報酬率(截至到期日之報酬) = [(每單位累積配息金額 + 每單位最終實物贖回權益等值) / 每單位商品面",
        "額] - 1",
        "= [(52,500.00 日幣 + 6,323.00 日幣) / 1,000,000 日幣] - 1",
    )
    assert found == ["6,323.00日幣", "52,500.00日幣", "6,323.00日幣", "1,000,000日幣"]
    found = amount_first(
        "=42.0000 美元×142 股/1+36.00 美元=6,000.00 美元",
        "= 100.00 澳幣×12+6,000.00 澳幣-10,000.00 澳幣=-2,800.00 澳幣",
    )
    assert found == ["100.00澳幣", "6,000.00澳幣", "10,000.00澳幣", "2,800.00澳幣"]


def test_stock_prices_on_the_same_line_are_still_skipped():
    """#170 的逐行規則不變：同一行有股價或「股」時整行不算（含該行算式的結果）。"""
    assert (
        currency_first(
            "期末股價為美元 83.30，低於執行價美元 166.59，", "=日圓 1,000,000 × 0.006 / 美元 166.59 =36.0166 股"
        )
        == []
    )
    assert amount_first("=78.9300 美元×42 股/0.056565+1,393.85 南非幣=60,000.06 南非幣") == []


def test_a_wrong_currency_on_the_remainder_is_still_listed():
    """剩餘金額寫成標的幣別時仍列出，由共用規則判不一致。"""
    found = money.amounts(
        ScenarioIndex(
            lines(
                "最後評價日籃子中最差表現連結標的期末股價×零股數/期末匯率",
                "=美元 83.30 × 0.0166 / 0.006",
                "=美元 230.45",
            )
        ),
        money.CURRENCY_THEN_AMOUNT,
        currency_group=1,
    )
    assert [(m.text, m.currency) for m in found] == [("美元230.45", "美元")]
    assert [e.text for e in found[0].evidence] == ["=美元 230.45"]
