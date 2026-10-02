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

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from . import barc_schedule as schedule
from .layout import Document, LayoutSpec, Span, TextIndex, join_text, parse_date, squash

TEMPLATE_ID = "barc-zh-pd"
PARSER_VERSION = "2"

# 章名「第一章 商品基本資料」、條號「1.」、子項「(1)」（範本規格 §3）
LAYOUT = LayoutSpec(
    chapter_pattern=r"第{zh}章\s*{name}",
    article_re=re.compile(r"^(\d{1,2})\.$"),
    article_max_x=62.0,
    subitem_re=re.compile(r"^\((\d{1,2})\)\s*(.*)$"),
    subitem_max_x=100.0,
)

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
class HeaderPct:
    """價格表欄頭「X（為最初價格的N%）」；field 為對應的定義欄位（strike_pct／ko_pct／ki_pct）。"""

    field: str
    mention: Mention


@dataclass
class BarcTermSheet:
    fields: dict[str, ParsedField]
    price_rows: list[PriceRow]
    coupon_mentions: dict[str, list[Mention]]  # monthly / annual / repeat（§9(3)、§16 重複出現的月配息率）
    full_text: TextIndex
    document: Document
    scenario_rows: list[PriceRow] = field(default_factory=list)
    header_pcts: list[HeaderPct] = field(default_factory=list)

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


def _isin(text: str) -> str | None:
    v = squash(text)
    return v if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", v) else None


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


def document(lines: Sequence[Line]) -> Document:
    return Document(lines, LAYOUT)


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


def _standard_prices(table: ParsedField, rows: list[PriceRow]) -> ParsedField:
    """標準欄位 `underlying_prices`：§15 價格表各列（表上沒有代號，以 `underlyings` 同順序為準）。"""
    name = "underlying_prices"
    if not table.ok:
        return ParsedField(name, table.status, None, list(table.evidence), list(table.candidates), table.note)
    value = tuple(
        standard_fields.PriceRow(None, dict(r.values), tuple(Evidence.of(ln) for ln in r.lines)) for r in rows
    )
    return ParsedField(name, FieldStatus.PRESENT, value, list(table.evidence))


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


def _ch2_item(
    doc: Document, article: str, label: str, name: str, convert: Callable[[str], Any] = str.strip
) -> ParsedField:
    """第二章某條（標題以 article 開頭）中「label：值」；值換行時接續同一縮排的下一行。"""
    target = [sp for sp in doc.articles(2).values() if sp.title.startswith(article)]
    if len(target) != 1:
        return (
            ParsedField.missing(name, f"第二章找不到「{article}」條")
            if not target
            else ParsedField.ambiguous(name, [], [doc.lines[sp.start] for sp in target], f"「{article}」條出現多次")
        )
    lines = doc.span_lines(target[0])
    hits = []
    for i, ln in enumerate(lines):
        m = re.match(rf"^{label}[:：]\s*(.+)$", ln.text)
        if not m:
            continue
        block = [ln]
        for nxt in lines[i + 1 :]:
            if abs(nxt.x0 - ln.x0) > 3 or re.match(r"^[^:：]{2,12}[:：]", nxt.text):
                break
            block.append(nxt)
        hits.append((convert(m.group(1) + join_text(block[1:])), block))
    return _distinct(name, hits, f"「{article}」條找不到「{label}」")


def _chairman(doc: Document) -> ParsedField:
    return _ch2_item(doc, "受託或銷售機構", "負責人姓名", "chairman")


# ---------------------------------------------------------------- 文件內重複出現處（Issue #38）


