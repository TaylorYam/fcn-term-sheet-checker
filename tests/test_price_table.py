"""價格表讀取（parsers/price_table.py，Issue #287）：直接餵擷取後的行（含每個字的 x 範圍）測各種版面。

合成 PDF 排不出真實樣本的表格版面（表頭跨頁且相鄰幾格擷取成同一行、最後一檔溢到下一頁），所以這裡手寫 `Line`。
標的、名稱與價格皆虛構；GS 的幾組座標與行的順序照真實樣本擷取的結果（各欄的格左緣對齊、一頁讀完才換頁）。
"""

from __future__ import annotations

from decimal import Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.parsers import gs
from fcn_checker.parsers.price_table import Column, TableSpec, price_rows, read, split_at_columns
from fcn_checker.schema import FieldStatus, Line


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


def centered(page: int, xc: float, y: float, text: str) -> Line:
    """置中在 `xc` 的一格（沒有字座標）。"""
    w = sum(width(ch) for ch in text)
    return Line(page, xc - w / 2, y, xc + w / 2, y + 11, text)


# 簡單版面：列號、名稱、彭博代碼、期初價格、執行價、自動提前出場價、下限價（各欄置中）
X = {"n": 40.0, "name": 100.0, "code": 180.0, "initial": 260.0, "strike": 340.0, "ko": 420.0, "ki": 500.0}
SPEC = TableSpec(
    where="測試價格表",
    initial="期初價格",
    columns=(Column("strike", "執行價"), Column("ko", "自動提前出場價"), Column("ki", "下限價", required=False)),
    anchor="number",
    pct=r"之(\d+(?:\.\d+)?)%",
    pct_label="(期初價格之X%)",
    code="彭博代碼",
)


def header(page: int = 1, y: float = 100.0, *, ki: bool = True, code: bool = True) -> list[Line]:
    heads = [
        (X["name"], ["連結標的"]),
        (X["initial"], ["期初價格"]),
        (X["strike"], ["執行價", "(期初價格之", "80.00%)"]),
        (X["ko"], ["自動提前出場價", "(期初價格之", "100.00%)"]),
    ]
    if code:
        heads.append((X["code"], ["彭博代碼"]))
    if ki:
        heads.append((X["ki"], ["下限價", "(期初價格之", "60.00%)"]))
    return [centered(page, xc, y + 12 * i, t) for xc, parts in heads for i, t in enumerate(parts)]


def row(
    page: int, y: float, n: int | None, name: str, ticker: str | None, prices: tuple[str, ...], ccy: str = " 美元"
) -> list[Line]:
    keys = ["initial", "strike", "ko", "ki"][: len(prices)]
    out = [centered(page, X[k], y, p + ccy) for k, p in zip(keys, prices, strict=True)]
    out.append(centered(page, X["name"], y, name))
    if n is not None:
        out.append(centered(page, X["n"], y, str(n)))
    if ticker is not None:
        out.append(centered(page, X["code"], y, ticker))
    return out


ROW1 = ("100.0000", "80.0000", "100.0000", "60.0000")
ROW2 = ("50.0000", "40.0000", "50.0000", "30.0000")


def test_number_anchor_reads_columns_pcts_tickers_names_and_currency():
    lines = [*header(), *row(1, 150, 1, "甲公司", "AAA UW", ROW1), *row(1, 170, 2, "乙公司", "BBB UN", ROW2)]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    v = table.value
    assert v.columns == ("initial", "strike", "ko", "ki")
    assert v.pcts == {"strike": Decimal("80.00"), "ko": Decimal("100.00"), "ki": Decimal("60.00")}
    assert v.tickers == ["AAA UW", "BBB UN"]
    assert v.names == ["甲公司", "乙公司"]
    assert v.currencies == ["美元", "美元"]
    assert v.prices[1] == {
        "initial": Decimal("50.0000"),
        "strike": Decimal("40.0000"),
        "ko": Decimal("50.0000"),
        "ki": Decimal("30.0000"),
    }
    assert v.heads["code"] == "彭博代碼"
    assert {ln.text for ln in v.header_lines} >= {"期初價格", "80.00%)"}


