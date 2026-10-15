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
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, NamedTuple, Protocol

from ..config import IssuerStandard, ReferenceFormat, ReviewStandard
from ..investor_sheet import IisSheet
from ..orders.reference import OrderRecord
from ..schema import CheckResult, DocKind, Evidence, FieldStatus, Item, OrderValue, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import Occurrence, TermSheet, is_standard, not_provided
from ..trading_calendar import TradingCalendar


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
    """上手說明書內部規則的輸入：讀出結果、審查標準、上手代號，不含參考條件表（ADR 0009）。

    唯一例外是上手在註冊表宣告的參考條件表欄位（`Issuer.reference_fields`），只有這些讀得到。
    """

    ts: TermSheet
    std: ReviewStandard
    issuer: str
    declared: Mapping[str, OrderValue | None]  # 上手宣告的參考條件表欄位 → 該列的值
    sheet_source: str
    calendar: TradingCalendar  # 交易所假日表：推算一籃子預定交易日（Issue #208）
    document: DocKind = DocKind.TERM_SHEET  # 上手說明書內部規則只核對說明書
    pages: int | None = None  # 說明書 PDF 頁數（含沒有文字層的頁；頁首總頁數規則用）

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


def monthly_of_annual(annual: Decimal) -> Decimal:
    """月配息率 = 年利率 ÷ 12，四捨五入（half-up）到 4 位：GS、Natixis 說明書的月配息率推算（Issue #248、#261）。"""
    return (annual / 12).quantize(Q4, ROUND_HALF_UP)


def annual_of_period(period: Decimal) -> Decimal:
    """每期（每月）配息率 × 12，四捨五入（half-up）到 2 位：Nomura 說明書與投資人須知的年利率推算（Issue #239）。"""
    return (period * 12).quantize(Q2, ROUND_HALF_UP)


TRADING_DAY_TOLERANCE = "一籃子預定交易日：跳過週末與所有標的交易所的休市日（交易所假日表）"


UNCERTAIN_TRADING_DAY = "trading_calendar_uncertain"  # 原因碼：無法確認期始日是否因交易所假日順延


class StartVerdict(NamedTuple):
    """一個期始日的判定：通過、不一致或需人工覆核，以及不通過時的說明（「應為 <日期>」，無法確認時附原因）。"""

    status: S
    note: str = ""


def period_start(ctx: IssuerContext, prev_end: dt.date, start: dt.date) -> StartVerdict:
    """提前出場觀察期的期始日 vs 前一期期末日後的下一個一籃子預定交易日（標的取自標準欄位 `underlyings`，Issue #208）。

    相等 → 通過。假日只會讓期始日往後延，所以比推算結果早、或落在週末或已知休市日，一定不一致；
    比推算結果晚、而推算結果無法確定（讀不到標的、標的交易所不在假日表、超出涵蓋範圍）時轉人工覆核。"""
    u = ctx.ts.f("underlyings")
    tickers = u.value if u.ok else None
    following = ctx.calendar.next_trading_day(prev_end, tickers)
    if start == following.day:
        return StartVerdict(S.PASS)
    note = f"應為 {following.day}"
    if following.uncertain and start > following.day and not ctx.calendar.closed(start, tickers):
        return StartVerdict(S.REVIEW_REQUIRED, f"{note}（{following.uncertain}）")
    return StartVerdict(S.MISMATCH, note)


def uncertain_start(check: Check, notes: Sequence[str], **detail: Any) -> CheckResult:
    """期始日都只是無法確認（沒有確定不一致的）：轉人工覆核並寫出各期應為哪一天與原因；`detail` 同 `Check.result`。"""
    return check.result(
        S.REVIEW_REQUIRED,
        reason=UNCERTAIN_TRADING_DAY,
        message="無法確認期始日是否因交易所假日順延：" + "；".join(notes),
        tolerance=TRADING_DAY_TOLERANCE,
        **detail,
    )