def _title_name(lines: Sequence[Line]) -> ParsedField:
    """p1 封面標題（「中文產品說明書」之後、第一個封面標籤之前）的商品名稱。"""
    p1 = [ln for ln in lines if ln.page == 1]
    title = next((ln for ln in p1 if "中文產品說明書" in ln.text), None)
    label = next((ln for ln in p1 if ln.x0 < _COVER_LABEL_MAX_X and re.match(r"^商品代號\s*[:：]", ln.text)), None)
    if title is None or label is None:
        return ParsedField.missing("title_name", "p1 找不到封面標題範圍")
    body = [ln for ln in p1 if title.y0 + 3 < ln.y0 < label.y0 - 3]
    if not body:
        return ParsedField.missing("title_name", "p1 封面標題沒有商品名稱")
    return ParsedField.present("title_name", squash(join_text(body)), body)


def _article_value(doc: Document, sp: Span | None, label: str, name: str) -> ParsedField:
    """第一章某條「label：值」（值去空白）。"""
    lines = [ln for ln in doc.span_lines(sp) if not re.fullmatch(r"\d{1,2}\.", ln.text)]
    m = re.search(rf"{label}[:：](.+)$", squash(join_text(lines)))
    if not m:
        return ParsedField.missing(name, f"找不到「{label}：」")
    return ParsedField.present(name, m.group(1), lines)


def _issue_price(doc: Document, art6: Span | None) -> ParsedField:
    lines = doc.span_lines(art6)
    m = re.search(r"發行價格為商品面額之([\d.]+)%", squash(join_text(lines)))
    if not m:
        return ParsedField.missing("issue_price_pct", "第 6 條找不到「發行價格為商品面額之…%」")
    return ParsedField.present("issue_price_pct", Decimal(m.group(1)), lines)


def _distributor_cover(lines: list[Line]) -> dict[str, ParsedField]:
    """封面「受託或銷售機構之名稱、電話及地址」→ 名稱、電話、地址。"""
    keys = ("distributor_name_cover", "distributor_phone_cover", "distributor_address_cover")
    raw = _cover_value(lines, "受託或銷售機構之名稱、電話及地址")
    if not raw or not raw[0][0]:
        return {k: ParsedField.missing(k, "封面找不到「受託或銷售機構之名稱、電話及地址」") for k in keys}
    if len(raw) > 1:
        return {k: ParsedField.ambiguous(k, [], [ln for _, lns in raw for ln in lns], "封面此欄出現多次") for k in keys}
    text, lns = raw[0]
    m = re.fullmatch(r"(.+?)，電話[:：](.+?)，地址[:：](.+)", squash(text))
    if not m:
        return {k: ParsedField.invalid(k, lns, "無法拆成「名稱，電話：…，地址：…」") for k in keys}
    return {k: ParsedField.present(k, v, lns) for k, v in zip(keys, m.groups(), strict=True)}


FEE_LABELS = ("申購費用", "提前贖回費用", "管理費用", "分銷費用", "保費費用", "解約費用", "其他費用")
_FEE_RANGE = re.compile(r"([\d.]+%~[\d.]+%)")


def _fees(ch4: list[Line]) -> dict[str, ParsedField]:
    """第四章「投資人應負擔的各項費用」表：各費用項目的費率區間（費率欄，位於費用項目欄與收取時點欄之間）。"""
    out: dict[str, ParsedField] = {}
    start = next((i for i, ln in enumerate(ch4) if ln.text.startswith("投資人應負擔的各項費用")), None)
    if start is None:
        return {f"fee_{lab}": ParsedField.missing(f"fee_{lab}", "第四章找不到費用表") for lab in FEE_LABELS}
    seg = ch4[start:]
    end = next((i for i, ln in enumerate(seg) if ln.text.startswith("附註")), len(seg))
    seg = seg[:end]
    item_hdr = next((ln for ln in seg if ln.text == "費用項目"), None)
    when_hdr = next((ln for ln in seg if ln.text == "收取時點"), None)
    if item_hdr is None or when_hdr is None:
        return {f"fee_{lab}": ParsedField.missing(f"fee_{lab}", "費用表表頭不完整") for lab in FEE_LABELS}
    rows = [ln for ln in seg if ln.x1 <= item_hdr.x1 + 80 and ln.x0 < item_hdr.x0 and ln.text.startswith(FEE_LABELS)]
    for k, row in enumerate(rows):
        label = next(lab for lab in FEE_LABELS if row.text.startswith(lab))
        nxt = rows[k + 1] if k + 1 < len(rows) else None
        cells = [
            ln
            for ln in seg
            if ln.page == row.page
            and item_hdr.x1 < ln.x0 < when_hdr.x0 - 15
            and row.y0 - 3 <= ln.y0
            and (nxt is None or nxt.page != row.page or ln.y0 < nxt.y0 - 3)
        ]
        ranges = _FEE_RANGE.findall(squash(join_text(cells)))
        name = f"fee_{label}"
        if not ranges:
            out[name] = ParsedField.missing(name, f"「{label}」沒有費率區間")
        else:
            out[name] = _distinct(name, [(r, [row, *cells]) for r in ranges])
    for lab in FEE_LABELS:
        out.setdefault(f"fee_{lab}", ParsedField.missing(f"fee_{lab}", f"費用表找不到「{lab}」"))
    return out


