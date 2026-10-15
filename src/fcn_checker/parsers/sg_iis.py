"""SG 中文投資人須知（docs/templates/sg-zh-iis.md）：段落標題＋標籤定位，新、舊兩種版面同一範本。

依 6 份真實樣本建立（同說明書 SG1–SG6）。頁底只有頁碼數字（擷取時已排除），沒有「共 M 頁」。一律在去空白的全文上讀，
舊版面數字與單位、年月日之間的空白因此不影響。KO／KI 寫法只在「收益分配事項」段內判斷，句型同說明書第 8 項
（parsers/sg.py 的 `ko_memory`、`ki_type`）；參、投資風險也有類似句子。標的表每格拆成多行，價格依標的列切段。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import iis, money, sg, sg_tables
from .layout import TextIndex, join_wrapped_tickers, parse_date

TEMPLATE_ID = "sg-zh-iis"
PARSER_VERSION = "1"
TITLE = "中文投資人須知"
NAME_ZH_TAIL = "（不保本及無擔保）"
NAME_EN_TAIL = squash(sg.NAME_EN_TAIL)
SECTIONS = (
    "本商品之投資風險警語",
    "壹、相關機構",
    "貳、境外結構型商品事項",
    "商品簡介：",
    "收益分配事項",
    "贖回價金之計算：",
    "參、投資風險",
    "肆、費用",
    "伍、相關機構之權利、義務及責任",
    "陸、協助投資人權益之保護方式",
    "柒、",
)
CODES = r"TDCC商品代號\d{12}，受託(?:或銷售)?機構商品代號\d{12}，ISIN為[A-Z]{2}[A-Z0-9]{10}"
PAYOUT_SECTION = ("收益分配事項，舉例如下：", "贖回價金之計算：")  # 收益分配事項：配息率與 KO／KI 寫法所在的段落
MATURITY = "■到期贖回金額："  # 收益分配事項裡到期贖回各款的開頭；之前是自動提前出場的寫法
NO_AUTOCALL = "主要給付項目為配息金額及到期贖回金額："
KO_D = r"(?:記憶事件)?觀察期間：自" + iis.DATE + r"起（含）起至最終評價日（含）止之每一個預定交易日"
KO_D_EVENT = r"自動提前出場事件：若於(?:記憶事件)?觀察期間內之任一預定交易日"
KO_P = r"自動提前出場事件：若於任一評價日i\(i=(\d+)-(\d+)\)"
P_RANGE = r"評價日i\(i=(\d+)-(\d+)\)"
PRICE = rf"(\d[\d,]*\.\d{{4}})({money.CURRENCY})"
PCT = r"\(連結標的期初價格的(\d+(?:\.\d+)?)%\)"
PROVIDED = frozenset(
    {
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "product_type",
        "print_dates",
        "risk_level_summary",
        "currency_zh",
        "currency_others",
        "denomination",
        "issue_price_pct",
        "underlyings",
        "underlying_prices",
        "tenor_months",
        "trade_date",
        "issue_date",
        "final_valuation_date",
        "maturity_date",
        "subscription_dates",
        "coupon_pa_pct",
        "monthly_coupons",
        "has_autocall",
        "ko_memory",
        "ki_type",
        "issuer_names",
        "guarantor_names",
        "guarantor_names_org",
        "agent_names",
        "distributor_names",
        "distributor_addresses",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def detect(lines: Sequence[Line]) -> DetectionResult:
    text = TextIndex(lines).text
    first = squash(lines[0].text) if lines else ""
    names = (
        r"中文投資人須知SGISSUER.+?" + re.escape(NAME_ZH_TAIL) + r"SGISSUER.+?" + re.escape(NAME_EN_TAIL) + "商品種類："
    )
    failed = []
    for ok, message in (
        (first == TITLE, "第一列不是「中文投資人須知」"),
        (
            re.match(names, text) is not None,
            "封面名稱不是「SG ISSUER …（不保本及無擔保）」與「SG ISSUER … (Non Principal Guaranteed and Unsecured)」",
        ),
        (re.search(CODES, text) is not None, "找不到封面代號句「TDCC商品代號…，受託機構商品代號…，ISIN為…」"),
        ("壹、相關機構1.發行機構：SGISSUER" in text, "相關機構的發行機構不是SG"),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _table(lines: list[Line], fields: dict[str, ParsedField]) -> None:
    """商品簡介標的表（範本規格 §4.4）：代碼一列一個，價格依表頭欄序切到各列；表頭的 % 讀成 KO／KI／執行價 %。"""
    start = next((i for i, ln in enumerate(lines) if squash(ln.text).startswith("連結標的資產：")), None)
    end = next((i for i, ln in enumerate(lines) if squash(ln.text).startswith("商品年期：")), None)
    names = ("underlyings", "underlying_prices", "strike_pct", "ko_pct", "ki_pct")
    if start is None or end is None or end <= start:
        fields.update({n: ParsedField.missing(n, "找不到商品簡介「連結標的資產」標的表") for n in names})
        return
    region = join_wrapped_tickers(lines[start + 1 : end])
    rows = [i for i, ln in enumerate(region) if sg.TICKER.fullmatch(ln.text.strip())]
    header = TextIndex(region[: rows[0]] if rows else region)
    cols = ["initial"]
    if "自動提前出場價" in header.text:
        cols.append("ko")
    if "下限價" in header.text:
        cols.append("ki")
    cols.append("strike")
    for name, label in (("ko_pct", "自動提前出場價"), ("ki_pct", "下限價"), ("strike_pct", "執行價")):
        if label + "(連結標的期初價格的" in header.text:  # 表頭沒寫 % 的欄不核對 %（SG6 執行價）
            fields[name] = iis.capture(name, header, re.escape(label) + PCT, Decimal)
    if not rows or "期初價格" not in header.text or "執行價" not in header.text:
        note = "標的表找不到彭博代碼（代號 空白 交易所碼）或表頭「期初價格」「執行價」"
        fields.update({n: ParsedField.invalid(n, region, note) for n in ("underlyings", "underlying_prices")})
        return
    tickers = [re.sub(r"\s+", " ", region[i].text.strip()) for i in rows]
    fields["underlyings"] = ParsedField.present("underlyings", tickers, [region[i] for i in rows])
    out = []
    for k, (a, ticker) in enumerate(zip(rows, tickers, strict=True)):
        cells = region[a : rows[k + 1] if k + 1 < len(rows) else len(region)]
        found = re.findall(PRICE, "|".join(squash(ln.text) for ln in cells))
        if len(found) != len(cols):
            fields["underlying_prices"] = ParsedField.invalid(
                "underlying_prices",
                cells,
                f"標的表第 {k + 1} 列的價格數（{len(found)}）不等於表頭價格欄數（{len(cols)}）",
            )
            return
        prices = {c: Decimal(v.replace(",", "")) for c, (v, _) in zip(cols, found, strict=True)}
        out.append(standard_fields.PriceRow(ticker, prices, tuple(Evidence.of(ln) for ln in cells), name=f"k={k + 1}"))
    fields["underlying_prices"] = ParsedField(
        "underlying_prices", FieldStatus.PRESENT, tuple(out), [Evidence.of(ln) for ln in region]
    )


def _ko_terms(sec: list[Line] | None, fields: dict[str, ParsedField]) -> None:
    """有無自動提前出場、KO 觀察方式、P 型第一個可提前出場期、D 型觀察起日、記憶式、KI 型態（範本規格 §4.1–§4.3）。"""
    names = ("has_autocall", "ko_observation", "first_callable_period", "ko_memory", "ki_type")
    if sec is None:
        fields.update({n: ParsedField.missing(n, "找不到「收益分配事項」段落") for n in names})
        return
    ti = TextIndex(sec)
    cut = ti.text.find(MATURITY)
    ko = ti.text[:cut] if cut >= 0 else ti.text  # 到期贖回各款之前：自動提前出場的寫法
    with_autocall = "自動提前出場事件：" in ko
    without_autocall = NO_AUTOCALL in ti.text and "自動提前出場" not in ko.replace(NO_AUTOCALL, "")
    if with_autocall == without_autocall or (with_autocall and NO_AUTOCALL in ti.text):  # 都不是，或兩種都寫了
        note = "收益分配事項的自動提前出場寫法缺漏、不在範本規格內，或同時寫了有、沒有自動提前出場"
        fields.update({n: ParsedField.invalid(n, sec, note) for n in names})
        return
    has_autocall = with_autocall
    fields["has_autocall"] = ParsedField.present("has_autocall", has_autocall, sec[:1])
    fields["ko_memory"] = sg.ko_memory(ti, sec, has_autocall, where="收益分配事項")
    fields["ki_type"] = sg.ki_type(ti, sec, where="收益分配事項")
    if not has_autocall:
        return
    daily_periods = list(re.finditer(KO_D, ko))
    daily_events = list(re.finditer(KO_D_EVENT, ko))
    periodic_events = list(re.finditer(KO_P, ko))
    periodic_ranges = set(re.findall(P_RANGE, ko))  # 所有「評價日i (i=k-n)」的 (k, n)，必須只有一種
    if len(daily_periods) == 1 and len(daily_events) == 1 and not periodic_events:
        period = daily_periods[0]
        ev = ti.lines_for(period.start(), period.end())
        fields["ko_observation"] = ParsedField.present("ko_observation", "D", ev)
        start = parse_date(period[1])
        fields["ko_observation_start"] = (
            ParsedField.present("ko_observation_start", start, ev)
            if start
            else ParsedField.invalid("ko_observation_start", ev, f"「{period[1]}」不是日期")
        )
    elif len(periodic_events) == 1 and not daily_periods and len(periodic_ranges) == 1:
        event = periodic_events[0]
        ev = ti.lines_for(event.start(), event.end())
        fields["ko_observation"] = ParsedField.present("ko_observation", "P", ev)
        fields["first_callable_period"] = ParsedField.present("first_callable_period", int(event[1]), ev)
    else:
        note = "自動提前出場的觀察期間或評價日寫法缺漏、重複、前後不一或不在範本規格內"
        fields["ko_observation"] = ParsedField.invalid("ko_observation", sec, note)
        fields["first_callable_period"] = ParsedField.invalid("first_callable_period", sec, note)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = list(lines)
    ti = TextIndex(text_lines)
    fields: dict[str, ParsedField] = {}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 封面
    put("name_zh", r"中文投資人須知(SGISSUER.+?" + re.escape(NAME_ZH_TAIL) + ")SGISSUER")
    put("name_en", re.escape(NAME_ZH_TAIL) + r"(SGISSUER.+?" + re.escape(NAME_EN_TAIL) + ")商品種類：")
    put("product_type", r"商品種類：(.+?)TDCC商品代號")
    put("isin", r"ISIN為([A-Z]{2}[A-Z0-9]{10})")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (
            ("product_code_tdcc", "封面 TDCC 商品代號", "封面「TDCC商品代號」", r"TDCC商品代號(\d{12})"),
            (
                "product_code_trustee",
                "封面受託機構商品代號",
                "封面「受託機構商品代號」",
                r"受託(?:或銷售)?機構商品代號(\d{12})",
            ),
        ),
    )
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        ti,
        (("print_date_iis", "刊印日期", "封面刊印日期", r"中文投資人須知刊印日期：" + iis.DATE),),
        parse_date,
    )
    # 商品簡介
    put("risk_level_summary", r"本商品風險程度：本商品風險程度為(RR\d)")
    put("currency_zh", r"計價幣別：(.+?)計價貨幣投資本金保本率")
    put("denomination", r"每單位面額：([\d,]+)", iis.integer)
    put("issue_price_pct", r"發行價格：每單位面額的(\d+(?:\.\d+)?)%", Decimal)
    _table(text_lines, fields)
    put("tenor_months", r"商品年期：(\d+)個月", int)
    put("trade_date", r"訂價日：" + iis.DATE, parse_date)
    put("issue_date", r"發行日[／∕/]交割日：" + iis.DATE, parse_date)
    put("final_valuation_date", r"最終評價日：" + iis.DATE, parse_date)
    put("maturity_date", r"到期日：" + iis.DATE, parse_date)
    put("subscription_start", r"商品開始受理申購日期：" + iis.DATE + "起", parse_date)
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            Occurrence(
                "subscription_start", "開始受理申購日", "商品簡介商品開始受理申購日期", fields["subscription_start"]
            )
        ],
    )
    # 收益分配事項
    put("coupon_pa_pct", r"（相當於年率(\d+(?:\.\d+)?)%）", Decimal)
    fields["monthly_coupons"] = iis.occurrences(
        "monthly_coupons",
        ti,
        (("monthly_coupon_rate", "月配息率", "收益分配事項配息率", r"配息率=(\d+(?:\.\d+)?)%（相當於年率"),),
        iis.number,
    )
    _ko_terms(iis.section(ti, PAYOUT_SECTION), fields)
    has_autocall = fields["has_autocall"].ok and fields["has_autocall"].value
    pay = [("payment_currency_coupon", "配息給付幣別", "收益分配事項配息日", "於配息日")]
    if has_autocall:
        pay.append(
            ("payment_currency_autocall", "自動提前出場給付幣別", "收益分配事項自動提前出場日", "於自動提前出場日")
        )
    pay.append(("payment_currency_maturity", "到期給付幣別", "收益分配事項到期日", "於到期日"))
    fields["currency_others"] = iis.occurrences(  # 金額旁的幣別字 = 參考條件表承作幣別（Issue #170）
        "currency_others",
        ti,
        (
            ("denomination_currency", "每單位面額幣別", "商品簡介每單位面額", rf"每單位面額：[\d,]+({money.CURRENCY})"),
            *(
                (f, n, w, rf"{lead}，發行機構將依下列計算公式以({money.CURRENCY})為計價單位給付")
                for f, n, w, lead in pay
            ),
        ),
    )
    # 表頭寫出 % 的欄才核對 %；有自動提前出場才有 KO 觀察方式；只有 P 型寫出第一個可提前出場期
    # （D 型的觀察起日 `ko_observation_start` 由範本專屬規則核對，不交給共用規則）
    daily = fields.get("ko_observation") is not None and fields["ko_observation"].value == "D"
    provided = PROVIDED | {n for n in ("strike_pct", "ki_pct", "ko_pct") if n in fields}
    if has_autocall:
        provided |= {"ko_observation"} | (set() if daily else {"first_callable_period"})
    # 警語與相關機構（審查標準）
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (
            ("issuer_name_w5a", "發行機構名稱（警語五）", "警語五", r"保證配息，係由(.+?)（發行機構）或"),
            ("issuer_name_w5b", "發行機構名稱（警語五）", "警語五", r"所保證。(.+?)（發行機構）與"),
            ("issuer_name_w6", "發行機構名稱（警語六）", "警語六", r"其餘內容應由(.+?)（發行機構）或"),
            ("issuer_name_org", "發行機構名稱（相關機構）", "相關機構第 1 點", r"1\.發行機構：(.+?)\(地址："),
        ),
    )
    fields["guarantor_names"] = iis.occurrences(
        "guarantor_names",
        ti,
        (
            ("guarantor_name_w5a", "保證機構名稱（警語五）", "警語五", r"（發行機構）或([^（]+?)（保證機構）保證"),
            ("guarantor_name_w5b", "保證機構名稱（警語五）", "警語五", r"（發行機構）與([^（]+?)（保證機構）有財務"),
        ),
    )
    fields["guarantor_names_org"] = iis.occurrences(  # 英文名沒有括號的寫法另由審查標準的等價寫法接受
        "guarantor_names_org",
        ti,
        (("guarantor_name_org", "保證機構名稱（相關機構）", "相關機構第 2 點", r"2\.保證機構：(.+?)\(地址："),),
    )
    fields["agent_names"] = iis.occurrences(
        "agent_names",
        ti,
        (("agent_name_org", "總代理人名稱（相關機構）", "相關機構第 3 點", r"3\.總代理人：(.+?)\(地址："),),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (
            ("distributor_name_w4a", "受託機構名稱（警語四）", "警語四", r"四、本商品雖經(.+?)審查"),
            ("distributor_name_w4b", "受託機構名稱（警語四）", "警語四", r"之價值，且(.+?)不負本商品投資盈虧之責"),
            ("distributor_name_w4c", "受託機構名稱（警語四）", "警語四", r"投資盈虧之責。(.+?)依法不得承諾"),
            ("distributor_name_w5", "受託機構名稱（警語五）", "警語五", r"而非由(.+?)所保證"),
            ("distributor_name_w6", "受託機構名稱（警語六）", "警語六", r"受託或銷售機構（即(.+?)）負責外"),
            (
                "distributor_name_org",
                "受託機構名稱（相關機構）",
                "相關機構第 4 點",
                r"4\.受託或銷售機構：(.+?)（下稱「",
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
                "相關機構第 4 點",
                r"4\.受託或銷售機構：[^(]{1,40}\(地址：(.+?)\)貳、",
            ),
        ),
    )
    fees = iis.section(ti, ("肆、費用", "伍、"))
    for label in iis.FEES:
        name = standard_fields.fee_field(label)
        fields[name] = (
            sg_tables.fee(label, name, fees)
            if fees is not None
            else ParsedField.missing(name, "找不到「肆、費用」段落")
        )
    return iis.IisSheet(fields, ti, frozenset(provided))
