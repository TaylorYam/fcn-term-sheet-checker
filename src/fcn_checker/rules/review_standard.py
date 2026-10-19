"""審查標準規則：各上手共用，依上手代號取得已解析的審查標準（docs/rules/review-standard.md）。

只用說明書標準欄位、全文索引與審查標準；參考條件表只有禁用語特例要看的交易日，由呼叫端傳入。對外只有兩個進入點：說明書的 `review_standard_rules`，
與投資人須知的 `iis_review_standard_rules`（只核對範本有的項目，ADR 0007）。
項目：standard.* 的預期值出自審查標準；面額、受理申購日、刊印日期（doc.*）的錯訊沿用「預期」。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..config import ISSUER_NAME_IGNORES, NAME_FLAGS, RISK_LEVEL
from ..investor_sheet import read_iis
from ..schema import CheckResult, DocKind, Evidence, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import AGENT_PLACES, GUARANTOR_PLACES, fee_field
from ..text import full_brackets, squash
from .kit import Check, Context, occurrences_of, standard_field

__all__ = ["iis_review_standard_rules", "review_standard_rules"]

# ---------------------------------------------------------------- 審查標準


def _approval_date(ctx: Context) -> CheckResult:
    """審查通過日期 = 審查標準中交易日當天或之前最近一次的日期（Issue #120）。"""
    rid, pf, trade = "standard.approval_date", standard_field(ctx, "approval_date"), standard_field(ctx, "trade_date")
    check = Check(rid, "approval_date", Item.standard("受託機構審查通過日期"), ctx.document)
    if not pf.ok:
        return check.review(pf)
    if not trade.ok:
        return check.needs(pf).result(
            S.REVIEW_REQUIRED,
            actual=pf.value,
            reason="trade_date_unavailable",
            message="讀不到交易日，無法決定適用的審查通過日期",
        )
    check = check.needs(pf, trade)
    expected = ctx.std.approval_date_on(trade.value)
    if expected is None:
        first = ctx.std.approval_dates[0]
        return check.result(
            S.REVIEW_REQUIRED,
            actual=pf.value,
            reason="approval_date_not_configured",
            message=f"交易日 {trade.value} 早於審查標準最早的審查通過日期 {first}，沒有適用的日期",
        )
    return check.compare(
        expected,
        pf.value,
        message=f"依交易日 {trade.value} 應為當天或之前最近一次的審查通過日期",
        fail_message="（可能沿用舊的審查通過日期）",
    )


# ---------------------------------------------------------------- 審查標準：說明書各出處（各上手共用，語意見 docs/rules/review-standard.md）


