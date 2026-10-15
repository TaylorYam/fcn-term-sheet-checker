"""HSBC 價格表的真實版面（Issue #252）：彭博代號在字中間換行、情境試算價格表表頭跨頁。

合成 PDF 的價格表排不出這兩種版面，這裡直接用擷取後的行測解析。標的、名稱與價格皆虛構；
行的順序照真實樣本擷取的結果（一格一行、格內文字依寬度拆行、一頁讀完才換頁）。
"""

from __future__ import annotations

from decimal import Decimal

from fcn_checker.parsers import hsbc_tables
from fcn_checker.schema import Line


def lines(*texts: str, page: int = 1) -> list[Line]:
    return [Line(page, 50.0, 100.0 + 12 * i, 300.0, 110.0 + 12 * i, t) for i, t in enumerate(texts)]


HEAD_12_1 = (
    "連結標的：", "彭博", "代號", "中文名稱", "幣", "別", "交易所",
    "期初股價", "執行價", "(即期初股價的75%)", "自動提前到期價格", "(即期初股價的100%)", "觸及不保本價格", "(即期初股價的70%)",
)  # fmt: skip


def test_bloomberg_code_wrapped_in_the_middle_of_the_symbol():
    """12(1) 標的表：`ZBAB UQ` 寫成 `ZBA` / `B UQ`（代號在字中間換行）也讀得到；代號與交易所碼之間換行照舊。"""
    table = hsbc_tables.price_table(
        "price_table",
        lines(
            *HEAD_12_1,
            *("ZA ", "UQ", "虛構甲公司", "US", "D", "NASDA", "Q", "100.0000", "75.0000", "100.0000", "70.0000"),
            *("ZBA", "B UQ", "虛構乙", "US", "D", "NASDA", "Q", "200.0000", "150.0000", "200.0000", "140.0000"),
        ),
    )
    assert table.ok, table.note
    rows = table.value["rows"]
    assert [r["ticker"] for r in rows] == ["ZA UQ", "ZBAB UQ"]
    assert [(r["label"], r["currency"], r["exchange"]) for r in rows] == [
        ("虛構甲公司", "USD", "NASDAQ"),
        ("虛構乙", "USD", "NASDAQ"),
    ]
    assert rows[1]["prices"] == {
        "initial": Decimal("200.0000"),
        "strike": Decimal("150.0000"),
        "ko": Decimal("200.0000"),
        "ki": Decimal("140.0000"),
    }


def test_scenario_table_header_split_across_pages():
    """情境試算價格表：欄名都在上一頁、「(即期初股價的X%)」都在下一頁；百分比依序對到期初股價以外的欄。"""
    table = hsbc_tables.price_table(
        "scenario_table",
        [
            *lines("彭博代號", "幣別", "交易所", "期初股價", "執行價", "自動提前到期價格", "觸及不保本價格", page=7),
            *lines(
                "(即期初股價的80.62%)",
                "(即期初股價的100%)",
                "(即期初股價的70%)",
                *("ZA UN", "USD", "NYSE", "400.0000", "322.4800", "400.0000", "280.0000"),
                page=8,
            ),
        ],
    )
    assert table.ok, table.note
    assert table.value["headers"] == {"strike": Decimal("80.62"), "ko": Decimal("100"), "ki": Decimal("70")}
    [row] = table.value["rows"]
    assert row["ticker"] == "ZA UN" and row["prices"]["ki"] == Decimal("280.0000")


def test_percent_before_its_column_name_is_rejected():
    """百分比出現在「執行價」欄名之前：不是範本寫法，判無法辨識。"""
    table = hsbc_tables.price_table(
        "scenario_table",
        lines(
            "彭博代號", "期初股價", "(即期初股價的75%)", "執行價", "自動提前到期價格", "(即期初股價的100%)",
            "ZA UN", "USD", "NYSE", "100.0000", "75.0000", "100.0000",
        ),
    )  # fmt: skip
    assert not table.ok and "百分比" in table.note