def _scenario_notional(ti: TextIndex) -> ParsedField:
    hits = [
        (int(m.group(1).replace(",", "")), ti.lines_for(m.start(), m.end()))
        for m in ti.finditer(r"每單位商品面額=([\d,]+)")
    ]
    return _distinct("scenario_notional", hits, "第 16 條找不到情境假設「每單位商品面額 =」")


_HEADER_PCT = re.compile(r"(執行價格|觸及生效價格|觸發水準)（為最初價格的([\d.]+)%")
_HEADER_FIELD = {"執行價格": "strike_pct", "觸及生效價格": "ki_pct", "觸發水準": "ko_pct"}


def _header_pcts(ti: TextIndex, article: str) -> list[HeaderPct]:
    """價格表欄頭的百分比（定義句「X」詳見下表所示（…）不符合此寫法，不會被重複計入）。"""
    return [
        HeaderPct(_HEADER_FIELD[m.group(1)], Mention(article, Decimal(m.group(2)), ti.lines_for(m.start(), m.end())))
        for m in ti.finditer(_HEADER_PCT)
    ]


# §16 情境試算中月配息率的寫法：(100% + M%)、乘以M%之配息率、× M%(四捨五入…)、x M% x n
_SCENARIO_MONTHLY = (
    r"100%\+([\d.]+)%",
    r"乘以([\d.]+)%之配息率",
    r"[×xX]([\d.]+)%(?=\(四捨五|[×xX]\d)",
)


def _repeat_mentions(doc: Document, arts: dict[int, Span], s16: TextIndex) -> list[Mention]:
    """月配息率在 §9(3)「相關配息率」與 §16 情境試算中的每次出現。

    §9(3) 只有期間每日觀察型態才列出相關配息率（期末定日型態以文字定義，沒有數值），找不到時不列；
    期間每日觀察型態找不到時由規則轉人工覆核。
    §16 一定有情境試算；找不到時以 NaN 標記，交由規則轉人工覆核。
    """
    out: list[Mention] = []
    s9 = doc.subitems(arts.get(9))
    if 3 in s9:
        ti = TextIndex(doc.span_lines(s9[3]))
        # 每一處「相關配息率為」後面都必須讀得到數值；讀不到以 NaN 標記，交由規則轉人工覆核
        for m in ti.finditer(r"相關配息率為"):
            v = re.match(r"[:：]?([\d.]+)%", ti.text[m.end() :])
            value = Decimal(v.group(1)) if v else Decimal("NaN")
            end = m.end() + (v.end() if v else 0)
            out.append(Mention("第9條(3)", value, ti.lines_for(m.start(), end)))
    found = sorted(
        (m for pat in _SCENARIO_MONTHLY for m in s16.finditer(pat)),
        key=lambda m: m.start(),
    )
    for m in found:
        out.append(Mention("第16條", Decimal(m.group(1)), s16.lines_for(m.start(), m.end())))
    if not found:
        out.append(Mention("第16條", Decimal("NaN"), []))
    return out