def test_column_order_follows_header_x_positions(monkeypatch):
    """欄序依表頭各欄的 x 位置（例：CA 是 K → KI → KO），不依宣告的順序。"""
    monkeypatch.setitem(X, "ko", 500.0)
    monkeypatch.setitem(X, "ki", 420.0)
    lines = [*header(), *row(1, 150, 1, "甲公司", "AAA UW", ("10.0", "8.0", "10.0", "6.0"))]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert table.value.columns == ("initial", "strike", "ki", "ko")
    assert table.value.prices[0] == {
        "initial": Decimal("10.0"),
        "strike": Decimal("8.0"),
        "ki": Decimal("6.0"),
        "ko": Decimal("10.0"),
    }


def test_ticker_anchor_with_symbol_and_exchange_wrapped_onto_two_lines():
    """代碼列錨點：4 碼以上的代號換成兩行（`AVGO` / `UW`）先接回再當列錨點；表內沒有列號。"""
    spec = TableSpec(**{**SPEC.__dict__, "anchor": "ticker"})
    lines = [
        *header(),
        centered(1, X["code"], 145, "AVGO"),
        centered(1, X["code"], 156, "UW"),
        *row(1, 150, None, "甲公司", None, ROW1),
        *row(1, 175, None, "乙公司", "T UN", ROW2),
    ]
    table = read("t", lines, spec)
    assert table.ok, table.note
    assert table.value.tickers == ["AVGO UW", "T UN"]
    assert table.value.prices[0]["strike"] == Decimal("80.0000")


def test_price_anchor_rows_without_row_numbers_or_tickers():
    """價格列錨點（情境分析重印的表沒有列號與代碼）：以執行價格欄的價格切列。"""
    spec = TableSpec(**{**SPEC.__dict__, "anchor": "price", "code": None})
    lines = [*header(code=False), *row(1, 150, None, "甲公司", None, ROW1), *row(1, 170, None, "乙公司", None, ROW2)]
    table = read("t", lines, spec)
    assert table.ok, table.note
    assert table.value.tickers == [None, None]
    assert table.value.names == ["甲公司", "乙公司"]
    assert [p["ko"] for p in table.value.prices] == [Decimal("100.0000"), Decimal("50.0000")]


def test_price_anchor_with_initial_price_one_line_above_the_row():
    """期初價格的數字比同列其他格高一行、幣別字和同列對齊（CA 第 16 項真實樣本，Issue #278）：仍是同一列。"""
    spec = TableSpec(**{**SPEC.__dict__, "anchor": "price", "code": None})
    first = [ln for ln in row(1, 160, None, "甲公司", None, ROW1) if ln.text != "100.0000 美元" or ln.x0 > 300]
    lines = [
        *header(code=False),
        centered(1, X["initial"], 144.4, "100.0000 美"),
        centered(1, X["initial"], 160, "元"),
        *first,
        *row(1, 176, None, "乙公司", None, ROW2),
    ]
    table = read("t", lines, spec)
    assert table.ok, table.note
    assert table.value.prices[0]["initial"] == Decimal("100.0000")
    assert table.value.currencies == ["美元", "美元"]


def test_currency_word_wrapped_onto_the_next_line_stays_in_its_cell():
    """每格幣別字的最後一字換到下一行（`100.0000 美` / `元`）：接回同一格，不算到下一列。"""
    lines = [*header()]
    for y, n, prices in ((150, 1, ROW1), (176, 2, ROW2)):
        for key, p in zip(["initial", "strike", "ko", "ki"], prices, strict=True):
            lines += [centered(1, X[key], y, f"{p} 美"), centered(1, X[key], y + 10, "元")]
        lines += [centered(1, X["n"], y + 5, str(n)), centered(1, X["code"], y + 5, f"Z{n}Z UW")]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert table.value.prices[1]["initial"] == Decimal("50.0000")
    assert table.value.currencies == ["美元", "美元"]
    assert [len(r.lines) for r in table.value.rows] == [10, 10]


