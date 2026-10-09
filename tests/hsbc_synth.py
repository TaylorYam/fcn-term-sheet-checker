"""HSBC 合成說明書與投資人須知 PDF：把商品規格（tests/reference_synth.py 的 `ProductSpec`）畫成仿 HSBC 版面；
商品、標的與價格皆虛構。參考條件表列不在這裡產生（`reference_synth.reference_row`）。

版面文字大多寫死（期初價 100、執行價 70%、KO 100%、KI 60%、每月 7 日六期、面額 10,000、交易日 2030-01-07），
與 `Spec` 的預設值一致；畫進 PDF 的規格值只有商品代號、KO 觀察方式、記憶式、KI 型態、標的數與年利率，
其餘改字用 `replacements`／`scenario_replacements`（整份旋鈕統一留第二個 PR）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from harness import STANDARD, iis_path
from pdf_writer import FONT, PdfWriter, zh_date
from reference_synth import UL, ProductSpec

STD = STANDARD


def underlyings(count: int) -> tuple[UL, ...]:
    """HSBC 合成文件的標的：ZZn UW、虛構標的n、期初價 100。"""
    return tuple(UL(f"虛構標的{i}", "NASDAQ", f"ZZ{i} UW", Decimal("100.0000")) for i in range(1, count + 1))


@dataclass
class Spec(ProductSpec):
    """HSBC 合成文件的規格：商品規格加上改字旋鈕。標的以 `count` 為主；最終比價日與到期日由排程推得。"""

    issuer: str = "HSBC"
    product_code: str = "325199990001"
    ki: str = "AM"
    annual: Decimal = Decimal("12")
    first_callable: int = 2
    underlyings: tuple[UL, ...] = underlyings(2)
    count: int | None = None  # 標的數；None → 依 underlyings
    partial_coupon: bool = False
    replacements: dict[str, str] = field(default_factory=dict)
    scenario_replacements: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.count is None:
            self.count = len(self.underlyings)
        else:
            self.underlyings = underlyings(self.count)
        self.final_date, self.maturity_date = self.ends[-1], self.payments[-1]

    @property
    def ends(self):
        return [dt.date(2030, 1 + j, 7) for j in range(1, self.tenor + 1)]

    @property
    def payments(self):
        return [d + dt.timedelta(days=3) for d in self.ends]

    @property
    def name(self):
        return STD["product_name"]["hsbc"]["zh"].format(
            tenor=6, ccy_zh="美元", memory_zh="記憶式" if self.memory else ""
        )

    @property
    def en(self):
        return STD["product_name"]["hsbc"]["en"].format(
            maxi_en="Maxi " if self.count > 1 else "",
            daily_en="Daily " if self.ko_obs == "D" else "",
            memory_en="Memory " if self.memory else "",
        )


def build_pdf(path: Path, s: Spec, *, iis: bool = True):
    """合成說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時，旁邊另寫一份同商品投資人須知（ADR 0007：兩份一起核對）。"""
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s)
    w = PdfWriter()

    def line(t, x=110):
        t = s.replacements.get(t, t)
        if t:
            # Wrap between text runs, never in the middle of a printed number.
            while t:
                end = min(48, len(t))
                while end < len(t) and t[end - 1] in "0123456789,." and t[end] in "0123456789,.":
                    end += 1
                w.line(x, t[:end])
                t = t[end:]

    def chapter(n, name):
        w.new_page()
        w.line(60, f"第{n}章 {name}")

    def article(n, text):
        w.line(60, f"{n}.")
        line(text)

    def sub(n):
        w.line(80, f"({n})")

    def table(scenario=False):
        for t in ["期初股價", "執行價(即期初股價的70%)", "自動提前到期價格(即期初股價的100%)"] + (
            ["觸及不保本價格(即期初股價的60%)"] if s.ki != "none" else []
        ):
            line(s.scenario_replacements.get(t, t) if scenario else t)
        for i in range(len(s.underlyings)):
            for t in [f"ZZ{i + 1} UW", f"虛構標的{i + 1}", "USD", "NASDAQ", "100.0000", "70.0000", "100.0000"] + (
                ["60.0000"] if s.ki != "none" else []
            ):
                line(s.scenario_replacements.get(t, t) if scenario else t)

    short = s.name.replace("（以下簡稱「本商品」）", "")
    line(short, 60)
    line(s.en, 60)
    line("中文產品說明書(最終版)")
    line(f"商品代號/商品中文名稱：{s.product_code}/{s.name}")
    line(f"商品英文名稱：{s.en}")
    line("商品種類：股權連結商品")
    line("計價幣別：美元")
    line("發行機構：" + STD["issuer_name"]["hsbc"])
    line("電話：+852-0000-0000")
    line("[受託或銷售機構]審查通過之日期：2026年6月11日")
    line("(參考性審閱版)內容，刊印日期：2030年1月7日")
    line("(最終版)刊印日期：2030年1月7日")
    line("受託或銷售機構之名稱、電話及地址：" + STD["distributor"]["name"])
    line("電話：+886-2-5556-1313")
    line(STD["distributor"]["address"])
    line("公會審查")
    warning = STD["risk"]["fixed_warning_by_issuer"]["hsbc"]
    line(warning)
    chapter("一", "商品基本資料")
    for n in range(1, 32):
        article(
            n,
            {
                1: "商品名稱：" + short + s.en,
                2: warning,
                5: "計價幣別：美元",
                6: "每單位面額：美元10,000元",
                7: "最低交易金額：美元10,000元",
                10: "發行價格：100%",
                11: "主要給付項目",
                12: "連結標的資產",
                15: "本商品年期",
                18: "情境分析",
                27: "ISIN：XS1999900001",
            }.get(n, "範本說明"),
        )
        if n == 11:
            sub(1)
            line(f"固定配息率={s.annual}%×1/12")
            line("配息期數=6")
            line("Nt" if s.ko_obs == "D" else "付息日(如未在該計息期間自動提前到期)")
            for i, (end, pay) in enumerate(zip(s.ends, s.payments, strict=True), 1):
                vals = [str(i)]
                if s.ko_obs == "D":
                    start = s.ends[i - 2] + dt.timedelta(days=1) if i > 2 else None
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
            line("自動提前到期價格為期初股價×100%")
            line("所有連結標的皆已成為鎖定股票" if s.memory else "評價等於或大於其自動提前到期價格")
            if s.ko_obs == "D":
                line("自動提前到期決定日為自" + zh_date(s.ends[1]) + "(含)起每個預定交易日")
            else:
                line("自動提前到期決定日")
                line("自動提前到期金額付款日")
                for i in range(1, 6):
                    for t in [str(i), zh_date(s.ends[i]), zh_date(s.payments[i])]:
                        line(t)
                line("投資人應注意")
            sub(3)
            line("執行價為期初股價×70%")
            if s.ki == "none":
                line("期末股價〔等於或大於〕執行價；期末股價〔小於〕執行價")
            else:
                line("觸及不保本價格為期初股價×60%")
                line("觸及不保本事件決定日為" + ("最後評價日" if s.ki == "AM" else "每個預定交易日"))
        if n == 12:
            sub(1)
            table()
            sub(2)
            line("標的說明")
        if n == 15:
            vals = [
                "本商品年期為6個月",
                "發行日：2030年1月14日",
                "到期日目前表定為" + zh_date(s.payments[-1]),
                "日期說明",
                "最後評價日：" + zh_date(s.ends[-1]),
                "交易日：2030年1月7日",
            ]
            for i, t in enumerate(vals, 1):
                sub(i)
                line(t)
        if n == 18:
            line("商品天期為6個月期，每單位面額為美元10,000元")
            line("固定配息率為1.0000%，配息期數=6，且假設")
            table(scenario=True)
            line("*假設天期")
            line("發行價格為100%")
            line("每單位期初投資金額=美元10,000.00(=10,000×100%)")
            for i, zh in enumerate("一二三" if s.ki == "none" else "一二三四"):
                line("情境分析" + zh + ")")
                periods = 2 if i == 0 else 6
                line(f"第1個至第{periods}個計息期間")
                if i > 0:
                    line("於6個月存續期間共6次配息")
                line("固定配息金額=美元10,000×1.0000%=美元100.00")
                if i > 0:
                    line("6個計息期間配息金額共為美元600.00")
                if i == 0 and s.partial_coupon:
                    line("第3個計息期間配息金額=美元10,000×1.0000%×5/20=美元25.00")
                if i >= 2:
                    line("假設標的（虛構標的1），執行價美元70.0000")
                    if s.ki != "none":
                        line("觸及不保本價格美元60.0000")
                if i == (2 if s.ki == "none" else 3):
                    line("交割股數：假設實物給付")
                else:
                    line("到期贖回金額為美元10,000×100%=美元10,000.00")
                    line(
                        f"損益=美元10,000.00+美元{100 * periods}.00"
                        + ("+美元25.00" if i == 0 and s.partial_coupon else "")
                        + f"-美元10,000.00=美元{100 * periods + (25 if i == 0 and s.partial_coupon else 0)}.00"
                    )
                    if i > 0:
                        line("平均年化報酬率(以簡單平均年化報酬率之方式計算)為12.00%")
    chapter("二", "相關機構事業概況")
    line("發行機構：(1)事業名稱：" + STD["issuer_name"]["hsbc"].replace("（", "(").replace("）", ")"))
    line("受託或銷售機構：(a)事業名稱：" + STD["distributor"]["name"] + "(b)電話：02-5556-1313")
    line("(c)營業所在地：" + STD["distributor"]["address"] + "(d)負責人姓名：" + STD["distributor"]["chairman"])
    line("結算機構")
    chapter("三", "商品風險揭露")
    line(warning)
    chapter("四", "一般交易事項")
    line("商品開始受理申購日：2030年1月7日")
    line("商品申購結束受理日：2030年1月7日")
    line("最低申購金額：美元10,000元")
    line("最低加購金額：美元10,000元")
    for t in ["申購費用", "提前贖回費用", "分銷費用"]:
        w.row([(60, t), (180, s.replacements.get("0%~5%", "0%~5%"))])
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
    pages: int = IIS_PAGES,
    replace: dict[str, str] | None = None,
    page_total: int | None = None,
) -> Path:
    """仿 HSBC 中文投資人須知（4 頁）；值與 `s` 的說明書一致。`replace` 逐行替換文字（製造錯誤）、`pages` 改頁數、
    `page_total` 改頁首「共 M 頁」的 M。"""
    w = PdfWriter()
    replace = replace or {}
    dist, addr = STD["distributor"]["name"], STD["distributor"]["address"]
    warning = STD["risk"]["fixed_warning_by_issuer"]["hsbc"]
    issuer = STD["issuer_name"]["hsbc"].split("（")[0]
    short = s.name.replace("（以下簡稱「本商品」）", "").replace("（", "(").replace("）", ")")
    names = [f"虛構標的{i + 1}" for i in range(len(s.underlyings))]
    tickers = [f"ZZ{i + 1} UW" for i in range(len(s.underlyings))]

    def line(t: str, x: float = 60) -> None:
        for old, new in replace.items():
            t = t.replace(old, new)
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
        line("十、投資人應詳閱本中文投資人須知內容，刊印日期：2030 年1 月7 日")
        line("相關機構")
        line(f"發行機構: {issuer}，電話: 852 0000 0000，地址：香港中環")
        line(f"受託或銷售機構：{dist}，電話：+886-2-5556-1313，地址：{addr}(營業活動所在地)")
        line("第一　商品簡介")
        line("3. " + warning)

    def page2() -> None:
        line("6. 計價幣別：美元")
        line("7. 每單位面額：10,000 美元")
        line(f"10. 連結標的資產: {', '.join(names)}")
        line(f"(彭博代碼: {', '.join(tickers)})。")
        line("11. 本商品年期: 如未發生自動提前到期事件，且投資人持有本商品至到期日，為6 個月")
        line("12. 發行日：預定為2030 年1 月14 日")
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
        build()
    for _ in range(pages - len(builders)):
        w.new_page()
        line("（續）")
    for i, page in enumerate(w.doc):
        page.insert_text((250, 30), f"第 {i + 1} 頁，共 {page_total or len(w.doc)} 頁", fontname=FONT, fontsize=8)
        page.insert_text((60, 45), "PUBLIC", fontname="helv", fontsize=8)
    return w.save(path)
