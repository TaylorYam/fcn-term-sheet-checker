"""BARC 中文產品說明書 parser（範本 barc-zh-pd）。

依 docs/templates/barc-zh-product-description.md：以「章＋條號＋子項＋標籤」定位，
取得標準化欄位與證據。抓不到 → missing；多個不同值 → ambiguous；不猜值。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ..schema import Evidence, FieldStatus, Line, ParsedField
from . import barc_schedule as schedule
from .layout import Document, Span, TextIndex, join_text, parse_date, squash

TEMPLATE_ID = "barc-zh-pd"
PARSER_VERSION = "1"

# 第一章 §13 子項順序（範本規格 §2.2）
S13_LABELS = (
    "商品年期",
    "交易日",
    "發行日",
    "最終評價日",
    "到期日或最終實物贖回日",
    "配息支付日",
    "指定提前贖回事件",
    "評價日",
    "附註及相關定義",
)

_COVER_LABEL_MAX_X = 100.0
_COVER_VALUE_MIN_X = 250.0
_NUM_RE = re.compile(r"^[\d,]+\.\d+$")
_PCT_RE = re.compile(r"為最初價格的([\d.]+)%")
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9./-]* [A-Z0-9]{2}$")

# 價格表欄位：表頭第一行 → 欄位鍵（範本規格 §5.4）
PRICE_COLUMNS = (
    ("initial", re.compile(r"^最初價格$")),
    ("strike", re.compile(r"^執行價格（")),
    ("ko", re.compile(r"^(自動提前出場觸發價|觸發水準（)")),
    ("ki", re.compile(r"^觸及生效價格（")),
)


@dataclass
class DetectionResult:
    matched: bool
    failed: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class PriceRow:
    name: str
    values: dict[str, Decimal]
    lines: list[Line]


@dataclass
class Mention:
    """同一參數在不同條文中的出處。"""

    article: str
    value: Decimal
    lines: list[Line]


@dataclass
class BarcTermSheet:
    fields: dict[str, ParsedField]
    price_rows: list[PriceRow]
    coupon_mentions: dict[str, list[Mention]]  # monthly / annual
    full_text: TextIndex
    document: Document
    scenario_rows: list[PriceRow] = field(default_factory=list)

    def f(self, name: str) -> ParsedField:
        return self.fields[name]


# ---------------------------------------------------------------- 共用


def _distinct(name: str, hits: list[tuple[Any, list[Line]]], note: str = "") -> ParsedField:
    if not hits:
        return ParsedField.missing(name, note)
    values: list[Any] = []
    for v, _ in hits:
        if v not in values:
            values.append(v)
    lines = [ln for _, lns in hits for ln in lns]
    if len(values) > 1:
        return ParsedField.ambiguous(name, values, lines, note or "說明書中出現多個不同的值")
    return ParsedField.present(name, values[0], lines)


def _first_date_field(name: str, lines: list[Line], text: str) -> ParsedField:
    d = parse_date(text)
    if d is None:
        if re.search(r"\d{4}\s*年", text):
            return ParsedField.invalid(name, lines, "日期格式無法辨識或不合法")
        return ParsedField.missing(name, "找不到日期")
    return ParsedField.present(name, d, lines)


# ---------------------------------------------------------------- 封面


def product_code(lines: Sequence[Line]) -> ParsedField:
    """第一頁商品代號，與正式核對共用擷取及歧義判定。"""
    return _cover_field("product_code", list(lines), "商品代號", squash)


def _cover_value(lines: list[Line], label: str) -> list[tuple[str, list[Line]]]:
    """p1 兩欄版面：左側標籤、右側數值；多行值以下一個標籤為界。"""
    page1 = [ln for ln in lines if ln.page == 1]
    labels = [ln for ln in page1 if ln.x0 < _COVER_LABEL_MAX_X]
    hits: list[tuple[str, list[Line]]] = []
    pat = re.compile(rf"^{re.escape(label)}\s*[:：]\s*$")
    for k, lab in enumerate(labels):
        if not pat.match(lab.text):
            continue
        nxt = labels[k + 1].y0 if k + 1 < len(labels) else 1e9
        vals = [ln for ln in page1 if ln.x0 > _COVER_VALUE_MIN_X and lab.y0 - 3 <= ln.y0 < nxt - 3]
        if vals:
            hits.append((join_text(vals), [lab, *vals]))
        else:
            hits.append(("", [lab]))
    return hits


def _cover_field(name: str, lines: list[Line], label: str, convert: Callable[[str], Any] = lambda s: s) -> ParsedField:
    raw = _cover_value(lines, label)
    if not raw:
        return ParsedField.missing(name, f"封面找不到「{label}」")
    hits = []
    for text, lns in raw:
        if not text:
            return ParsedField.missing(name, f"封面「{label}」沒有值")
        v = convert(text)
        if v is None:
            return ParsedField.invalid(name, lns, f"封面「{label}」的值無法辨識")
        hits.append((v, lns))
    return _distinct(name, hits)


# ---------------------------------------------------------------- 範本辨識


def detect(doc: Document) -> DetectionResult:
    """範本規格 §8：全部條件成立才判定為 BARC 中文產品說明書。"""
    lines = doc.lines
    failed: list[str] = []
    ev: list[Evidence] = []
    p1 = [ln for ln in lines if ln.page == 1]

    title = [ln for ln in p1 if "中文產品說明書" in ln.text]
    if title:
        ev.append(Evidence.of(title[0]))
    else:
        failed.append("p1 沒有「中文產品說明書」")

    issuer = _cover_value(lines, "發行機構")
    if issuer and "Barclays Bank PLC" in issuer[0][0]:
        ev.extend(Evidence.of(x) for x in issuer[0][1])
    else:
        failed.append("p1「發行機構」不含 Barclays Bank PLC")

    name = _cover_value(lines, "商品中文名稱")
    nm = squash(name[0][0]) if name else ""
    if nm.startswith("英商巴克萊銀行") and "自動提前出場結構型商品" in nm:
        ev.extend(Evidence.of(x) for x in name[0][1])
    else:
        failed.append("商品名稱不是以「英商巴克萊銀行」開頭或不含「自動提前出場結構型商品」")

    if 1 not in doc.chapters:
        failed.append("找不到「第一章 商品基本資料」")
    else:
        art13 = doc.articles(1).get(13)
        subs = doc.subitems(art13)
        titles = [subs[n].title if n in subs else "" for n in range(1, len(S13_LABELS) + 1)]
        if not all(t.startswith(label) for t, label in zip(titles, S13_LABELS, strict=True)):
            failed.append("第一章第 13 條子項順序與範本不符")
        elif art13:
            ev.append(Evidence.of(doc.lines[art13.start]))
    return DetectionResult(not failed, failed, ev)


# ---------------------------------------------------------------- 第一章各欄位


def _definition_pct(name: str, lines: list[Line], term: str) -> ParsedField:
    """§15「X」詳見下表所示（為最初價格的N%）。"""
    hits = []
    for ln in lines:
        if ln.text.startswith(f"「{term}」詳見下表所示"):
            m = _PCT_RE.search(ln.text)
            if not m:
                return ParsedField.invalid(name, [ln], f"「{term}」定義句找不到百分比")
            hits.append((Decimal(m.group(1)), [ln]))
    return _distinct(name, hits)


def _ko_pct_and_memory(lines: list[Line], name_zh: ParsedField) -> tuple[ParsedField, ParsedField]:
    memory = _definition_pct("ko_pct", lines, "自動提前出場觸發價格")
    plain = _definition_pct("ko_pct", lines, "觸發水準")
    if memory.status != FieldStatus.MISSING and plain.status != FieldStatus.MISSING:
        ko = ParsedField.ambiguous("ko_pct", [], [], "同時出現「自動提前出場觸發價格」與「觸發水準」")
        ko.evidence = memory.evidence + plain.evidence
        return ko, ParsedField.ambiguous("ko_memory", [True, False], [], ko.note)
    if memory.status == FieldStatus.MISSING and plain.status == FieldStatus.MISSING:
        note = "§15 找不到「自動提前出場觸發價格」或「觸發水準」定義"
        return ParsedField.missing("ko_pct", note), ParsedField.missing("ko_memory", note)
    ko = memory if memory.status != FieldStatus.MISSING else plain
    by_term = memory.status != FieldStatus.MISSING
    if name_zh.ok and ("記憶式" in name_zh.value) != by_term:
        mem = ParsedField.ambiguous(
            "ko_memory", [by_term, not by_term], [], "§15 觸發價格名詞與商品名稱是否含「記憶式」不一致"
        )
        mem.evidence = ko.evidence + name_zh.evidence
        return ko, mem
    mem = ParsedField(name="ko_memory", status=FieldStatus.PRESENT, value=by_term, evidence=list(ko.evidence))
    return ko, mem


def _ki_type(lines: list[Line], ki_pct: ParsedField, strike: ParsedField) -> ParsedField:
    """none 無 KI／AM 到期觀察／D 每日觀察／M 每月觀察。

    「無 KI」必須由「§15 已解析出執行價格定義、且沒有觸及生效價格定義」明確判定，不能因抓不到就推定。
    """
    name = "ki_type"
    if ki_pct.status == FieldStatus.MISSING:
        if not strike.ok:
            return ParsedField.missing(name, "§15 無法解析，不能判斷 KI 型態")
        return ParsedField(
            name,
            FieldStatus.NOT_APPLICABLE,
            "none",
            list(strike.evidence),
            note="§15 有「執行價格」定義、沒有「觸及生效價格」定義 → 無 KI",
        )
    if not ki_pct.ok:
        return ParsedField(name, ki_pct.status, None, list(ki_pct.evidence), note=ki_pct.note)
    defs = [i for i, ln in enumerate(lines) if ln.text.startswith("「觸及生效評價日」")]
    if not defs:
        return ParsedField(
            name,
            FieldStatus.PRESENT,
            "AM",
            list(ki_pct.evidence),
            note="有觸及生效價格、無「觸及生效評價日」定義 → 到期觀察",
        )
    if len(defs) > 1:
        return ParsedField.ambiguous(name, [], [lines[i] for i in defs], "「觸及生效評價日」定義出現多次")
    i = defs[0]
    block = [lines[i]]
    for ln in lines[i + 1 : i + 4]:
        if ln.text.startswith("「") or ln.x0 < lines[i].x0 - 5:
            break
        block.append(ln)
    text = squash(join_text(block))
    if "自交易日起" in text and "各預定交易日" in text:
        return ParsedField.present(name, "D", block)
    if "配息評價日" in text or "每月" in text:
        f = ParsedField.present(name, "M", block)
        f.note = "觸及生效評價日為各期評價日（Monthly KI，尚無樣本）"
        return f
    return ParsedField.invalid(name, block, "「觸及生效評價日」定義不屬於已知型態")


def _ko_observation(doc: Document, art13: Span | None) -> ParsedField:
    """§13(6)–(8) 範圍：有「期始日(含)」表頭 → D（期間每日）；有評價日表頭 → P（期末定日）。"""
    subs = doc.subitems(art13)
    if not all(n in subs for n in (6, 9)):
        return ParsedField.missing("ko_observation", "找不到第 13 條第 (6)–(9) 項")
    lines = doc.lines[subs[6].start : subs[9].start]
    d = [ln for ln in lines if ln.text.startswith("期始日(含)")]
    p = [ln for ln in lines if ln.text in ("評價日t", "自動提前出場評價日")]
    if d and p:
        return ParsedField.ambiguous("ko_observation", ["D", "P"], d + p, "同時出現期間表與定日表表頭")
    if d:
        return ParsedField.present("ko_observation", "D", d[:1])
    if p:
        return ParsedField.present("ko_observation", "P", p[:1])
    return ParsedField.missing("ko_observation", "第 13 條找不到提前出場觀察表頭")


def _underlyings(doc: Document, art10: Span | None) -> ParsedField:
    lines = doc.span_lines(art10)
    hdr = [i for i, ln in enumerate(lines) if ln.text.startswith("彭博代號")]
    if len(hdr) != 1:
        return (
            ParsedField.missing("underlyings", "第 10 條找不到「彭博代號」表頭")
            if not hdr
            else ParsedField.ambiguous("underlyings", [], [lines[i] for i in hdr], "「彭博代號」表頭出現多次")
        )
    h = hdr[0]
    end = next((i for i in range(h + 1, len(lines)) if re.match(r"^\(2\)\s*相對權重", lines[i].text)), None)
    if end is None:
        return ParsedField.missing("underlyings", "第 10 條找不到「(2) 相對權重」結束錨點")
    hx = lines[h].xc
    cells = [ln for ln in lines[h + 1 : end] if ln.text.endswith("Equity") and abs(ln.xc - hx) < 80]
    if not cells:
        return ParsedField.missing("underlyings", "標的表沒有彭博代號")
    tickers = [re.sub(r"\s+Equity$", "", c.text).strip() for c in cells]
    bad = [c for c, t in zip(cells, tickers, strict=True) if not _TICKER_RE.match(t)]
    if bad:
        return ParsedField.invalid("underlyings", bad, "彭博代號格式無法辨識")
    return ParsedField.present("underlyings", tickers, [lines[h], *cells])


_PRICE_START = r"^「最初價格」詳見下表所示"
_PRICE_END = r"^(「表現最差之標的資產」指|發行機構應以實物交割時)"
_SCENARIO_START = r"本商品標的資產之相關資訊"
_SCENARIO_END = r"^情境分析結果不保證"


def _price_table(
    lines: list[Line], name: str = "price_table", start: str = _PRICE_START, end_pat: str = _PRICE_END
) -> tuple[ParsedField, list[PriceRow]]:
    """各標的價格表（§15；§16(3) 情境分析重印）。表格可能跨頁；欄位依表頭第一行的 x 中心指派。"""
    starts = [i for i, ln in enumerate(lines) if re.search(start, ln.text)]
    if len(starts) != 1:
        if not starts:
            return ParsedField.missing(name, "找不到價格表開始錨點"), []
        return ParsedField.ambiguous(name, [], [lines[i] for i in starts], "價格表開始錨點出現多次"), []
    s = starts[0]
    end = next((i for i in range(s + 1, len(lines)) if re.match(end_pat, lines[i].text)), None)
    if end is None:
        return ParsedField.missing(name, "找不到價格表結束錨點"), []
    seg = lines[s:end]
    cols: dict[str, Line] = {}
    for ln in seg:
        if ln.text.startswith("「"):
            continue
        for key, pat in PRICE_COLUMNS:
            if pat.match(ln.text):
                if key in cols:
                    return ParsedField.ambiguous(name, [], [cols[key], ln], f"價格表表頭「{key}」出現多次"), []
                cols[key] = ln
    if "initial" not in cols or "strike" not in cols or "ko" not in cols:
        return ParsedField.missing(name, "價格表表頭不完整"), []
    header_y = {(c.page, c.y0) for c in cols.values()}
    nums = [ln for ln in seg if _NUM_RE.match(ln.text)]
    groups: list[list[Line]] = []
    for ln in nums:
        for g in groups:
            if g[0].page == ln.page and abs(g[0].y0 - ln.y0) < 3:
                g.append(ln)
                break
        else:
            groups.append([ln])
    if not groups:
        return ParsedField.missing(name, "價格表沒有數值"), []
    rows: list[PriceRow] = []
    name_limit = cols["initial"].x0 - 5
    for g in groups:
        vals: dict[str, Decimal] = {}
        for c in g:
            key, hdr = min(cols.items(), key=lambda kv: abs(kv[1].xc - c.xc))
            if abs(hdr.xc - c.xc) > 60 or key in vals:
                return ParsedField.invalid(name, g, "價格表數值無法對應到唯一欄位"), []
            try:
                vals[key] = Decimal(c.text.replace(",", ""))
            except InvalidOperation:
                return ParsedField.invalid(name, [c], "價格表數值無法辨識"), []
        if set(vals) != set(cols):
            return ParsedField.invalid(name, g, "價格表某列缺少欄位"), []
        y = g[0].y0
        name_lines = [
            ln
            for ln in seg
            if ln.page == g[0].page
            and ln.x1 <= name_limit
            and -3 <= ln.y0 - y <= 16
            and not _NUM_RE.match(ln.text)
            and (ln.page, ln.y0) not in header_y
            and ln.text != "標的資產"
        ]
        rows.append(PriceRow(join_text(name_lines), vals, sorted(g, key=lambda x: x.x0)))
    field_ = ParsedField.present(name, len(rows), [cols[k] for k in cols] + [ln for r in rows for ln in r.lines])
    return field_, rows


_MONTHLY_PATTERNS = {
    "第9條": r"每月之配息率[（(]?為([\d.]+)%",
    "第14條": r"每月之配息率[（(]?為([\d.]+)%",
    "第15條(2)": r"商品面額×([\d.]+)%\(顯示",
    "第17條": r"每月之配息率[（(]?為([\d.]+)%",
}
_ANNUAL_PATTERNS = {
    "第9條": r"年利率為([\d.]+)%",
    "第14條": r"年利率為([\d.]+)%",
    "第17條": r"年利率為([\d.]+)%",
}


def _mentions(doc: Document, arts: dict[int, Span], patterns: dict[str, str]) -> list[Mention]:
    out: list[Mention] = []
    for label, pat in patterns.items():
        n = int(re.search(r"\d+", label).group(0))
        ti = TextIndex(doc.span_lines(arts.get(n)))
        found = False
        for m in ti.finditer(pat):
            out.append(Mention(label, Decimal(m.group(1)), ti.lines_for(m.start(), m.end())))
            found = True
        if not found:
            out.append(Mention(label, Decimal("NaN"), []))
    return out


def _coupon(doc: Document, art14: Span | None) -> tuple[ParsedField, ParsedField]:
    ti = TextIndex(doc.span_lines(art14))
    hits_m, hits_a = [], []
    for m in ti.finditer(r"「配息率」係指每月之配息率為([\d.]+)%.*?年利率為([\d.]+)%"):
        lns = ti.lines_for(m.start(), m.end())
        hits_m.append((Decimal(m.group(1)), lns))
        hits_a.append((Decimal(m.group(2)), lns))
    note = "第 14 條找不到「配息率」係指…年利率為…"
    return _distinct("monthly_coupon_pct", hits_m, note), _distinct("coupon_pa_pct", hits_a, note)


def _content(doc: Document, sp: Span) -> list[Line]:
    """子項內容行（去掉單獨一行的標號，例如「(2)」）。"""
    return [ln for ln in doc.span_lines(sp) if not re.fullmatch(r"\(\d{1,2}\)", ln.text)]


def _s13_date(doc: Document, subs: dict[int, Span], n: int, name: str) -> ParsedField:
    sp = subs.get(n)
    if sp is None:
        return ParsedField.missing(name, f"找不到第 13 條第 ({n}) 項")
    lines = _content(doc, sp)
    text = join_text(lines)
    label = S13_LABELS[n - 1]
    m = re.search(rf"{label}[†*]*[:：]", text)
    if not m:
        return ParsedField.missing(name, f"第 13 條第 ({n}) 項找不到「{label}：」")
    return _first_date_field(name, lines, text[m.end() :])


def _tenor(doc: Document, subs: dict[int, Span]) -> ParsedField:
    sp = subs.get(1)
    if sp is None:
        return ParsedField.missing("tenor_months", "找不到第 13 條第 (1) 項")
    lines = _content(doc, sp)
    m = re.search(r"商品年期[:：]\s*(\d+)\s*個月", join_text(lines))
    if not m:
        return ParsedField.missing("tenor_months", "第 13 條第 (1) 項找不到「N 個月」")
    return ParsedField.present("tenor_months", int(m.group(1)), lines)


def _denomination(doc: Document, art6: Span | None) -> ParsedField:
    lines = doc.span_lines(art6)
    m = re.search(r"面額為([\d,]+)\s*(\S+?)。", join_text(lines))
    if not m:
        return ParsedField.missing("denomination", "第 6 條找不到「面額為…」")
    return ParsedField.present("denomination", int(m.group(1).replace(",", "")), lines)


def _labelled_date(lines: Sequence[Line], pattern: str, name: str, where: str) -> ParsedField:
    hits = []
    for ln in lines:
        m = re.search(pattern, ln.text)
        if m:
            d = parse_date(ln.text[m.end() :])
            if d is None:
                return ParsedField.invalid(name, [ln], f"{where}日期無法辨識")
            hits.append((d, [ln]))
    return _distinct(name, hits, f"找不到{where}")


def _amount(lines: Sequence[Line], pattern: str, name: str) -> ParsedField:
    ti = TextIndex(lines)
    hits = [(int(m.group(1).replace(",", "")), ti.lines_for(m.start(), m.end())) for m in ti.finditer(pattern)]
    return _distinct(name, hits, "第四章找不到此金額")


def _chairman(doc: Document) -> ParsedField:
    arts = doc.articles(2)
    target = [sp for sp in arts.values() if sp.title.startswith("受託或銷售機構")]
    if len(target) != 1:
        return (
            ParsedField.missing("chairman", "第二章找不到「受託或銷售機構」條")
            if not target
            else ParsedField.ambiguous(
                "chairman", [], [doc.lines[sp.start] for sp in target], "「受託或銷售機構」條出現多次"
            )
        )
    hits = []
    for ln in doc.span_lines(target[0]):
        m = re.match(r"^負責人姓名[:：]\s*(.+)$", ln.text)
        if m:
            hits.append((m.group(1).strip(), [ln]))
    return _distinct("chairman", hits, "「受託或銷售機構」條找不到「負責人姓名」")


# ---------------------------------------------------------------- 入口


def parse(lines: Sequence[Line]) -> tuple[DetectionResult, BarcTermSheet]:
    doc = Document(lines)
    det = detect(doc)
    flds: dict[str, ParsedField] = {}
    all_lines = list(lines)

    flds["product_code"] = product_code(all_lines)
    flds["currency_zh"] = _cover_field("currency_zh", all_lines, "計價幣別", lambda s: squash(s))
    flds["name_zh"] = _cover_field("name_zh", all_lines, "商品中文名稱")
    flds["name_en"] = _cover_field("name_en", all_lines, "商品英文名稱", lambda s: re.sub(r"\s+", " ", s).strip())
    flds["approval_date"] = _cover_field("approval_date", all_lines, "受託或銷售機構審查通過之日期", parse_date)
    flds["print_date"] = _labelled_date(doc.before_chapter1(), r"刊印日期[:：]", "print_date", "「刊印日期」")

    arts = doc.articles(1)
    s13 = doc.subitems(arts.get(13))
    flds["tenor_months"] = _tenor(doc, s13)
    flds["trade_date"] = _s13_date(doc, s13, 2, "trade_date")
    flds["issue_date"] = _s13_date(doc, s13, 3, "issue_date")
    flds["final_valuation_date"] = _s13_date(doc, s13, 4, "final_valuation_date")
    flds["maturity_date"] = _s13_date(doc, s13, 5, "maturity_date")
    flds["ko_observation"] = _ko_observation(doc, arts.get(13))
    flds["denomination"] = _denomination(doc, arts.get(6))
    flds["underlyings"] = _underlyings(doc, arts.get(10))
    flds["monthly_coupon_pct"], flds["coupon_pa_pct"] = _coupon(doc, arts.get(14))

    s15 = doc.span_lines(arts.get(15))
    flds["strike_pct"] = _definition_pct("strike_pct", s15, "執行價格")
    flds["ko_pct"], flds["ko_memory"] = _ko_pct_and_memory(s15, flds["name_zh"])
    flds["ki_pct"] = _definition_pct("ki_pct", s15, "觸及生效價格")
    flds["ki_type"] = _ki_type(s15, flds["ki_pct"], flds["strike_pct"])
    flds["price_table"], rows = _price_table(s15)
    flds["scenario_price_table"], scenario_rows = _price_table(
        doc.span_lines(arts.get(16)), "scenario_price_table", _SCENARIO_START, _SCENARIO_END
    )

    tables = schedule.find_tables(doc, arts.get(13))
    flds["coupon_table"] = schedule.coupon_table(tables)
    flds["ko_table"] = schedule.ko_table(tables, flds["ko_observation"])
    flds["guaranteed_periods"] = schedule.guaranteed_periods(flds["ko_table"])
    flds["guaranteed_periods_text"] = schedule.guaranteed_periods_text(doc, arts.get(13))

    flds["subscription_start_date"] = _labelled_date(
        doc.chapter_lines(4), r"^商品開始受理申購日期[:：]", "subscription_start_date", "第四章「商品開始受理申購日期」"
    )
    flds["chairman"] = _chairman(doc)
    ch4 = doc.chapter_lines(4)
    flds["min_subscription"] = _amount(ch4, r"最低申購金額依受託或銷售機構規定，至少為([\d,]+)", "min_subscription")
    flds["min_redemption"] = _amount(ch4, r"最低贖回商品面額為([\d,]+)", "min_redemption")

    mentions = {
        "monthly": _mentions(doc, arts, _MONTHLY_PATTERNS),
        "annual": _mentions(doc, arts, _ANNUAL_PATTERNS),
    }
    return det, BarcTermSheet(flds, rows, mentions, TextIndex(all_lines), doc, scenario_rows)
