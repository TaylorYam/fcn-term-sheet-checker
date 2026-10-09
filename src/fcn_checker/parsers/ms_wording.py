"""MS 條款句型表：說明書第 16、17 項與投資人須知「贖回價金之計算」段共用的觸及下限（KI）、自動提前出場（KO）觀察、
記憶式寫法與判斷（範本規格 docs/templates/ms-zh-product-description.md §4.1–4.4、ms-zh-iis.md §4.1–4.3；Issue #146）。

MS 改版只改這裡。句型比對一律去空白、括號統一全形；兩份文件的寫法只差「第k 期／個定價日」、「（含）」與
「觸及下限事件」有沒有「」，表內一併接受。兩份 parser 各自決定在哪一段文字判斷、以及讀不到時的說明文字。
"""

from __future__ import annotations

import re

from ..schema import ParsedField
from ..text import full_brackets
from .iis import DATE
from .layout import TextIndex

MEMORY_NAME = "（記憶式自動提前出場）"  # 中文名稱裡的記憶式字樣
MEMORY_DEFINITION = "記憶事件："  # 記憶事件定義句（投資人須知 S04-IIS 沒有，只有觀察日）
MEMORY_OBSERVATION = "記憶事件觀察日："
# 非記憶式的提前出場條件寫法：多標的（說明書／投資人須知各一種）、單一標的
NON_MEMORY_KO = ("所有連結標的之收盤價皆等於或高於", "所有連結標的皆等於或高於", "該連結標的之收盤價大於或等於")
# KO 觀察寫法：表型與第 1 組的第一個可提前出場期 k；D 型第 2、3 組另有觀察起訖日
KO_WORDINGS = (
    (
        rf"(?:記憶事件)?觀察日：每日觀察，為自第(\d+)個配息週期終止日（{DATE}）（包含）至期末定價日（{DATE}）（包含）",
        "D",
    ),
    (rf"{MEMORY_OBSERVATION}每一個定價日自第(\d+)(?:期|個)定價日(?:（含）)?開始觀察", "P"),
    (r"自動提前出場事件：自第(\d+)(?:期|個)定價日（含）開始，若於任一定價日", "P"),
)
KI_SENTENCE = re.compile(r"(?:「觸及下限事件」|觸及下限事件)：若在([^，]*?)，")  # 投資人須知有「」、說明書沒有
KI_WORDINGS = {
    "期末定價日": "AM",
    "交易日（含）至期末定價日（含）間的任一共同預定交易日": "D",
    "交易日（含）至期末定價日（含）間的任一預定交易日": "D",
    "任一配息週期終止日（含期末定價日）": "P",
}
NO_KI_SENTENCES = ("收盤價高於或等於其執行價", "收盤價低於其執行價")  # 無 KI 的到期贖回：現金／實物兩種


def ki_type(ti: TextIndex, *, invalid_note: str) -> ParsedField:
    """觸及下限事件定義句 → KI 型態；沒有這句且到期贖回只有 ≥／< 執行價兩種寫法時為無 KI（`none`）。

    寫法不在表內 → 不合法；兩者都不是 → 不合法，說明用呼叫端的 `invalid_note`。括號全半形在這裡統一，呼叫端不必先換。
    """
    name = "ki_type"
    text = full_brackets(ti.text)
    hits = []
    for m in KI_SENTENCE.finditer(text):
        lns = ti.lines_for(m.start(), m.end())
        if m[1] not in KI_WORDINGS:
            return ParsedField.invalid(name, lns, "觸及下限事件的觀察寫法不在範本規格內")
        hits.append((KI_WORDINGS[m[1]], lns))
    if hits:
        return ParsedField.from_hits(name, hits)
    if "觸及下限" not in text and all(s in text for s in NO_KI_SENTENCES):
        return ParsedField.present(name, "none", ti.lines)
    return ParsedField.invalid(name, ti.lines, invalid_note)


def ko_memory(ti: TextIndex, *, definition_required: bool, invalid_note: str) -> ParsedField:
    """記憶式（有記憶事件觀察日；`definition_required` 時還要有記憶事件定義句：說明書要、投資人須知不要）或
    非記憶式（沒有記憶事件、有非記憶式的提前出場條件）；兩者都成立或都不成立 → 不合法，說明用呼叫端的 `invalid_note`。"""
    text = full_brackets(ti.text)
    memory = MEMORY_OBSERVATION in text and (MEMORY_DEFINITION in text or not definition_required)
    plain = "記憶事件" not in text and any(p in text for p in NON_MEMORY_KO)
    if memory == plain:
        return ParsedField.invalid("ko_memory", ti.lines, invalid_note)
    return ParsedField.present("ko_memory", memory, ti.lines)


def ko_observations(ti: TextIndex) -> list[tuple[re.Match[str], str]]:
    """表內每個 KO 觀察寫法的命中與表型；位置可直接給 `ti.lines_for`（括號換字不改位置）。呼叫端要求剛好一個。"""
    text = full_brackets(ti.text)
    return [(m, kind) for pattern, kind in KO_WORDINGS for m in re.finditer(pattern, text)]
