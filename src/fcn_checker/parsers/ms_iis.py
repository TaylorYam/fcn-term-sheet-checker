"""MS 中文投資人須知（docs/templates/ms-zh-iis.md）：段落標題＋標籤定位，只支援新版範本。

依 8 份真實樣本建立（新版 6 份；舊版 2 份辨識失敗，轉人工覆核）。範本沒有商品代號、面額、最低申購金額、發行價格、
標的彭博代碼、年利率與各標的價格；另外寫出交易日、期末定價日與提前出場、觸及下限條件（核對規則 §3 D 類）。
每頁底部「第 N 頁，共 M頁」不算內文；第 1 頁右上角的刊印日期在擷取順序上排在第 1 頁最後，讀其他欄位時先排除，
避免插進跨頁的文字。KO／KI 寫法只在「贖回價金之計算」段內判斷（主要投資風險段也有類似句子）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import iis, ms_wording
from .layout import TextIndex, parse_date

TEMPLATE_ID = "ms-zh-iis"
PARSER_VERSION = "1"
SECTIONS = (
    "本商品之投資風險警語：",
    "相關機構：",
    "境外結構型商品事項：",
    "商品簡介：",
    "收益分配事項：",
    "贖回價金之計算：",
    "主要投資風險：",
    "費用：",
)
PAGE_FOOTER = re.compile(r"-?\s*第\s*\d+\s*頁，共\s*\d+\s*頁")
PRINT_DATE = "中文投資人須知刊印日期"
NAME_EN = "MorganStanley&Co.InternationalPlcissuanceof"  # 新版（舊版為 plc）
REDEMPTION_SECTION = ("贖回價金之計算：", "發行不成立之處理")  # KO／KI 寫法所在的段落（開頭、結尾錨點）
COUPON_SECTION = ("收益分配事項：", "贖回價金之計算：")  # 月配息率所在的段落（開頭、結尾錨點）
PROVIDED = frozenset(
    {
        "page_totals",
        "isin",
        "name_zh",
        "name_en",
        "product_type",
        "currency_zh",
        "underlying_names",
        "tenor_months",
        "trade_date",
        "issue_date",
        "maturity_date",
        "final_valuation_date",
        "redemption_start",
        "monthly_coupons",
        "ko_observation",
        "ko_memory",
        "first_callable_period",
        "ki_type",
        "risk_level_summary",
        "print_dates",
        "issuer_names",
        "issuer_names_zh",
        "distributor_names",
        "distributor_addresses",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """內文：去掉每頁底部「第 N 頁，共 M頁」。"""
    return [x for x in lines if not PAGE_FOOTER.fullmatch(x.text)]


def detect(lines: Sequence[Line]) -> DetectionResult:
    text = TextIndex(body(lines)).text
    failed = []
    for ok, message in (
        ("中文投資人須知" in text, "封面缺少「中文投資人須知」"),
        ("商品中文名稱：英商摩根士丹利發行" in text, "商品中文名稱不是「英商摩根士丹利發行…」"),
        (
            "商品英文名稱：" + NAME_EN in text,
            "商品英文名稱不是新版寫法「Morgan Stanley & Co. International Plc issuance of…」（舊版範本不支援）",
        ),
        (
            re.search(r"相關機構：?(?:1\.)?發行機構：英商摩根士丹利國際股份有限公司", text) is not None,
            "相關機構的發行機構不是MS",
        ),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in body(lines)[:2]])


def _section(ti: TextIndex, anchors: tuple[str, str]) -> TextIndex | None:
    """兩個錨點之間（含錨點所在行）的文字；找不到或錨點重複時為 None。"""
    start, end = anchors
    if ti.text.count(start) != 1:
        return None
    a = ti.text.index(start)
    b = ti.text.find(end, a)
    if b < 0:
        return None
    return TextIndex(ti.lines_for(a, b + len(end)))


def _underlying_names(ti: TextIndex) -> ParsedField:
    """商品簡介「連結標的資產：(1) 連結標的1: 甲公司(ALPHA INC) (2) …」；單一標的寫「(1) 連結標的: 甲公司(…)」。
    名稱保留英文之間的空白（比對時忽略空白）。"""
    name = "underlying_names"
    found = iis.raw(ti, r"連結標的資產[:：].+?商品年期")
    if found is None:
        return ParsedField.missing(name, "找不到「連結標的資產」")
    text, lns = found
    m = re.search(r"連結標的資產\s*[:：]\s*(.+?)\s*(?:\d+\))?\s*商品年期", text)
    parts = re.split(r"\((\d+)\)\s*連結標的\s*\d*\s*[:：]\s*", m[1]) if m else []
    labels, names = parts[1::2], [re.sub(r"\s+", " ", x).strip() for x in parts[2::2]]
    if not names or parts[0].strip() or labels != [str(i) for i in range(1, len(names) + 1)] or not all(names):
        return ParsedField.invalid(name, lns, "連結標的資產不是「(1) 連結標的1: 名稱 (2) …」的寫法")
    return ParsedField.present(name, names, lns)


def _ko_terms(sec: TextIndex | None) -> dict[str, ParsedField]:
    """KO 觀察方式、第一個可提前出場期、記憶式，D 型另有觀察起訖日（範本規格 §4.1、§4.2；句型見 ms_wording.py）。

    記憶式只看有沒有記憶事件觀察日（S04-IIS 沒有記憶事件定義句）。
    """
    names = ("ko_observation", "first_callable_period", "ko_memory")
    if sec is None:
        return {n: ParsedField.missing(n, "找不到「贖回價金之計算」段落") for n in names}
    lns = sec.lines
    out: dict[str, ParsedField] = {}
    note = "記憶事件寫法缺漏或不在範本規格內"
    out["ko_memory"] = ms_wording.ko_memory(sec, definition_required=False, note=note)
    found = ms_wording.ko_observations(sec)
    if len(found) != 1:
        note = "自動提前出場的觀察日寫法缺漏、重複或不在範本規格內"
        out.update({n: ParsedField.invalid(n, lns, note) for n in ("ko_observation", "first_callable_period")})
        return out
    m, kind = found[0]
    ev = sec.lines_for(m.start(), m.end())
    out["ko_observation"] = ParsedField.present("ko_observation", kind, ev)
    out["first_callable_period"] = ParsedField.present("first_callable_period", int(m[1]), ev)
    if kind == "D":
        for key, group in (("ko_observation_start", 2), ("ko_observation_end", 3)):
            d = parse_date(m[group])
            out[key] = (
                ParsedField.present(key, d, sec.lines_for(m.start(group), m.end(group)))
                if d
                else ParsedField.invalid(key, ev, f"「{m[group]}」不是日期")
            )
    return out


def _ki_type(sec: TextIndex | None) -> ParsedField:
    """「觸及下限事件」的觀察寫法；沒有這句且到期贖回只有 ≥／< 執行價兩種寫法時為無 KI（範本規格 §4.3）。"""
    if sec is None:
        return ParsedField.missing("ki_type", "找不到「贖回價金之計算」段落")
    return ms_wording.ki_type(sec, missing_note="找不到觸及下限事件，也不是無 KI 的到期贖回寫法")


def _monthly_coupons(sec: TextIndex | None, ko: ParsedField) -> ParsedField:
    """收益分配事項的固定配息率；D 型另有提前出場配息公式 {X%×n(j)/N(j)}（範本規格 §5）。"""
    if sec is None:
        return ParsedField.missing("monthly_coupons", "找不到「收益分配事項」段落")
    specs = [("monthly_coupon_fixed", "月配息率", "收益分配事項固定配息率", r"固定配息率[(（](\d+(?:\.\d+)?)%[)）]")]
    if (ko.ok and ko.value == "D") or (not ko.ok and "×n(j)/N(j)}" in sec.text):
        specs.append(
            (
                "monthly_coupon_formula",
                "月配息率（提前出場配息公式）",
                "收益分配事項提前出場配息公式",
                r"\{(\d+(?:\.\d+)?)%×n[(（]j[)）]/N[(（]j[)）]\}",
            )
        )
    return iis.occurrences("monthly_coupons", sec, specs, iis.number)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    full = TextIndex(body(lines))
    ti = TextIndex([x for x in body(lines) if not squash(x.text).startswith(PRINT_DATE)])  # 刊印日期另外讀
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines)}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 封面
    put("name_zh", r"商品中文名稱[:：](.+?)商品英文名稱[:：]")
    put("name_en", r"商品英文名稱[:：](.+?)\(ISIN[:：]")
    put("isin", r"\(ISIN[:：]([A-Z]{2}[A-Z0-9]{10})\)")
    put("product_type", r"商品種類[:：](.+?)本商品之投資風險警語")
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        full,
        (("print_date_iis", "刊印日期", "封面刊印日期", PRINT_DATE + "[:：]" + iis.DATE),),
        parse_date,
    )
    # 商品簡介
    put("risk_level_summary", r"本商品風險程度[:：](RR\d)")
    put("currency_zh", r"計價幣別[:：](.+?)[(（][A-Z]{3}[)）]")
    fields["underlying_names"] = _underlying_names(ti)
    put("tenor_months", r"商品年期[:：](\d+)個月期", int)
    put("trade_date", r"交易日[:：]" + iis.DATE, parse_date)
    put("issue_date", r"發行日[:：]" + iis.DATE, parse_date)
    put("maturity_date", r"到期日[:：]" + iis.DATE, parse_date)
    put("final_valuation_date", r"期末定價日[:：]" + iis.DATE, parse_date)
    put("redemption_start", r"開始受理贖回日期[:：]" + iis.DATE, parse_date)
    # 收益分配事項、贖回價金之計算
    redemption = _section(ti, REDEMPTION_SECTION)
    fields.update(_ko_terms(redemption))
    fields["ki_type"] = _ki_type(redemption)
    fields["monthly_coupons"] = _monthly_coupons(_section(ti, COUPON_SECTION), fields["ko_observation"])
    provided = PROVIDED | {k for k in ("ko_observation_start", "ko_observation_end") if k in fields}
    # 相關機構與警語（審查標準）
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (
            (
                "issuer_name_org",
                "發行機構名稱（相關機構）",
                "相關機構第 1 點",
                r"相關機構：(?:1\.)?發行機構[:：](.+?)；營業所在地",
            ),
        ),
    )
    fields["issuer_names_zh"] = iis.occurrences(  # 只寫中文的出處
        "issuer_names_zh",
        ti,
        (
            ("issuer_name_w5", "發行機構名稱（警語 5）", "警語 5)", r"5\)本商品持有期間.+?係由(.+?)（發行機構）保證"),
            ("issuer_name_w6", "發行機構名稱（警語 6）", "警語 6)", r"6\)本中文.+?或由發行機構(.+?)依法負責"),
            ("issuer_name_summary", "發行機構名稱（商品簡介）", "商品簡介第 4) 點", r"本商品發行機構為(.+?)，"),
        ),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (
            ("distributor_name_w4a", "受託機構名稱（警語 4）", "警語 4)", r"4\)商品雖經(.+?)審查"),
            ("distributor_name_w4b", "受託機構名稱（警語 4）", "警語 4)", r"之價值，且(.+?)不負本商品投資盈虧之責"),
            ("distributor_name_w4c", "受託機構名稱（警語 4）", "警語 4)", r"盈虧之責。(.+?)依法不得承諾"),
            ("distributor_name_w5", "受託機構名稱（警語 5）", "警語 5)", r"5\)本商品持有期間.+?而非由(.+?)所保證"),
            ("distributor_name_w6", "受託機構名稱（警語 6）", "警語 6)", r"係由受託機構[(（]即(.+?)[)）]負責外"),
            ("distributor_name_w9", "受託機構名稱（警語 9）", "警語 9)", r"9\)(.{1,30}?)應提供專業投資人"),
            (
                "distributor_name_org",
                "受託機構名稱（相關機構）",
                "相關機構第 3 點",
                r"3\.受託機構[:：](.+?)；營業所在地",
            ),
        ),
    )
    fields["distributor_addresses"] = iis.occurrences(
        "distributor_addresses",
        ti,
        (
            (
                "distributor_address_org",
                "受託機構地址（相關機構）",
                "相關機構第 3 點",
                r"3\.受託機構[:：].{1,30}?；營業所在地[:：](.+?)(?:[(（]營業活動所在地[)）])?二、境外結構型商品事項",
            ),
        ),
    )
    for label, pattern in (
        ("申購費用", r"申購費用[^%]{0,20}?"),
        ("提前贖回費用", r"提前贖回費用[^%]{0,20}?"),
        ("分銷費用", r"分銷費用[^%]{0,60}?"),  # 列首另有「（如屬發行機構或發行人給予…應單獨列示）」
    ):
        put(f"fee_{label}", pattern + iis.RATE)
    return iis.IisSheet(fields, full, frozenset(provided))
