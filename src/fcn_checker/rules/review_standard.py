"""審查標準規則：各上手共用，依上手代號取得已解析的審查標準（docs/rules/review-standard.md）。

只用說明書標準欄位、全文索引與審查標準，不碰參考條件表。對外只有兩個進入點：說明書的 `review_standard_rules`，
與投資人須知的 `iis_review_standard_rules`（只核對範本有的項目，ADR 0007）。
項目：standard.* 的預期值出自審查標準；面額、受理申購日、刊印日期（doc.*）的錯訊沿用「預期」。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable

from ..config import ISSUER_NAME_IGNORES, NAME_FLAGS, RISK_LEVEL
from ..investor_sheet import read_iis
from ..schema import CheckResult, DocKind, Evidence, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import fee_field
from ..text import full_brackets, squash
from .kit import Context, doc_review, occurrences_of, result, standard_field

__all__ = ["iis_review_standard_rules", "review_standard_rules"]

# ---------------------------------------------------------------- 審查標準


def _approval_date(ctx: Context) -> CheckResult:
    """審查通過日期 = 審查標準中交易日當天或之前最近一次的日期（Issue #120）。"""
    rid, pf, trade = "standard.approval_date", standard_field(ctx, "approval_date"), standard_field(ctx, "trade_date")
    item = Item.standard("受託機構審查通過日期")
    if not pf.ok:
        return doc_review(rid, "approval_date", pf, item=item, document=ctx.document)
    if not trade.ok:
        return result(
            rid,
            "approval_date",
            S.REVIEW_REQUIRED,
            actual=pf.value,
            pf=pf,
            reason="trade_date_unavailable",
            message="讀不到交易日，無法決定適用的審查通過日期",
            item=item,
        )
    expected = ctx.std.approval_date_on(trade.value)
    evidence = pf.evidence + trade.evidence
    if expected is None:
        first = ctx.std.approval_dates[0]
        return result(
            rid,
            "approval_date",
            S.REVIEW_REQUIRED,
            actual=pf.value,
            evidence=evidence,
            reason="approval_date_not_configured",
            message=f"交易日 {trade.value} 早於審查標準最早的審查通過日期 {first}，沒有適用的日期",
            item=item,
        )
    ok = pf.value == expected
    return result(
        rid,
        "approval_date",
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        evidence=evidence,
        reason="" if ok else "value_mismatch",
        message=f"依交易日 {trade.value} 應為當天或之前最近一次的審查通過日期"
        + ("" if ok else "（可能沿用舊的審查通過日期）"),
        item=item,
    )


# ---------------------------------------------------------------- 審查標準：說明書各出處（各上手共用，BARC 語意）


def _denomination(ctx: Context) -> CheckResult:
    """面額 = 審查標準該幣別的預設值；不同時轉人工覆核（客戶可能要求特殊面額）。"""
    rid, pf, cz = "doc.denomination", standard_field(ctx, "denomination"), standard_field(ctx, "currency_zh")
    item = Item.expected("面額")
    if not pf.ok:
        return doc_review(rid, "denomination", pf, item=item, document=ctx.document)
    iso = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    default = ctx.std.denomination.get(iso) if iso else None
    if default is None:
        return result(
            rid,
            "denomination",
            S.REVIEW_REQUIRED,
            actual=pf.value,
            pf=pf,
            reason="currency_unknown",
            message="無法確認幣別，找不到面額預設值",
            item=item,
        )
    ok = pf.value == default
    return result(
        rid,
        "denomination",
        S.PASS if ok else S.REVIEW_REQUIRED,
        expected=default,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "denomination_non_default",
        message="" if ok else f"面額不是 {iso} 預設值；客戶可能要求特殊面額，請人工確認",
        item=item,
    )


def _subscription_dates(ctx: Context) -> list[CheckResult]:
    """各受理申購日出處（開始、結束）= 交易日。"""
    rid, trade = "doc.subscription_start_date", standard_field(ctx, "trade_date")
    items, problem = occurrences_of(ctx, rid, "subscription_dates", Item.expected("受理申購日"))
    if problem:
        return [problem]
    out = []
    for occ in items:
        pf, item = occ.value, Item.expected(occ.name)
        bad = next((x for x in (pf, trade) if not x.ok), None)
        if bad is not None:
            out.append(doc_review(rid, occ.field, bad, item=item, document=ctx.document))
            continue
        ok = pf.value == trade.value
        out.append(
            result(
                rid,
                occ.field,
                S.PASS if ok else S.MISMATCH,
                expected=trade.value,
                actual=pf.value,
                evidence=pf.evidence + trade.evidence,
                reason="" if ok else "value_mismatch",
                message=f"{occ.where}須等於交易日",
                item=item,
            )
        )
    return out


