"""BARC 說明書合成器：把商品規格（tests/reference_synth.py 的 `ProductSpec`）畫成仿 BARC 中文產品說明書版面的 PDF。

所有數值、代號、名稱皆為虛構；不含任何真實交易資料。版面座標依範本規格
docs/templates/barc-zh-product-description.md 觀察值設定。PDF 排版用 tests/pdf_writer.py，
參考條件表列用 tests/reference_synth.py、單份核對用 tests/harness.py。

改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：
- 說明書封面 `ts.cover`：各欄的值 `ts.cover.{欄}`（product_code、distributor_code、isin、name_zh、name_en、kind、
  issuer、currency、distributor、approval_date），標題 `ts.title`。
- 說明書第一章各條 `ts.art{n}`：第 13 條配息表 `ts.art13.coupon.{t}.{欄}`（valuation、payment）、觀察期／提前出場表
  `ts.art13.ko.{t}.{欄}`（start、end、trigger、payment、ko_valuation、early_redemption；定日記憶式表頭
  `ts.art13.ko.header`）；第 15／16 條價格表 `ts.art15.price`／`ts.art16.price`（格子 `.{標的序}.{欄}`：name、
  initial、strike、ko、ki）；第 16 條情境 `ts.art16.i`／`.ii`／`.iii`。
- 說明書其他各章：第二章各條 `ts.ch二.{n}`（5 = 受託或銷售機構）、第三章 `ts.ch三`、第四章 `ts.ch四`（費用表各列
  `ts.ch四.fees.{費用項目}`）、第五章 `ts.ch五`。
- 投資人須知：`iis.p1`～`iis.p4`（依頁；費用表各列 `iis.p3.fees.{費用項目}`）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fcn_checker.issuers import BARC, Issuer
from fcn_checker.schema import CheckReport
from harness import STANDARD, check_sheet, iis_path
from pdf_writer import FONT, Edit, PdfWriter, zh_date
from reference_synth import Q4, ProductSpec, build_reference_sheet, price, reference_row

FIXED_WARNING = STANDARD["risk"]["fixed_warning"]
DISTRIBUTOR = STANDARD["distributor"]
ISSUER_NAME = STANDARD["issuer_name"]["barc"]
FEES = dict(STANDARD["fees"])
SYNTH_ISIN = "XS0000000000"  # 合成說明書封面的 ISIN
APPROVAL_DATE = dt.date(2026, 6, 11)  # 封面受託或銷售機構審查通過之日期


@dataclass
class Spec(ProductSpec):
    """BARC 合成說明書的規格：商品規格加上 BARC 專屬的版面結構旋鈕。預設值下說明書、投資人須知與參考條件表列完全一致。

    Non-Call 由保證配息期 G 推得（D 型 = G；P 型 = G + 1）；`first_callable` 不能直接給。
    只是改字的錯誤（名稱、日期、警語、費率、表格某一格、重複出現處的數值…）一律用 `build_pdf(..., edits=...)`；
    這裡只留改變版面結構的旋鈕：`guaranteed_text`、`omit`、`extra_strike_def`、`extra_text`、
    `cross_page_price_table`、`break_coupon_after`。
    """

    issuer: str = "BARC"
    product_code: str = "029199990001"
    first_callable: int = field(init=False, default=1)
    monthly: Decimal | None = None  # 月配息率；None → 由年利率推算
    guaranteed: int | None = None  # 保證配息期；None → Daily 為 1、Period End 為 0
    # ---- 版面結構旋鈕 ----
    guaranteed_text: int | None = None  # §13(7) 定義句的期數；None → 同 guaranteed
    omit: frozenset[str] = frozenset()  # 不畫的項目："trade_date"／"issue_date"（改寫另行公告）、"strike_def"
    extra_strike_def: str | None = None  # 第二個執行價格定義（歧義）
    extra_text: str = ""  # 額外插入第五章的文字
    cross_page_price_table: bool = False  # 第 15 條價格表表頭跨頁
    break_coupon_after: int | None = None  # 第 13 條表格在第 N 期之後換頁

    def __post_init__(self) -> None:
        self.first_callable = self.guaranteed_value if self.ko_obs == "D" else self.guaranteed_value + 1

    @property
    def monthly_value(self) -> Decimal:
        if self.monthly is not None:
            return self.monthly
        return (self.annual * self.tenor / 12 / self.tenor).quantize(Q4, ROUND_HALF_UP)

    @property
    def guaranteed_value(self) -> int:
        if self.guaranteed is not None:
            return self.guaranteed
        return 1 if self.ko_obs == "D" else 0

    @property
    def print_date(self) -> dt.date:
        """封面刊印日期：交易日 +1。"""
        return self.trade_date + dt.timedelta(days=1)

    def expected_name_zh(self) -> str:
        mem = "記憶式" if self.memory else ""
        return (
            f"英商巴克萊銀行{self.tenor}個月{self.currency_zh}計價連結股權{mem}自動提前出場結構型商品"
            "（不保本）（無擔保及無保證機構）（下稱「本商品」）"
        )

    def expected_name_en(self) -> str:
        mem = "Memory " if self.memory else ""
        return (
            f"{self.tenor} Months {self.ccy} {mem}Autocallable Equity Linked Note issued by Barclays Bank PLC "
            "(unsecured and non-guaranteed)"
        )


def fmt_price(v: Decimal) -> str:
    return f"{v:,.4f}"


def _finish(w: PdfWriter, path: Path) -> Path:
    """BARC 頁尾：封面右下「Page 1 of N」，其餘頁置中頁碼。"""
    n = w.doc.page_count
    for i, page in enumerate(w.doc):
        if i == 0:
            page.insert_text((508.7, 792), f"Page 1 of {n}", fontname=FONT, fontsize=8)
        else:
            page.insert_text((295.5, 806), str(i + 1), fontname=FONT, fontsize=8)
    return w.save(path)


# ---------------------------------------------------------------- 說明書


def next_weekday(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def schedule_rows(s: Spec) -> list[dict[str, Any]]:
    """虛構的每期日期（末期 = 最終評價日／到期日），以及依保證配息期 G 產生的提前出場欄位。"""
    g = s.guaranteed_value
    rows: list[dict[str, Any]] = []
    for t in range(1, s.tenor + 1):
        if t == s.tenor:
            v, p = s.final_date, s.maturity_date
        else:
            v = s.trade_date + dt.timedelta(days=(s.final_date - s.trade_date).days * t // s.tenor)
            while v.weekday() >= 5:
                v += dt.timedelta(days=1)
            p = v + dt.timedelta(days=3)
        if s.ko_obs == "D":
            if t < g:
                start, end = "N/A", "N/A"
            elif t == g:
                start, end = "N/A", zh_date(v)
            else:
                prev = s.issue_date if t == 1 else rows[-1]["valuation"]
                start, end = zh_date(next_weekday(prev)), zh_date(v)
        else:
            start = end = None
        rows.append(
            {
                "t": t,
                "valuation": v,
                "payment": p,
                "start": start,
                "end": end,
                "noncallable": s.ko_obs == "P" and not s.memory and t <= g,
            }
        )
    return rows


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時，旁邊另寫一份同商品投資人須知（ADR 0007：兩份一起核對），
    `edits` 兩份都套用（依段落代號）。"""
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits)
    w = PdfWriter(edits)
    name_zh = s.expected_name_zh()
    name_en = s.expected_name_en()
    monthly = s.monthly_value
    ko_term = "自動提前出場觸發價格" if s.memory else "觸發水準"

    def split_at(text: str, *cuts: int) -> list[str]:
        """整段改字後在固定位置切開（版面上分成幾行的文字，改字要看整段）。"""
        bounds = [0, *cuts, len(text)]
        return [text[a:b] for a, b in zip(bounds, bounds[1:], strict=False)]

    # ---- p1 封面 ----
    w.section = "cover"
    w.line(255.6, "中文產品說明書", size=12, gap=16)
    w.section = "title"
    w.para(45.4, name_zh.replace("（下稱「本商品」）", ""), width=42, size=12, gap=16)
    w.section = "cover"
    w.space(6)

    def cover(label: str, key: str, value: str, lines: Callable[[str], list[str]] = lambda v: [v]) -> None:
        """封面一欄：值的段落代號是 cover.<key>；整段改字後再依 `lines` 分行。"""
        value_lines = lines(w.edit(value, f"cover.{key}"))
        w.need(13 * len(value_lines) + 6)
        w.put(41.0, w.y, label)
        with w.verbatim():
            for k, v in enumerate(value_lines):
                w.put(301.4, w.y + 13 * k, v)
        w.y += 13 * len(value_lines) + 6

    def en_lines(v: str) -> list[str]:
        cut = v.find("issued")
        return [v[:cut].strip(), v[cut:]] if cut > 0 else [v]

    cover("商品代號:", "product_code", s.product_code)
    cover("受託或銷售機構商品代號:", "distributor_code", s.product_code)
    cover("ISIN:", "isin", SYNTH_ISIN)
    cover("商品中文名稱:", "name_zh", name_zh, lambda v: [v[k : k + 25] for k in range(0, len(v), 25)])
    cover("商品英文名稱:", "name_en", name_en, en_lines)
    cover("商品種類:", "kind", "股權連結債券")
    cover("發行機構:", "issuer", ISSUER_NAME)
    cover("計價幣別:", "currency", s.currency_zh)
    d_name, d_phone, d_addr = DISTRIBUTOR["name"], DISTRIBUTOR["phone"], DISTRIBUTOR["address"]
    cover(
        "受託或銷售機構之名稱、電話及地址:",
        "distributor",
        f"{d_name}，電話：{d_phone}，地址：{d_addr}",
        lambda v: [v[: v.find("，地址：") + 3], v[v.find("，地址：") + 3 :]] if "，地址：" in v else [v],
    )
    cover("受託或銷售機構審查通過之日期:", "approval_date", zh_date(APPROVAL_DATE))
    w.line(41.0, "警語：", size=12, gap=18)
    first, rest = split_at(w.edit(FIXED_WARNING), 40)
    with w.verbatim():
        w.numbered("1.", 41.0, 69.4, first, gap=13)
        w.para(69.4, rest)
    w.numbered("2.", 41.0, 69.4, "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。", gap=16)
    w.numbered("3.", 41.0, 69.4, "本商品係依境外結構型商品管理規則於中華民國境內受託投資或受託買賣之投資標的。", gap=16)
    w.para(69.4, "OSU 依據國際金融業務條例辦理受託投資或受託買賣所允許之投資標的。")
    w.space()
    w.line(41.0, f"本中文產品說明書刊印日期：{zh_date(s.print_date)}", gap=16)

    # ---- 第一章 ----
    w.new_page()
    w.section = "ch一"
    w.line(41.0, "第一章 商品基本資料", size=12, gap=22)
    art = "art"  # 條的段落代號前綴：第一章 art{n}，第二、三章 ch二.{n}／ch三.{n}

    def article(n: int, title: str) -> None:
        w.section = f"{art}{n}"
        w.numbered(f"{n}.", 41.0, 69.4, title)

    def sub(n: int, title: str, x_num: float = 69.4, x_body: float = 97.7) -> None:
        w.numbered(f"({n})", x_num, x_body, title)

    w.section = "art1"
    first, rest = split_at(w.edit(f"商品名稱：{name_zh}"), 45)
    with w.verbatim():
        article(1, first)
        w.para(69.4, rest)
    w.section = "art2"
    first, rest = split_at(w.edit("商品風險程度:" + FIXED_WARNING), 41)
    with w.verbatim():
        article(2, first)
        w.para(69.4, rest, width=44)
    article(3, "發行機構名稱及其長期債務信用評等：英商巴克萊銀行股份有限公司（Barclays Bank PLC）")
    article(4, "商品之發行評等：不適用。")
    article(5, f"計價幣別：{s.currency_zh}")
    article(6, f"商品面額與發行價格：每單位商品面額為{s.denom:,} {s.currency_zh}。發行價格為商品面額之100%。")
    article(7, "計價貨幣本金保本率：無，本商品為不保障本金之境外結構型商品。")
    article(8, "投資本金達成100％保本之各項條件：不適用。")
    article(9, "主要給付項目及其計算方式：")
    sub(1, "配息金額：")
    w.line(81.0, "以本商品未發生提前贖回或終止為前提，發行機構將於每一個「配息支付日t」支付每單位商品面額乘以每")
    w.line(81.0, f"月之配息率（為{monthly}%(顯示至小數點後第4 位)，即年利率為{s.annual}%）所計算之配息金額。")
    sub(2, "到期贖回：")
    w.line(81.0, "有關到期贖回之詳細說明，請參閱本章第15 條之說明。")
    sub(3, "指定提前現金交割金額：")
    w.line(81.0, "若「指定提前贖回事件」發生，發行機構將支付商品面額100%加計「相關配息金額」。")
    if s.ko_obs == "D":
        w.line(81.0, "「相關配息金額」係指依以下相關配息率而計算之金額：")
        w.line(106.1, f"(i) 就於第1 個自動提前出場觀察期期末日當日發生者而言，相關配息率為{monthly}%；或")
        w.line(106.1, f"(ii) 就除(i)外之其他情況而言，相關配息率為：{monthly}% × Ant/Dt")
    else:
        w.line(81.0, "「相關配息金額」係指若「指定提前贖回事件」未曾發生時，原應支付之配息金額。")
    article(10, "連結標的資產及其相對權重、與投資績效之關連情形：")
    w.line(56.0, "(1) 連結標的資產：係指下表所示之標的資產（合稱「一籃子標的資產」）。", gap=19)
    w.row([(108.6, "標的資產"), (284.7, "交易所"), (418.6, "彭博代號（僅供參考）")], gap=22)
    for u in s.underlyings:
        w.row([(60.0, u.name[:14]), (254.7, u.exchange), (433.0, f"{u.ticker} Equity")], gap=24)
    w.line(71.1, "ADR 即存託憑證。", gap=19)
    w.line(56.0, "(2) 相對權重：不適用。", gap=19)
    w.line(56.0, "(3) 投資績效之關連情形：請參閱本章第15 條之說明。", gap=19)
    article(11, "連結標的資產之相關說明：")
    w.line(81.0, "標的資產之相關資訊請參閱發行機構網站。")
    article(12, "標的資產調整：")
    w.line(81.0, "計算代理機構得依相關規定調整標的資產。")

    article(13, "商品年期、發行日、到期日及其他依商品性質而定之日期：")
    sub(1, f"商品年期：{s.tenor} 個月")
    if "trade_date" not in s.omit:
        sub(2, f"交易日：{zh_date(s.trade_date)}")
    else:
        sub(2, "交易日：另行公告")
    sub(3, f"發行日：{zh_date(s.issue_date)}" if "issue_date" not in s.omit else "發行日：另行公告")
    sub(4, f"最終評價日**：係指{zh_date(s.final_date)}，應視為評價日，如該日為「中斷日」應適用評價日有關")
    w.line(97.7, "「中斷日」之順延規定（並請參閱本條第(9)項之說明）")
    sub(5, f"到期日或最終實物贖回日†*：{zh_date(s.maturity_date)}（並請參閱本條第(9)項之說明）")
    rows = schedule_rows(s)

    def cells(table: str, t: int, spec: list[tuple[float, str, str]]) -> list[tuple[float, str, str]]:
        """一列的格子；每格的段落代號是 art13.<表>.<期>.<欄>。"""
        return [(x, text, f"art13.{table}.{t}.{key}") for x, key, text in spec]

    def table_row(t: int, cs: list[tuple[float, str, str]], gap: float = 20) -> None:
        w.row([(58.1 if t < 10 else 55.6, str(t)), *cs], gap=gap)
        if s.break_coupon_after == t:
            w.new_page()

    if s.ko_obs == "D" and not s.memory:
        sub(6, "配息支付日†*：依下表「觀察期」所示（並請參閱本條第(9)項之說明）")
    else:
        hdr = "評價日t" if (s.ko_obs == "P" and not s.memory) else "配息評價日t"
        sub(6, "配息支付日†*：依下表所示（並請參閱本條第(9)項之說明）")
        w.row([(68.7, "t"), (142.8, hdr), (281.8, "配息支付日t")])
        for r in rows:
            val = zh_date(r["valuation"])
            if r["noncallable"]:  # 註記換行：日期左移、下一行接「前出場評價日)」
                cs = cells(
                    "coupon",
                    r["t"],
                    [(106.7, "valuation", f"{val}(非自動提"), (268.1, "payment", zh_date(r["payment"]))],
                )
                w.put(137.9, w.y + 13, "前出場評價日)")
                table_row(r["t"], cs, gap=32)
            else:
                cs = cells("coupon", r["t"], [(131.7, "valuation", val), (270.6, "payment", zh_date(r["payment"]))])
                table_row(r["t"], cs)
    sub(7, "指定提前贖回事件：係指倘若所有標的資產之相關價格等於或大於其觸發價格，發行機構應提前贖回本商品。")
    w.line(97.7, "指定提前現金贖回日：係指定提前贖回事件發生後第三個營業日。")
    if s.memory and s.ko_obs == "D":
        n, g = s.tenor, s.guaranteed_text if s.guaranteed_text is not None else s.guaranteed_value
        if g == 0:
            text = f"自動提前出場觀察期：就t 等於1 至{n} 的情況而言，則指自相關期始日起（含）至相關期末日止（含）之各期間；"
        else:
            ordinal = "首個" if g == 1 else f"第{'一二三四五六七八九十'[g - 1]}個"
            text = (
                f"自動提前出場觀察期：就{ordinal}（即t 等於{g} 的情況）自動提前出場觀察期而言，指期末日{g}，"
                f"且就各後續自動提前出場觀察期（其中當t 等於{g + 1} 至{n} 的情況）而言，"
                "則指自相關期始日起（含）至相關期末日止（含）之各期間；"
            )
        w.para(97.7, text + "上述各期間仍不為調整（如以下「自動提前出場觀察期」一表所示）。", width=42)
        w.line(97.7, "自動提前出場評價日：指自動提前出場觀察期內之各一籃子預定交易日。")
    if s.ko_obs == "D":
        w.line(69.4, "觀察期：")
        if s.memory:
            w.line(246.4, "自動提前出場觀察期")
            w.row([(58.1, "t"), (130.5, "期始日(含)t"), (284.3, "期末日(含)t"), (409.4, "自動提前出場觸發百分比")])
            spec = [(118.0, "start"), (271.9, "end"), (447.0, "trigger")]
        else:
            w.line(199.5, "觀察期", gap=11)
            w.put(436.9, w.y, "配息支付日t")  # 實際樣本此表頭比其他表頭高約 11 pt
            w.y += 11
            w.row([(58.1, "t"), (130.5, "期始日(含)t"), (284.3, "期末日(含)t")])
            spec = [(118.0, "start"), (271.9, "end"), (425.7, "payment")]
        for r in rows:
            text = {
                "start": r["start"],
                "end": r["end"],
                "trigger": "N/A" if r["end"] == "N/A" else f"{s.ko}%",
                "payment": zh_date(r["payment"]),
            }
            table_row(r["t"], cells("ko", r["t"], [(x, k, text[k]) for x, k in spec]))
    elif s.memory and s.ko_obs == "P":
        w.line(97.7, "自動提前出場評價日：依下表所示：")
        w.row(
            [
                (74.5, "t"),
                (139.1, "自動提前出場評價日", "art13.ko.header"),
                (272.0, "自動提前出場觸發百分比", "art13.ko.header"),
                (424.9, "指定提前現金贖回日", "art13.ko.header"),
            ]
        )
        for r in rows:
            spec_p = [
                (145.5, "ko_valuation", zh_date(r["valuation"])),
                (309.5, "trigger", f"{s.ko}%"),
                (428.7, "early_redemption", zh_date(r["payment"])),
            ]
            w.row([(74.5, str(r["t"])), *cells("ko", r["t"], spec_p)])
    sub(8, "評價日：指各配息評價日、自動提前出場評價日及最終評價日。")
    sub(9, "附註及相關定義：")
    w.line(81.0, "「營業日」係指倫敦及紐約之商業銀行開門營業之日。")

    article(14, "配息資料及其計算公式：")
    w.line(81.0, "每單位商品面額 × 配息率")
    w.line(81.0, f"「配息率」係指每月之配息率為{monthly}%(顯示至小數點後第4 位)（即年利率為{s.annual}%）。")

    article(15, "到期贖回計算公式，最低保證配息率及參與率:")
    sub(1, "到期贖回：", x_num=67.7, x_body=96.0)
    multi = len(s.underlyings) > 1
    if multi:
        w.line(67.7, "「最終價格」就某標的資產而言，指該標的資產於「最終評價日」之「相關價格」；")
    else:
        w.line(67.7, "「最終價格」指標的資產於「最終評價日」之「相關價格」；")
    w.line(67.7, "「最初價格」詳見下表所示；", gap=23)
    if "strike_def" not in s.omit:
        w.line(67.7, f"「執行價格」詳見下表所示（為最初價格的{s.strike}%）；", gap=23)
    if s.extra_strike_def:
        w.line(67.7, f"「執行價格」詳見下表所示（為最初價格的{s.extra_strike_def}%）；", gap=23)
    if s.ki == "D":
        w.line(66.0, "「觸及生效事件」係指於任一觸及生效評價日，如任一標的資產的「相關價格」小於其「觸")
        w.line(66.0, "及生效價格」，則視為發生「觸及生效事件」；", gap=23)
        w.line(67.7, "「觸及生效評價日」指所有標的資產自交易日起（含）至最終評價日止（含）之各預定交易日；", gap=23)
    if s.ki == "M":
        w.line(67.7, "「觸及生效評價日」指各配息評價日；", gap=23)
    if s.ki != "none":
        w.line(67.7, f"「觸及生效價格」詳見下表所示（為最初價格的{s.ki_pct}%)；", gap=23)
    w.line(67.7, f"「{ko_term}」詳見下表所示（為最初價格的{s.ko}%）；", gap=23)

    def price_table(section: str, cross_page: bool) -> None:
        """價格表：表頭在段落 `section`，格子在 `section.{標的序}.{欄}`；畫完回到原段落。"""
        outer, w.section = w.section, section
        cols = [("initial", 173.3), ("strike", 251.4), ("ko", 354.5)] + ([("ki", 457.5)] if s.ki != "none" else [])
        head = {
            "initial": ["最初價格"],
            "strike": ["執行價格（為最初價", f"格的{s.strike}%）(四捨", "五入至小數點後第4", "位)"],
            "ko": (
                ["自動提前出場觸發價", "格（為最初價格乘以", "自動提前出場觸發百", "分比）(四捨五入至"]
                if s.memory
                else ["觸發水準（為最初價", f"格的{s.ko}%）(四捨", "五入至小數點後第4", "位)"]
            ),
            "ki": ["觸及生效價格（為最", f"初價格的{s.ki_pct}%）(", "四捨五入至小數點後", "第4 位)"],
        }
        if cross_page:
            w.y = w.BOTTOM - 26  # 表頭前兩行在本頁底部，其餘在下一頁
        w.need(26)
        # 表頭逐欄寫入（同一欄的各行相連），與真實樣本「每格一個文字區塊」的擷取順序一致
        for rows in ((0, 1), (2, 3)) if cross_page else ((0, 1, 2, 3),):
            if rows[0] == 2:
                w.new_page()
            w.need(13 * len(rows))
            if rows[0] == 0:
                w.put(71.5, w.y, "標的資產")
            for key, x in cols:
                for j, k in enumerate(rows):
                    if k < len(head[key]):
                        w.put(x, w.y + 13 * j, head[key][k])
            w.y += 13 * len(rows)
        w.space(10)
        pcts = {"strike": s.strike, "ko": s.ko, "ki": s.ki_pct}
        for i, u in enumerate(s.underlyings, 1):
            vals = {"initial": fmt_price(u.initial)}
            for key in ("strike", "ko", "ki"):
                vals[key] = fmt_price(price(u.initial, pcts[key]))
            w.need(40)
            y = w.y
            for key, x in (("initial", 174.6), ("strike", 277.7), ("ko", 380.8)) + (
                (("ki", 483.8),) if s.ki != "none" else ()
            ):
                w.put(x, y, vals[key], section=f"{section}.{i}.{key}")
            name_lines = _wrap_name(u.name)
            for k, part in enumerate(name_lines):
                w.put(51.6, y + 1 + 13 * k, part, section=f"{section}.{i}.name")
            w.y += max(24, 13 * len(name_lines) + 12)
        w.section = outer

    price_table("art15.price", s.cross_page_price_table)
    if multi:
        w.line(67.7, "「表現最差之標的資產」指於最終評價日當日價值最低之標的資產。")
    w.line(96.0, "發行機構應以實物交割時，將根據發行機構及相關結算機構規則進行交割活動。")
    sub(2, "最低保證配息率：發行機構將於每一個配息支付日支付依下列公式計算之配息金額：", x_num=67.7, x_body=96.0)
    w.line(182.5, f"每單位商品面額 × {monthly}%(顯示至小數點後第4 位)")
    sub(3, "參與率：不適用。", x_num=67.7, x_body=96.0)
    article(16, "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失：")
    sub(3, "以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：", x_num=67.7, x_body=96.0)
    notional = f"{s.denom:,} {s.currency_zh}"
    w.line(77.7, f"a) 每單位商品面額 = {notional}")
    w.line(77.7, "b) 投資標的單位數 = 1 單位")
    w.line(77.7, "d) 本商品標的資產之相關資訊：")
    price_table("art16.price", False)
    w.line(77.7, "情境分析結果不保證未來績效。")
    _scenarios(w, s, notional)
    article(17, "平均年化報酬率：")
    sub(1, "平均年化報酬率：本商品於各配息支付日支付之配息金額，")
    w.line(97.7, f"均以每月之配息率（為{monthly}%，即年利率為{s.annual}%）乘以每單位商品面額計算。")
    article(18, "提前贖回事件：")
    w.line(81.0, "發行機構於發生違約事件時得提前贖回本商品。")

    # ---- 第二章 ----
    w.new_page()
    w.section = "ch二"
    w.line(41.0, "第二章 相關機構事業概況", size=12, gap=22)
    art = "ch二."
    example = ("範例股份有限公司", "範例市範例路1 號")
    distributor = (DISTRIBUTOR["name"], DISTRIBUTOR["address"])
    for n, title, boss, (corp, addr) in [
        (1, "發行機構：", "Alex Example（CFO）", (ISSUER_NAME, "1 Example Place, London")),
        (2, "總代理人：", "王小明（董事長）", example),
        (3, "保證機構：", "無", example),
        (4, "計算代理機構：", "Casey Sample（CFO）", example),
        (5, "受託或銷售機構：", DISTRIBUTOR["chairman"], distributor),
        (6, "報價機構：", "Robin Test", example),
    ]:
        article(n, title)
        sub(1, f"事業名稱：{corp}", x_num=67.7, x_body=96.0)
        sub(2, "設立日期：2000 年1 月1 日", x_num=67.7, x_body=96.0)
        sub(3, f"營業所在地：{addr}", x_num=67.7, x_body=96.0)
        sub(4, f"負責人姓名：{boss}", x_num=67.7, x_body=96.0)

    # ---- 第三章 ----
    w.new_page()
    w.section = "ch三"
    w.line(41.0, "第三章 商品風險揭露", size=12, gap=22)
    art = "ch三."
    article(1, "投資風險警語：")
    first, rest = split_at(w.edit(FIXED_WARNING), 38)
    with w.verbatim():
        w.numbered("(1)", 69.4, 97.7, first, gap=13)
        w.para(97.7, rest, width=38)

    # ---- 第四章 ----
    w.new_page()
    w.section = "ch四"
    w.line(41.0, "第四章 一般交易事項", size=12, gap=22)
    w.numbered("1.", 35.4, 59.5, "商品開始受理申購、開始受理贖回日期及後續受理贖回日期：")
    w.numbered("(1)", 59.5, 83.7, f"商品開始受理申購日期：{zh_date(s.trade_date)}。")
    w.numbered("(2)", 59.5, 83.7, "開始受理投資人提前贖回日期：於發行日後的次一個營業日。")
    w.numbered("2.", 35.4, 59.5, "投資人應負擔的各項費用及金額或計算基準之表列：")
    w.row([(108.4, "費用項目"), (248.4, "費率"), (314.2, "收取時點"), (378.0, "收取方式"), (517.4, "收取人")])
    for label, rate in [
        (["申購費用"], ["申購價金的", FEES["申購費用"]]),
        (["提前贖回費用"], ["投資人提前贖", "回價金的", FEES["提前贖回費用"]]),
        (["管理費用（信託管理費）"], ["無"]),
        (["分銷費用（如屬發行機構", "給予受託機構之報酬）"], ["申購價金的", FEES["分銷費用"]]),
        (["其他費用"], ["無"]),
    ]:
        h = 11 * max(len(label), len(rate)) + 10
        w.need(h)
        fee = f"ch四.fees.{label[0].split('（')[0]}"  # 費用列的段落代號：ch四.fees.<費用項目>
        for k, t in enumerate(label):
            w.put(41.4, w.y + 11 * k, t, section=fee)
        for k, t in enumerate(rate):
            w.put(226.2, w.y + 11 * k, t, section=fee)
        w.put(301.4, w.y, "不適用" if rate == ["無"] else "申購時")
        w.y += h
    w.line(35.4, "附註：分銷費用係由投資人負擔。")
    w.numbered("4.", 35.4, 59.5, "最低申購金額及累加申購金額：")
    w.line(59.5, f"最低申購金額依受託或銷售機構規定，至少為{s.denom:,} {s.currency_zh}，最低累加申購金額為1 單位。")
    w.numbered("8.", 35.4, 59.5, "提前贖回之方式：")
    w.line(88.5, f"(a) 若投資人透過受託或銷售機構要求發行機構於次級市場提前贖回，最低贖回商品面額為{s.denom:,}")
    w.line(88.5, f"{s.currency_zh}，且須為商品面額之整數倍。")

    # ---- 第五章 ----
    w.new_page()
    w.section = "ch五"
    w.line(41.0, "第五章 特別記載事項", size=12, gap=22)
    w.line(41.0, "本商品之其他事項依銷售說明書辦理。")
    if s.extra_text:
        w.para(41.0, s.extra_text)
    return _finish(w, path)