def next_weekday(d: dt.date) -> dt.date:
    """後 1 個平日（只排除週末；沒有假日曆）。交易所交易日用 `next_trading_day`。"""
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def shown(value: Decimal, like: Decimal) -> Decimal:
    """依說明書顯示位數（`like` 的小數位數）四捨五入（half-up）。"""
    exp = like.as_tuple().exponent
    return value.quantize(Decimal(1).scaleb(exp), ROUND_HALF_UP) if isinstance(exp, int) else value


SCENARIO_PRICE_ROUNDING = "等於標的表，或期初價格 × % 依文件位數四捨五入／無條件捨去"


def scenario_price_ok(actual: Decimal, table: Decimal, initial: Decimal | None, pct: Decimal | None) -> bool:
    """情境文字的執行價／下限價（Issue #259）：等於標的表的值，或等於期初價格 × `pct`% 依 `actual` 的位數四捨五入
    或無條件捨去（期初 × % 剛好在 5 時，有發行機構表上四捨五入、情境捨去）。`initial`／`pct` 讀不到時只看標的表。"""
    if actual == table:
        return True
    exp = actual.as_tuple().exponent
    if initial is None or pct is None or not isinstance(exp, int):
        return False
    exact, step = initial * pct / 100, Decimal(1).scaleb(exp)
    return actual in (exact.quantize(step, ROUND_HALF_UP), exact.quantize(step, ROUND_DOWN))


def field_value(ts: TermSheet, name: str) -> Any:
    """標準欄位讀得到時的值，否則 None（只用來輔助判斷，讀不到不另外報）。"""
    pf = read_standard(ts, name)
    return pf.value if pf.ok else None


def cumulative_coupon_issues(
    label: str, actual: Decimal, per: Decimal, periods: int, base: Decimal, rate: Decimal, step: Decimal
) -> list[str]:
    """累積配息兩種算法都接受（Issue #244；發行機構同一份說明書裡兩種都用）：四捨五入後的每期配息 `per` × 期數，
    或本金 `base` × 月配息率 `rate`% × 期數再四捨五入到每期配息的位數 `step`。兩種都不符時錯訊列出兩個預期值。"""
    rounded = per * periods
    exact = (base * rate / 100 * periods).quantize(step, ROUND_HALF_UP)
    if actual in (rounded, exact):
        return []
    other = "" if exact == rounded else f"，或 {base} × {rate}% × {periods} 期 = {exact}"
    return [f"{label} {actual} 應為每期配息 {per} × {periods} 期 = {rounded}{other}"]


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

    def needs(self, *deps: ParsedField) -> Check:
        """掛上依賴的欄位（依序；第一個有問題的決定人工覆核的原因）。"""
        return replace(self, deps=(*self.deps, *deps))

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
        """被核對文件明確判定不適用的欄位（例：範本沒有）：不核對，結果為不適用並附說明。"""
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
    """`Check(...).result(...)` 的捷徑，給結果身分只用一次的呼叫端（backfill、batch、測試）：`detail` 同 `Check.result`，
    另可帶 `ov`、`pf`（證據來源）、`document`。規則檔一律直接用 `Check`。"""
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
    """`Check(...).review(pf)` 的捷徑（結果身分只用一次的呼叫端）：被核對文件的欄位讀不到、歧義或不合法，轉人工覆核。"""
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


# ---------------------------------------------------------------- 上手說明書內部規則共用（MS、BNP；Issue #196）


def first_bad(deps: Sequence[ParsedField]) -> ParsedField | None:
    """依賴欄位中第一個讀不到、歧義或不合法的；全部可用時為 None。"""
    return next((p for p in deps if not p.ok), None)


def doc_check(ctx: IssuerContext, rid: str, field: str, name: str, *deps: ParsedField) -> Check:
    """文件內部比對的結果身分：項目名稱為 `name`（預期值來自同一份文件其他位置），依賴欄位有問題轉人工覆核。"""
    return Check(rid, field, Item.expected(name), ctx.document).needs(*deps)


