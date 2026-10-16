"""UBS 中文投資人須知（docs/templates/ubs-zh-iis.md）：段名＋標籤定位，句型在去空白的全文上讀。

依 6 份真實樣本建立（同說明書 UB1–UB6；只支援新版，舊版「股權結構型債券」辨識失敗）。A4 版面、每頁頁底「第N頁，共4頁」
（有的「第N 頁」）。第 1 頁「商品中文名稱：…（下稱“本商品”）；商品英文名稱：…；商品種類：…。」同一段，接著「警語」
1.～10.、刊印日期（「刋」／「刊」）。段名「相關機構」「商品事項」前面可能有「一、」「二、」。範本沒有商品代號、ISIN、
交易日、期末訂價日、面額、價格表、%、配息率與 KO／KI 條文（收益分配只寫參照說明書），這些欄位不交出、不核對。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .. import standard_fields
from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..standard_fields import Occurrence
from ..text import squash
from . import iis, ubs
from .common import RawText, fee_rates
from .layout import TextIndex, parse_date

TEMPLATE_ID = "ubs-zh-iis"
PARSER_VERSION = "2"  # 2：警語的受託機構名稱（Issue #306）
SECTIONS = ("警語", "相關機構", "商品事項", "商品風險揭露", "相關機構之權利、義務及責任")
NOISE = re.compile(iis.PAGE_OF)
D = ubs.D
TICKER = r"彭博代碼[:：]([A-Z0-9][A-Z0-9./-]*?)([A-Z]{2})）"  # 去空白後「ABCUW）」：最後兩個大寫字母是交易所碼
# 警語裡寫受託機構名稱的地方（Issue #306：每處都算受託機構名稱出處）：(欄位, 位置, pattern)
WARNING_NAMES = (
    ("distributor_name_w4a", "警語 4.「本商品雖經…審查」", r"4\.本商品雖經(.+?)[(（](?:下稱|受託機構)"),
    ("distributor_name_w4b", "警語 4.「…不負本商品投資盈虧之責」", r"之價值，且(.+?)[(（]受託機構[)）]不負本商品"),
    ("distributor_name_w4c", "警語 4.「…依法不得承諾擔保」", r"盈虧之責。(.+?)[(（]受託機構[)）]依法不得承諾"),
    ("distributor_name_w5", "警語 5.「而非由…所保證」", r"而非由(.+?)[(（]受託機構[)）]所保證"),
    ("distributor_name_w6", "警語 6.「係由…負責」", r"另行訂定/提供者，係由(.+?)[(（]受託機構[)）]負責外"),
)
PROVIDED = frozenset(
    {
        "page_totals",
        "name_zh",
        "name_en",
        "product_type",
        "print_dates",
        "currency_zh",
        "underlyings",
        "tenor_months",
        "issue_date",
        "maturity_date",
        "issuer_names",
        "issuing_branch",
        "distributor_names",
        "distributor_addresses",
        "distributor_phones",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """頁碼以外的行（頁碼另由 `iis.page_totals` 讀）；私用區字元同說明書先刪除。"""
    return [ln for ln in ubs.clean_lines(lines) if not NOISE.fullmatch(ln.text.strip())]


def detect(lines: Sequence[Line]) -> DetectionResult:
    lines = body(lines)
    text = TextIndex(lines).text
    head = squash(lines[0].text) if lines else ""
    second = squash(lines[1].text) if len(lines) > 1 else ""
    zh = re.search(r"商品中文名稱[:：](.+?)[（(]下稱", text)
    en = re.search(r"商品英文名稱[:：](.+?)；商品種類", text)
    kind = re.search(r"商品種類[:：](.+?)。", text)
    failed = []
    for ok, message in (
        (
            head == "中文投資人須知" and second.startswith("商品中文名稱：瑞銀"),
            "前兩行不是「中文投資人須知」「商品中文名稱：瑞銀…」",
        ),
        (
            zh is not None and zh[1].startswith("瑞銀") and ubs.NAME_ZH_MARK in zh[1],
            f"中文名稱不是以「瑞銀」開頭或沒有「{ubs.NAME_ZH_MARK}」",
        ),
        (
            en is not None and en[1].startswith("UBS") and squash(ubs.NAME_EN_MARK) in en[1],
            f"英文名稱不是以「UBS」開頭或沒有「{ubs.NAME_EN_MARK}」",
        ),
        (
            kind is not None and kind[1] == ubs.PRODUCT_TYPE,
            f"商品種類不是新版「{ubs.PRODUCT_TYPE}」"
            + ("（舊版範本「股權結構型債券」不支援）" if kind is not None and kind[1] == ubs.OLD_PRODUCT_TYPE else ""),
        ),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
        (
            re.search(r"警語.+本投資人須知[刋刊]印日期[:：].+相關機構", text) is not None,
            "警語與相關機構之間沒有刊印日期",
        ),
        (re.search(r"[•‧]發行機構[:：]瑞士銀行", text) is not None, "相關機構沒有「發行機構：瑞士銀行」"),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in lines[:2]])


def underlyings(ti: TextIndex) -> ParsedField:
    """商品簡介 9. 連結標的資產：「名稱 （彭博代碼：ABC UW）」逗號分隔，依說明書順序。"""
    a, b = ti.text.find("9.連結標的資產"), ti.text.find("10.天期/年期")
    if a < 0 or b < a:
        return ParsedField.missing("underlyings", "找不到商品簡介 9. 連結標的資產")
    found = list(re.finditer(TICKER, ti.text[a:b]))
    if not found:
        return ParsedField.missing("underlyings", "商品簡介 9. 讀不到彭博代碼")
    return ParsedField.present("underlyings", [f"{m[1]} {m[2]}" for m in found], ti.lines_for(a, b))


def read(lines: Sequence[Line]) -> iis.IisSheet:
    text_lines = body(lines)
    ti = TextIndex(text_lines)
    raw = RawText(text_lines)
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines)}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    # 第 1 頁
    fields["name_zh"] = raw.capture("name_zh", r"商品中文名稱\s*[:：]\s*(.+?)\s*[（(]\s*下稱", ubs.clean_name)
    fields["name_en"] = raw.capture("name_en", r"商品英文名稱\s*[:：]\s*(.+?)\s*；\s*商品種\s*類", ubs.clean_name)
    put("product_type", r"商品種類[:：](.+?)。")
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        ti,
        (("print_date_iis", "print_date", "本投資人須知刊印日期", r"本投資人須知[刋刊]印日期[:：]" + D),),
        parse_date,
    )
    # 商品簡介
    put("currency_zh", rf"6\.計價幣別[:：]({ubs.ZH_CCY})。")
    fields["underlyings"] = underlyings(ti)
    put("tenor_months", r"10\.天期/年期[:：](\d+)個月", int)
    put("issue_date", r"11\.發行日/交割日[:：]" + D, parse_date)
    a = ti.text.find("12.期末交割日/到期日")
    b = ti.text.find("13.", a)
    if a < 0 or b < a:
        fields["maturity_date"] = ParsedField.missing("maturity_date", "找不到商品簡介 12. 期末交割日/到期日")
    else:
        found = [
            (parse_date(m[1]), ti.lines_for(a + m.start(), a + m.end()))
            for m in re.finditer(r"即現(?:預計為?)?" + D, ti.text[a:b])
        ]
        fields["maturity_date"] = ParsedField.from_hits("maturity_date", found, missing_note="商品簡介 12. 讀不到日期")
    # 相關機構（審查標準）
    branch = raw.capture("issuing_branch", r"[•‧]\s*發行機構\s*[:：]\s*(.+?)\s*。")
    fields["issuing_branch"] = branch
    fields["issuer_names"] = standard_fields.occurrences(
        "issuer_names",
        [Occurrence("issuer_name_org", "issuer_name_org", "相關機構", ubs.derived_name(branch, "issuer_name_org"))],
    )
    dist = r"[•‧]\s*受託機構(?:/銷售機構)?名稱\s*[:：]\s*(.+?)\s*，\s*地址\s*(?:[:：]|為)\s*(.+?)\s*。\s*(?:二、)?\s*商品事項"
    fields["distributor_names"] = standard_fields.occurrences(
        "distributor_names",
        [
            Occurrence(
                "distributor_name_org",
                "distributor_name_org",
                "相關機構",
                raw.capture("distributor_name_org", dist),
            )
        ],
    )
    fields["distributor_addresses"] = standard_fields.occurrences(
        "distributor_addresses",
        [
            Occurrence(
                "distributor_address_org",
                "distributor_address_org",
                "相關機構",
                raw.capture("distributor_address_org", dist, ubs.without_place_note, group=2),
            )
        ],
    )
    iis.warning_distributor_names(fields, ti, ("警語1.", "相關機構"), WARNING_NAMES)
    fields["distributor_phones"] = standard_fields.occurrences(
        "distributor_phones",
        [
            Occurrence(
                "distributor_phone_contact",
                "distributor_phone_contact",
                "末頁受託機構聯絡方式",
                raw.capture(
                    "distributor_phone_contact", r"受託機構聯絡方式\s*[:：].*?電話\s*[:：]?\s*([+\d][\d\-() ]*\d)"
                ),
            )
        ],
    )
    a = ti.text.find("投資人同意下列相關費用")
    if a < 0:
        fields.update(
            {
                standard_fields.fee_field(x): ParsedField.missing(standard_fields.fee_field(x), "找不到費用表")
                for x in iis.FEES
            }
        )
    else:  # 表後的「附註：通路服務費…」不算費用項目
        fees = text_lines[ti.line_at(a) :]
        cut = next((i for i, ln in enumerate(fees) if squash(ln.text).startswith("附註")), None)
        fields.update(fee_rates(fees[:cut]))
    return iis.IisSheet(fields, ti, PROVIDED)