def _scenarios(w: PdfWriter, s: Spec, notional: str) -> None:
    """§16(3) 情境分析：(i) 有利（第 1 期提前出場）、(ii) 一般（持有至到期）、(iii) 最差。"""
    m = s.monthly_value
    ccy = s.currency_zh
    gross = Decimal(s.denom) * (100 + m) / 100
    w.section = "art16.i"
    w.numbered("(i)", 77.7, 106.1, "有利情況：假設本商品於第1 個觀察期期末日發生「指定提前贖回事件」。")
    w.line(77.7, "每單位指定提前現金交割金額 = 每單位商品面額 × (100% + 相關配息率)")
    w.line(77.7, f"= {notional} × (100% + {m}%) = {gross:,.2f} {ccy}")
    w.line(77.7, f"每單位累積配息金額 = 0.00 {ccy}")
    w.line(81.0, "總報酬率(截至指定提前贖回事件日之報酬) = [(每單位指定提前現金交割金額 + 每單位累積配息金額) /")
    w.line(52.7, f"= [({gross:,.2f} {ccy} + 0.00 {ccy}) / {notional}] - 1 = {m}%(平均年化報酬率：{s.annual}%)")
    w.section = "art16.ii"
    w.numbered("(ii)", 77.7, 106.1, "一般情況：假設本商品未發生「指定提前贖回事件」，發行機構將於每一個「配息支付日")
    w.line(106.1, f"t」支付商品面額乘以{m}%之配息率所計算之配息金額。舉例說明如下：")
    w.line(77.7, f"執行價格（為最初價格的{s.strike}%）")
    coupon = (Decimal(s.denom) * m / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    w.line(77.7, f"每單位配息金額 = {notional} × {m}% (四捨五入至小數點後第2 位)")
    w.line(77.7, f"每單位累積配息金額 = {coupon:,.2f} {ccy} × {s.tenor} = {coupon * s.tenor:,.2f} {ccy}")
    w.line(77.7, "總報酬率(截至到期日之報酬) = [(每單位累積配息金額 + 每單位最終現金交割金額) / 每單位商品面額] - 1")
    w.line(
        77.7,
        f"= [({coupon * s.tenor:,.2f} {ccy} + {notional})/ {notional}] - 1 = {m * s.tenor}% (平均年化報酬率：{s.annual}%)",
    )
    w.section = "art16.iii"
    w.numbered("(iii)", 77.7, 106.1, "最差情況：假設表現最差之標的資產之最終價格小於其執行價格，發行機構將支付每單位")
    w.line(106.1, f"商品面額乘以{m}%之配息率所計算之每單位配息金額。")
    w.line(77.7, f"每單位配息金額 = {notional} × {m}% (四捨五入至小數點後第2 位)")
    w.line(77.7, f"= [(5,000.00 {ccy})/ {notional}] - 1 = -50.00% (平均年化報酬率：-100.00%)")


def _wrap_name(name: str) -> list[str]:
    if name.isascii():
        out, cur = [], ""
        for word in name.split():
            if cur and len(cur) + 1 + len(word) > 18:
                out.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        return [*out, cur]
    return [name[k : k + 9] for k in range(0, len(name), 9)]


def build_not_barc_pdf(path: Path) -> Path:
    w = PdfWriter()
    w.line(255.6, "中文產品說明書", size=12, gap=16)
    w.line(41.0, "法商範例銀行12 個月美元計價連結股權結構型商品（不保本）")
    w.line(41.0, "商品代號:")
    w.put(301.4, w.y - 13, "037199990001")
    w.line(41.0, "發行機構:")
    w.put(301.4, w.y - 13, "法商範例銀行（Example Bank SA）")
    return _finish(w, path)


# ---------------------------------------------------------------- 參考條件表與單份核對


def check_rows(
    tmp_path: Path, pdf: Path, rows: list[dict[str, Any]], *, headers: list[str] | None = None
) -> CheckReport:
    """以合成參考條件表（rows）核對一份說明書，回傳該份的 CheckReport。"""
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows, headers)
    return check_sheet(pdf, sheet)