def _denomination(ctx: Context) -> CheckResult:
    """面額 = 審查標準該幣別的預設值；不同時轉人工覆核（客戶可能要求特殊面額）。"""
    rid, pf, cz = "doc.denomination", standard_field(ctx, "denomination"), standard_field(ctx, "currency_zh")
    check = Check(rid, "denomination", Item.expected("面額"), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    iso = ctx.std.currency_zh_to_iso.get(cz.value) if cz.ok else None
    default = ctx.std.denomination.get(iso) if iso else None
    if default is None:
        return check.result(
            S.REVIEW_REQUIRED, actual=pf.value, reason="currency_unknown", message="無法確認幣別，找不到面額預設值"
        )
    return check.compare(
        default,
        pf.value,
        fail=S.REVIEW_REQUIRED,
        reason="denomination_non_default",
        fail_message=f"面額不是 {iso} 預設值；客戶可能要求特殊面額，請人工確認",
    )


def _subscription_dates(ctx: Context, trade: ParsedField | None = None) -> list[CheckResult]:
    """各受理申購日出處（開始、結束）不晚於交易日（2026-10-15，#254）；交易日預設取說明書（投資人須知由呼叫端給
    參考條件表的交易日）。"""
    rid, trade = "doc.subscription_start_date", trade or standard_field(ctx, "trade_date")
    items, problem = occurrences_of(ctx, rid, "subscription_dates", Item.expected("受理申購日"))
    if problem:
        return [problem]
    out = []
    for occ in items:
        check = Check(rid, occ.field, Item.expected(occ.name), ctx.document).needs(occ.value, trade)
        if (problem := check.blocked) is not None:
            out.append(problem)
            continue
        out.append(
            check.compare(
                f"{trade.value} 當天或之前",
                occ.value.value,
                ok=occ.value.value <= trade.value,
                tolerance="不晚於交易日",
                fail_message=f"{occ.where}不可晚於交易日",
            )
        )
    return out


def _print_dates(ctx: Context, trade: ParsedField | None = None) -> list[CheckResult]:
    """各刊印日期出處在交易日（含）至發行日（不含）之間（2026-10-15，#254）。交易日預設取說明書（投資人須知由呼叫端給
    參考條件表的交易日）；發行日取同一份文件的發行日。"""
    rid, trade = "doc.print_date", trade or standard_field(ctx, "trade_date")
    items, problem = occurrences_of(ctx, rid, "print_dates", Item.expected("刊印日期"))
    if problem:
        return [problem]
    issue = standard_field(ctx, "issue_date")
    out = []
    for occ in items:
        pf = occ.value
        check = Check(rid, occ.field, Item.expected(occ.name), ctx.document).needs(pf, trade, issue)
        if (problem := check.blocked) is not None:
            out.append(problem)
            continue
        out.append(
            check.compare(
                f"{trade.value}（含）～ {issue.value}（不含）",
                pf.value,
                ok=trade.value <= pf.value < issue.value,
                tolerance="交易日（含）至發行日（不含）之間",
                fail_message="刊印日期須在交易日（含）至發行日（不含）之間",
            )
        )
    return out


def _codepoints(s: str) -> str:
    return " ".join(f"{c}U+{ord(c):04X}" for c in s)


def _chairmen(ctx: Context) -> list[CheckResult]:
    """受託機構負責人：第二章那一處與範本的其他出處（`chairman_others`，例：BBVA 第 1 頁明細）每一處 = 審查標準。"""
    others, problem = occurrences_of(ctx, "standard.chairman", "chairman_others", Item.standard("受託機構負責人"))
    return [
        _chairman(ctx, "chairman", standard_field(ctx, "chairman"), "受託機構負責人"),
        *([problem] if problem else []),
        *(_chairman(ctx, occ.field, occ.value, occ.name) for occ in others),
    ]


def _chairman(ctx: Context, field: str, pf: ParsedField, name: str) -> CheckResult:
    """一處受託機構負責人姓名 = 審查標準，逐字（含字碼）相等。"""
    rid, exp = "standard.chairman", ctx.std.chairman
    check = Check(rid, field, Item.standard(name), ctx.document, expected=exp).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    return check.compare(
        exp,
        pf.value,
        fail_message=f"須逐字（含字碼）相等：預期 {_codepoints(exp)}；{ctx.document} {_codepoints(pf.value)}",
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
    return Check(rid, "fixed_warning", Item.standard("固定風險警語"), ctx.document).compare(
        expected,
        len(hits),
        evidence=evidence,
        reason="occurrence_count",
        tolerance="忽略空白與換行後逐字相等" + ("（另接受審查標準列出的開頭句寫法）" if len(targets) > 1 else ""),
        message=f"固定風險警語逐字相符 {len(hits)} 次，應為 {expected} 次",
        fail_message="（可能被改字、缺漏或多出）",
    )


def _risk_level(ctx: Context) -> CheckResult:
    """全文每一處風險等級 = 審查標準；寫法依上手（risk.level_formats，沒列出的上手為【RRn】）。"""
    check = Check("standard.risk_level", "risk_level", Item.standard("風險等級"), ctx.document)
    ti = ctx.ts.full_text
    formats = ctx.issuer_std.risk_level_formats
    patterns = [re.escape(squash(f)).replace(re.escape(RISK_LEVEL), r"(RR\d)") for f in formats]
    found = sorted(((m.group(1), m) for p in patterns for m in re.finditer(p, ti.text)), key=lambda x: x[1].start())
    shown_formats = "、".join(f.replace(RISK_LEVEL, "RRn") for f in formats)
    if not found:
        return check.result(
            S.REVIEW_REQUIRED,
            expected=ctx.std.risk_level,
            reason="document_missing",
            message=f"{ctx.document}找不到{shown_formats}風險等級",
        )
    bad = [(lv, m) for lv, m in found if lv != ctx.std.risk_level]
    shown = bad or found[:1]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for _, m in shown]
    return check.compare(
        ctx.std.risk_level,
        sorted({lv for lv, _ in found}),
        ok=not bad,
        evidence=evidence,
        message=f"全文共 {len(found)} 處{shown_formats}",
    )


def _forbidden_wording(ctx: Context, trade: ParsedField) -> CheckResult:
    """片語以外出現禁用語即不一致；`trade`（參考條件表交易日）在審查標準 `wording.exempt_until`（含）以前時
    特例通過並註明（已通知上手改版，Issue #247）。讀不到交易日時照原規則判。"""
    check = Check("standard.forbidden_wording", "forbidden_wording", Item.standard("禁用語「受託投資」"), ctx.document)
    ti = ctx.ts.full_text
    text = ti.text
    for phrase in ctx.std.allowed_phrases:
        p = squash(phrase)
        text = text.replace(p, "□" * len(p))  # 遮蔽允許片語，保留字元位置
    hits = [m for w in ctx.std.forbidden for m in re.finditer(re.escape(squash(w)), text)]
    evidence = [Evidence.of(ti.lines_for(m.start(), m.end())[0]) for m in hits]
    found = f"允許片語以外出現「{'、'.join(ctx.std.forbidden)}」{len(hits)} 處"
    until = ctx.std.forbidden_exempt_until
    if hits and until is not None and trade.ok and trade.value <= until:
        return check.compare(
            0,
            len(hits),
            ok=True,
            message=f"{found}；交易日 {trade.value} 在 {until}（含）以前，已通知上手改版，特例通過",
            tolerance=f"交易日（參考條件表）在 {until}（含）以前的文件特例通過",
            evidence=evidence,
        )
    return check.compare(
        0,
        len(hits),
        evidence=evidence,
        reason="forbidden_wording",
        fail_message=f"{found}（SOP：須改為「受託買賣」）",
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
    check = Check(rid, field, Item.standard(name), document, expected=expected).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    return check.compare(
        expected,
        pf.value,
        ok=norm(pf.value) == norm(expected),
        tolerance=tolerance or "忽略空白與換行後逐字相等",
        fail_message=f"{what}與審查標準不同",
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
    fields = [  # 標準欄位、說明的開頭、項目名稱、是否只比中文；範本沒有的出處（不適用，例：BBVA 沒有封面發行機構欄）不核對
        (name, what, zh, zh_only)
        for name, what, zh, zh_only in (
            ("issuer_name_cover", "封面「發行機構」", "封面發行機構名稱", False),
            ("issuer_name_ch2", "第二章「發行機構」事業名稱", "第二章發行機構名稱", False),
            ("issuer_name_ch1", "第一章發行機構中文名稱", "第一章發行機構名稱", True),
        )
        if standard_field(ctx, name).status != FieldStatus.NOT_APPLICABLE
    ]
    if expected is None:
        return [
            Check(rid, name, Item.standard(zh), ctx.document)
            .needs(standard_field(ctx, name))
            .result(
                S.REVIEW_REQUIRED,
                reason="standard_missing",
                message=f"審查標準沒有 {issuer} 的發行機構全名（issuer_name.{issuer.lower()}）",
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
    """受託或銷售機構名稱、電話、地址：封面與第二章每一處（範本另有第四章電話、其他名稱地址出處時含那幾處）= 審查標準；
    電話、地址另接受審查標準列出的等價寫法。範本沒有的出處（不適用）不核對。"""
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
    if standard_field(ctx, "distributor_phone_ch4").status != FieldStatus.NOT_APPLICABLE:
        checks += (
            (
                "distributor_phone_ch4",
                "phone",
                std.distributor_phone,
                "第四章第 16 項受託機構聯絡方式",
                "第四章受託機構電話",
            ),  # 只有 BNP 範本有這處
        )
    out = [
        _distributor_text(ctx, rid, name, standard_field(ctx, name), exp, what, zh, kind=kind)
        for name, kind, exp, what, zh in checks
    ]
    for container, kind, exp, what in (  # 範本的其他出處（例：BBVA 第 1 頁明細；Issue #305）
        ("distributor_names_others", "name", std.distributor_name, "受託或銷售機構名稱"),
        ("distributor_addresses_others", "address", std.distributor_address, "受託或銷售機構地址"),
    ):
        others, problem = occurrences_of(ctx, rid, container, Item.standard(what))
        out.extend([problem] if problem else [])
        out.extend(
            _distributor_text(ctx, rid, occ.field, occ.value, exp, f"{occ.where}的{what}", occ.name, kind=kind)
            for occ in others
        )
    return out


def _distributor_text(
    ctx: Context, rid: str, field: str, pf: ParsedField, exp: str, what: str, name: str, *, kind: str
) -> CheckResult:
    """受託或銷售機構的一處文字（`kind`：name 名稱／phone 電話／address 地址）= 審查標準；
    電話、地址另接受審查標準列出的等價寫法（忽略空白後相等）。"""
    std = ctx.issuer_std
    listed = {
        "phone": (std.distributor_phone_equivalents, "電話", "distributor.phone_equivalents"),
        "address": (std.distributor_address_equivalents, "地址", "distributor.address_equivalents"),
        "name": (std.distributor_name_equivalents, "名稱", "distributor.name_equivalents"),
    }.get(kind)
    if listed is not None and pf.ok and squash(pf.value) in {squash(v) for v in listed[0]}:
        return (
            Check(rid, field, Item.standard(name), ctx.document)
            .needs(pf)
            .result(
                S.PASS,
                expected=exp,
                actual=pf.value,
                tolerance=f"審查標準列出的{listed[1]}等價寫法（{listed[2]}）",
            )
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
    exp = ctx.std.issue_price_pct
    return (
        Check(rid, field, Item.standard(name), ctx.document, expected=exp)
        .needs(pf)
        .compare(
            exp,
            pf.value,
            fail=S.REVIEW_REQUIRED,
            reason="issue_price_non_standard",
            fail_message=f"發行價格不是商品面額之 {exp}%，請人工確認",
        )
    )


def _quanto(ctx: Context, rows) -> bool | None:
    """承作幣別和任一檔標的報價幣別（價格旁的幣別字）不同；讀不到幣別字或對照不到 ISO 代碼時為 None。"""
    cz = standard_field(ctx, "currency_zh")
    to_iso = ctx.std.currency_zh_to_iso
    quotes = [to_iso.get(r.currency) if r.currency else None for r in rows]
    if not cz.ok or to_iso.get(cz.value) is None or None in quotes:
        return None
    return any(q != to_iso[cz.value] for q in quotes)


# 名稱樣板旗標 → (讀的說明書欄位, 判斷（判斷不出為 None）, 用到它的佔位符, 欄位不適用時的值（None：照常轉人工覆核）)
_NAME_FLAG_SOURCES = (
    ("maxi", "underlyings", lambda ctx, v: len(v) >= 2, {"maxi_zh", "maxi_en", "underlying_zh", "underlying_en"}, None),
    (
        "daily",
        "ko_observation",
        lambda ctx, v: v == "D",
        {"daily_zh", "daily_en", "ko_observation_zh", "ko_observation_en"},
        None,
    ),
    (
        "autocall",
        "autocall_schedule",
        lambda ctx, v: v.first_callable < v.periods,
        {"autocall_zh", "autocall_en"},
        None,
    ),
    ("quanto", "underlying_prices", lambda ctx, v: _quanto(ctx, v), {"quanto_zh", "quanto_en"}, None),
    ("ki", "ki_type", lambda ctx, v: v != "none", {"ki_zh", "ki_en"}, False),
)


def _name_flags(ctx: Context, used: frozenset[str]) -> tuple[dict[str, bool], ParsedField | None]:
    """名稱樣板的 maxi（標的數 ≥ 2；{underlying_*} 也依它選 1 檔或 2 檔以上的寫法）、daily（KO 每日觀察）、
    autocall（第一個可提前出場期早於到期，即 Non-Call < 天期）、quanto（承作幣別 ≠ 標的報價幣別）、ki（有 KI）旗標；
    只讀樣板用到的說明書欄位。不依上手分支。"""
    flags: dict[str, bool] = {}
    for flag, field, test, names, not_applicable in _NAME_FLAG_SOURCES:
        if used & names:
            pf = standard_field(ctx, field)
            if not_applicable is not None and pf.status == FieldStatus.NOT_APPLICABLE:  # 例：無 KI 交成不適用
                flags[flag] = not_applicable
                continue
            if not pf.ok:
                return flags, pf
            value = test(ctx, pf.value)
            if value is None:
                return flags, ParsedField.invalid(field, [], f"判斷不出名稱樣板的「{flag}」旗標")
            flags[flag] = value
    return flags, None


def _product_name(ctx: Context) -> list[CheckResult]:
    """用說明書的天期、幣別、是否記憶式（樣板有用到時再加標的數、KO 觀察方式）組出預期名稱後比對。

    名稱樣板依上手讀取審查標準 `product_name.<上手代號小寫>`；沒有樣板時轉人工覆核。中文名稱括號全半形不計。
    """
    rid, issuer = "standard.product_name", ctx.issuer
    items = {"name_zh": Item.standard("中文商品名稱"), "name_en": Item.standard("英文商品名稱")}
    tpl = ctx.issuer_std.product_name
    if tpl is None:
        return [
            Check(rid, field, items[field], ctx.document)
            .needs(pf)
            .result(
                S.REVIEW_REQUIRED,
                actual=pf.value,
                reason="standard_missing",
                message=f"審查標準沒有 {issuer} 的商品名稱樣板（product_name.{issuer.lower()}）",
            )
            for field, pf in ((f, standard_field(ctx, f)) for f in ("name_zh", "name_en"))
        ]
    tenor, cz, mem = (
        standard_field(ctx, "tenor_months"),
        standard_field(ctx, "currency_zh"),
        standard_field(ctx, "ko_memory"),
    )
    out = []
    for field in ("name_zh", "name_en"):
        pf = standard_field(ctx, field)
        check = Check(rid, field, items[field], ctx.document).needs(pf)
        # 組名稱要四個欄位都讀得到；結果的證據只掛名稱那一欄，守門時才把其他三個暫掛上去
        if (problem := check.needs(tenor, cz, mem).blocked) is not None:
            out.append(problem)
            continue
        iso = ctx.std.currency_zh_to_iso.get(cz.value)
        if iso is None:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    actual=pf.value,
                    reason="currency_unknown",
                    message="幣別不在審查標準的對照表，無法組出預期名稱",
                )
            )
            continue
        lang = "zh" if field == "name_zh" else "en"
        flags, bad = _name_flags(ctx, tpl.placeholders(lang))
        if bad is not None:
            out.append(check.review(bad))
            continue
        if tpl.single_unsupported and flags.get("maxi") is False:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    actual=pf.value,
                    reason="variant_not_covered",
                    message="單一標的沒有樣本，名稱寫法未知，請人工確認",
                )
            )
            continue
        flags["memory"] = bool(mem.value)
        values = {"tenor": tenor.value, "ccy_zh": cz.value, "ccy": tpl.ccy_text(iso)}
        for flag in NAME_FLAGS:
            for code in ("zh", "en"):
                values[f"{flag}_{code}"] = tpl.flag_text(flag, code) if flags.get(flag) else ""
        if flags.get("autocall"):  # 會提前出場且記憶式時改用 autocall_memory_*（SG「（記憶式自動提前出場）」）
            for code in ("zh", "en"):
                values[f"autocall_{code}"] = tpl.autocall_text(flags["memory"], code)
        if "maxi" in flags:
            for code in ("zh", "en"):
                values[f"underlying_{code}"] = tpl.underlying_text(flags["maxi"], code)
        if {"ko_observation_zh", "ko_observation_en"} & tpl.placeholders(lang):
            for code in ("zh", "en"):
                values[f"ko_observation_{code}"] = tpl.ko_observation_text(flags["daily"], code)
        expected = tpl.text(lang, tenor.value).format(**values)
        if lang == "zh":
            norm = (lambda s: full_brackets(squash(s))) if tpl.normalize_brackets else squash
            tol = "忽略空白；全形／半形括號不計" if tpl.normalize_brackets else "忽略空白"
        else:
            norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
            tol = "連續空白視為一個"
            if tpl.ignore_whitespace_en:
                norm = squash
                tol = "忽略所有空白與換行"
        out.append(
            check.compare(
                expected,
                pf.value,
                ok=norm(expected) == norm(pf.value),
                tolerance=tol,
                message="依審查標準名稱樣板與說明書天期、幣別、是否記憶式"
                + ("、標的數" if {"underlying_zh", "underlying_en"} & tpl.placeholders(lang) else "")
                + ("、是否會提前出場" if {"autocall_zh", "autocall_en"} & tpl.placeholders(lang) else "")
                + ("、KO 觀察方式" if {"ko_observation_zh", "ko_observation_en"} & tpl.placeholders(lang) else "")
                + ("、是否為 Quanto" if {"quanto_zh", "quanto_en"} & tpl.placeholders(lang) else "")
                + ("、有無 KI" if {"ki_zh", "ki_en"} & tpl.placeholders(lang) else "")
                + "組出",
            )
        )
    return out


# ---------------------------------------------------------------- 審查標準有該上手分節才核對的固定值（BNP）


def _guarantor_name(ctx: Context) -> list[CheckResult]:
    """保證機構名稱：審查標準 guarantor_name.<上手> 列出的每個出處 = 該出處的固定值（三處寫法可不同）；
    沒有該上手分節的上手不核對（沒有保證機構的範本）。"""
    rid = "standard.guarantor_name"
    return [
        _fixed_text(rid, field, standard_field(ctx, field), expected, what, name, document=ctx.document)
        for place, expected in ctx.issuer_std.guarantor_names.items()
        for field, what, name in (GUARANTOR_PLACES[place],)
    ]


def _agent_name(ctx: Context) -> list[CheckResult]:
    """總代理人名稱：審查標準 agent_name.<上手> 列出的每個出處 = 固定值（SG；地址電話不核對）；沒有分節的上手不核對。"""
    rid = "standard.agent_name"
    return [
        _fixed_text(rid, field, standard_field(ctx, field), expected, what, name, document=ctx.document)
        for place, expected in ctx.issuer_std.agent_names.items()
        for field, what, name in (AGENT_PLACES[place],)
    ]


def _product_type(ctx: Context) -> list[CheckResult]:
    """封面「商品種類」= 審查標準 product_type.<上手> 的固定值；沒有設定的上手不核對（MS 依標的數由上手規則核對）。"""
    expected = ctx.issuer_std.product_type
    if expected is None:
        return []
    pf = standard_field(ctx, "product_type")
    return [
        _fixed_text(
            "standard.product_type", "product_type", pf, expected, "商品種類", "商品種類", document=ctx.document
        )
    ]


def _minimum_issue_size(ctx: Context) -> list[CheckResult]:
    """第四章發行不成立門檻的金額寫法 = 審查標準 minimum_issue_size.<上手>；沒有設定的上手不核對。"""
    expected = ctx.issuer_std.minimum_issue_size
    if expected is None:
        return []
    pf = standard_field(ctx, "minimum_issue_size")
    return [
        _fixed_text(
            "standard.minimum_issue_size",
            "minimum_issue_size",
            pf,
            expected,
            "發行不成立門檻",
            "發行不成立門檻",
            document=ctx.document,
        )
    ]


# ---------------------------------------------------------------- 投資人須知（ADR 0007）


def _iis_warning(ctx: Context) -> CheckResult:
    """固定警語全文同說明書（依上手版本），次數用審查標準的投資人須知專屬值。"""
    expected = ctx.issuer_std.iis_fixed_warning_occurrences
    if expected is None:
        return Check("standard.fixed_warning", "fixed_warning", Item.standard("固定風險警語"), ctx.document).result(
            S.REVIEW_REQUIRED,
            reason="standard_missing",
            message=f"審查標準沒有 {ctx.issuer} 投資人須知的固定警語次數（iis.fixed_warning_occurrences.{ctx.issuer.lower()}）",
        )
    return _fixed_warning(ctx, expected)


def _iis_risk_summary(ctx: Context) -> CheckResult:
    """商品簡介「本商品風險程度：RRn」（沒有【】，全文【RRn】規則抓不到）= 審查標準風險等級。"""
    rid, pf, item = (
        "standard.risk_level",
        read_iis(ctx.iis, "risk_level_summary"),
        Item.standard("風險等級（商品簡介）"),
    )
    check = Check(rid, "risk_level_summary", item, ctx.document, expected=ctx.std.risk_level).needs(pf)
    return check.compare(ctx.std.risk_level, pf.value)


def _iis_occurrences(
    ctx: Context, rid: str, name: str, expected: str | None, what: str, missing: str, kind: str
) -> list[CheckResult]:
    """投資人須知出處清單型欄位（各處受託機構名稱／地址／電話、發行機構名稱）每一處 = 審查標準。"""
    container = read_iis(ctx.iis, name)
    if not container.ok:
        return [Check(rid, name, Item.standard(what), ctx.document).review(container)]
    items = container.value
    if expected is None:
        return [
            Check(rid, occ.field, Item.standard(occ.name), ctx.document)
            .needs(occ.value)
            .result(S.REVIEW_REQUIRED, reason="standard_missing", message=missing)
            for occ in items
        ]
    return [
        _distributor_text(ctx, rid, occ.field, occ.value, expected, f"{occ.where}的{what}", occ.name, kind=kind)
        for occ in items
    ]


# 投資人須知寫機構名稱的出處欄位 → (rule_id, 名稱, 審查標準的表, 用哪個出處的固定值, 是否接受等價寫法)
_IIS_PLACE_NAMES = {
    "guarantor_names": ("standard.guarantor_name", "保證機構名稱", "guarantor_name", "cover", False),  # BNP、SG 警語
    "guarantor_names_short": ("standard.guarantor_name", "保證機構名稱", "guarantor_name", "ch1", False),  # BNP 簡稱
    "guarantor_names_org": ("standard.guarantor_name", "保證機構名稱", "guarantor_name", "cover", True),  # SG 相關機構
    "agent_names": ("standard.agent_name", "總代理人名稱", "agent_name", "cover", False),  # SG 相關機構
}


def _iis_place_names(ctx: Context, name: str) -> list[CheckResult]:
    """投資人須知各處保證機構／總代理人名稱 = 審查標準 guarantor_name／agent_name.<上手> 某出處的固定值
    （出處見 `_IIS_PLACE_NAMES`）；相關機構的保證機構名稱另接受 guarantor_name_equivalents.<上手>（SG 英文名沒有括號）。
    沒有該值時轉人工覆核。"""
    std = ctx.issuer_std
    rid, what, table, place, equivalent = _IIS_PLACE_NAMES[name]
    names = std.guarantor_names if table == "guarantor_name" else std.agent_names
    listed = {squash(v) for v in std.guarantor_name_equivalents} if equivalent else set()
    container = read_iis(ctx.iis, name)
    if not container.ok:
        return [Check(rid, name, Item.standard(what), ctx.document).review(container)]
    expected = names.get(place)
    if expected is None:
        missing = f"審查標準沒有 {ctx.issuer} 的{what}（{table}.{ctx.issuer.lower()}.{place}）"
        return [
            Check(rid, occ.field, Item.standard(occ.name), ctx.document)
            .needs(occ.value)
            .result(S.REVIEW_REQUIRED, reason="standard_missing", message=missing)
            for occ in container.value
        ]
    out = []
    for occ in container.value:
        if occ.value.ok and squash(occ.value.value) in listed:
            check = Check(rid, occ.field, Item.standard(occ.name), ctx.document).needs(occ.value)
            source = f"guarantor_name_equivalents.{ctx.issuer.lower()}"
            out.append(
                check.result(
                    S.PASS,
                    expected=expected,
                    actual=occ.value.value,
                    tolerance=f"審查標準列出的名稱等價寫法（{source}）",
                )
            )
        else:
            what_here = f"{occ.where}的{what}"
            out.append(_fixed_text(rid, occ.field, occ.value, expected, what_here, occ.name, document=ctx.document))
    return out


def iis_review_standard_rules(ctx: Context, *, trade: ParsedField) -> list[CheckResult]:
    """投資人須知的審查標準規則：`ctx` 帶投資人須知的讀出結果，只核對範本有的項目（`ctx.provides`）。

    風險等級、固定警語（次數為投資人須知專屬）、禁用語一律核對；刊印日期的交易日由呼叫端給（參考條件表）。
    只寫中文的發行機構名稱出處（`issuer_names_zh`，例：HSBC 各處、MS 警語與商品簡介）只比全名的中文部分。
    """
    std, provides = ctx.issuer_std, ctx.provides
    issuer_name = std.issuer_name
    issuer_name_zh = re.split(r"[（(]", issuer_name, maxsplit=1)[0] if issuer_name is not None else None
    out = [_risk_level(ctx), _iis_warning(ctx), _forbidden_wording(ctx, trade)]
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
    for name in _IIS_PLACE_NAMES:
        if provides(name):
            out.extend(_iis_place_names(ctx, name))
    if any(provides(fee_field(label)) for label in ctx.std.fees):
        out.extend(_fees(ctx))
    if provides("subscription_dates"):
        out.extend(_subscription_dates(ctx, trade))
    if provides("product_type"):  # 沒有 product_type.<上手> 的上手不核對（MS 依標的數由範本專屬規則核對）
        out.extend(_product_type(ctx))
    if provides("minimum_issue_size"):
        out.extend(_minimum_issue_size(ctx))
    return out


# ---------------------------------------------------------------- 入口


def review_standard_rules(ctx: Context, *, reference_trade: ParsedField) -> list[CheckResult]:
    """審查標準規則：各上手共用，依上手代號取得已解析的審查標準；只用說明書，唯一例外是禁用語特例看的
    參考條件表交易日（`reference_trade`，由呼叫端給，同投資人須知）。"""
    return [
        _denomination(ctx),
        *_subscription_dates(ctx),
        *_print_dates(ctx),
        _approval_date(ctx),
        *_chairmen(ctx),
        _fixed_warning(ctx),
        _risk_level(ctx),
        _forbidden_wording(ctx, reference_trade),
        *_product_name(ctx),
        *_issuer_name(ctx),
        *_distributor_info(ctx),
        *_fees(ctx),
        *_issue_price(ctx),
        *_guarantor_name(ctx),
        *_agent_name(ctx),
        *_product_type(ctx),
        *_minimum_issue_size(ctx),
    ]
