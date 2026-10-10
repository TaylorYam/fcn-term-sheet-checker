"""HSBC 合成說明書與投資人須知 PDF：把商品規格（tests/reference_synth.py 的 `ProductSpec`）畫成仿 HSBC 版面；
商品、標的與價格皆虛構。參考條件表列不在這裡產生（`reference_synth.reference_row`）。

百分比、價格表、期數與日期、面額、幣別與 Non-Call 都由規格算出；預設值下兩份文件與參考條件表列完全一致。
改字製造錯誤用 `edits`（tests/pdf_writer.py 的 `Edit`），段落代號：說明書 `ts.cover`（第一章之前）、第一章各條
`ts.art{n}`（第 12 條價格表 `ts.art12.table`、第 18 條情境表 `ts.art18.table`）、其他各章 `ts.ch二`～`ts.ch五`；
投資人須知 `iis.p1`～`iis.p4`（依頁）。
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from harness import STANDARD, iis_path
from pdf_writer import FONT, Edit, PdfWriter, zh_date
from reference_synth import UL, ProductSpec, price

CENT = Decimal("0.01")


def underlyings(count: int) -> tuple[UL, ...]:
    """HSBC 合成文件的標的：ZZn UW、虛構標的n、期初價 100。"""
    return tuple(UL(f"虛構標的{i}", "NASDAQ", f"ZZ{i} UW", Decimal("100.0000")) for i in range(1, count + 1))


def add_months(d: dt.date, n: int) -> dt.date:
    """往後 n 個月的同一天；該月沒有這一天時取月底。"""
    m = d.month - 1 + n
    year, month = d.year + m // 12, m % 12 + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def zh_date_tight(d: dt.date) -> str:
    """HSBC 封面與第 15 條的日期寫法（數字與年月日之間不空格）。"""
    return f"{d.year}年{d.month}月{d.day}日"


def pct(x: Decimal) -> str:
    """說明書顯示的百分比：去掉多餘的零（70.00 → 70）。"""
    return f"{x.normalize():f}"


@dataclass
class Spec(ProductSpec):
    """HSBC 合成文件的規格：商品規格加上 HSBC 的版面選項。標的由 `count` 產生；最終比價日與到期日由排程推得（不能直接給）。"""

    issuer: str = "HSBC"
    product_code: str = "325199990001"
    ki: str = "AM"
    annual: Decimal = Decimal("12")
    first_callable: int = 2
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 2  # 標的數
    partial_coupon: bool = False  # 情境一多一行不足一期的配息

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count)
        self.final_date, self.maturity_date = self.ends[-1], self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期期末日：交易日往後第 j 個月的同一天。"""
        return [add_months(self.trade_date, j) for j in range(1, self.tenor + 1)]

    @property
    def payments(self) -> list[dt.date]:
        return [d + dt.timedelta(days=3) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Decimal("0.0001"), ROUND_HALF_UP)

    @property
    def coupon(self) -> Decimal:
        """每期固定配息金額（面額 × 月配息率）。"""
        return (self.denom * self.monthly / 100).quantize(CENT, ROUND_HALF_UP)

    def coupons(self, periods: int) -> Decimal:
        """前 `periods` 期配息合計：以年利率算後才四捨五入（不是每期金額 × 期數）。"""
        return (self.denom * self.annual / 100 / 12 * periods).quantize(CENT, ROUND_HALF_UP)

    @property
    def name(self):
        return STANDARD["product_name"]["hsbc"]["zh"].format(
            tenor=self.tenor, ccy_zh=self.currency_zh, memory_zh="記憶式" if self.memory else ""
        )

    @property
    def en(self):
        return STANDARD["product_name"]["hsbc"]["en"].format(
            maxi_en="Maxi " if len(self.underlyings) > 1 else "",
            daily_en="Daily " if self.ko_obs == "D" else "",
            memory_en="Memory " if self.memory else "",
        )


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時，旁邊另寫一份同商品投資人須知（ADR 0007：兩份一起核對），
    `edits` 兩份都套用（依段落代號）。"""
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits)
    w = PdfWriter(edits)
    ccy, denom, monthly, n = s.currency_zh, f"{s.denom:,}", s.monthly, s.tenor
    first = s.first_callable

    def line(t, x=110):
        t = w.edit(t)
        # Wrap between text runs, never in the middle of a printed number.
        with w.verbatim():
            while t:
                end = min(48, len(t))
                while end < len(t) and t[end - 1] in "0123456789,." and t[end] in "0123456789,.":
                    end += 1
                w.line(x, t[:end])
                t = t[end:]

    def chapter(n, name):
        w.new_page()
        w.section = f"ch{n}"
        w.line(60, f"第{n}章 {name}")

    def article(n, text):
        w.section = f"art{n}"
        w.line(60, f"{n}.")
        line(text)

    def sub(n):
        w.line(80, f"({n})")

    def table(section):
        w.section = section
        for t in [
            "期初股價",
            f"執行價(即期初股價的{pct(s.strike)}%)",
            f"自動提前到期價格(即期初股價的{pct(s.ko)}%)",
        ] + ([f"觸及不保本價格(即期初股價的{pct(s.ki_pct)}%)"] if s.ki != "none" else []):
            line(t)
        for u in s.underlyings:
            prices = [price(u.initial, p) for p in (s.strike, s.ko) + ((s.ki_pct,) if s.ki != "none" else ())]
            for t in [u.ticker, u.name, "USD", u.exchange, f"{u.initial:.4f}", *(f"{p:.4f}" for p in prices)]:
                line(t)

    w.section = "cover"
    short = s.name.replace("（以下簡稱「本商品」）", "")
    line(short, 60)
    line(s.en, 60)
    line("中文產品說明書(最終版)")
    line(f"商品代號/商品中文名稱：{s.product_code}/{s.name}")
    line(f"商品英文名稱：{s.en}")
    line("商品種類：股權連結商品")
    line(f"計價幣別：{ccy}")
    line("發行機構：" + STANDARD["issuer_name"]["hsbc"])
    line("電話：+852-0000-0000")
    line("[受託或銷售機構]審查通過之日期：2026年6月11日")
    line(f"(參考性審閱版)內容，刊印日期：{zh_date_tight(s.trade_date)}")
    line(f"(最終版)刊印日期：{zh_date_tight(s.trade_date)}")
    line("受託或銷售機構之名稱、電話及地址：" + STANDARD["distributor"]["name"])
    line("電話：+886-2-5556-1313")
    line(STANDARD["distributor"]["address"])
    line("公會審查")
    warning = STANDARD["risk"]["fixed_warning_by_issuer"]["hsbc"]
    line(warning)
    chapter("一", "商品基本資料")
    for k in range(1, 32):
        article(
            k,
            {
                1: "商品名稱：" + short + s.en,
                2: warning,
                5: f"計價幣別：{ccy}",
                6: f"每單位面額：{ccy}{denom}元",
                7: f"最低交易金額：{ccy}{denom}元",
                10: "發行價格：100%",
                11: "主要給付項目",
                12: "連結標的資產",
                15: "本商品年期",
                18: "情境分析",
                27: "ISIN：XS1999900001",
            }.get(k, "範本說明"),
        )
        if k == 11:
            sub(1)
            line(f"固定配息率={s.annual}%×1/12")
            line(f"配息期數={n}")
            line("Nt" if s.ko_obs == "D" else "付息日(如未在該計息期間自動提前到期)")
            for i, (end, pay) in enumerate(zip(s.ends, s.payments, strict=True), 1):
                vals = [str(i)]
                if s.ko_obs == "D":
                    start = s.ends[i - 2] + dt.timedelta(days=1) if i > first else None
                    if start:
                        while start.weekday() >= 5:
                            start += dt.timedelta(days=1)
                    vals += [zh_date(start) if start else "-", zh_date(end), zh_date(pay), "20" if start else "-"]
                else:
                    vals += [zh_date(pay)]
                for t in vals:
                    line(t)
            line("註1：假設日期")
            sub(2)
            line(f"自動提前到期價格為期初股價×{pct(s.ko)}%")
            line("所有連結標的皆已成為鎖定股票" if s.memory else "評價等於或大於其自動提前到期價格")
            if s.ko_obs == "D":
                line("自動提前到期決定日為自" + zh_date(s.ends[first - 1]) + "(含)起每個預定交易日")
            else:
                line("自動提前到期決定日")
                line("自動提前到期金額付款日")
                for i, j in enumerate(range(first - 1, n), 1):
                    for t in [str(i), zh_date(s.ends[j]), zh_date(s.payments[j])]:
                        line(t)
                line("投資人應注意")
            sub(3)
            line(f"執行價為期初股價×{pct(s.strike)}%")
            if s.ki == "none":
                line("期末股價〔等於或大於〕執行價；期末股價〔小於〕執行價")
            else:
                line(f"觸及不保本價格為期初股價×{pct(s.ki_pct)}%")
                line("觸及不保本事件決定日為" + ("最後評價日" if s.ki == "AM" else "每個預定交易日"))
        if k == 12:
            sub(1)
            table("art12.table")
            w.section = "art12"
            sub(2)
            line("標的說明")
        if k == 15:
            vals = [
                f"本商品年期為{n}個月",
                f"發行日：{zh_date_tight(s.issue_date)}",
                "到期日目前表定為" + zh_date(s.payments[-1]),
                "日期說明",
                "最後評價日：" + zh_date(s.ends[-1]),
                f"交易日：{zh_date_tight(s.trade_date)}",
            ]
            for i, t in enumerate(vals, 1):
                sub(i)
                line(t)
        if k == 18:
            line(f"商品天期為{n}個月期，每單位面額為{ccy}{denom}元")
            line(f"固定配息率為{monthly}%，配息期數={n}，且假設")
            table("art18.table")
            w.section = "art18"
            line("*假設天期")
            line("發行價格為100%")
            line(f"每單位期初投資金額={ccy}{s.denom:,.2f}(={denom}×100%)")
            partial = (s.coupon * 5 / 20).quantize(CENT, ROUND_HALF_UP) if s.partial_coupon else None
            ul = s.underlyings[0]
            for i, zh in enumerate("一二三" if s.ki == "none" else "一二三四"):
                line("情境分析" + zh + ")")
                periods = first if i == 0 else n
                line(f"第1個至第{periods}個計息期間")
                if i > 0:
                    line(f"於{n}個月存續期間共{n}次配息")
                line(f"固定配息金額={ccy}{denom}×{monthly}%={ccy}{s.coupon:,.2f}")
                if i > 0:
                    line(f"{n}個計息期間配息金額共為{ccy}{s.coupons(n):,.2f}")
                extra = partial if i == 0 else None
                if extra is not None:
                    line(f"第{periods + 1}個計息期間配息金額={ccy}{denom}×{monthly}%×5/20={ccy}{extra:,.2f}")
                if i >= 2:
                    line(f"假設標的（{ul.name}），執行價{ccy}{price(ul.initial, s.strike):.4f}")
                    if s.ki != "none":
                        line(f"觸及不保本價格{ccy}{price(ul.initial, s.ki_pct):.4f}")
                if i == (2 if s.ki == "none" else 3):
                    line("交割股數：假設實物給付")
                else:
                    line(f"到期贖回金額為{ccy}{denom}×100%={ccy}{s.denom:,.2f}")
                    got = s.coupons(periods)
                    line(
                        f"損益={ccy}{s.denom:,.2f}+{ccy}{got:,.2f}"
                        + (f"+{ccy}{extra:,.2f}" if extra is not None else "")
                        + f"-{ccy}{s.denom:,.2f}={ccy}{got + (extra or 0):,.2f}"
                    )
                    if i > 0:
                        line(f"平均年化報酬率(以簡單平均年化報酬率之方式計算)為{s.annual:.2f}%")
    chapter("二", "相關機構事業概況")
    line("發行機構：(1)事業名稱：" + STANDARD["issuer_name"]["hsbc"].replace("（", "(").replace("）", ")"))
    line("受託或銷售機構：(a)事業名稱：" + STANDARD["distributor"]["name"] + "(b)電話：02-5556-1313")
    line(
        "(c)營業所在地：" + STANDARD["distributor"]["address"] + "(d)負責人姓名：" + STANDARD["distributor"]["chairman"]
    )
    line("結算機構")
    chapter("三", "商品風險揭露")
    line(warning)
    chapter("四", "一般交易事項")
    line(f"商品開始受理申購日：{zh_date_tight(s.trade_date)}")
    line(f"商品申購結束受理日：{zh_date_tight(s.trade_date)}")
    line(f"最低申購金額：{ccy}{denom}元")
    line(f"最低加購金額：{ccy}{denom}元")
    for t in ["申購費用", "提前贖回費用", "分銷費用"]:
        w.row([(60, t), (180, "0%~5%")])
    chapter("五", "特別記載事項")
    line("其他說明")
    for i, page in enumerate(w.doc):
        page.insert_text((270, 806), f"第{i + 1}頁，共{len(w.doc)}頁", fontname=FONT, fontsize=8)
    return w.save(path)


# ---------------------------------------------------------------- 投資人須知（docs/templates/hsbc-zh-iis.md）

IIS_PAGES = 4  # 審查標準 iis.pages


def build_iis_pdf(
    path: Path,
    s: Spec,
    *,
    edits: Sequence[Edit] = (),
    pages: int = IIS_PAGES,
    page_total: int | None = None,
) -> Path:
    """仿 HSBC 中文投資人須知（4 頁）；值與 `s` 的說明書一致。`edits` 改字（製造錯誤）、`pages` 改頁數、
    `page_total` 改頁首「共 M 頁」的 M。"""
    w = PdfWriter(edits, kind="iis")
    dist, addr = STANDARD["distributor"]["name"], STANDARD["distributor"]["address"]
    warning = STANDARD["risk"]["fixed_warning_by_issuer"]["hsbc"]
    issuer = STANDARD["issuer_name"]["hsbc"].split("（")[0]
    short = s.name.replace("（以下簡稱「本商品」）", "").replace("（", "(").replace("）", ")")
    names = [u.name for u in s.underlyings]
    tickers = [u.ticker for u in s.underlyings]

    def line(t: str, x: float = 60) -> None:
        t = w.edit(t)
        with w.verbatim():
            for k in range(0, len(t), 44):
                w.line(x, t[k : k + 44])

    def page1() -> None:
        line("中文投資人須知(最終版)", 214)
        line(short)
        line(s.en)
        line("本商品之投資風險警語：")
        line("一、" + warning)
        line(f"四、本商品雖經{dist}審查，並不代表證實申請事項。")
        line(f"五、本商品持有期間如有保證配息收益或保證保本率，係由{issuer}保證，而非由{dist}所保證。")
        line("七、本商品係依境外結構型商品管理規則規定，於臺灣境內受託投資、受託買賣或為投資型保單之投資標的。")
        line(f"十、投資人應詳閱本中文投資人須知內容，刊印日期：{zh_date(s.trade_date)}")
        line("相關機構")
        line(f"發行機構: {issuer}，電話: 852 0000 0000，地址：香港中環")
        line(f"受託或銷售機構：{dist}，電話：+886-2-5556-1313，地址：{addr}(營業活動所在地)")
        line("第一　商品簡介")
        line("3. " + warning)

    def page2() -> None:
        line(f"6. 計價幣別：{s.currency_zh}")
        line(f"7. 每單位面額：{s.denom:,} {s.currency_zh}")
        line(f"10. 連結標的資產: {', '.join(names)}")
        line(f"(彭博代碼: {', '.join(tickers)})。")
        line(f"11. 本商品年期: 如未發生自動提前到期事件，且投資人持有本商品至到期日，為{s.tenor} 個月")
        line(f"12. 發行日：預定為{zh_date(s.issue_date)}")
        line(f"13. 到期日：如未發生提前贖回之條件，目前表定為{zh_date(s.payments[-1])}。")
        line("14. 開始受理贖回日期：發行日後的次一個預定交易日")

    def page3() -> None:
        line("第五　商品風險揭露")
        line("第六　商品相關費用")
        for t in ["費用項目", "申購費用", "申購價金的", "0%~5%", "提前贖回費用", "投資人提前贖", "回價金的", "0%~5%"]:
            line(t)

    def page4() -> None:
        for t in [
            "報酬無",
            "費用申購價金的",
            "0%~5%",
            "分銷費用(如屬發行機構或發行人給予受託或銷售機構之報酬)",
            "折讓無",
        ]:
            line(t)
        line("第七　相關機構之權利、義務及責任")
        line("第八　協助投資人權益之保護方式")
        line("3. 受託或銷售機構連絡方式：電話：+886-2-5556-1313")

    builders = [page1, page2, page3, page4]
    for k, build in enumerate(builders):
        if 0 < k < pages:
            w.new_page()
        w.section = f"p{k + 1}"
        build()
    for _ in range(pages - len(builders)):
        w.new_page()
        line("（續）")
    for i, page in enumerate(w.doc):
        page.insert_text((250, 30), f"第 {i + 1} 頁，共 {page_total or len(w.doc)} 頁", fontname=FONT, fontsize=8)
        page.insert_text((60, 45), "PUBLIC", fontname="helv", fontsize=8)
    return w.save(path)
