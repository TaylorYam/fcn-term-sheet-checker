"""GS 價格表的真實版面（Issue #264）：表頭跨頁且相鄰幾格擷取成同一行、最後一檔溢到下一頁。

合成 PDF 的價格表排不出這兩種版面，這裡直接用擷取後的行（含每個字的 x 範圍）測解析。標的、名稱與價格皆虛構；
座標與行的順序照真實樣本擷取的結果（各欄的格左緣對齊、一頁讀完才換頁）。
"""

from __future__ import annotations

from decimal import Decimal

from fcn_checker.parsers import gs
from fcn_checker.parsers.common import split_at_columns
from fcn_checker.schema import Line


def width(ch: str) -> float:
    return 2.75 if ch.isspace() else 5.5 if ord(ch) < 256 else 11.0


def cell(page: int, y: float, *segments: tuple[float, str]) -> Line:
    """一行文字；多個 (x, 文字) 段＝相鄰幾格擷取成同一行（段與段之間有空隙）。"""
    chars: list[tuple[float, float]] = []
    for x, text in segments:
        for ch in text:
            chars.append((x, x + width(ch)))
            x += width(ch)
    text = "".join(t for _, t in segments)
    return Line(page, segments[0][0], y, chars[-1][1], y + 11, text, tuple(chars))


# 欄的左緣：列號、名稱、彭博代碼、參考幣別、初始參考價格、執行價格、提前出場價格、下限觸發價格
N, NAME, CODE, CCY, INIT, K, KO, KI = 104.4, 143.7, 232.0, 273.6, 315.3, 366.4, 428.8, 491.3


def row(page: int, y: float, k: int, name: str, symbol: str, prices: tuple[str, str, str, str]) -> list[Line]:
    """一檔標的：代碼拆三行（代號／交易所碼／Equity）；初始參考價格與執行價格擷取成同一行（真實樣本）。"""
    sym, exch = symbol.split()
    return [
        cell(page, y + 12, (N, str(k))),
        cell(page, y + 12, (NAME, name)),
        cell(page, y, (CODE, sym)),
        cell(page, y + 12, (CODE, exch)),
        cell(page, y + 24, (CODE, "Equity")),
        cell(page, y + 12, (CCY, "美元")),
        cell(page, y + 12, (INIT, prices[0] + " "), (K, prices[1])),
        cell(page, y + 12, (KO, prices[2])),
        cell(page, y + 12, (KI, prices[3])),
    ]


def test_header_split_across_pages_with_cells_extracted_as_one_line():
    """表頭從頁底接到下一頁，頁底那一行橫跨 6 欄（`彭博代參考幣初始參考執行價格(i) 提前出場價下限觸發價`）：
    依字座標切回各欄後，K／KO／KI % 與各檔價格都讀到。"""
    lines = [
        cell(9, 695.8, (N, "連結標"), (NAME, "連結標的股份發")),
        cell(9, 695.8, (CODE, "彭博代"), (CCY, "參考幣"), (INIT, "初始參考"), (K, "執行價格(i) "), (KO, "提前出場價"), (KI, "下限觸發價")),
        cell(10, 98.7, (N, "的(i)")),
        cell(10, 98.7, (NAME, "行人(i)")),
        cell(10, 98.7, (CODE, "碼(i)")),
        cell(10, 98.7, (CCY, "別(i)")),
        cell(10, 98.7, (INIT, "價格(i)")),
        cell(10, 99.7, (K, "(初始參考價")),
        cell(10, 113.9, (K, "格(i)之75.0")),
        cell(10, 127.3, (K, "0%)")),
        cell(10, 99.7, (KO, "格(i)(初始")),
        cell(10, 113.9, (KO, "參考價格(i)")),
        cell(10, 127.3, (KO, "之100.00%)")),
        cell(10, 99.7, (KI, "格(i)(初始")),
        cell(10, 113.9, (KI, "參考價格(i)")),
        cell(10, 127.3, (KI, "之65.00%)")),
        *row(10, 145.3, 1, "虛構甲公司", "ZAAA UQ", ("121.5700", "91.1775", "121.5700", "79.0205")),
        *row(10, 187.3, 2, "虛構乙公司", "ZBBB UW", ("332.8900", "249.6675", "332.8900", "216.3785")),
    ]  # fmt: skip
    table = gs.price_table("underlying_table", lines)
    assert table.ok, table.note
    v = table.value
    assert v["columns"] == ["initial", "strike", "ko", "ki"]
    assert v["pcts"] == {"strike": Decimal("75.00"), "ko": Decimal("100.00"), "ki": Decimal("65.00")}
    assert v["tickers"] == ["ZAAA UQ", "ZBBB UW"]
    assert v["rows"][0] == {
        "initial": Decimal("121.5700"),
        "strike": Decimal("91.1775"),
        "ko": Decimal("121.5700"),
        "ki": Decimal("79.0205"),
    }
    assert v["indexed"]


