"""文字正規化：parser 與規則共用，不依賴任何一方。"""

from __future__ import annotations

import re


def squash(text: str) -> str:
    """移除所有空白（含換行）。"""
    return re.sub(r"\s+", "", text)
