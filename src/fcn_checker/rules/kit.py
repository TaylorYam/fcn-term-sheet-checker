"""核對規則的共用工具：規則的輸入（Context）、產生核對結果、缺值轉人工覆核、讀參考條件表的值、轉型與比對。

規則只接收標準化後的說明書欄位、參考條件表欄位與審查標準；不讀檔、不改來源值。
說明書欄位一律經 `read_standard` 以標準欄位名稱讀取（例如 `trade_date`、`strike_pct`）。
抓不到、歧義、未知值一律轉人工覆核，不猜值。
每筆結果都要帶項目（`Item`：中文名稱與預期值出處），名稱寫在規則旁；錯訊只依項目組句（Issue #91）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from ..config import IssuerStandard, ReferenceFormat, ReviewStandard
from ..investor_sheet import IisSheet
from ..orders.reference import OrderRecord
from ..schema import CheckResult, DocKind, Evidence, FieldStatus, Item, OrderValue, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import Occurrence, TermSheet, is_standard, not_provided


class RuleContext(Protocol):
    """兩種規則輸入共同的部分：結果工具（`standard_field`、`order_value`）只依賴這些。"""

    ts: TermSheet

    @property
    def issuer_std(self) -> IssuerStandard: ...

    @property
    def sheet_source(self) -> str: ...

    def sheet_field(self, key: str) -> OrderValue | None: ...


class _IssuerStandardOf:
    std: ReviewStandard
    issuer: str  # 上手代號；上手專屬的審查標準值由 issuer_std 依此解析

    @property
    def issuer_std(self) -> IssuerStandard:
        return self.std.for_issuer(self.issuer)


@dataclass
class Context(_IssuerStandardOf):
    """各上手共用的規則（參考條件表欄位、Non-Call、回填、審查標準）與投資人須知規則的輸入：被核對文件的讀出結果、
    參考條件表的列與格式設定。

    被核對的是哪份文件只在這裡講一次（`document`）：規則的訊息提到被核對的文件時用它的稱呼，
    錯訊的文件那一邊也依結果記下的文件種類組句。投資人須知（ADR 0007）另帶頁數、檔名前 12 碼與同商品說明書。
    """

    ts: TermSheet  # 被核對文件的讀出結果（投資人須知時是 IisSheet，同樣以 f／full_text 讀欄位）
    order: OrderRecord
    std: ReviewStandard
    fmt: ReferenceFormat
    issuer: str
    document: DocKind = DocKind.TERM_SHEET
    pages: int | None = None  # 投資人須知：PDF 頁數
    file_code: str = ""  # 投資人須知：檔名前 12 碼
    term_sheet: TermSheet | None = None  # 投資人須知：同商品說明書（這批沒有、讀不到或沒有配對成功時為 None）

    @property
    def sheet_source(self) -> str:
        return self.order.source

    def sheet_field(self, key: str) -> OrderValue | None:
        return self.order.fields.get(key)

    @property
    def sheet(self) -> IisSheet:
        """投資人須知的讀出結果（投資人須知規則用；說明書沒有）。"""
        if not isinstance(self.ts, IisSheet):
            raise TypeError("核對的不是投資人須知，沒有投資人須知讀出結果")
        return self.ts

    def provides(self, name: str) -> bool:
        """投資人須知範本有沒有這個欄位；不在其中的不核對。"""
        return self.sheet.provides(name)


@dataclass
class IssuerContext(_IssuerStandardOf):
    """上手說明書內部規則的輸入：讀出結果、審查標準、上手代號，不含參考條件表（ADR 0005）。

    唯一例外是上手在註冊表宣告的參考條件表欄位（`Issuer.reference_fields`），只有這些讀得到。
    """

    ts: TermSheet
    std: ReviewStandard
    issuer: str
    declared: Mapping[str, OrderValue | None]  # 上手宣告的參考條件表欄位 → 該列的值
    sheet_source: str

    def sheet_field(self, key: str) -> OrderValue | None:
        if key not in self.declared:
            raise ValueError(
                f"{self.issuer} 的說明書內部規則讀了未宣告的參考條件表欄位「{key}」（見 Issuer.reference_fields）"
            )
        return self.declared[key]


# ---------------------------------------------------------------- 兩家上手共用的項目名稱

PRICE_ITEM = {"initial": "進場價", "strike": "執行價", "ko": "KO價", "ki": "下限價"}  # 同 Excel 欄名 UL_n_<名稱>
PRICE_LABEL = {"strike": "執行價", "ko": "KO 價", "ki": "下限價（觸及生效價）"}  # 價格推算結果的欄位與說明書內部規則用
HEADER_PCT_ITEM = {
    "strike": "價格表執行價格欄頭百分比",
    "ko": "價格表 KO 價格欄頭百分比",
    "ki": "價格表 KI 價格欄頭百分比",
}


def price_item(n: int, col: str) -> str:
    """第 n 檔標的（從 1 起）某價格的項目名稱，例：UL_2 KO價。"""
    return f"UL_{n} {PRICE_ITEM[col]}"


# ---------------------------------------------------------------- 共用

Q4 = Decimal("0.0001")  # 價格與月配息率四捨五入到 4 位
Q2 = Decimal("0.01")  # 金額（情境配息與損益）四捨五入到 2 位


def next_weekday(d: dt.date) -> dt.date:
    """後 1 個平日（只排除週末；沒有假日曆）。"""
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def shown(value: Decimal, like: Decimal) -> Decimal:
    """依說明書顯示位數（`like` 的小數位數）四捨五入（half-up）。"""
    exp = like.as_tuple().exponent
    return value.quantize(Decimal(1).scaleb(exp), ROUND_HALF_UP) if isinstance(exp, int) else value


def read_standard(ts: TermSheet, name: str) -> ParsedField:
    """共用規則讀說明書欄位的唯一方式：只能讀標準欄位；上手 adapter 沒交出時視為缺漏，相關規則轉人工覆核。"""
    if not is_standard(name):
        raise ValueError(f"{name} 不是標準欄位；共用規則需要的欄位要先加進 standard_fields.STANDARD_FIELDS")
    try:
        return ts.f(name)
    except KeyError:  # adapter 未照契約實作 f 時也不讓整份說明書變成執行錯誤
        return not_provided(name)


def standard_field(ctx: RuleContext, name: str) -> ParsedField:
    return read_standard(ctx.ts, name)


def result(
    rule_id: str,
    field: str,
    status: S,
    *,
    item: Item,
    expected: Any = None,
    actual: Any = None,
    pf: ParsedField | None = None,
    ov: list[OrderValue | None] | None = None,
    reason: str = "",
    message: str = "",
    tolerance: str | None = None,
    evidence: list[Evidence] | None = None,
    document: DocKind = DocKind.TERM_SHEET,
) -> CheckResult:
    """`item` 必填：這筆結果在講哪一項、預期值從哪裡來；`document` 是這筆結果屬於哪份文件。"""
    return CheckResult(
        rule_id=rule_id,
        field=field,
        status=status,
        expected=expected,
        actual=actual,
        tolerance=tolerance,
        reason_code=reason,
        message=message,
        document_evidence=list(evidence if evidence is not None else (pf.evidence if pf else [])),
        order_source=[o.source for o in (ov or []) if o is not None],
        item=item,
        document=document,
    )


DOC_REASON = {  # 說明前面接被核對文件的稱呼（例：說明書抓不到此欄位）
    FieldStatus.MISSING: ("document_missing", "抓不到此欄位"),
    FieldStatus.AMBIGUOUS: ("document_ambiguous", "出現多個不同的值"),
    FieldStatus.INVALID: ("document_invalid", "的值無法辨識"),
}


def doc_review(
    rule_id: str,
    field: str,
    pf: ParsedField,
    expected: Any = None,
    ov: list[OrderValue | None] | None = None,
    *,
    item: Item,
    document: DocKind = DocKind.TERM_SHEET,
) -> CheckResult:
    """被核對文件的欄位讀不到、歧義或不合法：轉人工覆核，說明以文件稱呼開頭。"""
    reason, msg = DOC_REASON.get(pf.status, ("document_not_applicable", "判定此欄位不適用"))
    detail = f"：{pf.note}" if pf.note else ""
    actual = pf.candidates or None
    return result(
        rule_id,
        field,
        S.REVIEW_REQUIRED,
        expected=expected,
        actual=actual,
        pf=pf,
        ov=ov,
        reason=reason,
        message=f"{document}{msg}{detail}",
        item=item,
        document=document,
    )


def doc_not_applicable(
    rule_id: str,
    field: str,
    pf: ParsedField,
    ov: list[OrderValue | None] | None = None,
    *,
    item: Item,
    document: DocKind = DocKind.TERM_SHEET,
) -> CheckResult:
    """被核對文件明確判定不適用的欄位（例：範本沒有、Non-Call = 天期時沒有 KO 價）：不核對，結果為不適用並附說明。"""
    return result(
        rule_id,
        field,
        S.NOT_APPLICABLE,
        expected=ov[0].value if ov and ov[0] is not None else None,
        pf=pf,
        ov=ov,
        message=pf.note or f"{document}判定此欄位不適用",
        item=item,
        document=document,
    )


def order_review(
    rule_id: str, field: str, ov: OrderValue | None, pf: ParsedField | None, reason: str, message: str, *, name: str
) -> CheckResult:
    """參考條件表的值有問題：項目是那一欄（以 Excel 欄名為名稱；沒有這欄時用 `name`）。"""
    return result(
        rule_id,
        field,
        S.REVIEW_REQUIRED,
        expected=ov.value if ov else None,
        actual=pf.value if pf and pf.ok else None,
        pf=pf,
        ov=[ov],
        reason=reason,
        message=message,
        item=Item.column(name, [ov]),
    )


def to_decimal(v: Any) -> Decimal | None:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, str):
        try:
            return Decimal(v.strip())
        except InvalidOperation:
            return None
    return None


def to_int(v: Any) -> int | None:
    d = to_decimal(v)
    if d is None or d != d.to_integral_value():
        return None
    return int(d)


def to_date(v: Any) -> dt.date | None:
    return v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else None


def order_value(
    ctx: RuleContext,
    key: str,
    rule_id: str,
    field: str,
    pf: ParsedField | None,
    convert: Callable[[Any], Any],
    what: str,
    *,
    name: str,
) -> tuple[Any, OrderValue | None, CheckResult | None]:
    """取得並轉換參考條件表欄位；缺漏或格式錯誤時回傳 REVIEW 結果（項目見 `order_review`）。"""
    ov = ctx.sheet_field(key)
    if ov is None or ov.value is None:
        return (
            None,
            ov,
            order_review(rule_id, field, ov, pf, "order_missing", f"{ctx.sheet_source}沒有此欄位或值為空白", name=name),
        )
    v = convert(ov.value)
    if v is None:
        problem = order_review(rule_id, field, ov, pf, "order_invalid", f"{ctx.sheet_source}的值不是{what}", name=name)
        return None, ov, problem
    return v, ov, None


KI_LABEL = {"none": "無 KI", "AM": "到期觀察", "D": "每日觀察", "P": "每期觀察", "M": "每月觀察（Monthly KI）"}


def doc_ki(pf: ParsedField) -> str | None:
    """說明書 KI 型態（標準欄位 `ki_type`）；無 KI 可用 NOT_APPLICABLE 狀態交出。抓不到或未知值回傳 None。"""
    if pf.status in (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE) and pf.value in KI_LABEL:
        return pf.value
    return None


def cmp_pct(order_v: Decimal, doc_v: Decimal) -> tuple[bool, Decimal]:
    """百分比：下單值依說明書顯示位數四捨五入（half-up）後比對。"""
    q = shown(order_v, doc_v)
    return q == doc_v, q


def occurrences_of(
    ctx: RuleContext, rid: str, name: str, item: Item
) -> tuple[tuple[Occurrence, ...], CheckResult | None]:
    """讀出處清單型的標準欄位；上手沒交出時回傳一筆人工覆核結果（項目為 `item`），範本沒有（不適用）時沒有出處也沒有結果。"""
    container = standard_field(ctx, name)
    if container.status == FieldStatus.NOT_APPLICABLE:
        return (), None
    if not container.ok:
        return (), doc_review(rid, name, container, item=item)
    return container.value, None