def _print_dates(ctx: Context, trade: ParsedField | None = None) -> list[CheckResult]:
    """各刊印日期出處在交易日當天至交易日後允許天數內（審查標準）；交易日預設取說明書。"""
    rid, trade = "doc.print_date", trade or standard_field(ctx, "trade_date")
    items, problem = occurrences_of(ctx, rid, "print_dates", Item.expected("刊印日期"))
    if problem:
        return [problem]
    limit = ctx.std.print_date_max_days_after_trade
    out = []
    for occ in items:
        pf, item = occ.value, Item.expected(occ.name)
        bad = next((x for x in (pf, trade) if not x.ok), None)
        if bad is not None:
            out.append(doc_review(rid, occ.field, bad, item=item, document=ctx.document))
            continue
        gap = (pf.value - trade.value).days
        ok = 0 <= gap <= limit
        out.append(
            result(
                rid,
                occ.field,
                S.PASS if ok else S.MISMATCH,
                expected=f"{trade.value} ～ {trade.value + dt.timedelta(days=limit)}",
                actual=pf.value,
                evidence=pf.evidence + trade.evidence,
                reason="" if ok else "value_mismatch",
                tolerance=f"交易日當天至交易日後 {limit} 天",
                message=f"刊印日期為交易日 {gap:+d} 天",
                item=item,
            )
        )
    return out


def _codepoints(s: str) -> str:
    return " ".join(f"{c}U+{ord(c):04X}" for c in s)


def _chairman(ctx: Context) -> CheckResult:
    rid, pf, exp = "standard.chairman", standard_field(ctx, "chairman"), ctx.std.chairman
    item = Item.standard("受託機構負責人")
    if not pf.ok:
        return doc_review(rid, "chairman", pf, exp, item=item, document=ctx.document)
    ok = pf.value == exp
    msg = "" if ok else f"須逐字（含字碼）相等：預期 {_codepoints(exp)}；{ctx.document} {_codepoints(pf.value)}"
    return result(
        rid,
        "chairman",
        S.PASS if ok else S.MISMATCH,
        expected=exp,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        message=msg,
        item=item,
    )


def _fixed_warning(ctx: Context, expected: int | None = None) -> CheckResult:
    """該上手適用的固定風險警語（有上手版本就用，否則用預設）逐字出現的次數 = 審查標準（`expected` 另給時用它）。

    上手另有允許的開頭句寫法（risk.fixed_warning_openings）時，只換開頭句的版本也算一次。
    """
    rid, std = "standard.fixed_warning", ctx.issuer_std
    ti = ctx.ts.full_text
    targets = [squash(w) for w in std.fixed_warnings]
    hits = sorted((m for t in targets for m in re.finditer(re.escape(t), ti.text)), key=lambda m: m.start())
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for m in hits]
    expected = std.fixed_warning_occurrences if expected is None else expected
    ok = len(hits) == expected
    return result(
        rid,
        "fixed_warning",
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=len(hits),
        evidence=evidence,
        reason="" if ok else "occurrence_count",
        tolerance="忽略空白與換行後逐字相等" + ("（另接受審查標準列出的開頭句寫法）" if len(targets) > 1 else ""),
        message=f"固定風險警語逐字相符 {len(hits)} 次，應為 {expected} 次"
        + ("" if ok else "（可能被改字、缺漏或多出）"),
        item=Item.standard("固定風險警語"),
    )


def _risk_level(ctx: Context) -> CheckResult:
    """全文每一處風險等級 = 審查標準；寫法依上手（risk.level_formats，沒列出的上手為【RRn】）。"""
    rid, item = "standard.risk_level", Item.standard("風險等級")
    ti = ctx.ts.full_text
    formats = ctx.issuer_std.risk_level_formats
    patterns = [re.escape(squash(f)).replace(re.escape(RISK_LEVEL), r"(RR\d)") for f in formats]
    found = sorted(((m.group(1), m) for p in patterns for m in re.finditer(p, ti.text)), key=lambda x: x[1].start())
    shown_formats = "、".join(f.replace(RISK_LEVEL, "RRn") for f in formats)
    if not found:
        return result(
            rid,
            "risk_level",
            S.REVIEW_REQUIRED,
            expected=ctx.std.risk_level,
            reason="document_missing",
            message=f"{ctx.document}找不到{shown_formats}風險等級",
            item=item,
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
        message=f"全文共 {len(found)} 處{shown_formats}",
        item=item,
    )