def test_cells_squeezed_into_one_line_without_character_boxes_still_fill_the_columns_in_order():
    """相鄰幾格擠成一行、又沒有字座標可以切開時，一列的價格依欄序對到各欄。"""
    lines = [*header()]
    lines += [
        centered(1, X["n"], 150, "1"),
        centered(1, X["code"], 150, "AAA UW"),
        centered(1, X["initial"], 150, "100.0000"),
        Line(1, X["strike"] - 20, 150, X["ki"] + 20, 161, "80.0000 100.0000 60.0000"),
    ]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert table.value.prices[0] == {
        "initial": Decimal("100.0000"),
        "strike": Decimal("80.0000"),
        "ko": Decimal("100.0000"),
        "ki": Decimal("60.0000"),
    }


def test_cells_squeezed_into_one_line_without_a_space_are_cut_at_the_gap():
    """相鄰兩格擠成一行、中間沒有空白但有間隙（`121.5700` `91.1775`）：依其他列共有的左緣從間隙切開，不接成一個數字。"""
    left = {k: X[k] - 20 for k in ("initial", "strike", "ko", "ki")}
    lines = [*header()]
    for y, n, prices in ((150, 1, ROW1), (170, 2, ROW2), (190, 3, ROW2)):
        lines += [centered(1, X["n"], y, str(n)), centered(1, X["code"], y, f"Z{n}Z UW")]
        if n == 1:
            lines.append(cell(1, y, (left["initial"], prices[0]), (left["strike"], prices[1])))
        else:
            lines += [cell(1, y, (left["initial"], prices[0])), cell(1, y, (left["strike"], prices[1]))]
        lines += [cell(1, y, (left["ko"], prices[2])), cell(1, y, (left["ki"], prices[3]))]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert table.value.prices[0]["initial"] == Decimal("100.0000")
    assert table.value.prices[0]["strike"] == Decimal("80.0000")


def test_a_stray_mark_beside_a_price_is_not_read_as_part_of_it():
    """價格旁並排的另一行（例：上標註腳 `5`）不接進價格：個數不符轉人工覆核，不悄悄讀成 `80.00005`。"""
    ok_row = row(1, 150, 1, "甲公司", "AAA UW", ROW1)
    mark = Line(1, X["strike"] + 32, 150, X["strike"] + 36, 156, "5")
    assert bad([*header(), *ok_row, mark]) == "測試價格表第 1 列有 5 個價格，與 4 欄不符"


def test_rows_spread_over_two_pages_are_read_by_their_own_anchors():
    lines = [*header(), *row(1, 750, 1, "甲公司", "AAA UW", ROW1), *row(2, 60, 2, "乙公司", "BBB UN", ROW2)]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert table.value.tickers == ["AAA UW", "BBB UN"]


def test_content_on_a_page_two_pages_after_the_last_row_is_not_dropped_silently():
    """所在頁沒有列錨點：緊接最後一列的下一頁接到最後一列，隔更遠讀不出（不悄悄丟掉）。"""
    lines = [*header(), *row(1, 150, 1, "甲公司", "AAA UW", ROW1)]
    near = read("t", [*lines, centered(2, X["name"], 20, "(續)")], SPEC)
    assert near.ok and near.value.names == ["甲公司(續)"]
    far = read("t", [*lines, centered(3, X["name"], 20, "(續)")], SPEC)
    assert far.status == FieldStatus.INVALID
    assert far.note == "測試價格表跨頁時讀不到該頁的列"
    assert [e.page for e in far.evidence] == [3]


def test_header_more_than_one_page_before_the_first_row_is_not_read():
    lines = [*header(page=1), *row(3, 150, 1, "甲公司", "AAA UW", ROW1)]
    table = read("t", lines, SPEC)
    assert table.status == FieldStatus.INVALID
    assert table.note == "測試價格表跨頁時讀不到該頁的列"


PRICE_SPEC = TableSpec(**{**SPEC.__dict__, "anchor": "price", "code": None})


def test_two_whole_numbers_stacked_in_a_narrow_column_are_not_joined():
    """窄欄裡上下兩列的整數期初價格（`3244` / `6938`，列距小）各自一列，不當成換行接成一個數字。"""
    lines = [*header(code=False)]
    for y, name, initial in ((150, "甲", "3244"), (163, "乙", "6938")):
        lines += row(1, y, None, name, None, (initial, *ROW1[1:]), ccy="")
    table = read("t", lines, PRICE_SPEC)
    assert table.ok, table.note
    assert [p["initial"] for p in table.value.prices] == [Decimal(3244), Decimal(6938)]


