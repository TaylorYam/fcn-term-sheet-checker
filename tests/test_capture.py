"""測試切點：各 parser 共用的欄位擷取 `capture(name, TextIndex, pattern, convert)`（Issue #146 統一前，先鎖住兩種版本的行為）。

- 投資人須知 parser：證據只引第 1 組（值）所在的行。
- 說明書 parser（BARC 以外）：證據引整個命中的行（含欄位標籤那一行）；MS 另把括號統一全形再比對。
手刻幾行文字，不讀 PDF。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fcn_checker.parsers.layout import TextIndex, capture, parse_date
from fcn_checker.schema import FieldStatus, Line


def line(page: int, y: float, text: str) -> Line:
    return Line(page, 50.0, y, 300.0, y + 10, text)


LABEL, VALUE, NEXT = (
    line(1, 100, "商品年期："),
    line(1, 112, "6個月期，自發行日起算"),
    line(1, 124, "交易日：2025年1月2日"),
)


def test_default_evidence_is_only_the_lines_of_the_captured_value():
    ti = TextIndex([LABEL, VALUE, NEXT])

    pf = capture("tenor_months", ti, r"商品年期：(\d+)個月期", int)

    assert (pf.status, pf.value) == (FieldStatus.PRESENT, 6)
    assert [e.text for e in pf.evidence] == [VALUE.text], "標籤那一行不算證據"


def test_whole_match_cites_the_label_line_too():
    ti = TextIndex([LABEL, VALUE, NEXT])

    pf = capture("tenor_months", ti, r"商品年期：(\d+)個月期", int, whole_match=True)

    assert (pf.status, pf.value) == (FieldStatus.PRESENT, 6)
    assert [e.text for e in pf.evidence] == [LABEL.text, VALUE.text], "說明書 parser 連標籤那一行一起引"


def test_values_that_cannot_be_converted_are_invalid_and_name_the_text():
    ti = TextIndex([NEXT, line(1, 136, "發行日：2025年13月40日")])

    bad_date = capture("issue_date", ti, r"發行日：(\d{4}年\d{1,2}月\d{1,2}日)", parse_date)
    bad_number = capture("x", ti, r"(交易日)：", Decimal)

    assert bad_date.status == FieldStatus.INVALID and bad_date.note == "「2025年13月40日」無法辨識"
    assert bad_number.status == FieldStatus.INVALID and bad_number.note == "「交易日」無法辨識"
    ok = capture("trade_date", ti, r"交易日：(\d{4}年\d{1,2}月\d{1,2}日)", parse_date)
    assert ok.value == dt.date(2025, 1, 2)


def test_same_value_everywhere_is_present_and_different_values_are_ambiguous():
    same = TextIndex([line(1, 100, "發行價格：商品面額之100%"), line(2, 100, "發行價格（商品面額的100%）")])
    differ = TextIndex([line(1, 100, "發行價格：商品面額之100%"), line(2, 100, "發行價格：商品面額之99%")])

    assert capture("p", same, r"發行價格[：（]商品面額[之的](\d+)%", int).value == 100
    pf = capture("p", differ, r"發行價格：商品面額之(\d+)%", int)
    assert pf.status == FieldStatus.AMBIGUOUS and pf.candidates == [100, 99] and len(pf.evidence) == 2
    assert capture("p", same, r"沒有這個標籤：(\d+)").status == FieldStatus.MISSING


def test_unified_brackets_let_full_width_patterns_match_half_width_text_with_the_right_evidence():
    lines = [line(1, 100, "固定配息率 (0.85%) 按月"), line(1, 112, "下一行")]

    plain, unified = TextIndex(lines), TextIndex(lines, unify_brackets=True)

    assert capture("c", plain, r"固定配息率（(\d+(?:\.\d+)?)%）", Decimal).status == FieldStatus.MISSING
    pf = capture("c", unified, r"固定配息率（(\d+(?:\.\d+)?)%）", Decimal)
    assert pf.value == Decimal("0.85") and [e.text for e in pf.evidence] == [lines[0].text]
    assert unified.text == "固定配息率（0.85%）按月下一行" and len(unified.text) == len(plain.text), (
        "一對一換字，位置不變"
    )