def structure_check(
    ctx: IssuerContext, rid: str, field: str, name: str, deps, issues: Sequence[str], tolerance: str | None = None
) -> CheckResult:
    """結構檢查（例：日期表）：`issues` 為不成立的說明，全部成立才通過。"""
    return doc_check(ctx, rid, field, name, *deps).compare(
        None, None, ok=not issues, reason="schedule_inconsistent", message="；".join(issues), tolerance=tolerance
    )


def evidence(lines) -> list[Evidence]:
    """原文行 → 證據。"""
    return [Evidence.of(ln) for ln in lines]


def calc_result(
    rid: str,
    field: str,
    item: Item | str,
    issues: Sequence[str],
    evidence: list[Evidence],
    *,
    review: bool = False,
    expected: Any = None,
    actual: Any = None,
    ov: Sequence[OrderValue | None] = (),
) -> CheckResult:
    """算式驗算的結果：`item` 為項目（或預期值來自說明書時的項目名稱）；`issues` 為不成立的說明；
    `review` 表示寫法未知（轉人工覆核）而不是算錯。"""
    item = Item.expected(item) if isinstance(item, str) else item
    return Check(rid, field, item, ov=tuple(ov)).compare(
        expected,
        actual,
        ok=not issues,
        fail=S.REVIEW_REQUIRED if review else S.MISMATCH,
        reason="scenario_unknown_wording" if review else "value_mismatch",
        message="；".join(issues),
        evidence=evidence,
    )


def annual_rate(ctx: IssuerContext, rid: str, field: str) -> tuple[Any, OrderValue | None, CheckResult | None]:
    """參考條件表年利率（上手以 reference_fields 宣告）；（值, 儲存格, 有問題時的人工覆核結果）。"""
    return order_value(ctx, "coupon_pa_pct", rid, field, None, to_decimal, "數字", name="年利率 %")


def rate_mentions(
    ctx: IssuerContext,
    rid: str,
    field: str,
    what: str,
    mentions,
    compare: Callable[[Decimal, Decimal], tuple[bool, Decimal]],
    tolerance: str,
    message: Callable[[Decimal], str],
) -> list[CheckResult]:
    """說明書每個出處（`parsers.common.Mention`）一筆，與參考條件表年利率推得的值比對；讀不到或出現多個轉人工覆核。

    `compare(年利率, 說明書值)` 回傳（是否一致, 顯示的預期值）；`message(年利率)` 為推算說明。
    """
    annual, ov, problem = annual_rate(ctx, rid, field)
    if problem:
        return [problem]
    out = []
    for m in mentions:
        check = Check(rid, field, Item.derived(f"{what} %（{m.where}）", [ov]), ctx.document, ov=(ov,))
        if m.value is None:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    evidence=list(m.evidence),
                    reason="document_missing",
                    message=f"說明書{m.where}找不到{what}或出現多個",
                )
            )
            continue
        ok, shown_value = compare(annual, m.value)
        out.append(
            check.compare(
                shown_value, m.value, ok=ok, evidence=list(m.evidence), tolerance=tolerance, message=message(annual)
            )
        )
    return out


def annualized_return(ctx: IssuerContext) -> list[CheckResult]:
    """獲利情境每處年化報酬率 = 參考條件表年利率（依說明書位數四捨五入）；出處由上手讀出結果的
    `annualized_mentions` 交出（MS：標題不含「較差」的情境；BNP：自動提前贖回、最佳情況）。"""
    return rate_mentions(
        ctx,
        "derive.annualized_return",
        "annualized_return_pct",
        "年化報酬率",
        ctx.ts.annualized_mentions,
        cmp_pct,
        "依說明書顯示位數四捨五入後比對",
        lambda annual: "獲利情境的年化報酬率應等於年利率",
    )