def test_text_before_the_header_is_not_part_of_it():
    """表前的說明句（含「彭博代碼為…」）緊接在表頭上面：不算表頭，不會變成第二個代碼欄。"""
    intro = Line(1, 40, 88.5, 560, 99.5, "連結標的資產：甲公司，彭博代碼為AAA UW；乙公司，彭博代碼為BBB UN。")
    lines = [intro, *header(), *row(1, 150, 1, "甲公司", "AAA UW", ROW1)]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert intro not in table.value.header_lines


def test_header_complete_on_the_first_row_page_ignores_the_page_before():
    """第 1 列那一頁的表頭已經齊全：上一頁頁底的文字不接進表頭。"""
    tail = centered(1, X["code"], 780, "說明文字")
    lines = [tail, *header(page=2, y=40), *row(2, 90, 1, "甲公司", "AAA UW", ROW1)]
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert table.value.heads["code"] == "彭博代碼"


def test_header_percentage_set_apart_from_the_column_name_still_belongs_to_it():
    """欄名一行、「(期初價格之X%)」隔一段距離在下一行（DBS）：依 x 位置歸到正上方的欄名。"""
    names = [(X["name"], "連結標的"), (X["code"], "彭博代碼"), (X["initial"], "期初價格")]
    names += [(X["strike"], "執行價"), (X["ko"], "自動提前出場價")]
    lines = [centered(1, x, 100, t) for x, t in names]
    lines += [centered(1, X["strike"], 120, "(期初價格之80.00%)"), centered(1, X["ko"], 120, "(期初價格之100.00%)")]
    lines += row(1, 150, 1, "甲公司", "AAA UW", ROW1[:3])
    table = read("t", lines, SPEC)
    assert table.ok, table.note
    assert table.value.pcts == {"strike": Decimal("80.00"), "ko": Decimal("100.00")}


def test_text_after_the_last_row_is_not_part_of_the_table():
    """最後一列下方隔一段距離的說明文字（同一頁）不算進表，不會變成多出來的價格。"""
    note = Line(1, 40, 190, 560, 201, "上述價格將四捨五入至小數點後第4位，共 2 檔")
    lines = [
        *header(code=False),
        *row(1, 150, None, "甲公司", None, ROW1),
        *row(1, 170, None, "乙公司", None, ROW2),
        note,
    ]
    table = read("t", lines, PRICE_SPEC)
    assert table.ok, table.note
    assert note not in table.value.rows[-1].lines


def test_row_number_and_ticker_on_one_line_still_anchor_the_row():
    """列號和代碼擷取成同一行（`2 BBB UN`）：仍是代碼列錨點。"""
    spec = TableSpec(**{**SPEC.__dict__, "anchor": "ticker"})
    second = row(1, 170, None, "乙公司", None, ROW2)
    squeezed = cell(1, 170, (X["n"] - 3, "2 "), (X["code"] - 18, "BBB UN"))
    lines = [*header(), *row(1, 150, 1, "甲公司", "AAA UW", ROW1), *second, squeezed]
    table = read("t", lines, spec)
    assert table.ok, table.note
    assert table.value.tickers == ["AAA UW", "BBB UN"]


def test_header_cells_of_neighbouring_columns_that_only_touch_stay_apart():
    """相鄰兩欄的表頭格只碰到邊（重疊不到 1pt，UBS）：不併成同一格。"""
    lines = [
        centered(1, X["initial"], 106, "期初價格"),
        Line(1, 300, 106, 441.3, 117, "執行價(期初價格之80.00%)"),
        Line(1, 440.6, 98, 565, 109, "自動提前出場價 (期初價格之"),
        centered(1, 503, 115, "100.00%)"),
        *row(1, 150, None, "甲公司", None, ("100.0000", "80.0000")),
        centered(1, 503, 150, "100.0000 美元"),
    ]
    table = read("t", lines, PRICE_SPEC)
    assert table.ok, table.note
    assert table.value.pcts == {"strike": Decimal("80.00"), "ko": Decimal("100.00")}


