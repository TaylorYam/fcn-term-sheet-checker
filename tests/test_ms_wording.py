"""測試切點：MS 說明書與投資人須知共用的條款句型表 `parsers/ms_wording.py`（Issue #146）與商品種類判斷 `rules/ms.py`。

手刻條款句子，不讀 PDF；兩份 parser 各自在哪一段判斷、讀不到時的說明文字見 test_check_ms.py、test_iis_ms.py。
"""

from __future__ import annotations

import datetime as dt

import pytest

from fcn_checker.parsers import ms_wording
from fcn_checker.parsers.layout import TextIndex, parse_date
from fcn_checker.rules.ms import PRODUCT_TYPES, product_type_for
from fcn_checker.schema import FieldStatus, Line


def text_index(*texts: str) -> TextIndex:
    return TextIndex([Line(1, 50.0, 100.0 + 12 * i, 300.0, 110.0 + 12 * i, t) for i, t in enumerate(texts)])


# ---------------------------------------------------------------- 觸及下限（KI）


@pytest.mark.parametrize(
    ("when", "kind"),
    [
        ("期末定價日", "AM"),
        ("交易日(含)至期末定價日(含)間的任一共同預定交易日", "D"),
        ("交易日（含）至期末定價日（含）間的任一預定交易日", "D"),
        ("任一配息週期終止日(含期末定價日)", "P"),
    ],
)
@pytest.mark.parametrize("quoted", ["", "「」"])
def test_ki_sentence_gives_the_type_for_term_sheet_and_iis_wordings(when, kind, quoted):
    event = f"{quoted[:1]}觸及下限事件{quoted[1:]}"
    ti = text_index(f"{event}：若在{when}，籃子中任一連結標的之收盤價低於其下限價格，則觸及下限事件視同發生。")

    pf = ms_wording.ki_type(ti, invalid_note="找不到")

    assert (pf.status, pf.value) == (FieldStatus.PRESENT, kind) and pf.evidence


def test_no_ki_is_the_two_strike_sentences_without_any_knock_in_wording():
    ti = text_index(
        "(a)現金交割：若於期末定價日時籃子中表現最差的連結標的之收盤價高於或等於其執行價：",
        "(b)實物交割：若於期末定價日時籃子中表現最差的連結標的之收盤價低於其執行價，以實物交割。",
    )

    pf = ms_wording.ki_type(ti, invalid_note="找不到")

    assert (pf.status, pf.value) == (FieldStatus.PRESENT, "none") and len(pf.evidence) == 2


def test_unknown_ki_wording_or_nothing_is_invalid_with_the_callers_note():
    unknown = text_index("觸及下限事件：若在任一交易日，籃子中任一連結標的之收盤價低於其下限價格，")
    nothing = text_index("到期日以現金結算收益。")

    assert ms_wording.ki_type(unknown, invalid_note="找不到").note == "觸及下限事件的觀察寫法不在範本規格內"
    pf = ms_wording.ki_type(nothing, invalid_note="找不到觸及下限事件定義，也不是無 KI 的到期贖回寫法")
    assert pf.status == FieldStatus.INVALID and pf.note == "找不到觸及下限事件定義，也不是無 KI 的到期贖回寫法"


# ---------------------------------------------------------------- 記憶式與 KO 觀察寫法


def test_memory_needs_the_definition_sentence_only_when_the_caller_requires_it():
    only_observation = text_index("記憶事件觀察日：每一個定價日自第1 期定價日（含）開始觀察")
    both = text_index(
        "記憶事件：任何連結標的於記憶事件觀察日之收盤價大於或等於其自動提前出場價。",
        "記憶事件觀察日：每一個定價日自第1 個定價日開始觀察",
    )

    assert ms_wording.ko_memory(both, definition_required=True, invalid_note="n").value is True
    assert ms_wording.ko_memory(only_observation, definition_required=False, invalid_note="n").value is True, (
        "S04-IIS 沒有定義句"
    )
    pf = ms_wording.ko_memory(
        only_observation, definition_required=True, invalid_note="第 17 項記憶事件寫法缺漏或不在範本規格內"
    )
    assert pf.status == FieldStatus.INVALID and pf.note == "第 17 項記憶事件寫法缺漏或不在範本規格內"