_RETURN_RE = re.compile(r"\]-1=(-?[\d.]+)%\(平均年化報酬率[:：](-?[\d.]+)%\)")
_CASES = ("(i)有利情況", "(ii)一般情況", "(iii)最差情況")


def _scenario_returns(ti: TextIndex) -> dict[str, ParsedField]:
    """§16(3) 有利、一般情況的總報酬率與平均年化報酬率；有利情況另記配息期數（相關配息率倍數＋累積配息期數）。"""
    text = ti.text
    pos = [text.find(c) for c in _CASES]
    out: dict[str, ParsedField] = {}
    for k, name in ((0, "scenario_favourable"), (1, "scenario_general")):
        if pos[k] < 0 or pos[k + 1] < pos[k]:
            out[name] = ParsedField.missing(name, f"第 16 條找不到「{_CASES[k]}」或「{_CASES[k + 1]}」")
            continue
        seg_start, seg = pos[k], text[pos[k] : pos[k + 1]]
        returns = list(_RETURN_RE.finditer(seg))
        if len(returns) != 1:
            note = "找不到總報酬率算式" if not returns else "總報酬率算式出現多次"
            out[name] = ParsedField.missing(name, note) if not returns else ParsedField.ambiguous(name, [], [], note)
            continue
        r = returns[0]
        value: dict[str, Any] = {"total": Decimal(r.group(1)), "annualized": Decimal(r.group(2))}
        if k == 0:
            rel = re.search(r"100%\+[\d.]+%(?:[×xX](\d+))?", seg)
            acc = re.search(r"[×xX][\d.]+%[×xX](\d+)", seg)
            if rel is None:
                out[name] = ParsedField.missing(name, "有利情況找不到「100% + 相關配息率」算式")
                continue
            value["periods"] = int(rel.group(1) or 1) + (int(acc.group(1)) if acc else 0)
        out[name] = ParsedField.present(name, value, ti.lines_for(seg_start + r.start(), seg_start + r.end()))
    return out


# ---------------------------------------------------------------- 入口