def test_a_name_starting_above_the_first_row_anchor_belongs_to_that_row():
    """第 1 檔名稱拆兩行、上面那一行比價格高（UBS）：在表頭最後一行之下，仍屬於第 1 列。"""
    lines = [
        *header(code=False),
        centered(1, X["name"], 140, "甲公司第一"),
        centered(1, X["name"], 158, "行與第二行"),
        *[ln for ln in row(1, 149, None, "x", None, ROW1) if ln.text != "x"],
    ]
    table = read("t", lines, PRICE_SPEC)
    assert table.ok, table.note
    assert table.value.names == ["甲公司第一行與第二行"]


# ---------------------------------------------------------------- 錯訊句型


def bad(lines: list[Line], spec: TableSpec = SPEC) -> str:
    table = read("t", lines, spec)
    assert not table.ok
    return table.note


def test_error_wording():
    ok_row = row(1, 150, 1, "甲公司", "AAA UW", ROW1)
    assert read("t", [], SPEC).status == FieldStatus.MISSING
    assert read("t", [], SPEC).note == "找不到測試價格表"
    assert bad(header()) == "測試價格表找不到第 1 列（列號）"
    no_ko = [ln for ln in header() if ln.xc != X["ko"]]
    assert bad([*no_ko, *ok_row]) == "測試價格表表頭找不到「自動提前出場價」欄"
    twice = [*header(), centered(1, 580, 100, "執行價(期初價格之80.00%)")]
    assert bad([*twice, *ok_row]) == "測試價格表表頭「執行價」出現多欄"
    no_pct = [ln for ln in header() if ln.text != "60.00%)"]
    assert bad([*no_pct, *ok_row]) == "測試價格表表頭「下限價」讀不到「(期初價格之X%)」"
    no_code = [ln for ln in ok_row if ln.text != "AAA UW"]
    assert bad([*header(), *no_code]) == "測試價格表第 1 列讀不到彭博代碼"
    short = [ln for ln in ok_row if ln.text != "60.0000 美元"]
    assert bad([*header(), *short]) == "測試價格表第 1 列有 3 個價格，與 4 欄不符"
    mixed = [*ok_row[:-4], centered(1, X["ki"], 150, "60.0000 日圓"), *ok_row[-3:]]
    assert bad([*header(), *[ln for ln in mixed if ln.text != "60.0000 美元"]]) == "測試價格表第 1 列各格的幣別字不同"


def test_spec_requires_a_code_column_for_ticker_anchors():
    with pytest.raises(ValueError):
        TableSpec(where="t", initial="期初價格", columns=(), anchor="ticker")


# ---------------------------------------------------------------- 轉標準欄位


def test_price_rows_takes_tickers_names_and_currency_from_the_adapter():
    table = read("t", [*header(), *row(1, 150, 1, "甲公司", "AAA UW", ROW1)], SPEC)
    rows = price_rows(table).value
    assert (rows[0].ticker, rows[0].name, rows[0].currency) == ("AAA UW", "甲公司", None)
    rows = price_rows(table, ["XYZ UN"], names=["第 1 檔"], currency=True).value
    assert (rows[0].ticker, rows[0].name, rows[0].currency) == ("XYZ UN", "第 1 檔", "美元")
    assert rows[0].prices["ki"] == Decimal("60.0000")
    broken = read("t", header(), SPEC)
    assert price_rows(broken).status == FieldStatus.INVALID and price_rows(broken).note == broken.note


# ---------------------------------------------------------------- GS 真實版面（Issue #264）

# 欄的左緣：列號、名稱、彭博代碼、參考幣別、初始參考價格、執行價格、提前出場價格、下限觸發價格
N, NAME, CODE, CCY, INIT, K, KO, KI = 104.4, 143.7, 232.0, 273.6, 315.3, 366.4, 428.8, 491.3


def gs_row(page: int, y: float, k: int, name: str, symbol: str, prices: tuple[str, str, str, str]) -> list[Line]:
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


