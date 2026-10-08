"""BARC 說明書合成器：仿 BARC 中文產品說明書版面的 PDF，以及與之一致的參考條件表列。

所有數值、代號、名稱皆為虛構；不含任何真實交易資料。版面座標依範本規格
docs/templates/barc-zh-product-description.md 觀察值設定。PDF 排版用 tests/pdf_writer.py，
參考條件表與單份核對用 tests/reference_synth.py、tests/harness.py。
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fcn_checker.issuers import BARC, Issuer
from harness import REVIEW_STANDARD, check_rows, iis_path
from pdf_writer import FONT, PdfWriter, zh_date
from reference_synth import make_row

_STD = tomllib.loads(REVIEW_STANDARD.read_text(encoding="utf-8"))
FIXED_WARNING = _STD["risk"]["fixed_warning"]
DISTRIBUTOR = _STD["distributor"]
ISSUER_NAME = _STD["issuer_name"]["barc"]
FEES = dict(_STD["fees"])
CURRENCY_ISO = dict(_STD["currency"])
Q4 = Decimal("0.0001")
SYNTH_ISIN = "XS0000000000"  # 合成說明書封面的 ISIN


@dataclass
class UL:
    name: str
    exchange: str
    ticker: str
    initial: Decimal


DEFAULT_ULS = (
    UL("甲乙丙科技股份有限公司ADR", "紐約證券交易所", "ZZA UN", Decimal("123.4500")),
    UL("Zeta Quantum Holdings Inc", "那斯達克證券交易所", "ZQH UW", Decimal("87.2000")),
    UL("丁戊電子公司", "那斯達克證券交易所", "DWE UW", Decimal("1234.5600")),
)


@dataclass
class Spec:
    """合成說明書與參考條件表的共同參數。預設兩者完全一致。"""

    product_code: str = "029199990001"
    currency_zh: str = "美元"
    tenor: int = 6
    memory: bool = True
    ko_obs: str = "D"  # D 期間每日／P 期末定日
    ki: str = "none"  # none／AM／D／M
    strike: Decimal = Decimal("70.00")
    ko: Decimal = Decimal("100.00")
    ki_pct: Decimal = Decimal("60.00")
    annual: Decimal = Decimal("12.00")
    monthly: Decimal | None = None  # None → 由年利率推算
    trade_date: dt.date = dt.date(2030, 1, 7)
    issue_date: dt.date = dt.date(2030, 1, 14)
    final_date: dt.date = dt.date(2030, 7, 8)
    maturity_date: dt.date = dt.date(2030, 7, 11)
    denomination: int | None = None  # None → 幣別預設值
    underlyings: tuple[UL, ...] = DEFAULT_ULS
    # ---- 說明書專用的變化 ----
    price_overrides: dict[tuple[int, str], str] = field(default_factory=dict)  # (標的序, 欄) → 文字
    approval_date: dt.date = dt.date(2026, 6, 11)
    print_date: dt.date | None = None  # None → 交易日 +1
    subscription_date: dt.date | None = None  # None → 交易日
    chairman: str = "林晋輝"
    warnings: tuple[str, ...] | None = None  # 三處警語文字；None → 審查標準原文 ×3
    rr: str = "RR4"
    extra_text: str = ""  # 額外插入第五章的文字
    name_zh: str | None = None
    name_en: str | None = None
    issuer_cover: str = "英商巴克萊銀行股份有限公司（Barclays Bank PLC）"
    cross_page_price_table: bool = False
    mention_overrides: dict[str, str] = field(default_factory=dict)  # "§9"/"§15"/"§17" → 月配息率文字
    omit: frozenset[str] = frozenset()  # 例：{"trade_date"}、{"issue_date"}
    extra_strike_def: str | None = None  # 第二個執行價格定義（歧義）
    # ---- 第二階段：配息表、提前出場表、§16、第四章 ----
    guaranteed: int | None = None  # 保證配息期；None → Daily 為 1、Period End 為 0
    guaranteed_text: int | None = None  # §13(7) 定義句的期數；None → 同 guaranteed
    coupon_overrides: dict[tuple[int, str], str] = field(default_factory=dict)  # (期, valuation/payment) → 文字
    ko_overrides: dict[tuple[int, str], str] = field(default_factory=dict)  # (期, start/end/trigger/…) → 文字
    break_coupon_after: int | None = None  # 表格在第 N 期之後換頁
    scenario_overrides: dict[tuple[int, str], str] = field(default_factory=dict)  # §16 重印表
    min_subscription: int | None = None
    min_redemption: int | None = None
    ko_header_override: str | None = None  # 定日記憶式提前出場表的「自動提前出場評價日」表頭改寫
    # ---- 文件內重複出現處與審查標準固定值（Issue #41）----
    title_name: str | None = None  # 封面標題；None → 中文名稱去掉「（下稱「本商品」）」
    art1_name: str | None = None  # 第一章第 1 條商品名稱；None → 中文名稱
    distributor_code: str | None = None  # 封面受託或銷售機構商品代號；None → 商品代號
    art5_currency: str | None = None  # 第一章第 5 條計價幣別；None → 封面幣別
    issue_price: str = "100"
    strike_headers: dict[str, str] = field(default_factory=dict)  # "§15"／"§16"／"§16(ii)" → 執行價格欄頭百分比
    repeat_overrides: dict[str, str] = field(
        default_factory=dict
    )  # "§9(3)"／"§16(i)"／"§16(ii)"／"§16(iii)" → 月配息率
    scenario_notional: int | None = None  # §16 情境假設面額；None → 面額
    general_total: str | None = None  # §16(ii) 總報酬率；None → 月配息率 × 期數
    general_annualized: str | None = None  # §16(ii) 平均年化報酬率；None → 年利率
    favourable_total: str | None = None  # §16(i) 總報酬率；None → 月配息率
    t_range_end: int | None = None  # §13(7)「t 等於 G+1 至 N」的 N；None → 天期
    issuer_ch2: str | None = None  # 第二章發行機構事業名稱
    distributor_cover: tuple[str, str, str] | None = None  # 封面受託或銷售機構（名稱、電話、地址）
    distributor_address_ch2: str | None = None
    fees: dict[str, str] = field(default_factory=dict)  # 費用項目 → 費率區間（覆寫審查標準值）

    def with_(self, **kw: Any) -> Spec:
        return replace(self, **kw)

    @property
    def ccy(self) -> str:
        return CURRENCY_ISO[self.currency_zh]

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
    def denom(self) -> int:
        return self.denomination or {"USD": 10000, "JPY": 1000000, "CNH": 100000}[self.ccy]

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


def price(initial: Decimal, pct: Decimal) -> Decimal:
    return (initial * pct / 100).quantize(Q4, ROUND_HALF_UP)


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


def build_pdf(path: Path, s: Spec, *, iis: bool = True) -> Path:
    """合成說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時，旁邊另寫一份同商品投資人須知（ADR 0007：兩份一起核對）。"""
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s)
    w = PdfWriter()
    warnings = s.warnings or (FIXED_WARNING,) * 3
    warnings = tuple(x.replace("RR4", s.rr) for x in warnings)
    name_zh = s.name_zh or s.expected_name_zh()
    name_en = s.name_en or s.expected_name_en()
    monthly = s.monthly_value
    m = {k: s.mention_overrides.get(k, f"{monthly}") for k in ("§9", "§15", "§17")}
    ko_term = "自動提前出場觸發價格" if s.memory else "觸發水準"

    # ---- p1 封面 ----
    w.line(255.6, "中文產品說明書", size=12, gap=16)
    title = s.title_name or name_zh.replace("（下稱「本商品」）", "")
    w.para(45.4, title, width=42, size=12, gap=16)
    w.space(6)

    def cover(label: str, value_lines: list[str]) -> None:
        w.need(13 * len(value_lines) + 6)
        w.put(41.0, w.y, label)
        for k, v in enumerate(value_lines):
            w.put(301.4, w.y + 13 * k, v)
        w.y += 13 * len(value_lines) + 6

    cover("商品代號:", [s.product_code])
    cover("受託或銷售機構商品代號:", [s.distributor_code or s.product_code])
    cover("ISIN:", [SYNTH_ISIN])
    cover("商品中文名稱:", [name_zh[k : k + 25] for k in range(0, len(name_zh), 25)])
    cut = name_en.index("issued")
    cover("商品英文名稱:", [name_en[:cut].strip(), name_en[cut:]])
    cover("商品種類:", ["股權連結債券"])
    cover("發行機構:", [s.issuer_cover])
    cover("計價幣別:", [s.currency_zh])
    d_name, d_phone, d_addr = s.distributor_cover or (DISTRIBUTOR["name"], DISTRIBUTOR["phone"], DISTRIBUTOR["address"])
    cover("受託或銷售機構之名稱、電話及地址:", [f"{d_name}，電話：{d_phone}，地址", f"：{d_addr}"])
    cover("受託或銷售機構審查通過之日期:", [zh_date(s.approval_date)])
    w.line(41.0, "警語：", size=12, gap=18)
    w.numbered("1.", 41.0, 69.4, warnings[0][:40], gap=13)
    w.para(69.4, warnings[0][40:])
    w.numbered("2.", 41.0, 69.4, "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。", gap=16)
    w.numbered("3.", 41.0, 69.4, "本商品係依境外結構型商品管理規則於中華民國境內受託投資或受託買賣之投資標的。", gap=16)
    w.para(69.4, "OSU 依據國際金融業務條例辦理受託投資或受託買賣所允許之投資標的。")
    w.space()
    print_date = s.print_date or s.trade_date + dt.timedelta(days=1)
    w.line(41.0, f"本中文產品說明書刊印日期：{zh_date(print_date)}", gap=16)

    # ---- 第一章 ----
    w.new_page()
    w.line(41.0, "第一章 商品基本資料", size=12, gap=22)

    def article(n: int, title: str) -> None:
        w.numbered(f"{n}.", 41.0, 69.4, title)

    def sub(n: int, title: str, x_num: float = 69.4, x_body: float = 97.7) -> None:
        w.numbered(f"({n})", x_num, x_body, title)

    art1 = s.art1_name or name_zh
    article(1, f"商品名稱：{art1[:40]}")
    w.para(69.4, art1[40:])
    article(2, "商品風險程度:" + warnings[1][:34])
    w.para(69.4, warnings[1][34:], width=44)
    article(3, "發行機構名稱及其長期債務信用評等：英商巴克萊銀行股份有限公司（Barclays Bank PLC）")
    article(4, "商品之發行評等：不適用。")
    article(5, f"計價幣別：{s.art5_currency or s.currency_zh}")
    article(
        6, f"商品面額與發行價格：每單位商品面額為{s.denom:,} {s.currency_zh}。發行價格為商品面額之{s.issue_price}%。"
    )
    article(7, "計價貨幣本金保本率：無，本商品為不保障本金之境外結構型商品。")
    article(8, "投資本金達成100％保本之各項條件：不適用。")
    article(9, "主要給付項目及其計算方式：")
    sub(1, "配息金額：")
    w.line(81.0, "以本商品未發生提前贖回或終止為前提，發行機構將於每一個「配息支付日t」支付每單位商品面額乘以每")
    w.line(81.0, f"月之配息率（為{m['§9']}%(顯示至小數點後第4 位)，即年利率為{s.annual}%）所計算之配息金額。")
    sub(2, "到期贖回：")
    w.line(81.0, "有關到期贖回之詳細說明，請參閱本章第15 條之說明。")
    sub(3, "指定提前現金交割金額：")
    w.line(81.0, "若「指定提前贖回事件」發生，發行機構將支付商品面額100%加計「相關配息金額」。")
    if s.ko_obs == "D":
        rel = s.repeat_overrides.get("§9(3)", f"{monthly}")
        w.line(81.0, "「相關配息金額」係指依以下相關配息率而計算之金額：")
        w.line(106.1, f"(i) 就於第1 個自動提前出場觀察期期末日當日發生者而言，相關配息率為{rel}%；或")
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
    co, ko_cells = s.coupon_overrides, s.ko_overrides

    def cells(t: int, spec: list[tuple[float, str, str]], over: dict) -> list[tuple[float, str]]:
        return [(x, over.get((t, key), text)) for x, key, text in spec]

    def table_row(t: int, cs: list[tuple[float, str]], gap: float = 20) -> None:
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
                    r["t"], [(106.7, "valuation", f"{val}(非自動提"), (268.1, "payment", zh_date(r["payment"]))], co
                )
                w.put(137.9, w.y + 13, "前出場評價日)")
                table_row(r["t"], cs, gap=32)
            else:
                cs = cells(r["t"], [(131.7, "valuation", val), (270.6, "payment", zh_date(r["payment"]))], co)
                table_row(r["t"], cs)
    sub(7, "指定提前贖回事件：係指倘若所有標的資產之相關價格等於或大於其觸發價格，發行機構應提前贖回本商品。")
    w.line(97.7, "指定提前現金贖回日：係指定提前贖回事件發生後第三個營業日。")
    if s.memory and s.ko_obs == "D":
        n, g = s.tenor, s.guaranteed_text if s.guaranteed_text is not None else s.guaranteed_value
        if g == 0:
            text = f"自動提前出場觀察期：就t 等於1 至{n} 的情況而言，則指自相關期始日起（含）至相關期末日止（含）之各期間；"
        else:
            ordinal = "首個" if g == 1 else f"第{'一二三四五六七八九十'[g - 1]}個"
            end = s.t_range_end or n
            text = (
                f"自動提前出場觀察期：就{ordinal}（即t 等於{g} 的情況）自動提前出場觀察期而言，指期末日{g}，"
                f"且就各後續自動提前出場觀察期（其中當t 等於{g + 1} 至{end} 的情況）而言，"
                "則指自相關期始日起（含）至相關期末日止（含）之各期間；"
            )
        w.para(97.7, text + "上述各期間仍不為調整（如以下「自動提前出場觀察期」一表所示）。", width=42)
        w.line(97.7, "自動提前出場評價日：指自動提前出場觀察期內之各一籃子預定交易日。")
    if s.ko_obs == "D":
        w.line(69.4, "觀察期：")
        if s.memory:
            w.line(246.4, "自動提前出場觀察期")
            w.row([(58.1, "t"), (130.5, "期始日(含)t"), (284.3, "期末日(含)t"), (409.4, "自動提前出場觸發百分比")])
            spec = [(118.0, "start", None), (271.9, "end", None), (447.0, "trigger", None)]
        else:
            w.line(199.5, "觀察期", gap=11)
            w.put(436.9, w.y, "配息支付日t")  # 實際樣本此表頭比其他表頭高約 11 pt
            w.y += 11
            w.row([(58.1, "t"), (130.5, "期始日(含)t"), (284.3, "期末日(含)t")])
            spec = [(118.0, "start", None), (271.9, "end", None), (425.7, "payment", None)]
        for r in rows:
            text = {
                "start": r["start"],
                "end": r["end"],
                "trigger": "N/A" if r["end"] == "N/A" else f"{s.ko}%",
                "payment": zh_date(r["payment"]),
            }
            over = {**ko_cells, **(co if not s.memory else {})}
            table_row(r["t"], cells(r["t"], [(x, k, text[k]) for x, k, _ in spec], over))
    elif s.memory and s.ko_obs == "P":
        w.line(97.7, "自動提前出場評價日：依下表所示：")
        w.row(
            [
                (74.5, "t"),
                (139.1, s.ko_header_override or "自動提前出場評價日"),
                (272.0, "自動提前出場觸發百分比"),
                (424.9, "指定提前現金贖回日"),
            ]
        )
        for r in rows:
            spec = [
                (145.5, "ko_valuation", zh_date(r["valuation"])),
                (309.5, "trigger", f"{s.ko}%"),
                (428.7, "early_redemption", zh_date(r["payment"])),
            ]
            w.row([(74.5, str(r["t"])), *cells(r["t"], spec, ko_cells)])
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

    def price_table(overrides: dict, cross_page: bool, strike_hdr: str) -> None:
        cols = [("initial", 173.3), ("strike", 251.4), ("ko", 354.5)] + ([("ki", 457.5)] if s.ki != "none" else [])
        head = {
            "initial": ["最初價格"],
            "strike": ["執行價格（為最初價", f"格的{strike_hdr}%）(四捨", "五入至小數點後第4", "位)"],
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
            vals.update({k: v for (idx, k), v in overrides.items() if idx == i})
            w.need(40)
            y = w.y
            w.put(174.6, y, vals["initial"])
            w.put(277.7, y, vals["strike"])
            w.put(380.8, y, vals["ko"])
            if s.ki != "none":
                w.put(483.8, y, vals["ki"])
            name_lines = _wrap_name(u.name)
            for k, part in enumerate(name_lines):
                w.put(51.6, y + 1 + 13 * k, part)
            w.y += max(24, 13 * len(name_lines) + 12)

    price_table(s.price_overrides, s.cross_page_price_table, s.strike_headers.get("§15", f"{s.strike}"))
    if multi:
        w.line(67.7, "「表現最差之標的資產」指於最終評價日當日價值最低之標的資產。")
    w.line(96.0, "發行機構應以實物交割時，將根據發行機構及相關結算機構規則進行交割活動。")
    sub(2, "最低保證配息率：發行機構將於每一個配息支付日支付依下列公式計算之配息金額：", x_num=67.7, x_body=96.0)
    w.line(182.5, f"每單位商品面額 × {m['§15']}%(顯示至小數點後第4 位)")
    sub(3, "參與率：不適用。", x_num=67.7, x_body=96.0)
    article(16, "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失：")
    sub(3, "以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：", x_num=67.7, x_body=96.0)
    notional = f"{s.scenario_notional or s.denom:,} {s.currency_zh}"
    w.line(77.7, f"a) 每單位商品面額 = {notional}")
    w.line(77.7, "b) 投資標的單位數 = 1 單位")
    w.line(77.7, "d) 本商品標的資產之相關資訊：")
    price_table({**s.price_overrides, **s.scenario_overrides}, False, s.strike_headers.get("§16", f"{s.strike}"))
    w.line(77.7, "情境分析結果不保證未來績效。")
    _scenarios(w, s, notional)
    article(17, "平均年化報酬率：")
    sub(1, "平均年化報酬率：本商品於各配息支付日支付之配息金額，")
    w.line(97.7, f"均以每月之配息率（為{m['§17']}%，即年利率為{s.annual}%）乘以每單位商品面額計算。")
    article(18, "提前贖回事件：")
    w.line(81.0, "發行機構於發生違約事件時得提前贖回本商品。")

    # ---- 第二章 ----
    w.new_page()
    w.line(41.0, "第二章 相關機構事業概況", size=12, gap=22)
    example = ("範例股份有限公司", "範例市範例路1 號")
    distributor = (DISTRIBUTOR["name"], s.distributor_address_ch2 or DISTRIBUTOR["address"])
    for n, title, boss, (corp, addr) in [
        (1, "發行機構：", "Alex Example（CFO）", (s.issuer_ch2 or ISSUER_NAME, "1 Example Place, London")),
        (2, "總代理人：", "王小明（董事長）", example),
        (3, "保證機構：", "無", example),
        (4, "計算代理機構：", "Casey Sample（CFO）", example),
        (5, "受託或銷售機構：", s.chairman, distributor),
        (6, "報價機構：", "Robin Test", example),
    ]:
        article(n, title)
        sub(1, f"事業名稱：{corp}", x_num=67.7, x_body=96.0)
        sub(2, "設立日期：2000 年1 月1 日", x_num=67.7, x_body=96.0)
        sub(3, f"營業所在地：{addr}", x_num=67.7, x_body=96.0)
        sub(4, f"負責人姓名：{boss}", x_num=67.7, x_body=96.0)

    # ---- 第三章 ----
    w.new_page()
    w.line(41.0, "第三章 商品風險揭露", size=12, gap=22)
    article(1, "投資風險警語：")
    w.numbered("(1)", 69.4, 97.7, warnings[2][:38], gap=13)
    w.para(97.7, warnings[2][38:], width=38)

    # ---- 第四章 ----
    w.new_page()
    w.line(41.0, "第四章 一般交易事項", size=12, gap=22)
    w.numbered("1.", 35.4, 59.5, "商品開始受理申購、開始受理贖回日期及後續受理贖回日期：")
    subscription = s.subscription_date or s.trade_date
    w.numbered("(1)", 59.5, 83.7, f"商品開始受理申購日期：{zh_date(subscription)}。")
    w.numbered("(2)", 59.5, 83.7, "開始受理投資人提前贖回日期：於發行日後的次一個營業日。")
    w.numbered("2.", 35.4, 59.5, "投資人應負擔的各項費用及金額或計算基準之表列：")
    w.row([(108.4, "費用項目"), (248.4, "費率"), (314.2, "收取時點"), (378.0, "收取方式"), (517.4, "收取人")])
    fees = {**FEES, **s.fees}
    for label, rate in [
        (["申購費用"], ["申購價金的", fees["申購費用"]]),
        (["提前贖回費用"], ["投資人提前贖", "回價金的", fees["提前贖回費用"]]),
        (["管理費用（信託管理費）"], ["無"]),
        (["分銷費用（如屬發行機構", "給予受託機構之報酬）"], ["申購價金的", fees["分銷費用"]]),
        (["其他費用"], ["無"]),
    ]:
        h = 11 * max(len(label), len(rate)) + 10
        w.need(h)
        for k, t in enumerate(label):
            w.put(41.4, w.y + 11 * k, t)
        for k, t in enumerate(rate):
            w.put(226.2, w.y + 11 * k, t)
        w.put(301.4, w.y, "不適用" if rate == ["無"] else "申購時")
        w.y += h
    w.line(35.4, "附註：分銷費用係由投資人負擔。")
    min_sub = s.min_subscription or s.denom
    min_red = s.min_redemption or s.denom
    w.numbered("4.", 35.4, 59.5, "最低申購金額及累加申購金額：")
    w.line(59.5, f"最低申購金額依受託或銷售機構規定，至少為{min_sub:,} {s.currency_zh}，最低累加申購金額為1 單位。")
    w.numbered("8.", 35.4, 59.5, "提前贖回之方式：")
    w.line(88.5, f"(a) 若投資人透過受託或銷售機構要求發行機構於次級市場提前贖回，最低贖回商品面額為{min_red:,}")
    w.line(88.5, f"{s.currency_zh}，且須為商品面額之整數倍。")

    # ---- 第五章 ----
    w.new_page()
    w.line(41.0, "第五章 特別記載事項", size=12, gap=22)
    w.line(41.0, "本商品之其他事項依銷售說明書辦理。")
    if s.extra_text:
        w.para(41.0, s.extra_text)
    return _finish(w, path)


def _scenarios(w: PdfWriter, s: Spec, notional: str) -> None:
    """§16(3) 情境分析：(i) 有利（第 1 期提前出場）、(ii) 一般（持有至到期）、(iii) 最差。"""
    m = s.monthly_value
    rep = {k: s.repeat_overrides.get(k, f"{m}") for k in ("§16(i)", "§16(ii)", "§16(iii)")}
    ccy = s.currency_zh
    gross = Decimal(s.denom) * (100 + m) / 100
    w.numbered("(i)", 77.7, 106.1, "有利情況：假設本商品於第1 個觀察期期末日發生「指定提前贖回事件」。")
    w.line(77.7, "每單位指定提前現金交割金額 = 每單位商品面額 × (100% + 相關配息率)")
    w.line(77.7, f"= {notional} × (100% + {rep['§16(i)']}%) = {gross:,.2f} {ccy}")
    w.line(77.7, f"每單位累積配息金額 = 0.00 {ccy}")
    w.line(81.0, "總報酬率(截至指定提前贖回事件日之報酬) = [(每單位指定提前現金交割金額 + 每單位累積配息金額) /")
    fav = s.favourable_total or f"{m}"
    w.line(52.7, f"= [({gross:,.2f} {ccy} + 0.00 {ccy}) / {notional}] - 1 = {fav}%(平均年化報酬率：{s.annual}%)")
    w.numbered("(ii)", 77.7, 106.1, "一般情況：假設本商品未發生「指定提前贖回事件」，發行機構將於每一個「配息支付日")
    w.line(106.1, f"t」支付商品面額乘以{rep['§16(ii)']}%之配息率所計算之配息金額。舉例說明如下：")
    w.line(77.7, f"執行價格（為最初價格的{s.strike_headers.get('§16(ii)', f'{s.strike}')}%）")
    coupon = (Decimal(s.denom) * m / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    w.line(77.7, f"每單位配息金額 = {notional} × {rep['§16(ii)']}% (四捨五入至小數點後第2 位)")
    w.line(77.7, f"每單位累積配息金額 = {coupon:,.2f} {ccy} × {s.tenor} = {coupon * s.tenor:,.2f} {ccy}")
    total = s.general_total or f"{m * s.tenor}"
    ann = s.general_annualized or f"{s.annual}"
    w.line(77.7, "總報酬率(截至到期日之報酬) = [(每單位累積配息金額 + 每單位最終現金交割金額) / 每單位商品面額] - 1")
    w.line(
        77.7, f"= [({coupon * s.tenor:,.2f} {ccy} + {notional})/ {notional}] - 1 = {total}% (平均年化報酬率：{ann}%)"
    )
    w.numbered("(iii)", 77.7, 106.1, "最差情況：假設表現最差之標的資產之最終價格小於其執行價格，發行機構將支付每單位")
    w.line(106.1, f"商品面額乘以{rep['§16(iii)']}%之配息率所計算之每單位配息金額。")
    w.line(77.7, f"每單位配息金額 = {notional} × {rep['§16(iii)']}% (四捨五入至小數點後第2 位)")
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


def _xl_date(d: dt.date) -> dt.datetime:
    return dt.datetime.combine(d, dt.time())


def first_callable(s: Spec) -> int:
    """Non-Call(月)：D 型 = 保證配息期 G（第 G 期期末日起可提前出場）；P 型 = G + 1。"""
    return s.guaranteed_value if s.ko_obs == "D" else s.guaranteed_value + 1


def reference_row(s: Spec, **overrides: Any) -> dict[str, Any]:
    """與合成說明書一致的 BARC 參考條件表列；回填欄位（TS、IIS、ISIN、發行日、比價日）預設空白。overrides 以 Excel 欄名覆寫。"""
    fields: dict[str, Any] = {
        "product_code": s.product_code,
        "denomination": s.denom,
        "currency": s.ccy,
        "trade_date": _xl_date(s.trade_date),
        "final_valuation_date": _xl_date(s.final_date),
        "maturity_date": _xl_date(s.maturity_date),
        "ko_pct": float(s.ko),
        "ko_observation": s.ko_obs,
        "ko_memory": "Y" if s.memory else "N",
        "strike_pct": float(s.strike),
        "ki_pct": float(s.ki_pct) if s.ki != "none" else "-",
        "ki_type": {"none": "-", "AM": "AM", "D": "D", "M": "M"}[s.ki],
        "coupon_pa_pct": float(s.annual),
        "tenor_months": s.tenor,
        "first_callable_period": first_callable(s),
    }
    for i in range(1, 6):
        u = s.underlyings[i - 1] if i <= len(s.underlyings) else None
        fields[f"underlying_{i}"] = u.ticker if u else "-"
        fields[f"underlying_{i}_initial_price"] = float(u.initial) if u else "-"
        fields[f"underlying_{i}_strike_price"] = float(price(u.initial, s.strike)) if u else "-"
        fields[f"underlying_{i}_ki_price"] = float(price(u.initial, s.ki_pct)) if u and s.ki != "none" else "-"
        fields[f"underlying_{i}_ko_price"] = float(price(u.initial, s.ko)) if u else "-"
    return make_row("BARC", fields, **overrides)


def check_pdf(tmp_path: Path, pdf: Path, spec: Spec | None = None, *, overrides=None, headers=None):
    """以合成參考條件表（一列，依 spec）核對一份說明書，回傳該份的 CheckReport。"""
    spec = spec or Spec()
    return check_rows(tmp_path, pdf, [reference_row(spec, **(overrides or {}))], headers=headers)


def check(tmp_path: Path, spec: Spec | None = None, *, pdf_spec: Spec | None = None, overrides=None, headers=None):
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec)
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


def build_iis_pdf(path: Path, s: Spec, *, pages: int = IIS_PAGES, replace: dict[str, str] | None = None) -> Path:
    """仿 BARC 中文投資人須知（4 頁）；值與 `s` 的說明書一致。`replace` 逐行替換文字（製造錯誤）、`pages` 改頁數。"""
    w = PdfWriter()
    replace = replace or {}
    dist = DISTRIBUTOR["name"]
    fees = {**FEES, **s.fees}
    name_zh = (s.name_zh or s.expected_name_zh()).replace("（下稱「本商品」）", "")
    name_en = s.name_en or s.expected_name_en()

    def text(t: str) -> str:
        for old, new in replace.items():
            t = t.replace(old, new)
        return t

    def line(x: float, t: str) -> None:
        w.line(x, text(t))

    def para(t: str) -> None:
        w.para(20.5, text(t), width=48)

    def page1() -> None:
        line(191.0, "中文投資人須知（專業投資人與OSU 客戶）")
        line(41.3, name_zh)
        line(20.6, f"({name_en})（下稱「本商品」）（商品種類：股權連結債券）")
        w.need(13)
        w.put(20.0, w.y, text(f"商品代號:{s.product_code}"))
        w.put(157.9, w.y, text(f"受託或銷售機構商品代號:{s.distributor_code or s.product_code}"))
        w.put(441.8, w.y, text(f"ISIN:{SYNTH_ISIN}"))
        w.y += 13
        line(20.0, "警語：")
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
        line(20.0, "相關機構事業概況：")
        para(f"1.發行機構：{ISSUER_NAME}；營業所在地：1 Example Road, London。")
        para(f"3.受託或銷售機構：{dist}；營業所在地：{DISTRIBUTOR['address']}。")
        line(20.0, "商品簡介：")

    def page2() -> None:
        line(20.5, f"3.本商品風險程度：{s.rr}")
        line(20.5, f"6.計價幣別：{s.currency_zh}。")
        para(
            f"7.商品面額與發行價格：每單位商品面額為{s.denom:,} {s.currency_zh}，最低申購金額為"
            f"{s.min_subscription or s.denom:,} {s.currency_zh}。發行價格為商品面額之{s.issue_price}%。"
        )
        line(20.5, "10.連結標的資產：" + "、".join(f"{u.ticker} Equity" for u in s.underlyings) + ".")
        line(20.5, f"11.商品年期：{s.tenor} 個月。")
        line(20.5, f"12.發行日：{zh_date(s.issue_date)}。")
        line(20.5, f"13.到期日或最終實物贖回日：{zh_date(s.maturity_date)}。")
        line(20.0, "收益分配事項：")
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
        w.put(53.3, y + 12 * (rows // 2), text("標的資產"))
        for x, _, head, _ in columns:
            top = y + 12 * ((rows - len(head)) // 2)
            for k, t in enumerate(head):
                w.put(x, top + 12 * k, text(t))
        w.y = y + 12 * rows + 6
        for u in s.underlyings:
            names = _wrap_name(u.name)
            w.need(12 * len(names) + 4)
            for k, n in enumerate(names):
                w.put(30.7, w.y + 12 * k, text(n))
            for _, x, _, pct in columns:
                w.put(x, w.y, text(fmt_price(u.initial if pct is None else price(u.initial, pct))))
            w.y += 12 * len(names) + 4
        line(48.4, "(3) 指定提前現金交割金額：請參閱中文產品說明書。")

    def page3() -> None:
        line(20.0, "本商品各類投資風險：")
        para("(1) 最低收益風險：在最差的狀況下，投資人將損失所有本金及利息。")
        line(20.0, "本商品之費用明細表：")
        for label, rate in (
            (["申購費用"], ["申購價金的", fees["申購費用"]]),
            (["提前贖回費用"], ["投資人提前贖", "回價金的", fees["提前贖回費用"]]),
            (["管理費用（信託管理費或管銷費用）"], ["無"]),
            (
                ["分銷費用（如屬發行機構或發行人給予", "受託或銷售機構之報酬、費用、折讓等", "各項利益應單獨列示）"],
                ["申購價金的", fees["分銷費用"]],
            ),
        ):
            h = 11 * max(len(label), len(rate)) + 6
            w.need(h)
            for k, t in enumerate(label):
                w.put(20.4, w.y + 11 * k, text(t))
            for k, t in enumerate(rate):
                w.put(230.8, w.y + 11 * k, text(t))
            w.y += h

    def page4() -> None:
        line(20.0, "相關機構之權利、義務及責任：")
        para("1. 發行機構將根據本商品有關條件支付應付之相關款項。")

    builders = [page1, page2, page3, page4]
    for k, build in enumerate(builders):
        if 0 < k < pages:
            w.new_page()
        build()
    for _ in range(pages - len(builders)):
        w.new_page()
        line(20.0, "（續）")
    return _finish(w, path)