def _forbidden_wording(ctx: Context) -> CheckResult:
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
        item=Item.standard("禁用語「受託投資」"),
    )


def _fixed_text(
    rid: str,
    field: str,
    pf: ParsedField,
    expected: str,
    what: str,
    name: str,
    *,
    norm: Callable[[str], str] = squash,
    tolerance: str | None = None,
    document: DocKind,
) -> CheckResult:
    """被核對文件的文字與審查標準固定值比對；預設忽略空白與換行，其餘逐字相等（`norm` 另給比對前的正規化）。"""
    item = Item.standard(name)
    if not pf.ok:
        return doc_review(rid, field, pf, expected, item=item, document=document)
    ok = norm(pf.value) == norm(expected)
    return result(
        rid,
        field,
        S.PASS if ok else S.MISMATCH,
        expected=expected,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        tolerance=tolerance or "忽略空白與換行後逐字相等",
        message="" if ok else f"{what}與審查標準不同",
        item=item,
    )


def _issuer_name_text(s: str, ignore: frozenset[str]) -> str:
    """發行機構全名比對前的正規化：忽略空白與換行，另依上手忽略括號（全形／半形）、英文名結尾的句點。"""
    s = squash(s)
    if "brackets" in ignore:
        s = re.sub(r"[()（）]", "", s)
    if "trailing_period" in ignore:
        s = re.sub(r"\.(?=[)）]?$)", "", s)
    return s


def _issuer_name(ctx: Context) -> list[CheckResult]:
    """發行機構中英文法人全名：封面「發行機構」與第二章「發行機構」條事業名稱 = 審查標準 issuer_name.<上手>。

    範本另有只寫中文的出處（`issuer_name_ch1`）時那一處只比中文；範本沒有的出處（不適用）不核對。
    上手另有寫法差異時依審查標準 issuer_name_ignore 忽略。
    """
    rid, issuer, std = "standard.issuer_name", ctx.issuer, ctx.issuer_std
    expected = std.issuer_name
    fields = [  # 標準欄位、說明的開頭、項目名稱、是否只比中文
        ("issuer_name_cover", "封面「發行機構」", "封面發行機構名稱", False),
        ("issuer_name_ch2", "第二章「發行機構」事業名稱", "第二章發行機構名稱", False),
    ]
    if standard_field(ctx, "issuer_name_ch1").status != FieldStatus.NOT_APPLICABLE:
        fields.append(("issuer_name_ch1", "第一章發行機構中文名稱", "第一章發行機構名稱", True))
    if expected is None:
        return [
            result(
                rid,
                name,
                S.REVIEW_REQUIRED,
                pf=standard_field(ctx, name),
                reason="standard_missing",
                message=f"審查標準沒有 {issuer} 的發行機構全名（issuer_name.{issuer.lower()}）",
                item=Item.standard(zh),
            )
            for name, _, zh, _ in fields
        ]
    zh_name = re.split(r"[（(]", expected, maxsplit=1)[0]
    return [
        _fixed_text(
            rid,
            name,
            standard_field(ctx, name),
            zh_name if zh_only else expected,
            what,
            zh,
            norm=lambda s: _issuer_name_text(s, std.issuer_name_ignore),
            tolerance="忽略空白、換行"
            + "".join(f"、{ISSUER_NAME_IGNORES[k]}" for k in sorted(std.issuer_name_ignore))
            + "後逐字相等",
            document=ctx.document,
        )
        for name, what, zh, zh_only in fields
    ]


