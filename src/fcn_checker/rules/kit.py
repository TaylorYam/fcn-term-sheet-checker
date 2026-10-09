"""核對規則的共用工具：規則的輸入（Context）、產生核對結果、缺值轉人工覆核、讀參考條件表的值、轉型與比對。

規則只接收標準化後的說明書欄位、參考條件表欄位與審查標準；不讀檔、不改來源值。
說明書欄位一律經 `read_standard` 以標準欄位名稱讀取（例如 `trade_date`、`strike_pct`）。
抓不到、歧義、未知值一律轉人工覆核，不猜值。
每筆結果都要帶項目（`Item`：中文名稱與預期值出處），名稱寫在規則旁；錯訊只依項目組句（Issue #91）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from ..config import IssuerStandard, ReferenceFormat, ReviewStandard
from ..investor_sheet import IisSheet
from ..orders.reference import OrderRecord
from ..schema import CheckResult, DocKind, Evidence, FieldStatus, Item, OrderValue, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import Occurrence, TermSheet, is_standard, not_provided


class RuleContext(Protocol):
    """兩種規則輸入共同的部分：結果工具（`standard_field`、`order_value`、`occurrences_of`）只依賴這些。"""

    ts: TermSheet
    document: DocKind  # 被核對的文件，訊息提到它時用它的稱呼

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
    order: OrderRecord  # 參考條件表的列：值、Excel 欄名與儲存格位置（回填規則也從這裡讀）
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
        return self.order.get(key)

    @property
    def iis(self) -> IisSheet:
        """投資人須知的讀出結果（投資人須知規則用；說明書沒有）。"""
        if not isinstance(self.ts, IisSheet):
            raise TypeError("核對的不是投資人須知，沒有投資人須知讀出結果")
        return self.ts

    def provides(self, name: str) -> bool:
        """投資人須知範本有沒有這個欄位；不在其中的不核對。"""
        return self.iis.provides(name)


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
    document: DocKind = DocKind.TERM_SHEET  # 上手說明書內部規則只核對說明書

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


MISSING = object()  # 參數沒給（和明確給 None 分開）


@dataclass(frozen=True)
class Check:
    """一條核對結果的身分：規則、欄位、項目、所屬文件；掛上依賴的欄位後產生結果。

    規則的四段式只寫一次在這裡：依賴的欄位（`needs`）第一個有問題的就轉人工覆核（`blocked`，同 `doc_review`），
    都沒問題才比對（`compare`）；證據預設是全部依賴欄位的證據，參考條件表來源（`ov`）寫進 `order_source`。
    `expected` 是人工覆核時也要顯示的預期值（例：表上的值、審查標準）；`compare` 另給顯示用的預期值時以它為準。
    """

    rule_id: str
    field: str
    item: Item
    document: DocKind = DocKind.TERM_SHEET
    deps: tuple[ParsedField, ...] = ()
    ov: tuple[OrderValue | None, ...] = ()
    expected: Any = None

    def needs(
        self, *deps: ParsedField, ov: Sequence[OrderValue | None] | None = None, expected: Any = MISSING
    ) -> Check:
        """掛上依賴的欄位（依序），可一併給參考條件表來源與人工覆核時顯示的預期值。"""
        return replace(
            self,
            deps=(*self.deps, *deps),
            ov=self.ov if ov is None else tuple(ov),
            expected=self.expected if expected is MISSING else expected,
        )

    @property
    def blocked(self) -> CheckResult | None:
        """第一個有問題的依賴欄位的人工覆核結果；都沒問題時為 None。"""
        bad = next((pf for pf in self.deps if not pf.ok), None)
        return None if bad is None else self.review(bad)

    def review(self, pf: ParsedField, *, expected: Any = MISSING) -> CheckResult:
        """被核對文件的欄位讀不到、歧義或不合法：轉人工覆核，說明以文件稱呼開頭。"""
        reason, msg = DOC_REASON.get(pf.status, ("document_not_applicable", "判定此欄位不適用"))
        detail = f"：{pf.note}" if pf.note else ""
        return self.result(
            S.REVIEW_REQUIRED,
            expected=self.expected if expected is MISSING else expected,
            actual=pf.candidates or None,
            evidence=pf.evidence,
            reason=reason,
            message=f"{self.document}{msg}{detail}",
        )

    def not_applicable(self, pf: ParsedField) -> CheckResult:
        """被核對文件明確判定不適用的欄位（例：範本沒有、Non-Call = 天期時沒有 KO 價）：不核對，結果為不適用並附說明。"""
        first = self.ov[0] if self.ov else None
        return self.result(
            S.NOT_APPLICABLE,
            expected=first.value if first is not None else None,
            evidence=pf.evidence,
            message=pf.note or f"{self.document}判定此欄位不適用",
        )

    def result(
        self,
        status: S,
        *,
        expected: Any = None,
        actual: Any = None,
        reason: str = "",
        message: str = "",
        tolerance: str | None = None,
        evidence: Sequence[Evidence] | None = None,
    ) -> CheckResult:
        """任意狀態的結果；證據沒給時是全部依賴欄位的證據。"""
        if evidence is None:
            evidence = [e for pf in self.deps for e in pf.evidence]
        return CheckResult(
            rule_id=self.rule_id,
            field=self.field,
            status=status,
            expected=expected,
            actual=actual,
            tolerance=tolerance,
            reason_code=reason,
            message=message,
            document_evidence=list(evidence),
            order_source=[o.source for o in self.ov if o is not None],
            item=self.item,
            document=self.document,
        )

    def compare(
        self,
        expected: Any,
        actual: Any,
        *,
        ok: bool | None = None,
        fail: S = S.MISMATCH,
        reason: str = "value_mismatch",
        message: str = "",
        fail_message: str = "",
        tolerance: str | None = None,
        evidence: Sequence[Evidence] | None = None,
    ) -> CheckResult:
        """依賴欄位都沒問題時比對（預設相等，`ok` 另給判定）：成立為 PASS，否則為 `fail` 並附 `reason`；
        `message` 一律寫，`fail_message` 只在不成立時接在後面。"""
        if (problem := self.blocked) is not None:
            return problem
        good = expected == actual if ok is None else ok
        return self.result(
            S.PASS if good else fail,
            expected=expected,
            actual=actual,
            reason="" if good else reason,
            message=message if good else message + fail_message,
            tolerance=tolerance,
            evidence=evidence,
        )


def result(rule_id: str, field: str, status: S, *, item: Item, **detail: Any) -> CheckResult:
    """`Check(...).result(...)` 的捷徑（測試與舊呼叫用）：`detail` 同 `Check.result`，另可帶 `ov`、`pf`（證據來源）、`document`。"""
    pf = detail.pop("pf", None)
    if pf is not None and detail.get("evidence") is None:
        detail["evidence"] = pf.evidence
    check = Check(
        rule_id, field, item, detail.pop("document", DocKind.TERM_SHEET), ov=tuple(detail.pop("ov", None) or ())
    )
    return check.result(status, **detail)


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
    """`Check(...).review(pf)` 的捷徑：被核對文件的欄位讀不到、歧義或不合法，轉人工覆核。"""
    return Check(rule_id, field, item, document, ov=tuple(ov or ()), expected=expected).review(pf)


def doc_not_applicable(
    rule_id: str,
    field: str,
    pf: ParsedField,
    ov: list[OrderValue | None] | None = None,
    *,
    item: Item,
    document: DocKind = DocKind.TERM_SHEET,
) -> CheckResult:
    """`Check(...).not_applicable(pf)` 的捷徑。"""
    return Check(rule_id, field, item, document, ov=tuple(ov or ())).not_applicable(pf)


def order_review(
    rule_id: str, field: str, ov: OrderValue | None, pf: ParsedField | None, reason: str, message: str, *, name: str
) -> CheckResult:
    """參考條件表的值有問題：項目是那一欄（以 Excel 欄名為名稱；沒有這欄時用 `name`）。"""
    return Check(rule_id, field, Item.column(name, [ov]), ov=(ov,)).result(
        S.REVIEW_REQUIRED,
        expected=ov.value if ov else None,
        actual=pf.value if pf and pf.ok else None,
        evidence=pf.evidence if pf else [],
        reason=reason,
        message=message,
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
        return (), doc_review(rid, name, container, item=item, document=ctx.document)
    return container.value, None
