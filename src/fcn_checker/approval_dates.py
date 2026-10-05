"""維護審查標準的審查通過日期清單（PANEL「審查通過日期…」，Issue #120）。

只能新增晚於最新一筆的日期，或修改／刪除最新一筆；較早的日期是已發生的審查，不能改。
寫回設定檔時只替換 `approval_dates`、`version`、`effective_date` 三行，其餘內容與註解不動；
寫入前先用新內容載入一次審查標準，確認格式正確才取代原檔。
"""

from __future__ import annotations

import datetime as dt
import os
import re
import tempfile
from pathlib import Path

from .config import load_review_standard
from .ingestion import IngestionError

_DATES = re.compile(r"^approval_dates[ \t]*=[ \t]*\[[^\]]*\]", re.M)
_VERSION = re.compile(r"^version[ \t]*=[ \t]*\d+[ \t]*(?=\r?$)", re.M)  # 設定檔可能是 CRLF
_EFFECTIVE = re.compile(r"^effective_date[ \t]*=[ \t]*\S+[ \t]*(?=\r?$)", re.M)


def _invalid(message: str) -> IngestionError:
    return IngestionError("approval_date_invalid", message)


def parse_date(text: str) -> dt.date:
    """PANEL 輸入的日期：YYYY-MM-DD 或 YYYY/MM/DD。"""
    try:
        return dt.date.fromisoformat(text.strip().replace("/", "-"))
    except ValueError:
        raise _invalid(f"「{text.strip()}」不是日期，請輸入 YYYY-MM-DD，例如 2026-12-10。") from None


def add(dates: tuple[dt.date, ...], new: dt.date) -> tuple[dt.date, ...]:
    """新增一筆；必須晚於目前最新的日期。"""
    if dates and new <= dates[-1]:
        raise _invalid(f"新的審查通過日期 {new} 必須晚於目前最新的 {dates[-1]}。")
    return (*dates, new)


def change_latest(dates: tuple[dt.date, ...], new: dt.date) -> tuple[dt.date, ...]:
    """修改最新一筆（修正打錯）；仍須晚於前一筆。"""
    if len(dates) >= 2 and new <= dates[-2]:
        raise _invalid(f"修改後的日期 {new} 必須晚於前一次的 {dates[-2]}；較早的日期不能修改。")
    return (*dates[:-1], new)


def remove_latest(dates: tuple[dt.date, ...]) -> tuple[dt.date, ...]:
    """刪除最新一筆；清單至少保留一筆。"""
    if len(dates) <= 1:
        raise _invalid("審查通過日期至少要保留一筆，不能刪除最後一筆。")
    return dates[:-1]


def _replace_once(pattern: re.Pattern[str], text: str, new: str, what: str) -> str:
    if len(pattern.findall(text)) != 1:
        raise _invalid(f"審查標準設定檔的 {what} 寫法和預期不同，無法自動修改；請找維護者直接修改設定檔。")
    return pattern.sub(lambda _: new, text)


def write(path: Path, dates: tuple[dt.date, ...], *, today: dt.date) -> int:
    """把日期清單寫回審查標準設定檔，version 加 1、effective_date 改為 today；回傳新的 version。"""
    path = Path(path)
    current = load_review_standard(path)  # 原檔有問題時先在這裡回報，不覆寫
    with path.open(encoding="utf-8", newline="") as f:
        text = f.read()
    version = current.version + 1
    text = _replace_once(
        _DATES, text, "approval_dates = [" + ", ".join(d.isoformat() for d in dates) + "]", "approval_dates"
    )
    text = _replace_once(_VERSION, text, f"version = {version}", "version")
    text = _replace_once(_EFFECTIVE, text, f"effective_date = {today.isoformat()}", "effective_date")
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            written = load_review_standard(Path(tmp))
            if (written.approval_dates, written.version, written.effective_date) != (
                tuple(sorted(dates)),
                version,
                today,
            ):
                raise _invalid("寫入後的審查標準內容和預期不同，已取消修改；請找維護者確認設定檔。")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except OSError as e:
        raise _invalid(f"無法寫入審查標準設定檔 {path}：{e.strerror or e}。請確認檔案沒有被其他程式鎖住後再試。") from e
    return version
