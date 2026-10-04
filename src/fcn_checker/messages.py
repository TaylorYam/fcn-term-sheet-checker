"""錯訊：作業人員看到的每條問題，都由這裡產生一段看得懂的中文（Issue #71）。

只寫哪裡對不起來、兩邊各是多少，不判定是說明書錯還是參考條件表錯，也不出現 rule_id、reason_code 等程式代碼。
每條核對結果建立時就帶著項目（`Item`：中文名稱與預期值出處，Issue #91），這裡只依出處組句：

- 參考條件表且兩邊不同：「<項目>對不起來：參考條件表 <值>／說明書 <值>」；多格（例：比價日）逐格列出 Excel 欄名。
- 其他：「<項目>：<規則的中文說明>」，有雙方值時附上「（參考條件表／審查標準／預期 <值>／說明書 <值>）」；
  不比對值的項目（配對、範本、讀檔、寫檔、參考條件表表頭）只寫說明。
- 規則沒寫說明時，依原因與狀態給中文預設。

核對結果檔「錯誤清單」的錯訊與 PANEL 結果明細都用 `problem_message`。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from .schema import CheckResult, CheckStatus, ItemSource, column_label

STATUS_ZH = {
    CheckStatus.PASS: "通過",
    CheckStatus.MISMATCH: "不一致",
    CheckStatus.REVIEW_REQUIRED: "需人工覆核",
    CheckStatus.NOT_APPLICABLE: "不適用",
    CheckStatus.ERROR: "執行錯誤",
}

# 雙方值裡另一邊的稱呼
SOURCE_ZH = {
    ItemSource.REFERENCE: "參考條件表",
    ItemSource.STANDARD: "審查標準",
    ItemSource.EXPECTED: "預期",
}

REASON_ZH = {
    "value_mismatch": "兩邊的值不同",
    "document_inconsistent": "說明書不同地方的值不一致",
    "document_missing": "說明書抓不到此欄位",
    "document_ambiguous": "說明書出現多個不同的值",
    "document_invalid": "說明書的值無法辨識",
    "date_order": "日期順序不對",
    "period_count": "期數不同",
    "occurrence_count": "出現次數不對",
    "unexpected_error": "執行時發生非預期錯誤",
}
STATUS_DEFAULT = {
    CheckStatus.MISMATCH: "兩邊的值不同",
    CheckStatus.REVIEW_REQUIRED: "需要人工確認",
    CheckStatus.ERROR: "執行錯誤",
}
MAX_VALUE = 80  # 值太長（例：整段固定警語）只顯示前段，完整值見核對紀錄


def show(v: Any) -> str:
    """值的顯示：日期 ISO、清單以頓號分隔、None 為「—」；太長的值截斷。"""
    if v is None or v == "":
        text = "—"
    elif isinstance(v, bool):
        text = "是" if v else "否"
    elif isinstance(v, (dt.date, Decimal, int, float)):
        text = v.isoformat() if isinstance(v, dt.date) else str(v)
    elif isinstance(v, (list, tuple)):
        text = "、".join(show(x) for x in v)
    elif isinstance(v, dict):
        text = "；".join(f"{k}：{show(x)}" for k, x in v.items())
    else:
        text = str(v)
    text = text.replace("\n", " ")
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "…"


def _simple(v: Any) -> bool:
    """可以直接列出的值（文字、數字、日期及其清單）；表格列（dict）、排程等結構不列，避免出現內部欄位代碼。"""
    if isinstance(v, (list, tuple)):
        return all(_simple(x) for x in v)
    return v is None or isinstance(v, (str, int, float, Decimal, dt.date))


def _shows_values(r: CheckResult, detail: str) -> bool:
    """不比對值的項目，以及說明裡已寫出雙方值的，不另附值。"""
    if r.item.source == ItemSource.NONE:
        return False
    if not all(_simple(v) for v in (r.expected, r.actual)):
        return False
    return not all(show(v) in detail for v in (r.expected, r.actual))


def _detail(r: CheckResult) -> str:
    return r.message or REASON_ZH.get(r.reason_code) or STATUS_DEFAULT.get(r.status, "需要人工確認")


def problem_message(r: CheckResult) -> str:
    """一條問題的中文錯訊。"""
    where, source = r.item.name, r.item.source
    if source == ItemSource.REFERENCE and r.status == CheckStatus.MISMATCH:
        if isinstance(r.expected, dict) and isinstance(r.actual, dict):  # 多格（例：比價日）逐格列出
            return "；".join(
                f"{column_label(str(k))}對不起來：參考條件表 {show(v)}／說明書 {show(r.actual.get(k))}"
                for k, v in r.expected.items()
            )
        return f"{where}對不起來：參考條件表 {show(r.expected)}／說明書 {show(r.actual)}"
    detail = _detail(r)
    has_both = r.expected is not None and r.actual is not None
    values = ""
    if has_both and _shows_values(r, detail):
        values = f"（{SOURCE_ZH[source]} {show(r.expected)}／說明書 {show(r.actual)}）"
    head = "" if detail.startswith(where) else f"{where}："  # 說明已經以項目開頭時不重複
    return f"{head}{detail}{values}"