def _distributor_info(ctx: Context) -> list[CheckResult]:
    """受託或銷售機構名稱、電話、地址：封面與第二章每一處 = 審查標準；電話、地址另接受審查標準列出的等價寫法。"""
    rid, std = "standard.distributor", ctx.issuer_std
    checks = (  # 標準欄位、種類、審查標準值、說明的開頭、項目名稱
        ("distributor_name_cover", "name", std.distributor_name, "封面受託或銷售機構名稱", "封面受託或銷售機構名稱"),
        ("distributor_phone_cover", "phone", std.distributor_phone, "封面受託或銷售機構電話", "封面受託或銷售機構電話"),
        (
            "distributor_address_cover",
            "address",
            std.distributor_address,
            "封面受託或銷售機構地址",
            "封面受託或銷售機構地址",
        ),
        (
            "distributor_name_ch2",
            "name",
            std.distributor_name,
            "第二章受託或銷售機構事業名稱",
            "第二章受託或銷售機構名稱",
        ),
        (
            "distributor_address_ch2",
            "address",
            std.distributor_address,
            "第二章受託或銷售機構營業所在地",
            "第二章受託或銷售機構地址",
        ),
    )
    return [
        _distributor_text(ctx, rid, name, standard_field(ctx, name), exp, what, zh, kind=kind)
        for name, kind, exp, what, zh in checks
    ]


def _distributor_text(
    ctx: Context, rid: str, field: str, pf: ParsedField, exp: str, what: str, name: str, *, kind: str
) -> CheckResult:
    """受託或銷售機構的一處文字（`kind`：name 名稱／phone 電話／address 地址）= 審查標準；
    電話、地址另接受審查標準列出的等價寫法（忽略空白後相等）。"""
    std = ctx.issuer_std
    listed = {
        "phone": (std.distributor_phone_equivalents, "電話", "distributor.phone_equivalents"),
        "address": (std.distributor_address_equivalents, "地址", "distributor.address_equivalents"),
    }.get(kind)
    if listed is not None and pf.ok and squash(pf.value) in {squash(v) for v in listed[0]}:
        return result(
            rid,
            field,
            S.PASS,
            expected=exp,
            actual=pf.value,
            pf=pf,
            tolerance=f"審查標準列出的{listed[1]}等價寫法（{listed[2]}）",
            item=Item.standard(name),
        )
    return _fixed_text(rid, field, pf, exp, what, name, document=ctx.document)


def _fees(ctx: Context) -> list[CheckResult]:
    """第四章費用表：審查標準列出的各費用項目費率區間逐字相等。"""
    rid = "standard.fees"
    return [
        _fixed_text(
            rid, label, standard_field(ctx, fee_field(label)), exp, f"「{label}」費率", label, document=ctx.document
        )
        for label, exp in ctx.std.fees.items()
    ]


def _issue_price(ctx: Context, *, others: bool = True) -> list[CheckResult]:
    """發行價格 = 商品面額之 N%（審查標準）；不同時轉人工覆核（可能為特殊條件），不判為錯誤。

    `others` 時另核對範本的其他出處（標準欄位 `issue_price_others`，例：MS 第四章申購價金；範本沒有時不產生結果）。
    """
    rid = "standard.issue_price"
    out = [_issue_price_at(ctx, rid, "issue_price_pct", standard_field(ctx, "issue_price_pct"), "發行價格")]
    if others:
        items, problem = occurrences_of(ctx, rid, "issue_price_others", Item.standard("發行價格"))
        out.extend([problem] if problem else (_issue_price_at(ctx, rid, o.field, o.value, o.name) for o in items))
    return out


def _issue_price_at(ctx: Context, rid: str, field: str, pf: ParsedField, name: str) -> CheckResult:
    exp, item = ctx.std.issue_price_pct, Item.standard(name)
    if not pf.ok:
        return doc_review(rid, field, pf, exp, item=item, document=ctx.document)
    ok = pf.value == exp
    return result(
        rid,
        field,
        S.PASS if ok else S.REVIEW_REQUIRED,
        expected=exp,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "issue_price_non_standard",
        message="" if ok else f"發行價格不是商品面額之 {exp}%，請人工確認",
        item=item,
    )


def _name_flags(ctx: Context, used: frozenset[str]) -> tuple[dict[str, bool], ParsedField | None]:
    """名稱樣板的 maxi（標的數 ≥ 2；{underlying_*} 也依它選 1 檔或 2 檔以上的寫法）、daily（KO 每日觀察）旗標；
    只讀樣板用到的說明書欄位。不依上手分支。"""
    flags: dict[str, bool] = {}
    for flag, field, test, names in (
        ("maxi", "underlyings", lambda v: len(v) >= 2, {"maxi_zh", "maxi_en", "underlying_zh", "underlying_en"}),
        ("daily", "ko_observation", lambda v: v == "D", {"daily_zh", "daily_en"}),
    ):
        if used & names:
            pf = standard_field(ctx, field)
            if not pf.ok:
                return flags, pf
            flags[flag] = test(pf.value)
    return flags, None


