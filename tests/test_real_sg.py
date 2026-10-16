"""本機 SG 真實樣本（Issue #194）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/sg-check-rules.md §5）：6 份（含 2 份舊版面）都辨識為 SG 範本；只有 §5 列出的差異
（舊範本的「受託投資」、舊負責人、交易日早於審查標準最早日期的審查通過日期、一檔標的除權息同比例轉人工覆核；
沿用舊字的 2026 年文件也有「受託投資」，交易日在 2026-10-15 以前特例通過，#247）。參考條件表用含 SG 列的 data/orders/核對表格式..xlsx。
投資人須知（Issue #213，docs/templates/sg-zh-iis.md）：6 份都辨識為 SG 投資人須知、每個範本欄位都讀得到，和同商品說明書
讀出的值一致；只有探勘結論的差異（舊範本固定警語 0 次、舊範本一檔標的除權息的 4 個價格；6 份警語七「受託投資」特例通過）。
說明書通過的 4 份連同投資人須知都通過，回填並打 V。
"""

import datetime as dt
from collections import Counter

import fitz
import openpyxl
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import SG, detect, detect_iis
from fcn_checker.panel_workflow import PanelSession
from fcn_checker.schema import CheckStatus as S
from fcn_checker.standard_fields import Occurrence
from fcn_checker.text import squash
from harness import REVIEW_STANDARD, ROOT, STANDARD, check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, pdfs, requires

pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "037*_TS.pdf", CHECK_FORMAT),
]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)
WARNING = squash(STANDARD["risk"]["fixed_warning_by_issuer"]["sg"])