def check_pdf(tmp_path: Path, pdf: Path, spec: Spec | None = None, *, overrides=None, headers=None):
    """以合成參考條件表（一列，依 spec）核對一份說明書，回傳該份的 CheckReport。"""
    spec = spec or Spec()
    return check_rows(tmp_path, pdf, [reference_row(spec, **(overrides or {}))], headers=headers)


def check(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    overrides=None,
    headers=None,
):
    """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋與 `spec` 一致的參考條件表一列，回傳該說明書的 CheckReport。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    return check_pdf(tmp_path, pdf, spec, overrides=overrides, headers=headers)


def barc_adapter(**overrides: Any) -> Issuer:
    """依上手 adapter interface 建立以 BARC parser 為底的測試用上手；overrides 換掉其中幾項。"""
    from fcn_checker.parsers import barc as parser
    from fcn_checker.rules import barc as rules

    fields: dict[str, Any] = {
        "code": "BARC",
        "template_id": parser.TEMPLATE_ID,
        "label": "BARC 測試 adapter",
        "parser_version": "test",
        "not_covered": (),
        "detect": lambda lines: parser.detect(parser.document(lines)),
        "read": parser.read,
        "rules": rules.run_all,
        "reference_fields": rules.REFERENCE_FIELDS,
        "iis": BARC.iis,  # 投資人須知照 BARC 範本
    }
    return Issuer(**{**fields, **overrides})


# ---------------------------------------------------------------- 投資人須知（docs/templates/barc-zh-iis.md）

IIS_PAGES = 4  # 審查標準 iis.pages


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), pages: int = IIS_PAGES) -> Path:
    """仿 BARC 中文投資人須知（4 頁）；值與 `s` 的說明書一致。`edits` 改字（製造錯誤）、`pages` 改頁數。"""
    w = PdfWriter(edits, kind="iis")
    dist = DISTRIBUTOR["name"]
    name_zh = s.expected_name_zh().replace("（下稱「本商品」）", "")
    name_en = s.expected_name_en()

    def para(t: str) -> None:
        w.para(20.5, t, width=48)

    def page1() -> None:
        w.line(191.0, "中文投資人須知（專業投資人與OSU 客戶）")
        w.line(41.3, name_zh)
        w.line(20.6, f"({name_en})（下稱「本商品」）（商品種類：股權連結債券）")
        w.need(13)
        w.put(20.0, w.y, f"商品代號:{s.product_code}")
        w.put(157.9, w.y, f"受託或銷售機構商品代號:{s.product_code}")
        w.put(441.8, w.y, f"ISIN:{SYNTH_ISIN}")
        w.y += 13
        w.line(20.0, "警語：")
        para("1." + FIXED_WARNING)
        para(
            f"4.本商品雖經{dist}審查，並不代表證實申請事項或保證本商品之價值，且{dist}不負本商品投資盈虧之責。{dist}依法不得承諾擔保投資本金或最低收益率。"
        )
        para(
            f"5.本商品持有期間如有保證配息收益和保證保本率係由英商巴克萊銀行股份有限公司（發行機構）保證，而非由{dist}保證。"
        )
        para(
            f"6.本中文投資人須知之內容如有虛偽或隱匿之情事者，除受託或銷售機構另行訂定者，係由{dist}負責外，其餘內容由總代理人負責。"
        )
        para("7.本商品係依境外結構型商品管理規則於中華民國境內受託投資或受託買賣之投資標的。")
        para(f"9.{dist}應提供專業投資人及OSU 客戶相關契約審閱期間。")
        w.line(20.0, "相關機構事業概況：")
        para(f"1.發行機構：{ISSUER_NAME}；營業所在地：1 Example Road, London。")
        para(f"3.受託或銷售機構：{dist}；營業所在地：{DISTRIBUTOR['address']}。")
        w.line(20.0, "商品簡介：")

    def page2() -> None:
        w.line(20.5, "3.本商品風險程度：RR4")
        w.line(20.5, f"6.計價幣別：{s.currency_zh}。")
        para(
            f"7.商品面額與發行價格：每單位商品面額為{s.denom:,} {s.currency_zh}，最低申購金額為"
            f"{s.denom:,} {s.currency_zh}。發行價格為商品面額之100%。"
        )
        w.line(20.5, "10.連結標的資產：" + "、".join(f"{u.ticker} Equity" for u in s.underlyings) + ".")
        w.line(20.5, f"11.商品年期：{s.tenor} 個月。")
        w.line(20.5, f"12.發行日：{zh_date(s.issue_date)}。")
        w.line(20.5, f"13.到期日或最終實物贖回日：{zh_date(s.maturity_date)}。")
        w.line(20.0, "收益分配事項：")
        para(
            f"(1) 配息金額：每單位商品面額乘以每月之配息率（為{s.monthly_value}%(顯示至小數點後第4位)，"
            f"即年利率為{s.annual}%）所計算之配息金額。"
        )
        # 價格表：記憶式商品 KO 欄頭不寫百分比；有 KI 時多一欄「觸及生效價格」（docs/templates/barc-zh-iis.md）
        ko_head = (
            [
                "自動提前出場觸發",
                "價格（為最初價格",
                "乘以自動提前出場",
                "觸發百分比）(四捨",
                "五入至小數點後第4",
                "位)",
            ]
            if s.memory
            else ["觸發水準（為最初", f"價格的{s.ko}%）(", "四捨五入至小數點", "後第4 位)"]
        )
        columns = [  # 欄頭 x、數字 x、欄頭各行、百分比
            (164.3, 165.7, ["最初價格"], None),
            (252.6, 276.6, ["執行價格（為最初", f"價格的{s.strike}%）(四", "捨五入至小數點後", "第4 位)"], s.strike),
            (363.5, 387.5, ko_head, s.ko),
        ]
        if s.ki != "none":
            columns.append(
                (
                    474.5,
                    498.5,
                    ["觸及生效價格（為", f"最初價格的{s.ki_pct}%", "）(四捨五入至小數", "點後第4 位)"],
                    s.ki_pct,
                )
            )
        rows = max(len(h) for _, _, h, _ in columns)
        w.need(12 * rows + 20)
        y = w.y
        w.put(53.3, y + 12 * (rows // 2), "標的資產")
        for x, _, head, _ in columns:
            top = y + 12 * ((rows - len(head)) // 2)
            for k, t in enumerate(head):
                w.put(x, top + 12 * k, t)
        w.y = y + 12 * rows + 6
        for u in s.underlyings:
            names = _wrap_name(u.name)
            w.need(12 * len(names) + 4)
            for k, n in enumerate(names):
                w.put(30.7, w.y + 12 * k, n)
            for _, x, _, pct in columns:
                w.put(x, w.y, fmt_price(u.initial if pct is None else price(u.initial, pct)))
            w.y += 12 * len(names) + 4
        w.line(48.4, "(3) 指定提前現金交割金額：請參閱中文產品說明書。")

    def page3() -> None:
        w.line(20.0, "本商品各類投資風險：")
        para("(1) 最低收益風險：在最差的狀況下，投資人將損失所有本金及利息。")
        w.line(20.0, "本商品之費用明細表：")
        for label, rate in (
            (["申購費用"], ["申購價金的", FEES["申購費用"]]),
            (["提前贖回費用"], ["投資人提前贖", "回價金的", FEES["提前贖回費用"]]),
            (["管理費用（信託管理費或管銷費用）"], ["無"]),
            (
                ["分銷費用（如屬發行機構或發行人給予", "受託或銷售機構之報酬、費用、折讓等", "各項利益應單獨列示）"],
                ["申購價金的", FEES["分銷費用"]],
            ),
        ):
            h = 11 * max(len(label), len(rate)) + 6
            w.need(h)
            fee = f"p3.fees.{label[0].split('（')[0]}"  # 費用列的段落代號：p3.fees.<費用項目>
            for k, t in enumerate(label):
                w.put(20.4, w.y + 11 * k, t, section=fee)
            for k, t in enumerate(rate):
                w.put(230.8, w.y + 11 * k, t, section=fee)
            w.y += h

    def page4() -> None:
        w.line(20.0, "相關機構之權利、義務及責任：")
        para("1. 發行機構將根據本商品有關條件支付應付之相關款項。")

    builders = [page1, page2, page3, page4]
    for k, build in enumerate(builders):
        if 0 < k < pages:
            w.new_page()
        w.section = f"p{k + 1}"
        build()
    for _ in range(pages - len(builders)):
        w.new_page()
        w.line(20.0, "（續）")
    return _finish(w, path)
