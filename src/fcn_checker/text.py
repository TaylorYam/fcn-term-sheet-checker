"""文字正規化：parser 與規則共用，不依賴任何一方。"""

from __future__ import annotations

import re

_FULL_BRACKETS = str.maketrans({"(": "（", ")": "）"})


def squash(text: str) -> str:
    """移除所有空白（含換行）。"""
    return re.sub(r"\s+", "", text)


def full_brackets(text: str) -> str:
    """半形括號換成全形，比對時括號全半形不計。"""
    return text.translate(_FULL_BRACKETS)