def _product_name(ctx: Context) -> list[CheckResult]:
    """用說明書的天期、幣別、是否記憶式（樣板有用到時再加標的數、KO 觀察方式）組出預期名稱後比對。

    名稱樣板依上手讀取審查標準 `product_name.<上手代號小寫>`；沒有樣板時轉人工覆核。中文名稱括號全半形不計。
    """
    rid, issuer = "standard.product_name", ctx.issuer
    items = {"name_zh": Item.standard("中文商品名稱"), "name_en": Item.standard("英文商品名稱")}
    tpl = ctx.issuer_std.product_name
    if tpl is None:
        names = {field: standard_field(ctx, field) for field in ("name_zh", "name_en")}
        return [
            result(
                rid,
                field,
                S.REVIEW_REQUIRED,
                actual=pf.value,
                pf=pf,
                reason="standard_missing",
                message=f"審查標準沒有 {issuer} 的商品名稱樣板（product_name.{issuer.lower()}）",
                item=items[field],
            )
            for field, pf in names.items()
        ]
    tenor, cz, mem = (
        standard_field(ctx, "tenor_months"),
        standard_field(ctx, "currency_zh"),
        standard_field(ctx, "ko_memory"),
    )
    out = []
    for field in ("name_zh", "name_en"):
        pf = standard_field(ctx, field)
        item = items[field]
        bad = next((p for p in (pf, tenor, cz, mem) if not p.ok), None)
        if bad is not None:
            out.append(doc_review(rid, field, bad, item=item, document=ctx.document))
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
                    item=item,
                )
            )
            continue
        lang = "zh" if field == "name_zh" else "en"
        flags, bad = _name_flags(ctx, tpl.placeholders(lang))
        if bad is not None:
            out.append(doc_review(rid, field, bad, item=item, document=ctx.document))
            continue
        flags["memory"] = bool(mem.value)
        values = {"tenor": tenor.value, "ccy_zh": cz.value, "ccy": iso}
        for flag in NAME_FLAGS:
            for code in ("zh", "en"):
                values[f"{flag}_{code}"] = tpl.flag_text(flag, code) if flags.get(flag) else ""
        if "maxi" in flags:
            for code in ("zh", "en"):
                values[f"underlying_{code}"] = tpl.underlying_text(flags["maxi"], code)
        expected = getattr(tpl, lang).format(**values)
        if lang == "zh":
            norm = (lambda s: full_brackets(squash(s))) if tpl.normalize_brackets else squash
            tol = "忽略空白；全形／半形括號不計" if tpl.normalize_brackets else "忽略空白"
        else:
            norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
            tol = "連續空白視為一個"
            if tpl.ignore_whitespace_en:
                norm = squash
                tol = "忽略所有空白與換行"
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
                message="依審查標準名稱樣板與說明書天期、幣別、是否記憶式"
                + ("、標的數" if {"underlying_zh", "underlying_en"} & tpl.placeholders(lang) else "")
                + "組出",
                item=item,
            )
        )
    return out


# ---------------------------------------------------------------- 投資人須知（ADR 0007）


def _iis_warning(ctx: Context) -> CheckResult:
    """固定警語全文同說明書（依上手版本），次數用審查標準的投資人須知專屬值。"""
    expected = ctx.issuer_std.iis_fixed_warning_occurrences
    if expected is None:
        return result(
            "standard.fixed_warning",
            "fixed_warning",
            S.REVIEW_REQUIRED,
            reason="standard_missing",
            message=f"審查標準沒有 {ctx.issuer} 投資人須知的固定警語次數（iis.fixed_warning_occurrences.{ctx.issuer.lower()}）",
            item=Item.standard("固定風險警語"),
        )
    return _fixed_warning(ctx, expected)


