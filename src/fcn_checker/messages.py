"""錯訊：作業人員看到的每條問題，都由這裡產生一段看得懂的中文（Issue #71）。

只寫哪裡對不起來、兩邊各是多少，不判定是說明書錯還是參考條件表錯，也不出現 rule_id、reason_code 等程式代碼。

- 參考條件表欄位與回填欄位：「<Excel 欄名>對不起來：參考條件表 <值>／說明書 <值>」。
- 其他類別（審查標準、說明書內部一致性、配對、未支援上手、讀檔錯誤）：「<項目>：<規則的中文說明>」，有雙方值時附上。
- 規則沒寫說明時，依原因與狀態給中文預設；不認得的欄位名稱改用規則類別的中文名稱。

核對結果檔「錯誤清單」的錯訊與 PANEL 結果明細都用 `problem_message`。
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

from .schema import CheckResult, CheckStatus

STATUS_ZH = {
    CheckStatus.PASS: "通過",
    CheckStatus.MISMATCH: "不一致",
    CheckStatus.REVIEW_REQUIRED: "需人工覆核",
    CheckStatus.NOT_APPLICABLE: "不適用",
    CheckStatus.ERROR: "執行錯誤",
}

FIELD_ZH = {
    "template": "範本",
    "issuer": "上手",
    "說明書": "說明書",
    "product_code": "商品代號",
    "isin": "ISIN Code",
    "currency": "幣別",
    "underlyings": "標的彭博代號",
    "strike_pct": "執行 %",
    "ko_pct": "KO %",
    "ko_type": "KO 觀察方式／記憶式",
    "ko_observation": "KO 觀察方式",
    "ko_memory": "記憶式",
    "ki_type": "KI 型態",
    "ki_pct": "KI %",
    "coupon_pa_pct": "年利率 %",
    "monthly_coupon_pct": "月配息率 %",
    "tenor_months": "天期（月）",
    "trade_date": "交易日",
    "issue_date": "發行日",
    "final_valuation_date": "最終評價日",
    "maturity_date": "到期日",
    "first_callable_period": "第一個可提前出場期",
    "compare_dates": "比價日",
    "issue_date_offset_days": "發行日 − 交易日（天）",
    "price_table": "價格表",
    "prices": "價格表",
    "scenario_price_table": "情境試算價格表",
    "denomination": "面額",
    "subscription_start_date": "開始受理申購日",
    "print_date": "刊印日期",
    "approval_date": "受託機構審查通過日期",
    "chairman": "受託機構負責人",
    "fixed_warning": "固定風險警語",
    "risk_level": "風險等級",
    "forbidden_wording": "禁用語「受託投資」",
    "name_zh": "中文商品名稱",
    "name_en": "英文商品名稱",
    "art1_name": "第一章第 1 條商品名稱",
    "issuer_name_cover": "封面發行機構名稱",
    "issuer_name_ch2": "第二章發行機構名稱",
    "distributor_name_cover": "封面受託或銷售機構名稱",
    "distributor_phone_cover": "封面受託或銷售機構電話",
    "distributor_address_cover": "封面受託或銷售機構地址",
    "distributor_name_ch2": "第二章受託或銷售機構名稱",
    "distributor_address_ch2": "第二章受託或銷售機構地址",
    "issue_price_pct": "發行價格",
    "coupon_table": "配息表",
    "coupon_periods": "配息期數",
    "ko_table": "提前出場表",
    "schedule": "配息表與提前出場表",
    "trigger": "提前出場觸發價格百分比",
    "observation_t_ranges": "自動提前出場觀察期",
    "headers": "情境試算價格表欄頭",
    "scenario": "情境試算",
    "initial_investment": "情境試算期初投資金額",
    "scenario_notional": "情境假設每單位面額",
    "scenario_favourable_total": "有利情況總報酬率",
    "scenario_favourable_annualized": "有利情況平均年化報酬率",
    "scenario_general_total": "一般情況總報酬率",
    "scenario_general_annualized": "一般情況平均年化報酬率",
    "art5_currency": "第一章第 5 條計價幣別",
    "currency_art5": "第一章第 5 條計價幣別",
    "distributor_product_code": "受託或銷售機構商品代號",
    "title_name": "封面標題商品名稱",
    "name_title": "封面標題商品名稱",
    "name_en_title": "封面標題英文商品名稱",
    "name_art1": "第一章第 1 條商品名稱",
    "coupon_date_order": "配息表日期順序",
    "payment": "配息支付日",
    "last_valuation": "末期評價日",
    "last_payment": "末期支付日",
    "start": "提前出場期始日",
    "end": "提前出場期末日",
    "early_redemption": "指定提前現金贖回日",
    "daily": "期間每日觀察的提前出場表",
    "periodic": "定期觀察的提前出場表",
    "strike": "價格表執行價格欄頭百分比",
    "ko": "價格表 KO 價格欄頭百分比",
    "ki": "價格表 KI 價格欄頭百分比",
    "min_subscription": "最低申購金額",
    "min_redemption": "最低贖回商品面額",
    "minimum_trade": "最低交易金額",
    "minimum_subscription": "最低申購金額",
    "minimum_additional": "最低加購金額",
    "subscription_start": "開始受理申購日",
    "subscription_end": "申購結束受理日",
    "print_date_review": "刊印日期（參考性審閱版）",
    "print_date_final": "刊印日期（最終版）",
}
PRICE_ZH = {"initial": "進場價", "strike": "執行價", "ki": "下限價", "ko": "KO價"}
_PRICE = re.compile(r"underlying_(\d+)_([a-z]+)_price")

# 情境試算欄位：`s<n>.<項目>` 或 `assumption.<項目>`（可能帶流水號）
SCENARIO_ZH = {
    "tenor": "天期",
    "denomination": "每單位面額",
    "monthly": "固定配息率",
    "periods": "配息期數",
    "issue_price": "發行價格",
    "coupon_count": "配息次數",
    "period_range": "計息期間",
    "principal": "本金給付金額",
    "principal_formula": "本金給付公式",
    "coupon_notional": "配息計算面額",
    "coupon_monthly": "配息率",
    "coupon_amount": "配息金額",
    "coupon_formula": "配息公式",
    "fraction": "部分期間比例",
    "formula_periods": "配息公式期數",
    "total_coupon": "配息總額",
    "total_periods": "配息總期數",
    "reference_strike": "執行價",
    "reference_ki": "觸及不保本價格",
    "profit": "損益金額",
    "annualized": "平均年化報酬率",
}
_SCENARIO = re.compile(r"(?:s(\d+)|assumption)\.([a-z_]+)(?:\.\d+)?")

# 規則類別（rule_id 前綴）→ 中文名稱；欄位名稱不認得時用它
CATEGORY_ZH = {
    "field": "參考條件表欄位",
    "backfill": "回填欄位",
    "order": "參考條件表欄名",
    "standard": "審查標準",
    "doc": "說明書內部一致性",
    "derive": "說明書內部一致性",
    "schedule": "配息表與提前出場表",
    "batch": "配對",
    "template": "範本",
    "input": "讀檔",
    "output": "寫檔",
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


def _category(r: CheckResult) -> str:
    return r.rule_id.split(".", 1)[0]


def _sheet_side(r: CheckResult) -> bool:
    """參考條件表欄位與回填欄位：一邊是參考條件表的值。"""
    return _category(r) in ("field", "backfill")


def _looks_like_code(s: str) -> bool:
    return not re.search(r"[一-鿿]", s)  # 不含中文字


def column_label(column: str) -> str:
    """Excel 欄名的顯示：`UL_2_進場價` → `UL_2 進場價`，其他照原欄名。"""
    return re.sub(r"^(UL_\d+)_", r"\1 ", column)


def subject(r: CheckResult) -> str:
    """問題所在：參考條件表欄名、欄位中文名稱，或規則類別的中文名稱。"""
    if r.column and (_sheet_side(r) or r.reason_code.startswith("order_")):  # 參考條件表的值有問題：指出哪一欄
        return column_label(r.column)
    field = re.sub(r"\.\d+$", "", r.field)  # 去掉流水號（例：s1.profit.17）
    if field in FIELD_ZH:
        return FIELD_ZH[field]
    m = _SCENARIO.fullmatch(field)
    if m and m[2] in SCENARIO_ZH:
        return (f"情境 {m[1]} " if m[1] else "情境假設") + SCENARIO_ZH[m[2]]
    m = _PRICE.fullmatch(field)
    if m and m[2] in PRICE_ZH:
        return f"UL_{m[1]} {PRICE_ZH[m[2]]}"
    if r.field and not _looks_like_code(r.field):
        return r.field
    return CATEGORY_ZH.get(_category(r), "核對項目")


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


def _left(r: CheckResult) -> str:
    """另一邊的值是哪裡來的。"""
    if _sheet_side(r) or r.order_source:
        return "參考條件表"
    if _category(r) == "standard":
        return "審查標準"
    return "預期"


def _simple(v: Any) -> bool:
    """可以直接列出的值；表格列（dict）等結構不列，避免出現內部欄位代碼。"""
    if isinstance(v, dict):
        return False
    return not isinstance(v, (list, tuple)) or all(_simple(x) for x in v)


def _shows_values(r: CheckResult) -> bool:
    """配對、範本、讀檔、欄名等問題，以及說明裡已寫出雙方值的，不另附值。"""
    if _category(r) in ("batch", "template", "input", "output", "order"):
        return False
    if not all(_simple(v) for v in (r.expected, r.actual)):
        return False
    detail = _detail(r)
    return not all(show(v) in detail for v in (r.expected, r.actual))


def _detail(r: CheckResult) -> str:
    return r.message or REASON_ZH.get(r.reason_code) or STATUS_DEFAULT.get(r.status, "需要人工確認")


def problem_message(r: CheckResult) -> str:
    """一條問題的中文錯訊。"""
    where = subject(r)
    has_both = r.expected is not None and r.actual is not None
    if _sheet_side(r) and r.status == CheckStatus.MISMATCH:
        if isinstance(r.expected, dict) and isinstance(r.actual, dict):  # 多格（例：比價日）逐格列出
            return "；".join(
                f"{column_label(str(k))}對不起來：參考條件表 {show(v)}／說明書 {show(r.actual.get(k))}"
                for k, v in r.expected.items()
            )
        return f"{where}對不起來：參考條件表 {show(r.expected)}／說明書 {show(r.actual)}"
    values = f"（{_left(r)} {show(r.expected)}／說明書 {show(r.actual)}）" if has_both and _shows_values(r) else ""
    detail = _detail(r)
    head = "" if detail.startswith(where) else f"{where}："  # 說明已經以項目開頭時不重複
    return f"{head}{detail}{values}"
