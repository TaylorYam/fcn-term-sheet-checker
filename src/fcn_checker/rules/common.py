"""各上手共用的核對規則與工具（docs/rules/review-standard.md 及各上手核對規則）。

規則只接收標準化後的說明書欄位、下單欄位與審查標準；不讀檔、不改來源值。
說明書欄位一律使用標準欄位名稱（例如 `trade_date`、`strike_pct`），上手專屬規則放在各上手模組。
抓不到、歧義、未知值一律轉人工覆核，不猜值。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from ..config import OrderFormat, ReviewStandard
from ..orders.inquiry import OrderRecord
from ..parsers.layout import TextIndex, squash
from ..schema import CheckResult, Evidence, FieldStatus, OrderValue, ParsedField
from ..schema import CheckStatus as S


class TermSheet(Protocol):
    """各上手 parser 的擷取結果：標準欄位與全文索引。"""

    full_text: TextIndex

    def f(self, name: str) -> ParsedField: ...


@dataclass
class Context:
    ts: TermSheet
    order: OrderRecord
    std: ReviewStandard
    fmt: OrderFormat


# ---------------------------------------------------------------- 共用


def result(
    rule_id: str,
    field: str,
    status: S,
    *,
    expected: Any = None,
    actual: Any = None,
    pf: ParsedField | None = None,
    ov: list[OrderValue | None] | None = None,
    reason: str = "",
    message: str = "",
    tolerance: str | None = None,
    evidence: list[Evidence] | None = None,
) -> CheckResult:
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
    )


DOC_REASON = {
    FieldStatus.MISSING: ("document_missing", "說明書抓不到此欄位"),
    FieldStatus.AMBIGUOUS: ("document_ambiguous", "說明書出現多個不同的值"),
    FieldStatus.INVALID: ("document_invalid", "說明書的值無法辨識"),
}


def doc_review(
    rule_id: str, field: str, pf: ParsedField, expected: Any = None, ov: list[OrderValue | None] | None = None
) -> CheckResult:
    reason, msg = DOC_REASON.get(pf.status, ("document_not_applicable", "說明書判定此欄位不適用"))
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
        message=msg + detail,
    )


def order_review(
    rule_id: str, field: str, ov: OrderValue | None, pf: ParsedField | None, reason: str, message: str
) -> CheckResult:
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
    ctx: Context, key: str, rule_id: str, field: str, pf: ParsedField | None, convert: Callable[[Any], Any], what: str
) -> tuple[Any, OrderValue | None, CheckResult | None]:
    """取得並轉換下單欄位；缺漏或格式錯誤時回傳 REVIEW 結果。"""
    ov = ctx.order.fields.get(key)
    if ov is None or ov.value is None:
        return None, ov, order_review(rule_id, field, ov, pf, "order_missing", "詢價表沒有此欄位或值為空白")
    v = convert(ov.value)
    if v is None:
        return None, ov, order_review(rule_id, field, ov, pf, "order_invalid", f"詢價表的值不是{what}")
    return v, ov, None


def cmp_pct(order_v: Decimal, doc_v: Decimal) -> tuple[bool, Decimal]:
    """百分比：下單值依說明書顯示位數四捨五入（half-up）後比對。"""
    exp = doc_v.as_tuple().exponent
    q = order_v.quantize(Decimal(1).scaleb(exp), ROUND_HALF_UP) if isinstance(exp, int) else order_v
    return q == doc_v, q


# ---------------------------------------------------------------- 範本與格式


def order_format_checks(order: OrderRecord) -> list[CheckResult]:
    out = []
    for col in order.unknown_columns:
        out.append(
            result(
                "order.unknown_column",
                "詢價表欄位",
                S.REVIEW_REQUIRED,
                expected=None,
                actual=None,
                ov=[col],
                reason="order_unknown_column",
                message=f"詢價表出現格式設定沒有的欄位「{col.value}」，格式可能已改版",
            )
        )
    for col in order.duplicate_columns:
        out.append(
            result(
                "order.duplicate_column",
                "詢價表欄位",
                S.REVIEW_REQUIRED,
                ov=[col],
                reason="order_duplicate_column",
                message=f"詢價表欄位「{col.value}」重複出現，無法確定以哪一欄為準",
            )
        )
    for name in order.missing_columns:
        out.append(
            result(
                "order.missing_column",
                "詢價表欄位",
                S.REVIEW_REQUIRED,
                reason="order_missing_column",
                message=f"詢價表缺少格式設定中的欄位「{name}」",
            )
        )
    return out


# ---------------------------------------------------------------- 標準欄位比對


def simple(
    ctx: Context,
    rule_id: str,
    key: str,
    convert: Callable[[Any], Any],
    what: str,
    compare: Callable[[Any, Any], tuple[bool, Any]] | None = None,
    tolerance: str | None = None,
    pf_key: str | None = None,
) -> CheckResult:
    pf = ctx.ts.f(pf_key or key)
    v, ov, problem = order_value(ctx, key, rule_id, key, pf, convert, what)
    if problem:
        return problem
    if not pf.ok:
        return doc_review(rule_id, key, pf, v, [ov])
    ok, shown = compare(v, pf.value) if compare else (v == pf.value, v)
    return result(
        rule_id,
        key,
        S.PASS if ok else S.MISMATCH,
        expected=shown,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        tolerance=tolerance,
    )


def product_code(ctx: Context) -> CheckResult:
    rid, pf, ov = "field.product_code", ctx.ts.f("product_code"), ctx.order.product_code
    if ov.value is None:
        return order_review(rid, "product_code", ov, pf, "order_missing", "詢價表沒有商品代號")
    if not pf.ok:
        return doc_review(rid, "product_code", pf, ov.value, [ov])
    ok = ov.value == pf.value
    return result(
        rid,
        "product_code",
        S.PASS if ok else S.MISMATCH,
        expected=ov.value,
        actual=pf.value,
        pf=pf,
        ov=[ov],
        reason="" if ok else "value_mismatch",
        message="" if ok else "說明書與詢價表的商品代號不同，可能拿錯檔案",
    )


# ---------------------------------------------------------------- 審查標準


def approval_date(ctx: Context) -> CheckResult:
    rid, pf = "standard.approval_date", ctx.ts.f("approval_date")
    if not pf.ok:
        return doc_review(rid, "approval_date", pf, ctx.std.approval_date)
    ok = pf.value == ctx.std.approval_date
    return result(
        rid,
        "approval_date",
        S.PASS if ok else S.MISMATCH,
        expected=ctx.std.approval_date,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        message="" if ok else "受託機構審查通過日期與審查標準不同（可能沿用舊審查日期）",
    )


def _codepoints(s: str) -> str:
    return " ".join(f"{c}U+{ord(c):04X}" for c in s)


def chairman(ctx: Context) -> CheckResult:
    rid, pf, exp = "standard.chairman", ctx.ts.f("chairman"), ctx.std.chairman
    if not pf.ok:
        return doc_review(rid, "chairman", pf, exp)
    ok = pf.value == exp
    msg = "" if ok else f"須逐字（含字碼）相等：預期 {_codepoints(exp)}；說明書 {_codepoints(pf.value)}"
    return result(
        rid,
        "chairman",
        S.PASS if ok else S.MISMATCH,
        expected=exp,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        message=msg,
    )


def fixed_warning(ctx: Context) -> CheckResult:
    rid = "standard.fixed_warning"
    ti = ctx.ts.full_text
    target = squash(ctx.std.fixed_warning)
    hits = [m for m in re.finditer(re.escape(target), ti.text)]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for m in hits]
    expected = ctx.std.fixed_warning_occurrences
    ok = len(hits) == expected
    return result(
        rid,
        "fixed_warning",
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=len(hits),
        evidence=evidence,
        reason="" if ok else "occurrence_count",
        tolerance="忽略空白與換行後逐字相等",
        message=f"固定風險警語逐字相符 {len(hits)} 次，應為 {expected} 次"
        + ("" if ok else "（可能被改字、缺漏或多出）"),
    )


def risk_level(ctx: Context) -> CheckResult:
    rid = "standard.risk_level"
    ti = ctx.ts.full_text
    found = [(m.group(1), m) for m in re.finditer(r"【(RR\d)】", ti.text)]
    if not found:
        return result(
            rid,
            "risk_level",
            S.REVIEW_REQUIRED,
            expected=ctx.std.risk_level,
            reason="document_missing",
            message="說明書找不到【RRn】風險等級",
        )
    bad = [(lv, m) for lv, m in found if lv != ctx.std.risk_level]
    shown = bad or found[:1]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for _, m in shown]
    ok = not bad
    return result(
        rid,
        "risk_level",
        S.PASS if ok else S.MISMATCH,
        expected=ctx.std.risk_level,
        actual=sorted({lv for lv, _ in found}),
        evidence=evidence,
        reason="" if ok else "value_mismatch",
        message=f"全文共 {len(found)} 處【RRn】",
    )


def forbidden_wording(ctx: Context) -> CheckResult:
    rid = "standard.forbidden_wording"
    ti = ctx.ts.full_text
    text = ti.text
    for phrase in ctx.std.allowed_phrases:
        p = squash(phrase)
        text = text.replace(p, "□" * len(p))  # 遮蔽允許片語，保留字元位置
    hits = [m for w in ctx.std.forbidden for m in re.finditer(re.escape(squash(w)), text)]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for m in hits]
    ok = not hits
    return result(
        rid,
        "forbidden_wording",
        S.PASS if ok else S.MISMATCH,
        expected=0,
        actual=len(hits),
        evidence=evidence,
        reason="" if ok else "forbidden_wording",
        message=""
        if ok
        else f"允許片語以外出現「{'、'.join(ctx.std.forbidden)}」{len(hits)} 處（SOP：須改為「受託買賣」）",
    )


def _field(ctx: Context, name: str, note: str) -> ParsedField:
    try:
        return ctx.ts.f(name)
    except KeyError:
        return ParsedField.missing(name, note)


def _fixed_text(rid: str, field: str, pf: ParsedField, expected: str, what: str) -> CheckResult:
    """說明書文字（已去空白）與審查標準固定值比對；忽略空白與換行，其餘逐字相等。"""
    if not pf.ok:
        return doc_review(rid, field, pf, expected)
    ok = squash(pf.value) == squash(expected)
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        tolerance="忽略空白與換行後逐字相等",
        message="" if ok else f"{what}與審查標準不同",
    )


def issuer_name(ctx: Context, issuer: str) -> list[CheckResult]:
    """發行機構中英文法人全名：封面「發行機構」與第二章「發行機構」條事業名稱 = 審查標準 issuer_name.<上手>。"""
    rid = "standard.issuer_name"
    expected = ctx.std.issuer_names.get(issuer.lower())
    fields = (("issuer_name_cover", "封面「發行機構」"), ("issuer_name_ch2", "第二章「發行機構」事業名稱"))
    if expected is None:
        return [
            result(
                rid,
                name,
                S.REVIEW_REQUIRED,
                pf=ctx.ts.f(name),
                reason="standard_missing",
                message=f"審查標準沒有 {issuer} 的發行機構全名（issuer_name.{issuer.lower()}）",
            )
            for name, _ in fields
        ]
    return [_fixed_text(rid, name, ctx.ts.f(name), expected, what) for name, what in fields]


def distributor_info(ctx: Context) -> list[CheckResult]:
    """受託或銷售機構名稱、電話、地址：封面與第二章每一處 = 審查標準。"""
    rid, std = "standard.distributor", ctx.std
    checks = (
        ("distributor_name_cover", std.distributor_name, "封面受託或銷售機構名稱"),
        ("distributor_phone_cover", std.distributor_phone, "封面受託或銷售機構電話"),
        ("distributor_address_cover", std.distributor_address, "封面受託或銷售機構地址"),
        ("distributor_name_ch2", std.distributor_name, "第二章受託或銷售機構事業名稱"),
        ("distributor_address_ch2", std.distributor_address, "第二章受託或銷售機構營業所在地"),
    )
    return [_fixed_text(rid, name, ctx.ts.f(name), exp, what) for name, exp, what in checks]


def fees(ctx: Context) -> list[CheckResult]:
    """第四章費用表：審查標準列出的各費用項目費率區間逐字相等。"""
    rid = "standard.fees"
    return [
        _fixed_text(rid, label, _field(ctx, f"fee_{label}", f"費用表找不到「{label}」"), exp, f"「{label}」費率")
        for label, exp in ctx.std.fees.items()
    ]


def issue_price(ctx: Context) -> CheckResult:
    """發行價格 = 商品面額之 N%（審查標準）；不同時轉人工覆核（可能為特殊條件），不判為錯誤。"""
    rid, pf, exp = "standard.issue_price", ctx.ts.f("issue_price_pct"), ctx.std.issue_price_pct
    if not pf.ok:
        return doc_review(rid, "issue_price_pct", pf, exp)
    ok = pf.value == exp
    return result(
        rid,
        "issue_price_pct",
        S.PASS if ok else S.REVIEW_REQUIRED,
        expected=exp,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "issue_price_non_standard",
        message="" if ok else f"發行價格不是商品面額之 {exp}%，請人工確認",
    )


_BRACKETS = str.maketrans({"(": "（", ")": "）"})


def product_name(ctx: Context, issuer: str) -> list[CheckResult]:
    """用說明書的天期、幣別、是否記憶式組出預期名稱後比對；中文名稱括號全半形不計。

    名稱樣板依上手讀取審查標準 `product_name.<上手代號小寫>`；沒有樣板時轉人工覆核。
    """
    rid = "standard.product_name"
    tpl = ctx.std.product_names.get(issuer.lower())
    if tpl is None:
        return [
            result(
                rid,
                field,
                S.REVIEW_REQUIRED,
                actual=ctx.ts.f(field).value,
                pf=ctx.ts.f(field),
                reason="standard_missing",
                message=f"審查標準沒有 {issuer} 的商品名稱樣板（product_name.{issuer.lower()}）",
            )
            for field in ("name_zh", "name_en")
        ]
    tenor, cz, mem = ctx.ts.f("tenor_months"), ctx.ts.f("currency_zh"), ctx.ts.f("ko_memory")
    out = []
    for field in ("name_zh", "name_en"):
        pf = ctx.ts.f(field)
        bad = next((p for p in (pf, tenor, cz, mem) if not p.ok), None)
        if bad is not None:
            out.append(doc_review(rid, field, bad))
            continue
        iso = ctx.std.currency_zh_to_iso.get(cz.value)
        if iso is None:
            out.append(
                result(
                    rid,
                    field,
                    S.REVIEW_REQUIRED,
                    actual=pf.value,
                    pf=pf,
                    reason="currency_unknown",
                    message="幣別不在審查標準的對照表，無法組出預期名稱",
                )
            )
            continue
        if field == "name_zh":
            expected = tpl.zh.format(tenor=tenor.value, ccy_zh=cz.value, memory_zh=tpl.memory_zh if mem.value else "")
            norm = (lambda s: squash(s).translate(_BRACKETS)) if tpl.normalize_brackets else squash
            tol = "忽略空白；全形／半形括號不計" if tpl.normalize_brackets else "忽略空白"
        else:
            expected = tpl.en.format(tenor=tenor.value, ccy=iso, memory_en=tpl.memory_en if mem.value else "")
            norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
            tol = "連續空白視為一個"
        ok = norm(expected) == norm(pf.value)
        out.append(
            result(
                rid,
                field,
                S.PASS if ok else S.MISMATCH,
                expected=expected,
                actual=pf.value,
                pf=pf,
                reason="" if ok else "value_mismatch",
                tolerance=tol,
                message="依審查標準名稱樣板與說明書天期、幣別、是否記憶式組出",
            )
        )
    return out
