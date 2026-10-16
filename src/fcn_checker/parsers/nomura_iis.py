"""Nomura 中文投資人須知（docs/templates/nomura-zh-iis.md）：段落標題＋標籤定位，句型在去空白的全文上讀。

依 8 份真實樣本建立（同說明書 NR1–NR8）。每頁頁碼「N/4」單獨一行。章名「第一 相關機構」…「第十 …」（「第一」後沒有「、」）。
KO／KI 寫法只在「第三 收益分配事項」到「第四 贖回價金之計算」之間判斷，句型同說明書第 7 項（標籤加「」），
第六 風險揭露也有類似句子。範本沒有刊印日期、標的表與百分比、最終評價日、日期表、面額與受理申購日期（不核對、不報缺漏）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import iis, money, nomura
from .layout import TextIndex, parse_date

TEMPLATE_ID = "nomura-zh-iis"
PARSER_VERSION = "2"  # 2：無 KI 要有第四的明確到期贖回款（Issue #297）
TITLE = "中文投資人須知"
SECTIONS = (
    "本商品之投資風險警語",
    "第一相關機構",
    "第二商品簡介",
    "第三收益分配事項",
    "第四贖回價金之計算",
    "第五受託不成立之處理",
    "第六商品風險揭露",
    "第七商品相關費用",
    "第八相關機構之權利、義務及責任",
    "第九協助投資人權益之保護方式",
    "第十發行機構、總代理人或受託機構與投資人爭議之處理方式",
)
COUPON = ("第三收益分配事項", "第四贖回價金之計算")  # 每期配息率、KO／KI 寫法所在的段落
REDEMPTION = ("第四贖回價金之計算", "第五受託不成立之處理")  # 到期贖回各款（無 KI 的判定依據）
ISSUER = squash(nomura.ISSUER_EN)
# 收益分配事項的標籤加「」：去掉後就是說明書第 7 項的句型
LABELS = re.compile(
    r"「(自動提前出場事件|記憶事件|自動提前出場決定日|自動提前出場日|配息日|下限觸發事件|下限觸發決定日)」："
)
KO_PERIODIC = r"自動提前出場決定日：各自動提前出場決定日詳見中文產品說明書第11項\(6\)「自動提前出場決定日」之表格所示。"
PROVIDED = frozenset(
    {
        "page_totals",
        "product_codes",
        "isin",
        "name_zh",
        "name_en",
        "product_type",
        "risk_level_summary",
        "currency_zh",
        "currency_others",
        "tenor_months",
        "trade_date",
        "issue_date",
        "maturity_date",
        "coupon_per_period_pct",
        "ko_observation",
        "ko_memory",
        "ki_type",
        "issuer_names",
        "guarantor_names",
        "agent_names",
        "distributor_names",
        "distributor_addresses",
        "distributor_phones",
        "minimum_issue_size",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """頁碼「N/4」以外的行（頁碼另由 `iis.page_totals` 讀）。"""
    return [ln for ln in lines if not nomura.PAGE_MARK.match(ln.text.strip())]


def _cover(text_lines: list[Line]) -> tuple[nomura.RawText, re.Match[str] | None]:
    """封面「中文投資人須知」之後到「本商品之投資風險警語」之前：中文名稱、英文名稱、商品種類（ISIN、商品代號同一行）。"""
    end = next((i for i, ln in enumerate(text_lines) if squash(ln.text).startswith("本商品之投資風險警語")), 0)
    raw = nomura.RawText(text_lines[1:end])
    m = re.fullmatch(
        r"(.+?\(無擔保\))\s*(\d+-Month.+?\(unsecured\))\s*商品種類：\s*(.+?)\s*\(ISIN\s*號碼:\s*([A-Z]{2}[A-Z0-9]{10})"
        r"\s*/\s*TDCC\s*商品代號為\s*(\d{12})\s*\)",
        raw.text,
        re.S,
    )
    return raw, m


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    first = squash(lines[0].text) if lines else ""
    raw, cover = _cover(lines)
    zh, en = (squash(cover[1]), squash(cover[2])) if cover else ("", "")
    failed = []
    for ok, message in (
        (first == TITLE, "第一列不是「中文投資人須知」"),
        (
            zh.startswith(ISSUER + "發行") and zh.endswith("(無擔保)"),
            f"封面中文名稱不是「{nomura.ISSUER_EN} 發行…(無擔保)」",
        ),
        (
            "AutocallFixedCouponNotes" in en and en.endswith(f"issuedby{ISSUER}(unsecured)"),
            f"封面英文名稱沒有「Autocall Fixed Coupon Notes」或不是以「issued by {nomura.ISSUER_EN} (unsecured)」結尾",
        ),
        (cover is not None, "封面找不到「商品種類：… (ISIN 號碼: … / TDCC商品代號為… )」"),
        (f"第一相關機構1.發行機構:{ISSUER}" in text, f"相關機構第 1 點的發行機構不是 {nomura.ISSUER_EN}"),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def _coupon_terms(ti: TextIndex, fields: dict[str, ParsedField]) -> None:
    """收益分配事項：每期配息率、KO 觀察方式與 D 型起日、記憶式、KI 型態（範本規格 §4）。"""
    names = ("coupon_per_period_pct", "ko_observation", "ko_observation_start", "ko_memory", "ki_type")
    sec = iis.section(ti, COUPON)
    if sec is None:
        fields.update(
            {n: ParsedField.missing(n, "找不到「第三 收益分配事項」到「第四 贖回價金之計算」段落") for n in names}
        )
        return
    sec_ti = TextIndex(sec)
    fields["coupon_per_period_pct"] = iis.capture(
        "coupon_per_period_pct", sec_ti, r"固定配息率：就每配息日而言，" + nomura.PCT, Decimal
    )
    # 標籤的「」移到前面補位（`□□標籤：`，長度不變、證據位置不變）後就是說明書第 7 項的句型
    sec_ti.text = LABELS.sub(lambda m: "□□" + m[1] + "：", sec_ti.text)
    daily = list(sec_ti.finditer(nomura.KO_DAILY))  # 同說明書第 7 項；每期觀察寫法不同（參照說明書）
    periodic = list(sec_ti.finditer(KO_PERIODIC))
    if len(daily) + len(periodic) != 1:
        note = "「自動提前出場決定日」的寫法缺漏、重複或不在範本規格內"
        fields["ko_observation"] = ParsedField.invalid("ko_observation", sec, note)
        fields["ko_observation_start"] = ParsedField.invalid("ko_observation_start", sec, note)
    elif daily:
        m = daily[0]
        ev = sec_ti.lines_for(m.start(), m.end())
        fields["ko_observation"] = ParsedField.present("ko_observation", "D", ev)
        fields["ko_observation_start"] = ParsedField.present("ko_observation_start", parse_date(m[1]), ev)
    else:
        m = periodic[0]
        fields["ko_observation"] = ParsedField.present("ko_observation", "P", sec_ti.lines_for(m.start(), m.end()))
        fields["ko_observation_start"] = standard_fields.absent(
            "ko_observation_start", "每期觀察的自動提前出場決定日（寫「詳見中文產品說明書第11項(6)」）"
        )
    fields["ko_memory"], _ = nomura.memory_terms(sec_ti, sec, where="收益分配事項")
    fields["ki_type"] = nomura.ki_type(
        sec_ti, sec, where="收益分配事項", redemption=("第四 贖回價金之計算", iis.section(ti, REDEMPTION))
    )


def _underlying_names(ti: TextIndex) -> ParsedField:
    """商品簡介「連結標的資產：連結標的1: 名稱；連結標的2: 名稱。」（單一標的「連結標的: 名稱。」）的英文名稱。"""
    name = "underlying_names_en"
    a, b = ti.text.find("連結標的資產："), ti.text.find("本商品年期：")
    if a < 0 or b <= a:
        return ParsedField.missing(name, "找不到商品簡介「連結標的資產：」")
    raw = nomura.RawText(ti.lines_for(a, b))
    region = raw.text[raw.text.find("連結標的資產：") + len("連結標的資產：") :]
    entries = list(re.finditer(r"連結標的(\d*)\s*[:：]\s*(.+?)\s*[；。]", region, re.S))
    numbers = [m[1] for m in entries]
    if not entries or (numbers != [""] and numbers != [str(n) for n in range(1, len(entries) + 1)]):
        return ParsedField.invalid(name, raw.lines, "連結標的資產讀不到「連結標的n: 名稱」或編號不連續")
    return ParsedField.present(name, [m[2] for m in entries], raw.lines)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines, iis.PAGE_SLASH, "N/M")}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 封面
    raw, cover = _cover(text_lines)
    for name, group in (("name_zh", 1), ("name_en", 2), ("product_type", 3), ("isin", 4)):
        fields[name] = (
            ParsedField.present(name, cover[group].strip(), raw.lines_for(cover.start(group), cover.end(group)))
            if cover
            else ParsedField.missing(name, "封面找不到中英文名稱與「商品種類：… (ISIN 號碼: … / TDCC商品代號為… )」")
        )
    fields["product_codes"] = iis.occurrences(
        "product_codes",
        ti,
        (("product_code_tdcc", "product_code", "封面「TDCC商品代號為」", r"TDCC商品代號為(\d{12})"),),
    )
    # 商品簡介
    put("risk_level_summary", r"本商品風險程度：(RR\d)")
    put("currency_zh", rf"計價幣別：({nomura.ZH_CCY})\([A-Z]{{3}}\)")
    fields["currency_others"] = standard_fields.occurrences(  # 說明文字的幣別字（Issue #316）
        "currency_others",
        money.currency_words(
            text_lines, rf"以({nomura.CCY})為計價單位", "payment_currency", "wording_currency", "收益分配事項"
        ),
    )
    fields["underlying_names_en"] = _underlying_names(ti)
    put("tenor_months", r"本商品年期：(\d+)個月", int)
    put("trade_date", r"\d{1,2}\.交易日：" + iis.DATE, parse_date)
    put("issue_date", r"\d{1,2}\.發行日：" + iis.DATE, parse_date)
    put("maturity_date", r"\d{1,2}\.預定到期日：" + iis.DATE, parse_date)
    full = nomura.RawText(text_lines)
    fields["redemption_start_text"] = full.capture(
        "redemption_start_text", r"開始受理投資人贖回日期：\s*(.+?)\s*(?:\d{1,2}\.\s*)?後續受理投資人贖回日期："
    )
    fields["redemption_later_text"] = full.capture(
        "redemption_later_text", r"後續受理投資人贖回日期：\s*(.+?)\s*第三\s*收益分配事項"
    )
    _coupon_terms(ti, fields)
    put("minimum_issue_size", r"有效申購金額總計至少達(.+?)；")
    # 相關機構（審查標準）
    org = (
        (
            "issuer_names",
            "issuer_name_org",
            "issuer_name_org",
            "第一 1.",
            r"1\.\s*發行機構\s*[:：]\s*(.+?)\s*，營業所在地",
        ),
        (
            "guarantor_names",
            "guarantor_name_org",
            "guarantor_name_org",
            "第一 2.",
            r"2\.\s*保證機構\s*[:：]\s*(.+?)\s*，營業所在地",
        ),
        (
            "agent_names",
            "agent_name_org",
            "agent_name_org",
            "第一 3.",
            r"3\.\s*總代理人\s*[:：]\s*(.+?)\s*，營業所在地",
        ),
        (
            "distributor_names",
            "distributor_name_org",
            "distributor_name_org",
            "第一 4.",
            r"4\.\s*受託機構\s*[:：]\s*(.+?)\s*，營業所在地",
        ),
        (
            "distributor_addresses",
            "distributor_address_org",
            "distributor_address_org",
            "第一 4.",
            r"4\.\s*受託機構\s*[:：]\s*.+?，營業所在地：\s*(.+?)\s*第二\s*商品簡介",
        ),
    )
    for container, field, code, where, pattern in org:
        fields[container] = standard_fields.occurrences(
            container, [standard_fields.Occurrence(field, code, where, full.capture(field, pattern))]
        )
    phone = nomura.rest_of_line("distributor_phone_rights", text_lines, "受託機構聯絡方式：")
    fields["distributor_phones"] = standard_fields.occurrences(
        "distributor_phones",
        [standard_fields.Occurrence("distributor_phone_rights", "distributor_phone_rights", "第九 1.", phone)],
    )
    fees = iis.section(ti, ("第七商品相關費用", "第八"))
    if fees is None:
        fields.update(
            {
                standard_fields.fee_field(x): ParsedField.missing(
                    standard_fields.fee_field(x), "找不到「第七 商品相關費用」段落"
                )
                for x in iis.FEES
            }
        )
    else:
        fields.update(nomura.fee_rates(fees))
    return iis.IisSheet(fields, ti, PROVIDED)
