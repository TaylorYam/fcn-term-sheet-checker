"""本機真實樣本的位置：資料夾結構與檔名只定義在這裡（Issue #128）。

根目錄預設是專案的 `data/`（被 Git 忽略），可用環境變數 `FCN_TEST_DATA_DIR` 換到其他資料夾，底下結構不變：

    orders/FCN參考條件.xlsx          完整參考條件表
    orders/FCN參考條件_待回補.xlsx   同一張表，回填欄位與 VWAP 商品的價格欄空白
    term-sheets/                     說明書與投資人須知（<商品代號>_TS.pdf、<商品代號>_IIS.pdf）

缺檔時測試略過，略過原因寫出缺的路徑；CI 沒有 data/，一律略過。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("FCN_TEST_DATA_DIR", ROOT / "data"))
ORDERS = DATA / "orders"
TERM_SHEETS = DATA / "term-sheets"
REFERENCE = ORDERS / "FCN參考條件.xlsx"
TO_FILL = ORDERS / "FCN參考條件_待回補.xlsx"


def pdfs(pattern: str = "*.pdf") -> list[Path]:
    return sorted(TERM_SHEETS.glob(pattern)) if TERM_SHEETS.is_dir() else []


def term_sheets(pattern: str = "*.pdf") -> list[Path]:
    """說明書（投資人須知 _IIS 不是說明書）。"""
    return [p for p in pdfs(pattern) if "_IIS" not in p.name]


def requires(*paths: Path) -> pytest.MarkDecorator:
    """缺任何一個就略過。檔名含 `*` 時，資料夾裡至少要有一個符合的檔案。"""
    gone = [p for p in paths if not _present(p)]
    shown = "、".join(_shown(p) for p in gone)
    return pytest.mark.skipif(bool(gone), reason=f"本機沒有真實樣本：缺 {shown}（data/ 被 Git 忽略）")


def _present(path: Path) -> bool:
    if "*" in path.name:
        return path.parent.is_dir() and any(path.parent.glob(path.name))
    return path.is_file()


def _shown(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