def test_header_line_holding_the_end_of_one_cell_and_the_start_of_the_next():
    """下一頁頁首一行是執行價格欄的結尾接提前出場價格欄的一段（`(i)之82.74%) 價格(i)之90.`），
    提前出場價格欄的 % 還分在下一行（`00%)`）：切開後兩欄的 % 都讀到。"""
    init, k, ko = 351.3, 410.1, 481.8
    lines = [
        cell(9, 677.9, (N, "連結標的")),
        cell(9, 691.2, (N, "(i)")),
        cell(9, 677.9, (NAME, "連結標的股份發行人")),
        cell(9, 691.2, (NAME, "(i)")),
        cell(9, 677.9, (255.8, "彭博代碼")),
        cell(9, 691.2, (255.8, "(i)")),
        cell(9, 677.9, (303.5, "參考幣別")),
        cell(9, 691.2, (303.5, "(i)")),
        cell(9, 677.9, (init, "初始參考價")),
        cell(9, 691.2, (init, "格(i)")),
        cell(9, 679.3, (k, "執行價格(i)(")),
        cell(9, 693.6, (k, "初始參考價格")),
        cell(9, 679.3, (ko, "提前出場價格")),
        cell(9, 693.6, (ko, "(i)(初始參考")),
        cell(10, 98.7, (k, "(i)之82.74%) "), (ko, "價格(i)之90.")),
        cell(10, 112.9, (ko, "00%)")),
        cell(10, 143.1, (N, "1")),
        cell(10, 143.1, (NAME, "虛構甲公司")),
        cell(10, 131.1, (255.8, "ZAAA")),
        cell(10, 143.7, (255.8, "UW")),
        cell(10, 156.5, (255.8, "Equity")),
        cell(10, 143.1, (303.5, "美元")),
        cell(10, 143.8, (init, "365.1100")),
        cell(10, 143.8, (k, "302.0920")),
        cell(10, 143.8, (ko, "328.5990")),
    ]
    table = gs.price_table("underlying_table", lines)
    assert table.ok, table.note
    assert table.value["pcts"] == {"strike": Decimal("82.74"), "ko": Decimal("90.00")}
    assert table.value["rows"] == [
        {"initial": Decimal("365.1100"), "strike": Decimal("302.0920"), "ko": Decimal("328.5990")}
    ]


def test_last_row_overflowing_onto_the_next_page_belongs_to_that_row():
    """投資人須知最後一檔的 `Equity`、`元` 溢到下一頁頁首（那一頁沒有列號）：接回最後一檔，不判讀不到。"""
    init, k, ko, ki = 230.7, 297.0, 396.5, 496.1
    header = [
        cell(1, 648.2, (15.5, "連結標")),
        cell(1, 657.0, (15.5, "的(i)")),
        cell(1, 648.2, (58.8, "連結標的股份發行")),
        cell(1, 663.9, (58.8, "人(i)")),
        cell(1, 648.2, (160.9, "彭博代碼(i) "), (init, "初始參考價")),
        cell(1, 663.9, (init, "格(i)")),
        cell(1, 648.2, (k, "執行價格(i)(初始")),
        cell(1, 663.9, (k, "參考價格(i)之70.")),
        cell(1, 679.5, (k, "00%)")),
        cell(1, 648.2, (ko, "提前出場價格(i)(")),
        cell(1, 663.9, (ko, "初始參考價格(i)")),
        cell(1, 679.5, (ko, "之105.00%)")),
        cell(1, 648.2, (ki, "下限觸發價格(i)(")),
        cell(1, 663.9, (ki, "初始參考價格(i)")),
        cell(1, 679.5, (ki, "之65.00%)")),
    ]

    def iis_row(y: float, n: int, symbol: str, prices: tuple[str, ...], overflow: bool) -> list[Line]:
        out = [
            cell(1, y, (33.8, str(n))),
            cell(1, y, (58.8, f"虛構{n}公司")),
            cell(1, y + 1, (160.9, symbol)),
            cell(1, y, (init, prices[0] + "美"), (k, prices[1] + "美元")),
            cell(1, y, (ko, prices[2] + "美元")),
            cell(1, y, (ki, prices[3] + "美元")),
        ]
        tail = [cell(2, 9.8, (160.9, "Equity")), cell(2, 9.8, (init, "元"))]
        return out + (tail if overflow else [cell(1, y + 10, (160.9, "Equity")), cell(1, y + 10, (init, "元"))])

    lines = [
        *header,
        *iis_row(691.6, 1, "ZAAA UW", ("544.4400", "381.1080", "571.6620", "353.8860"), overflow=False),
        *iis_row(754.7, 2, "ZBBB UW", ("186.5500", "130.5850", "195.8775", "121.2575"), overflow=True),
    ]
    table = gs.price_table("underlying_table", lines)
    assert table.ok, table.note
    v = table.value
    assert v["pcts"] == {"strike": Decimal("70.00"), "ko": Decimal("105.00"), "ki": Decimal("65.00")}
    assert v["tickers"] == ["ZAAA UW", "ZBBB UW"]
    assert v["rows"][1]["strike"] == Decimal("130.5850")
    assert any(ln.page == 2 and ln.text == "Equity" for ln in v["row_lines"][1])


def test_lines_without_character_boxes_are_left_alone():
    """沒有字座標（合成或改過文字的行）不切；有字座標但沒有字落在其他欄的左緣上也不切。"""
    plain = Line(1, 100.0, 10.0, 300.0, 21.0, "甲乙 丙丁")
    stale = Line(1, 100.0, 30.0, 300.0, 41.0, "改過的文字", ((100.0, 111.0),))
    whole = cell(1, 50.0, (100.0, "戊己庚"))
    other = cell(1, 70.0, (140.0, "辛"))
    again = cell(1, 90.0, (140.0, "壬"))
    assert split_at_columns([plain, stale, whole, other, again]) == [plain, stale, whole, other, again]