def test_gs_header_split_across_pages_with_cells_extracted_as_one_line():
    """表頭從頁底接到下一頁，頁底那一行橫跨 6 欄（`彭博代參考幣初始參考執行價格(i) 提前出場價下限觸發價`）：
    依字座標切回各欄後，K／KO／KI % 與各檔價格都讀到；代碼的第一行比列號高，仍屬於該列。"""
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
        *gs_row(10, 145.3, 1, "虛構甲公司", "ZAAA UQ", ("121.5700", "91.1775", "121.5700", "79.0205")),
        *gs_row(10, 187.3, 2, "虛構乙公司", "ZBBB UW", ("332.8900", "249.6675", "332.8900", "216.3785")),
    ]  # fmt: skip
    table = read("underlying_table", lines, gs.PRICE_TABLE)
    assert table.ok, table.note
    v = table.value
    assert v.columns == ("initial", "strike", "ko", "ki")
    assert v.pcts == {"strike": Decimal("75.00"), "ko": Decimal("100.00"), "ki": Decimal("65.00")}
    assert v.tickers == ["ZAAA UQ", "ZBBB UW"]
    assert v.names == ["虛構甲公司", "虛構乙公司"]
    assert v.prices[0] == {
        "initial": Decimal("121.5700"),
        "strike": Decimal("91.1775"),
        "ko": Decimal("121.5700"),
        "ki": Decimal("79.0205"),
    }
    assert "(i)" in v.heads["initial"]


def test_gs_header_line_holding_the_end_of_one_cell_and_the_start_of_the_next():
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
    table = read("underlying_table", lines, gs.PRICE_TABLE)
    assert table.ok, table.note
    assert table.value.pcts == {"strike": Decimal("82.74"), "ko": Decimal("90.00")}
    assert table.value.prices == [
        {"initial": Decimal("365.1100"), "strike": Decimal("302.0920"), "ko": Decimal("328.5990")}
    ]


def test_gs_last_row_overflowing_onto_the_next_page_belongs_to_that_row():
    """投資人須知最後一檔的 `Equity`、`元` 溢到下一頁頁首（那一頁沒有列號）：接回最後一檔，不判讀不到。"""
    init, k, ko, ki = 230.7, 297.0, 396.5, 496.1
    head = [
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
        *head,
        *iis_row(691.6, 1, "ZAAA UW", ("544.4400", "381.1080", "571.6620", "353.8860"), overflow=False),
        *iis_row(754.7, 2, "ZBBB UW", ("186.5500", "130.5850", "195.8775", "121.2575"), overflow=True),
    ]
    table = read("underlying_table", lines, gs.PRICE_TABLE)
    assert table.ok, table.note
    v = table.value
    assert v.pcts == {"strike": Decimal("70.00"), "ko": Decimal("105.00"), "ki": Decimal("65.00")}
    assert v.tickers == ["ZAAA UW", "ZBBB UW"]
    assert v.currencies == ["美元", "美元"]
    assert v.prices[1]["initial"] == Decimal("186.5500")
    assert any(ln.page == 2 and ln.text == "Equity" for ln in v.rows[1].lines)


# ---------------------------------------------------------------- 依字座標切欄


def test_extracted_lines_carry_one_span_per_character(tmp_path):
    """擷取時每行帶每個字的 x 範圍，個數與文字相同；同一列兩格擠成一行時可依另一行的左緣切開。"""
    path = tmp_path / "t.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((100, 100), "ALPHA", fontsize=10)
    page.insert_text((140, 100), "BETA", fontsize=10)
    page.insert_text((140, 120), "GAMMA", fontsize=10)
    page.insert_text((140, 140), "DELTA", fontsize=10)
    doc.save(path)
    lines = extract_lines(fitz.open(path))
    assert "ALPHA BETA" in [ln.text for ln in lines]  # 兩段擠成同一行
    assert all(len(ln.aligned_char_spans) == len(ln.text) for ln in lines)
    assert {ln.text for ln in split_at_columns(lines)} >= {"ALPHA", "BETA", "GAMMA", "DELTA"}


def test_lines_without_character_boxes_are_left_alone():
    """沒有字座標（合成或改過文字的行）不切；有字座標但沒有字落在其他欄的左緣上也不切。"""
    plain = Line(1, 100.0, 10.0, 300.0, 21.0, "甲乙 丙丁")
    stale = Line(1, 100.0, 30.0, 300.0, 41.0, "改過的文字", ((100.0, 111.0),))
    whole = cell(1, 50.0, (100.0, "戊己庚"))
    other = cell(1, 70.0, (140.0, "辛"))
    again = cell(1, 90.0, (140.0, "壬"))
    assert split_at_columns([plain, stale, whole, other, again]) == [plain, stale, whole, other, again]
