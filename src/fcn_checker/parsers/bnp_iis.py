"""BNP 中文投資人須知（docs/templates/bnp-zh-iis.md）：段落標題＋「●」標籤定位，標的表沿用說明書第 11 項的解析。

依 6 份真實樣本建立（同說明書 BN1–BN3、BN5–BN7）。每頁頂端「第N頁，共M頁」不算內文。警語 5 有一段文字被匯出成
一字一行（「法國巴黎銀行(保證機構)」逐字直排），所以一律在去空白的全文上讀。標的表的格與說明書第 11 項相同，只是
價格與「即期初價格之X%」之間是半形逗號；KO 觀察方式、記憶式、KI 型態的句型同說明書（parsers/bnp.py）。
P 型（每期觀察）只寫「詳細日期請參閱…第14項」，沒有第一個可提前出場期，那份就不核對 Non-Call。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import bnp, bnp_tables, iis, money
from .layout import TextIndex, parse_date

TEMPLATE_ID = "bnp-zh-iis"
PARSER_VERSION = "1"
TITLE = "中文投資人須知(適用於專業投資人及OSU客戶)"
NAME_EN = "issuedbyBNPParibasIssuanceB.V."
SECTIONS = (
    "本商品之投資風險警語：",
    "壹、相關機構",
    "貳、商品事項",
    "商品簡介",
    "收益分配事項、自動提前贖回金額及到期贖回金額之計算",
    "發行不成立之處理",
    "叁、投資風險",
    "肆、投資人應負擔的各項費用",
    "伍、相關機構之權利、義務及責任",
    "陸、協助投資人權益保護之方式",
)
NAME_TAIL = r"\(下稱「商品」\)"
REDEMPTION_SECTION = ("收益分配事項、自動提前贖回金額及到期贖回金額之計算", "發行不成立之處理")  # KO 事件、KI 寫法
KO_D = r"自動提前贖回評價日[:：]自第(\d+)個配息期結束日\(含該日\)起至期末評價日\(含該日\)的每一個預定交易日"
KO_P = r"自動提前贖回評價日[:：](?:每\d+個月一次[，,])?詳細日期請參閱中文產品說明書第一章第14項之說明"
KO_EVENT = (
    r"「自動提前贖回事件」[:：]計算代理機構將依專業合理的方式釐定，若於任一自動提前贖回評價日，(.+?)，"
    r"則視為發生自動提前贖回事件"
)
FEE_ROWS = ("申購費用", "提前贖回費用", "管理費用", "分銷費用", "保費費用", "解約費用", "其他費用")  # 費用表列首
FEE_LABEL_EDGE = 60  # 費用表第一欄（列首）的左緣上限（pt）；其他欄都在右邊
PROVIDED = frozenset(
    {
        "page_totals",
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "product_type",
        "currency_zh",
        "currency_others",
        "denomination",
        "min_subscription",
        "underlyings",
        "underlying_prices",
        "strike_pct",
        "ko_pct",
        "tenor_months",
        "subscription_dates",
        "trade_date",
        "issue_date",
        "final_valuation_date",
        "maturity_date",
        "monthly_coupon_shown_pct",
        "ko_observation",
        "ko_memory",
        "ki_type",
        "minimum_issue_size",
        "print_dates",
        "issuer_names",
        "guarantor_names",
        "guarantor_names_short",
        "distributor_names",
        "distributor_addresses",
        "distributor_phones",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """內文：去掉每頁頂端「第N頁，共M頁」。"""
    return [x for x in lines if not bnp.PAGE_HEADER.fullmatch(squash(x.text))]


def detect(lines: Sequence[Line]) -> DetectionResult:
    text = TextIndex(body(lines)).text
    failed = []
    for ok, message in (
        (TITLE in text, "封面缺少「中文投資人須知 (適用於專業投資人及OSU客戶)」"),
        (
            re.search(r"刊印日期[:：]\d{4}年\d{1,2}月\d{1,2}日法國巴黎銀行.+?" + NAME_TAIL, text) is not None,
            "商品中文名稱不是「法國巴黎銀行…(下稱「商品」)」",
        ),
        (NAME_EN in text, "商品英文名稱沒有「issued by BNP Paribas Issuance B.V.」"),
        (
            re.search(r"相關機構1?發行機構[:：]BNPParibas", text) is not None,  # 全名另由審查標準核對
            "相關機構的發行機構不是BNP",
        ),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in body(lines)[:2]])


def _section(ti: TextIndex, anchors: tuple[str, str]) -> list[Line] | None:
    """兩個錨點之間（含錨點所在行）的原文行；找不到或開頭錨點重複時為 None。"""
    start, end = anchors
    if ti.text.count(start) != 1:
        return None
    a = ti.text.index(start)
    b = ti.text.find(end, a)
    return None if b < 0 else ti.lines_for(a, b + len(end))


def _price_region(lines: list[Line]) -> list[Line]:
    """商品簡介的標的表：「連結標的名稱」那一行起，到「籃子：」或「● 商品年期」之前（同說明書第 11 項）。"""
    start = next((i for i, ln in enumerate(lines) if squash(ln.text).startswith("連結標的名稱")), None)
    if start is None:
        return []
    end = next(
        (i for i in range(start + 1, len(lines)) if squash(lines[i].text).startswith(("籃子：", "●商品年期"))),
        len(lines),
    )
    return lines[start:end]


def _ko_terms(ti: TextIndex) -> dict[str, ParsedField]:
    """商品簡介「自動提前贖回評價日」的寫法 → KO 觀察方式；D 型另有第一個可提前出場期 k（範本規格 §4.1）。"""
    d_hits, p_hits = list(ti.finditer(KO_D)), list(ti.finditer(KO_P))
    if len(d_hits) + len(p_hits) != 1:
        m = (d_hits + p_hits)[0] if d_hits or p_hits else None
        ev = ti.lines_for(m.start(), m.end()) if m else []
        note = "商品簡介「自動提前贖回評價日」的寫法缺漏、重複或不在範本規格內"
        return {n: ParsedField.invalid(n, ev, note) for n in ("ko_observation", "first_callable_period")}
    if d_hits:
        m = d_hits[0]
        ev = ti.lines_for(m.start(), m.end())
        return {
            "ko_observation": ParsedField.present("ko_observation", "D", ev),
            "first_callable_period": ParsedField.present("first_callable_period", int(m[1]), ev),
        }
    m = p_hits[0]
    return {"ko_observation": ParsedField.present("ko_observation", "P", ti.lines_for(m.start(), m.end()))}


def _fees(lines: list[Line]) -> dict[str, ParsedField]:
    """費用表：每列從列首（第一欄）到下一個列首之間，其他欄只有費率欄寫百分比，取剛好一個區間（例 0%~5%）。

    格內文字的擷取順序因樣本而異（列首換行的續行可能排在費率之前或之後，跨欄的格也可能黏成一行），所以不依座標拆欄。
    """
    heads = [i for i, ln in enumerate(lines) if ln.x0 < FEE_LABEL_EDGE and squash(ln.text).startswith(FEE_ROWS)]
    out: dict[str, ParsedField] = {}
    for label in iis.FEES:
        name = standard_fields.fee_field(label)
        rows = [k for k, i in enumerate(heads) if squash(lines[i].text).startswith(label)]
        if len(rows) != 1:
            out[name] = ParsedField.missing(
                name, f"費用表找不到「{label}」列" if not rows else f"費用表「{label}」列重複"
            )
            continue
        a = heads[rows[0]]
        b = heads[rows[0] + 1] if rows[0] + 1 < len(heads) else len(lines)
        cells = [ln for ln in lines[a + 1 : b] if ln.x0 >= FEE_LABEL_EDGE]
        rates = re.findall(iis.RATE, "".join(squash(ln.text) for ln in cells))
        out[name] = (
            ParsedField.present(name, rates[0], [lines[a], *cells])
            if len(rates) == 1
            else ParsedField.invalid(name, [lines[a], *cells], f"費用表「{label}」列的費率不是剛好一個區間")
        )
    return out


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines)}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 封面
    put("name_zh", r"刊印日期[:：]\d{4}年\d{1,2}月\d{1,2}日(法國巴黎銀行.+?)" + NAME_TAIL)
    put("name_en", NAME_TAIL + r"(.+?)商品種類[:：]")
    put("product_type", r"商品種類[:：](.+?)[，,]TDCC商品代號")
    put("isin", r"ISIN為([A-Z]{2}[A-Z0-9]{10})")
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (
            ("product_code_tdcc", "封面 TDCC 商品代號", "封面「TDCC 商品代號」", r"TDCC商品代號為(\d{12})"),
            ("product_code_trustee", "封面受託機構商品代號", "封面「受託機構商品代號」", r"受託機構商品代號為(\d{12})"),
        ),
    )
    fields["print_dates"] = iis.occurrences(
        "print_dates", ti, (("print_date_iis", "刊印日期", "封面刊印日期", r"刊印日期[:：]" + iis.DATE),), parse_date
    )
    # 商品簡介
    put("currency_zh", r"計價幣別[:：]([^●]+?)●商品面額")
    put("denomination", r"商品面額[:：]每單位商品([\d,]+)", iis.integer)
    put("min_subscription", r"最低申購金額[:：]([\d,]+)", iis.integer)
    fields["currency_others"] = iis.occurrences(  # 金額旁的幣別字 = 參考條件表承作幣別（Issue #170）
        "currency_others",
        ti,
        (
            (
                "denomination_currency",
                "商品面額幣別",
                "商品簡介商品面額",
                rf"商品面額[:：]每單位商品[\d,]+({money.CURRENCY})",
            ),
            (
                "min_subscription_currency",
                "最低申購金額幣別",
                "商品簡介最低申購金額",
                rf"最低申購金額[:：][\d,]+({money.CURRENCY})",
            ),
        ),
    )
    region = _price_region(text_lines)
    fields["underlying_table"] = bnp_tables.price_table("underlying_table", region, with_tickers=True)
    bnp.price_fields(fields, fields["underlying_table"], region)
    put("tenor_months", r"商品年期[:：](\d+)個月期", int)
    put("subscription_start", r"商品開始受理申購日期[:：]" + iis.DATE + "至", parse_date)
    put("subscription_end", r"商品開始受理申購日期[:：]\d{4}年\d{1,2}月\d{1,2}日至" + iis.DATE, parse_date)
    fields["subscription_dates"] = standard_fields.occurrences(
        "subscription_dates",
        [
            Occurrence(
                "subscription_start",
                "開始受理申購日（起）",
                "商品簡介商品開始受理申購日期（起）",
                fields["subscription_start"],
            ),
            Occurrence(
                "subscription_end",
                "開始受理申購日（迄）",
                "商品簡介商品開始受理申購日期（迄）",
                fields["subscription_end"],
            ),
        ],
    )
    put("trade_date", r"期初訂價日[:：]" + iis.DATE, parse_date)
    put("issue_date", r"發行日[:：]" + iis.DATE, parse_date)
    put("final_valuation_date", r"期末評價日[:：]" + iis.DATE, parse_date)
    put("maturity_date", r"到期日[:：].{0,40}?預定為" + iis.DATE, parse_date)
    fields.update(_ko_terms(ti))
    # 收益分配事項、自動提前贖回金額及到期贖回金額之計算
    put("monthly_coupon_shown_pct", r"其中C為(\d+(?:\.\d+)?)%", Decimal)
    redemption = _section(ti, REDEMPTION_SECTION)
    if redemption is None:
        note = "找不到「收益分配事項、自動提前贖回金額及到期贖回金額之計算」段落"
        fields["ko_memory"] = ParsedField.missing("ko_memory", note)
        fields["ki_type"] = ParsedField.missing("ki_type", note)
    else:
        fields["ko_memory"] = bnp.ko_memory("ko_memory", redemption, KO_EVENT, "找不到自動提前贖回事件定義句")
        fields["ki_type"] = bnp.ki_type(redemption)
    bnp.pct_fields(fields)
    put("minimum_issue_size", r"至少達(\(等值\)[^之]*?)之總申購價值")
    provided = set(PROVIDED)
    if "first_callable_period" in fields:  # 只有 D 型寫出第一個可提前出場期
        provided.add("first_callable_period")
    if fields["ki_pct"].status != FieldStatus.NOT_APPLICABLE:  # 有 KI 時標的表才有下限價格列
        provided.add("ki_pct")
    # 警語與相關機構（審查標準）
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (
            ("issuer_name_w5a", "發行機構名稱（警語 5）", "警語 5", r"保證保本率，係由(.+?)\(發行機構\)與"),
            ("issuer_name_w5b", "發行機構名稱（警語 5）", "警語 5", r"\(受託機構\)所保證。(.+?)\(發行機構\)與"),
            ("issuer_name_org", "發行機構名稱（相關機構）", "相關機構第 1 點", r"發行機構[:：](.+?)\(註冊辦公地點"),
        ),
    )
    fields["guarantor_names"] = iis.occurrences(
        "guarantor_names",
        ti,
        (("guarantor_name_org", "保證機構名稱（相關機構）", "相關機構第 3 點", r"保證機構[:：](.+?)\(下稱「"),),
    )
    fields["guarantor_names_short"] = iis.occurrences(
        "guarantor_names_short",
        ti,
        (
            # 名稱不含括號：避免從警語 5 第一個「(發行機構)與」一路抓到第二句
            ("guarantor_name_w5a", "保證機構名稱（警語 5）", "警語 5", r"\(發行機構\)與([^()]+?)\(保證機構\)保證"),
            ("guarantor_name_w5b", "保證機構名稱（警語 5）", "警語 5", r"\(發行機構\)與([^()]+?)\(保證機構\)間有"),
            ("guarantor_name_risk", "保證機構名稱（信用風險）", "投資風險「信用風險」", r"本商品之保證機構為(.+?)。"),
        ),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (
            ("distributor_name_w4a", "受託機構名稱（警語 4）", "警語 4", r"本商品雖經(.+?)\(受託機構\)審查"),
            ("distributor_name_w4b", "受託機構名稱（警語 4）", "警語 4", r"之價值，且(.+?)\(受託機構\)不負本"),
            ("distributor_name_w4c", "受託機構名稱（警語 4）", "警語 4", r"盈虧之責。(.+?)\(受託機構\)依法不得承諾"),
            ("distributor_name_w5", "受託機構名稱（警語 5）", "警語 5", r"而非由(.+?)\(受託機構\)所保證"),
            ("distributor_name_w6", "受託機構名稱（警語 6）", "警語 6", r"係由受託機構\(即(.+?)\)負責外"),
            ("distributor_name_org", "受託機構名稱（相關機構）", "相關機構第 4 點", r"受託機構[:：](.+?)\(地址[:：]"),
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
                r"受託機構[:：][^(]{1,30}\(地址[:：](.+?)\)貳、商品事項",
            ),
        ),
    )
    fields["distributor_phones"] = standard_fields.occurrences(
        "distributor_phones",
        [
            Occurrence(
                "distributor_phone_rights",
                "受託機構電話（權益保護）",
                "陸、協助投資人權益保護之方式第 1 點",
                bnp.labelled("distributor_phone_rights", text_lines, "受託機構聯絡方式："),
            )
        ],
    )
    fields.update(_fees(text_lines))
    return iis.IisSheet(fields, ti, frozenset(provided))