@pytest.fixture(scope="module")
def items():
    outcome = check_all(CHECK_FORMAT, pdfs("037*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_sg(items):
    term_sheets, _ = items
    assert len(term_sheets) == 6
    assert all(i.report.template == SG.template_id for i in term_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    bad = Counter((r.rule_id, r.reason_code) for i in term_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            (
                "field.underlying_prices",
                "split_suspected",
            ): 4,  # 舊範本一檔標的的四個價格欄都是說明書的同一比例（除權息調整）
            # 2023 年交易，早於審查標準最早的日期（2025 年那份寫 2025-06-24，Issue #239 加入審查日期後依交易日通過）
            ("standard.approval_date", "approval_date_not_configured"): 1,
            ("standard.chairman", "value_mismatch"): 1,  # 舊範本的前任負責人
            ("standard.fixed_warning", "occurrence_count"): 1,  # 舊範本的警語用「受託投資」
            # 「受託投資」（舊範本 4 處；沿用舊字的 2026 年舊版面文件 1 處）：交易日在 2026-10-15 以前，特例通過（#247）
        }
    )
    passed = [i for i in term_sheets if i.report.status == S.PASS]
    # 新版面 2026 年的 3 份（含單一標的、VWAP）、寫 2025-06-24 的 2025 年那份（Issue #239），
    # 以及只差「受託投資」的 2026 年舊版面那份（#247）
    assert len(passed) == 5
    # 封面受託機構地址「3段」與第二章簡稱「玉山證券」都是審查標準的等價寫法
    for field in ("distributor_address_cover", "distributor_name_ch2"):
        found = [r for i in term_sheets for r in i.report.results if r.field == field]
        assert len(found) == 6 and all(r.status == S.PASS and "equivalents" in (r.tolerance or "") for r in found), (
            field
        )
    # 無自動提前出場（Non-Call = 天期）的 1 份：KO % = 100、各標的 KO 價 = 進場價，與表上照常比對且相符
    no_autocall = [
        i
        for i in term_sheets
        if any(
            r.rule_id == "doc.redemption_pct" and r.field == "autocall_redemption_pct" and r.status == S.NOT_APPLICABLE
            for r in i.report.results
        )
    ]
    assert len(no_autocall) == 1
    results = no_autocall[0].report.results
    assert [r.status for r in results if r.rule_id == "field.ko_pct"] == [S.PASS]
    ko_prices = [r for r in results if r.rule_id == "field.underlying_prices" and r.item.name.endswith("KO價")]
    assert ko_prices and all(r.status == S.PASS for r in ko_prices)
    assert all(r.status in (S.PASS, S.NOT_APPLICABLE) for r in results if r.rule_id.startswith("field."))


def test_sg_investor_sheets_are_recognised(items):
    _, investor_sheets = items
    assert len(investor_sheets) == 6
    assert all(i.report.template == SG.iis.template_id and not i.unsupported for i in investor_sheets)
    bad = Counter((r.rule_id, r.status) for i in investor_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            # 警語七「為於中華民國境內受託投資」6 份：交易日在 2026-10-15 以前，特例通過（#247）
            ("standard.fixed_warning", S.MISMATCH): 1,  # 2023 舊範本警語一用「受託投資」，固定警語 0 次
            ("iis.underlying_prices", S.REVIEW_REQUIRED): 4,  # 同說明書：一檔標的四個價格是除權息後的同一比例（#222）
        }
    )


def test_term_sheets_that_pass_are_filled_with_their_investor_sheets(tmp_path, items):
    """說明書通過的 5 份：投資人須知的「受託投資」特例通過（#247）後也都通過，直接回填並打 V；
    另 1 檔（舊範本，分割調整等）的說明書與投資人須知進錯誤清單。"""
    session = PanelSession(REVIEW_STANDARD, ROOT / "config", install_root=tmp_path)
    session.select(CHECK_FORMAT, pdfs("037*.pdf"))
    session.load_preview()
    batch = session.start_check().batch.items
    pairs = {i.term_sheet.name[:12]: i for i in batch if i.term_sheet.name.endswith("_IIS.pdf")}
    passed = [i for i in batch if i.term_sheet.name.endswith("_TS.pdf") and i.report.status == S.PASS]
    assert len(passed) == 5  # 2025 年那份寫 2025-06-24（Issue #239）；2026 年舊版面那份「受託投資」特例通過（#247）
    assert all(pairs[ts.term_sheet.name[:12]].report.status == S.PASS for ts in passed)
    assert all(ts.fills_sheet for ts in passed)
    receipt = session.save(tmp_path / "reports", now=dt.datetime(2030, 1, 1, 9, 0))
    wb = openpyxl.load_workbook(receipt.output, data_only=True)
    rows = list(wb["回填後"].iter_rows(values_only=True))
    head = {h: k for k, h in enumerate(rows[1]) if h}
    filled = {str(r[head["TDCC Code"]]): r for r in rows[2:] if str(r[head["TDCC Code"]]).startswith("037")}
    assert set(filled) == {ts.term_sheet.name[:12] for ts in passed}
    for code, row in filled.items():
        sheet = SG.iis.read(_lines(next(p for p in pdfs(f"{code}_IIS.pdf"))))
        assert row[head["TS/IIS完成檢核"]] == "V"
        assert row[head["ISIN Code"]] == sheet.f("isin").value  # 回填的 ISIN 也等於投資人須知
        assert row[head["發行日"]].date() == sheet.f("issue_date").value
    errors = [
        r for r in wb["錯誤清單"].iter_rows(values_only=True) if r and any(str(c).startswith("037") for c in r if c)
    ]
    assert len(errors) == 2  # 舊範本那 1 檔的說明書與投資人須知


def _lines(path):
    doc = fitz.open(path)
    try:
        return extract_lines(doc)
    finally:
        doc.close()


SAME_AS_TERM_SHEET = (  # 投資人須知欄位 → 同商品說明書欄位（探勘結論：6 份全部相同）
    ("isin", "isin"),
    ("currency_zh", "currency_zh"),
    ("denomination", "denomination"),
    ("issue_price_pct", "issue_price_pct"),
    ("underlyings", "underlyings"),
    ("strike_pct", "strike_pct"),
    ("ko_pct", "ko_pct"),
    ("ki_pct", "ki_pct"),
    ("tenor_months", "tenor_months"),
    ("trade_date", "trade_date"),
    ("issue_date", "issue_date"),
    ("final_valuation_date", "final_valuation_date"),
    ("maturity_date", "maturity_date"),
    ("coupon_pa_pct", "coupon_pa_pct"),
    ("ko_observation", "ko_observation"),
    ("first_callable_period", "first_callable_period"),
    ("ko_memory", "ko_memory"),
    ("ki_type", "ki_type"),
    ("has_autocall", "has_autocall"),
)


def test_every_iis_field_is_read_and_matches_the_term_sheet():
    pairs = [(p, p.with_name(p.name.replace("_IIS", "_TS"))) for p in pdfs("037*_IIS.pdf")]
    assert len(pairs) == 6
    forbidden, warnings = [], []
    for iis_pdf, ts_pdf in pairs:
        sheet, ts = SG.iis.read(_lines(iis_pdf)), SG.read(_lines(ts_pdf))
        for name in sheet.provided:
            f = sheet.f(name)
            assert f.ok, (iis_pdf.name, name, f.note)
            if isinstance(f.value, tuple) and f.value and isinstance(f.value[0], Occurrence):
                assert all(o.value.ok for o in f.value), (iis_pdf.name, name)
        for mine, theirs in SAME_AS_TERM_SHEET:
            if sheet.provides(mine):
                assert sheet.f(mine).value == ts.f(theirs).value, (iis_pdf.name, mine)
        for name in ("name_zh", "name_en"):
            assert squash(sheet.f(name).value) == squash(ts.f(name).value), (iis_pdf.name, name)
        assert sheet.f("monthly_coupons").value[0].value.value == ts.f("monthly_coupon_pct").value
        mine = [r.prices for r in sheet.f("underlying_prices").value]
        theirs = [r.prices for r in ts.f("underlying_prices").value]
        # 無自動提前出場時投資人須知沒有 KO 欄（說明書讀出時另以 KO 價 = 期初價格交出）
        assert mine == [{k: v for k, v in t.items() if k in m} for m, t in zip(mine, theirs, strict=True)]
        assert all(
            set(t) - set(m) <= ({"ko"} if not sheet.f("has_autocall").value else set())
            for m, t in zip(mine, theirs, strict=True)
        )
        if sheet.f("ko_observation").ok and sheet.f("ko_observation").value == "D":
            assert sheet.f("ko_observation_start").value == ts.f("ko_wording").value["start"]
        assert sheet.f("print_dates").value[0].value.value == ts.f("trade_date").value  # 刊印日期 = 訂價日
        text = sheet.full_text.text
        forbidden.append(text.count("受託投資"))
        warnings.append(text.count(WARNING))
    assert sorted(forbidden) == [1, 1, 1, 1, 1, 2]  # 警語七 6 份都有；2023 舊範本另有警語一
    assert sorted(warnings) == [0, 1, 1, 1, 1, 1]  # 固定警語只在警語一；2023 舊範本 0 次


def test_no_other_sample_is_detected_as_an_sg_investor_sheet():
    """SG 說明書與其他上手的說明書、投資人須知都不符合 SG 投資人須知範本。"""
    sg_iis = {p.name for p in pdfs("037*_IIS.pdf")}
    others = [p for p in pdfs() if p.name not in sg_iis]
    assert len(others) > 40
    for path in others:
        issuer, _ = detect_iis(_lines(path), (SG,))
        assert issuer is None, path.name


def test_no_other_sample_is_detected_as_an_sg_term_sheet(items):
    """BARC、HSBC、MS、BNP、UBS 等說明書與所有投資人須知（含 SG 投資人須知）都不符合 SG 範本。"""
    term_sheets, _ = items
    sg = {i.term_sheet.name for i in term_sheets}
    others = [p for p in pdfs() if p.name not in sg]
    assert len(others) > 40
    for path in others:
        issuer, _ = detect(_lines(path), (SG,))
        assert issuer is None, path.name