def _iis_risk_summary(ctx: Context) -> CheckResult:
    """商品簡介「本商品風險程度：RRn」（沒有【】，全文【RRn】規則抓不到）= 審查標準風險等級。"""
    rid, pf, item = (
        "standard.risk_level",
        read_iis(ctx.iis, "risk_level_summary"),
        Item.standard("風險等級（商品簡介）"),
    )
    if not pf.ok:
        return doc_review(rid, "risk_level_summary", pf, ctx.std.risk_level, item=item, document=ctx.document)
    ok = pf.value == ctx.std.risk_level
    return result(
        rid,
        "risk_level_summary",
        S.PASS if ok else S.MISMATCH,
        expected=ctx.std.risk_level,
        actual=pf.value,
        pf=pf,
        reason="" if ok else "value_mismatch",
        item=item,
    )


def _iis_occurrences(
    ctx: Context, rid: str, name: str, expected: str | None, what: str, missing: str, kind: str
) -> list[CheckResult]:
    """投資人須知出處清單型欄位（各處受託機構名稱／地址／電話、發行機構名稱）每一處 = 審查標準。"""
    container = read_iis(ctx.iis, name)
    if not container.ok:
        return [doc_review(rid, name, container, item=Item.standard(what), document=ctx.document)]
    items = container.value
    if expected is None:
        return [
            result(
                rid,
                occ.field,
                S.REVIEW_REQUIRED,
                pf=occ.value,
                reason="standard_missing",
                message=missing,
                item=Item.standard(occ.name),
            )
            for occ in items
        ]
    return [
        _distributor_text(ctx, rid, occ.field, occ.value, expected, f"{occ.where}的{what}", occ.name, kind=kind)
        for occ in items
    ]


def iis_review_standard_rules(ctx: Context, *, trade: ParsedField) -> list[CheckResult]:
    """投資人須知的審查標準規則：`ctx` 帶投資人須知的讀出結果，只核對範本有的項目（`ctx.provides`）。

    風險等級、固定警語（次數為投資人須知專屬）、禁用語一律核對；刊印日期的交易日由呼叫端給（參考條件表）。
    只寫中文的發行機構名稱出處（`issuer_names_zh`，例：HSBC 各處、MS 警語與商品簡介）只比全名的中文部分。
    """
    std, provides = ctx.issuer_std, ctx.provides
    issuer_name = std.issuer_name
    issuer_name_zh = re.split(r"[（(]", issuer_name, maxsplit=1)[0] if issuer_name is not None else None
    out = [_risk_level(ctx), _iis_warning(ctx), _forbidden_wording(ctx)]
    if provides("risk_level_summary"):
        out.append(_iis_risk_summary(ctx))
    if provides("print_dates"):
        out.extend(_print_dates(ctx, trade))
    if provides("issue_price_pct"):
        out.extend(_issue_price(ctx, others=False))
    no_issuer = f"審查標準沒有 {ctx.issuer} 的發行機構全名（issuer_name.{ctx.issuer.lower()}）"
    occurrence_checks = (  # 欄位、rule_id、審查標準值、說明用名稱、審查標準沒有值時的說明、種類（決定等價寫法）
        ("issuer_names", "standard.issuer_name", issuer_name, "發行機構名稱", no_issuer, "issuer"),
        ("issuer_names_zh", "standard.issuer_name", issuer_name_zh, "發行機構名稱", no_issuer, "issuer"),
        ("distributor_names", "standard.distributor", std.distributor_name, "受託或銷售機構名稱", "", "name"),
        ("distributor_addresses", "standard.distributor", std.distributor_address, "受託或銷售機構地址", "", "address"),
        ("distributor_phones", "standard.distributor", std.distributor_phone, "受託或銷售機構電話", "", "phone"),
    )
    for name, rid, expected, what, missing, kind in occurrence_checks:
        if provides(name):
            out.extend(_iis_occurrences(ctx, rid, name, expected, what, missing, kind))
    if any(provides(fee_field(label)) for label in ctx.std.fees):
        out.extend(_fees(ctx))
    return out


# ---------------------------------------------------------------- 入口


def review_standard_rules(ctx: Context) -> list[CheckResult]:
    """審查標準規則：各上手共用，依上手代號取得已解析的審查標準；只用說明書，不碰參考條件表。"""
    return [
        _denomination(ctx),
        *_subscription_dates(ctx),
        *_print_dates(ctx),
        _approval_date(ctx),
        _chairman(ctx),
        _fixed_warning(ctx),
        _risk_level(ctx),
        _forbidden_wording(ctx),
        *_product_name(ctx),
        *_issuer_name(ctx),
        *_distributor_info(ctx),
        *_fees(ctx),
        *_issue_price(ctx),
    ]