def parse(lines: Sequence[Line]) -> tuple[DetectionResult, BarcTermSheet]:
    doc = document(lines)
    det = detect(doc)
    flds: dict[str, ParsedField] = {}
    all_lines = list(lines)

    flds["product_code"] = product_code(all_lines)
    flds["isin"] = _cover_field("isin", all_lines, "ISIN", _isin)
    flds["currency_zh"] = _cover_field("currency_zh", all_lines, "計價幣別", lambda s: squash(s))
    flds["name_zh"] = _cover_field("name_zh", all_lines, "商品中文名稱")
    flds["name_en"] = _cover_field("name_en", all_lines, "商品英文名稱", lambda s: re.sub(r"\s+", " ", s).strip())
    flds["approval_date"] = _cover_field("approval_date", all_lines, "受託或銷售機構審查通過之日期", parse_date)
    flds["print_date"] = _labelled_date(doc.before_chapter1(), r"刊印日期[:：]", "print_date", "「刊印日期」")
    flds["title_name"] = _title_name(all_lines)
    flds["distributor_product_code"] = _cover_field(
        "distributor_product_code", all_lines, "受託或銷售機構商品代號", squash
    )
    flds["issuer_name_cover"] = _cover_field("issuer_name_cover", all_lines, "發行機構", squash)
    flds.update(_distributor_cover(all_lines))

    arts = doc.articles(1)
    s13 = doc.subitems(arts.get(13))
    flds["tenor_months"] = _tenor(doc, s13)
    flds["trade_date"] = _s13_date(doc, s13, 2, "trade_date")
    flds["issue_date"] = _s13_date(doc, s13, 3, "issue_date")
    flds["final_valuation_date"] = _s13_date(doc, s13, 4, "final_valuation_date")
    flds["maturity_date"] = _s13_date(doc, s13, 5, "maturity_date")
    flds["ko_observation"] = _ko_observation(doc, arts.get(13))
    flds["denomination"] = _denomination(doc, arts.get(6))
    flds["issue_price_pct"] = _issue_price(doc, arts.get(6))
    flds["art1_name"] = _article_value(doc, arts.get(1), "商品名稱", "art1_name")
    art5 = _article_value(doc, arts.get(5), "計價幣別", "art5_currency")
    if art5.ok:  # 人民幣後面可能接付款帳戶說明：「人民幣(本商品以人民幣支付…)」
        art5.value = re.split(r"[(（]", art5.value, maxsplit=1)[0]
    flds["art5_currency"] = art5
    flds["underlyings"] = _underlyings(doc, arts.get(10))
    flds["monthly_coupon_pct"], flds["coupon_pa_pct"] = _coupon(doc, arts.get(14))

    s15 = doc.span_lines(arts.get(15))
    flds["strike_pct"] = _definition_pct("strike_pct", s15, "執行價格")
    flds["ko_pct"], flds["ko_memory"] = _ko_pct_and_memory(s15, flds["name_zh"])
    flds["ki_pct"] = _definition_pct("ki_pct", s15, "觸及生效價格")
    flds["ki_type"] = _ki_type(s15, flds["ki_pct"], flds["strike_pct"])
    flds["price_table"], rows = _price_table(s15)
    flds["underlying_prices"] = _standard_prices(flds["price_table"], rows)
    flds["scenario_price_table"], scenario_rows = _price_table(
        doc.span_lines(arts.get(16)), "scenario_price_table", _SCENARIO_START, _SCENARIO_END
    )

    tables = schedule.find_tables(doc, arts.get(13))
    flds["coupon_table"] = schedule.coupon_table(tables)
    flds["ko_table"] = schedule.ko_table(tables, flds["ko_observation"])
    flds["guaranteed_periods"] = schedule.guaranteed_periods(flds["ko_table"])
    flds["guaranteed_periods_text"] = schedule.guaranteed_periods_text(doc, arts.get(13))
    flds["observation_t_ranges"] = schedule.observation_t_ranges(doc, arts.get(13))

    s16 = TextIndex(doc.span_lines(arts.get(16)))
    flds["scenario_notional"] = _scenario_notional(s16)
    flds.update(_scenario_returns(s16))
    header_pcts = _header_pcts(TextIndex(s15), "第15條") + _header_pcts(s16, "第16條")

    flds["subscription_start_date"] = _labelled_date(
        doc.chapter_lines(4), r"^商品開始受理申購日期[:：]", "subscription_start_date", "第四章「商品開始受理申購日期」"
    )
    flds["chairman"] = _chairman(doc)
    sq = lambda s: squash(s)  # noqa: E731
    flds["issuer_name_ch2"] = _ch2_item(doc, "發行機構", "事業名稱", "issuer_name_ch2", sq)
    flds["distributor_name_ch2"] = _ch2_item(doc, "受託或銷售機構", "事業名稱", "distributor_name_ch2", sq)
    flds["distributor_address_ch2"] = _ch2_item(doc, "受託或銷售機構", "營業所在地", "distributor_address_ch2", sq)
    ch4 = doc.chapter_lines(4)
    flds.update(_fees(ch4))
    flds["min_subscription"] = _amount(ch4, r"最低申購金額依受託或銷售機構規定，至少為([\d,]+)", "min_subscription")
    flds["min_redemption"] = _amount(ch4, r"最低贖回商品面額為([\d,]+)", "min_redemption")

    mentions = {
        "monthly": _mentions(doc, arts, _MONTHLY_PATTERNS),
        "annual": _mentions(doc, arts, _ANNUAL_PATTERNS),
        "repeat": _repeat_mentions(doc, arts, s16),
    }
    return det, BarcTermSheet(flds, rows, mentions, TextIndex(all_lines), doc, scenario_rows, header_pcts)