@pytest.mark.parametrize(
    "sentence",
    [
        "自動提前出場事件：若於任一觀察日所有連結標的之收盤價皆等於或高於其自動提前出場價，則發生自動提前出場事件。",
        "自動提前出場事件：若於任一觀察日該連結標的之收盤價大於或等於其自動提前出場價，則發生自動提前出場事件。",
        "自動提前出場事件：自第1 個定價日（含）開始，若於任一定價日所有連結標的皆等於或高於其自動提前出場價，則視同發生。",
    ],
)
def test_non_memory_wordings_of_both_documents_are_plain_ko(sentence):
    pf = ms_wording.ko_memory(text_index(sentence), definition_required=True, invalid_note="n")

    assert (pf.status, pf.value) == (FieldStatus.PRESENT, False)


def test_memory_and_plain_sentences_together_or_neither_is_invalid():
    neither = text_index("自動提前出場日：自動提前出場事件發生日後的第3 個營業日。")
    together = text_index(
        "記憶事件觀察日：每一個定價日自第1 期定價日（含）開始觀察",
        "自動提前出場事件：若於任一觀察日該連結標的之收盤價大於或等於其自動提前出場價。",
    )

    assert ms_wording.ko_memory(neither, definition_required=False, invalid_note="缺漏").note == "缺漏"
    assert ms_wording.ko_memory(together, definition_required=False, invalid_note="缺漏").value is True, (
        "有記憶事件就不算非記憶式寫法"
    )
    only_plain_mentions_memory = text_index("記憶事件", "該連結標的之收盤價大於或等於其自動提前出場價")
    pf = ms_wording.ko_memory(only_plain_mentions_memory, definition_required=False, invalid_note="缺漏")
    assert pf.status == FieldStatus.INVALID and pf.note == "缺漏", "提到記憶事件卻沒有觀察日：兩種都不成立"


@pytest.mark.parametrize(
    ("sentence", "kind", "k"),
    [
        ("記憶事件觀察日：每一個定價日自第2 個定價日開始觀察，如當日非預定交易日，次一預定交易日為之。", "P", 2),
        ("記憶事件觀察日：每一個定價日自第2 期定價日（含）開始觀察，如當日非預定交易日，次一預定交易日為之。", "P", 2),
        (
            "自動提前出場事件：自第3 個定價日（含）開始，若於任一定價日所有連結標的皆等於或高於其自動提前出場價。",
            "P",
            3,
        ),
        (
            "自動提前出場事件：自第3 期定價日（含）開始，若於任一定價日該連結標的之收盤價大於或等於其自動提前出場價。",
            "P",
            3,
        ),
    ],
)
def test_periodic_ko_wordings_of_both_documents_give_the_first_callable_period(sentence, kind, k):
    [(m, found)] = ms_wording.ko_observations(text_index(sentence))

    assert (found, int(m[1])) == (kind, k)


@pytest.mark.parametrize("prefix", ["", "記憶事件"])
def test_daily_ko_wording_carries_the_observation_dates_and_accepts_the_memory_prefix(prefix):
    ti = text_index(
        f"{prefix}觀察日：每日觀察，為自第1 個配息週期終止日（2025年2月3日）（包含）至",
        "期末定價日（2025年7月1日）（包含）的任一共同預定交易日。",
    )

    [(m, kind)] = ms_wording.ko_observations(ti)

    assert kind == "D" and int(m[1]) == 1
    assert (parse_date(m[2]), parse_date(m[3])) == (dt.date(2025, 2, 3), dt.date(2025, 7, 1)), "日期由呼叫端轉換"
    assert len(ti.lines_for(m.start(), m.end())) == 2, "跨行的句子兩行都是證據"


def test_other_observation_wordings_are_not_in_the_table():
    assert ms_wording.ko_observations(text_index("觀察日：每月觀察")) == []
    assert ms_wording.ki_type(text_index("觸及下限事件」：若在期末定價日，"), invalid_note="x").note == "x", (
        "引號要成對"
    )


# ---------------------------------------------------------------- 商品種類


def test_product_type_depends_only_on_whether_there_are_two_or_more_underlyings():
    assert product_type_for(1) == PRODUCT_TYPES[False] == "股票或指數股票型基金連結結構型債券"
    assert product_type_for(2) == product_type_for(5) == PRODUCT_TYPES[True] == "股票與/或指數股票型基金連結結構型債券"
