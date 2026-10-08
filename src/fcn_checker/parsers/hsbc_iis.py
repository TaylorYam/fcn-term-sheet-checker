"""HSBC 中文投資人須知（docs/templates/hsbc-zh-iis.md）：段落標題＋點號＋標籤定位。

依 8 份真實樣本建立。範本沒有商品代號、ISIN、價格與年利率（配息與價格都寫「請見產品說明書」），
發行機構名稱只寫中文；頁首「第 N 頁，共 M 頁」與 `PUBLIC` 不算內文。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..schema import DetectionResult, Evidence, Line, ParsedField
from . import iis
from .layout import TextIndex, parse_date

TEMPLATE_ID = "hsbc-zh-iis"
PARSER_VERSION = "1"
SECTIONS = ("本商品之投資風險警語", "相關機構", "第一商品簡介", "第六商品相關費用")
PAGE_HEADER = re.compile(r"-?\s*第\s*\d+\s*頁，共\s*\d+\s*頁")

PROVIDED = frozenset(
    {
        "page_totals",
        "name_zh",
        "name_en",
        "currency_zh",
        "denomination",
        "underlyings",
        "underlying_names",
        "tenor_months",
        "issue_date",
        "maturity_date",
        "print_dates",
        "issuer_names",
        "distributor_names",
        "distributor_addresses",
        "distributor_phones",
        *(f"fee_{label}" for label in iis.FEES),
    }
)


def body(lines: Sequence[Line]) -> list[Line]:
    """內文：去掉頁首「第 N 頁，共 M 頁」與 `PUBLIC` 標記。"""
    return [x for x in lines if x.text != "PUBLIC" and not PAGE_HEADER.fullmatch(x.text)]


def detect(lines: Sequence[Line]) -> DetectionResult:
    text = TextIndex(body(lines)).text
    failed = []
    pos, ordered = -1, True
    for anchor in SECTIONS:
        pos = text.find(anchor, pos + 1)
        ordered = ordered and pos >= 0
    for ok, message in (
        ("中文投資人須知" in text, "封面缺少「中文投資人須知」"),
        (re.search(r"相關機構.?發行機構[:：]香港商香港上海滙豐銀行股份有限公司", text) is not None, "發行機構不是HSBC"),
        (ordered, "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, [Evidence.of(x) for x in body(lines)[:2]])


def _underlyings(ti: TextIndex) -> ParsedField:
    """第一第 10 點：「甲, 乙(彭博代碼: ABC UN, DEF UQ)」；代號保留交易所前的空白。"""
    found = iis.raw(ti, r"10\.連結標的資產.+?11\.本商品年期")
    if found is None:
        return ParsedField.missing("underlyings", "找不到「連結標的資產」")
    text, lns = found
    m = re.search(r"彭博代碼[:：]\s*([^)）]+)[)）]", text)
    if not m:
        return ParsedField.invalid("underlyings", lns, "連結標的資產沒有「彭博代碼」")
    return ParsedField.present("underlyings", [re.sub(r"\s+", " ", t).strip() for t in m[1].split(",")], lns)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    ti = TextIndex(body(lines))
    fields: dict[str, ParsedField] = {"page_totals": iis.page_totals(lines)}

    def put(name, pattern, convert=lambda x: x):
        fields[name] = iis.capture(name, ti, pattern, convert)

    put("name_zh", r"中文投資人須知(?:\([^)]*\))?(香港上海滙豐銀行.+?結構型商品)")
    put("name_en", r"中文投資人須知(?:\([^)]*\))?香港上海滙豐銀行.+?結構型商品(.+?)本商品之投資風險警語")
    put("currency_zh", r"6\.計價幣別[:：](.+?)7\.每單位面額")
    put("denomination", r"7\.每單位面額[:：]([\d,]+)", iis.integer)
    fields["underlyings"] = _underlyings(ti)
    put("underlying_names", r"10\.連結標的資產[:：](.+?)\(彭博代碼", lambda x: x.split(","))
    put("tenor_months", r"11\.本商品年期[:：].*?為(\d+)個月12\.", int)
    put("issue_date", r"12\.發行日[:：](?:預定為)?" + iis.DATE, parse_date)
    put("maturity_date", r"13\.到期日[:：].*?目前表定為" + iis.DATE, parse_date)
    fields["print_dates"] = iis.occurrences(
        "print_dates",
        ti,
        (("print_date_iis", "刊印日期", "警語十", r"刊印日期[:：]" + iis.DATE),),
        parse_date,
    )
    fields["issuer_names"] = iis.occurrences(
        "issuer_names",
        ti,
        (
            ("issuer_name_w5", "發行機構名稱（警語五）", "警語五", r"五、本商品持有期間.+?係由(.+?)保證"),
            ("issuer_name_org", "發行機構名稱（相關機構）", "相關機構", r"相關機構.?發行機構[:：](.+?)，電話"),
        ),
    )
    fields["distributor_names"] = iis.occurrences(
        "distributor_names",
        ti,
        (
            ("distributor_name_w4", "受託機構名稱（警語四）", "警語四", r"四、本商品雖經(.+?)審查"),
            ("distributor_name_w5", "受託機構名稱（警語五）", "警語五", r"五、本商品持有期間.+?而非由(.+?)所保證"),
            ("distributor_name_org", "受託機構名稱（相關機構）", "相關機構", r"受託或銷售機構[:：](.{1,30}?)，電話"),
        ),
    )
    fields["distributor_phones"] = iis.occurrences(
        "distributor_phones",
        ti,
        (
            (
                "distributor_phone_org",
                "受託機構電話（相關機構）",
                "相關機構",
                r"受託或銷售機構[:：].{1,30}?，電話[:：]([^，]+)，地址",
            ),
            (
                "distributor_phone_s8",
                "受託機構電話（第八）",
                "第八第 3 點",
                r"受託或銷售機構連絡方式[:：]電話[:：]([+\d-]+)",
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
                "相關機構",
                r"受託或銷售機構[:：].{1,30}?，電話[:：][^，]+，地址[:：](.+?)(?:[(（]營業活動所在地[)）])?第一商品簡介",
            ),
        ),
    )
    for label, pattern in (
        ("申購費用", r"申購費用[^%]{0,20}?"),
        ("提前贖回費用", r"提前贖回費用[^%]{0,20}?"),
        ("分銷費用", r"報酬無[^%]{0,30}?"),  # 分銷費用列拆成報酬／費用／折讓三個子列，費率在「費用」子列
    ):
        put(f"fee_{label}", pattern + iis.RATE)
    return iis.IisSheet(fields, ti, PROVIDED, issuer_name_zh_only=True)
