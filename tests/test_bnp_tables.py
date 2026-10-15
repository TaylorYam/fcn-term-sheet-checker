"""BNP 標的表的真實版面（Issue #240）：表格跨頁、列標籤與第一檔名稱換行。

合成 PDF 的標的表一格一行、各格同一個 x，排不出真實樣本「一欄一個 x」的版面，這裡直接用擷取後的行測解析。
標的、名稱與價格皆虛構；行的順序與座標照真實樣本擷取的結果（一頁讀完才換頁、格內文字依寬度拆行）。
"""

from __future__ import annotations

from decimal import Decimal

from fcn_checker.parsers import bnp, bnp_tables
from fcn_checker.schema import Line

LABEL_X, COLS = 50.0, (150.0, 250.0)


def ln(page: int, x: float, y: float, text: str) -> Line:
    return Line(page, x, y, x + 90, y + 10, text)


def cells(page: int, y: float, *texts: str) -> list[Line]:
    return [ln(page, x, y, t) for x, t in zip(COLS, texts, strict=True)]


def test_reprinted_table_split_across_pages_is_stitched_back():
    """第 18 項重印表跨頁：期初價格標籤後半、自動提前贖回價格的標籤後半與各格後半都印在下一頁頂端。"""
    lines = [
        ln(13, LABEL_X, 676, "連結標的名稱："),
        *cells(13, 676, "虛構甲公司", "虛構乙公司"),
        ln(13, LABEL_X, 692, "期初價格(Initial Price)："),
        *cells(13, 692, "400.0000", "200.0000"),
        ln(13, LABEL_X, 722, "執行價格(Strike"),
        ln(13, LABEL_X, 735, "Price)："),
        ln(13, COLS[0], 722, "320.0000，即期"),
        ln(13, COLS[0], 735, "初價格之80.00%"),
        ln(13, COLS[1], 722, "160.0000，即期"),
        ln(13, COLS[1], 735, "初價格之80.00%"),
        ln(13, LABEL_X, 767, "自動提前贖回價"),
        *cells(13, 766, "400.0000，即期", "200.0000，即期"),
        ln(14, LABEL_X, 79, "格(Automatic"),
        ln(14, LABEL_X, 92, "Early Redemption"),
        ln(14, LABEL_X, 104, "Price)："),
        ln(14, COLS[0], 80, "初價格之"),
        ln(14, COLS[0], 92, "100.00%"),
        ln(14, COLS[1], 80, "初價格之"),
        ln(14, COLS[1], 92, "100.00%"),
        ln(14, LABEL_X, 121, "下限價格(Knock-In"),
        ln(14, LABEL_X, 134, "Price)："),
        *cells(14, 121, "280.0000，即期初價格之70.00%", "140.0000，即期初價格之70.00%"),
        ln(14, LABEL_X, 151, "貨幣(Currency)："),
        *cells(14, 151, "美元", "美元"),
    ]
    table = bnp_tables.price_table("scenario_table", lines, with_tickers=False)
    assert table.ok, table.note
    rows = table.value["rows"]
    assert [r["prices"]["ko"] for r in rows] == [Decimal("400.0000"), Decimal("200.0000")]
    assert [r["prices"]["ki"] for r in rows] == [Decimal("280.0000"), Decimal("140.0000")]
    assert table.value["pcts"]["ko"] == [Decimal("100.00"), Decimal("100.00")]


def test_initial_price_label_split_across_pages():
    """跨頁的只有期初價格標籤後半（`Price)：` 在下一頁頂端、排在各格之後）。"""
    lines = [
        ln(13, LABEL_X, 738, "連結標的名稱："),
        *cells(13, 738, "虛構甲公司", "虛構乙公司"),
        ln(13, LABEL_X, 767, "期初價格(Initial"),
        *cells(13, 767, "400.0000", "200.0000"),
        ln(14, LABEL_X, 79, "Price)："),
        ln(14, LABEL_X, 96, "執行價格(Strike Price)："),
        *cells(14, 96, "320.0000，即期初價格之80.00%", "160.0000，即期初價格之80.00%"),
        ln(14, LABEL_X, 127, "自動提前贖回價格(Automatic Early Redemption Price)："),
        *cells(14, 127, "400.0000，即期初價格之100.00%", "200.0000，即期初價格之100.00%"),
        ln(14, LABEL_X, 160, "貨幣(Currency)："),
        *cells(14, 160, "美元", "美元"),
    ]
    table = bnp_tables.price_table("scenario_table", lines, with_tickers=False)
    assert table.ok, table.note
    assert [r["prices"]["initial"] for r in table.value["rows"]] == [Decimal("400.0000"), Decimal("200.0000")]


def _art11(name_lines: list[Line]) -> list[Line]:
    return [
        ln(4, 50, 114, "11."),
        ln(4, 80, 116, "連結標的資產價格，及其相對權重、與投資績效之關連情形："),
        *name_lines,
        ln(4, LABEL_X, 171, "彭博代碼："),
        *cells(4, 170, "ZBA UW", "ZBB UN"),
        ln(4, LABEL_X, 188, "交易所："),
        *cells(4, 188, "那斯達克全球精選", "紐約證券交易所"),
        ln(4, LABEL_X, 219, "期初價格(Initial Price)："),
        *cells(4, 219, "400.0000", "200.0000"),
        ln(4, LABEL_X, 260, "執行價格(Strike Price):"),
        *cells(4, 260, "320.0000 即期初價格之80.00%", "160.0000 即期初價格之80.00%"),
        ln(4, LABEL_X, 302, "自動提前贖回價格(Automatic Early Redemption Price)："),
        *cells(4, 302, "400.0000 即期初價格之100.00%", "200.0000 即期初價格之100.00%"),
        ln(4, LABEL_X, 340, "貨幣(Currency)："),
        *cells(4, 340, "美元", "美元"),
    ]


def test_name_label_wrapped_onto_two_lines():
    """「連結標的名稱：」在標籤欄換行成「連結標的名」「稱：」：仍找得到第 11 項標的表與各檔名稱。"""
    art11 = _art11(
        [
            ln(4, LABEL_X, 141, "連結標的名"),
            ln(4, LABEL_X, 154, "稱："),
            ln(4, COLS[0], 141, "虛構甲公司（德"),
            ln(4, COLS[0], 154, "拉瓦州）"),
            ln(4, COLS[1], 141, "虛構乙公司"),
        ]
    )
    table = bnp_tables.price_table("underlying_table", bnp._art11_region(art11), with_tickers=True)
    assert table.ok, table.note
    assert table.value["tickers"] == ["ZBA UW", "ZBB UN"]
    assert table.value["names"] == ["虛構甲公司（德拉瓦州）", "虛構乙公司"]


def test_first_name_on_the_label_line_wrapped():
    """第一檔名稱和標籤同一行又換行（「連結標的名稱：  虛構甲乙」＋「丙丁公司」）：接成同一檔。"""
    art11 = _art11(
        [
            ln(4, LABEL_X, 126, "連結標的名稱：  虛構甲乙"),
            ln(4, COLS[0], 139, "丙丁公司"),
            ln(4, COLS[1], 126, "虛構戊己公"),
            ln(4, COLS[1], 139, "司"),
        ]
    )
    table = bnp_tables.price_table("underlying_table", bnp._art11_region(art11), with_tickers=True)
    assert table.ok, table.note
    assert table.value["names"] == ["虛構甲乙丙丁公司", "虛構戊己公司"]
