"""本機 BNP 真實樣本（Issue #191）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

斷言與探勘結論一致（docs/rules/bnp-check-rules.md §5）：7 份都辨識為 BNP 範本；有參考條件表列的 6 份只有 §5 列出的差異
（一份一檔標的的價格欄疑似分割調整轉人工覆核、同一份情境一累積配息與每期配息 × 期數不符）；沒有列的 1 份為找不到列。
投資人須知（Issue #197，docs/templates/bnp-zh-iis.md）：6 份都辨識為 BNP 投資人須知、每個範本欄位都讀得到，
和同商品說明書讀出的值一致（2026-10-14 起本機參考條件表沒有 BNP 的列，先和說明書比；有列後再補兩份都通過才回填的斷言）。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BNP, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.standard_fields import Occurrence
from fcn_checker.text import squash
from harness import check_all
from real_data import REFERENCE, TERM_SHEETS, pdfs, requires

pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "082*_TS.pdf", REFERENCE),
]
PROBLEMS = (S.MISMATCH, S.REVIEW_REQUIRED, S.ERROR)


@pytest.fixture(scope="module")
def items():
    outcome = check_all(REFERENCE, pdfs("082*.pdf"))
    return [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")], [
        i for i in outcome.items if i.term_sheet.name.endswith("_IIS.pdf")
    ]


def test_every_sample_is_recognised_as_bnp(items):
    term_sheets, _ = items
    assert len(term_sheets) == 7
    assert all(i.report.template == BNP.template_id for i in term_sheets)


def test_samples_only_differ_where_the_exploration_said(items):
    term_sheets, _ = items
    bad = Counter((r.rule_id, r.reason_code) for i in term_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter(
        {
            (
                "field.underlying_prices",
                "split_suspected",
            ): 3,  # 一檔標的的進場價、執行價、KO 價都是說明書的同一比例（分割調整）
            (
                "doc.scenario_calculations",
                "value_mismatch",
            ): 1,  # 同一份情境一累積總月配息金額 ≠ 每期配息 × 期數（文件錯誤）
            ("batch.pairing", "reference_row_missing"): 1,  # 參考條件表沒有這一列
        }
    )
    passed = [i for i in term_sheets if i.report.status == S.PASS]
    assert len(passed) == 5  # 有列的 6 份只有 1 份（分割調整＋累積配息）不是全部通過
    # 封面與第二章的受託機構電話、地址都是審查標準的等價寫法
    for field in ("distributor_phone_cover", "distributor_address_cover", "distributor_address_ch2"):
        found = [r for i in term_sheets for r in i.report.results if r.field == field]
        assert len(found) == 6 and all(r.status == S.PASS and "equivalents" in (r.tolerance or "") for r in found), (
            field
        )
    # Non-Call = 天期的 1 份：KO % 與各標的 KO 價照常核對（與 MS 不同）
    with_row = [i for i in term_sheets if i.report.results and len(i.report.results) > 2]
    assert all(any(r.rule_id == "field.ko_pct" and r.status == S.PASS for r in i.report.results) for i in with_row)


def test_bnp_investor_sheets_are_recognised_and_only_miss_their_reference_rows(items):
    _, investor_sheets = items
    assert len(investor_sheets) == 6
    assert all(i.report.template == BNP.iis.template_id and not i.unsupported for i in investor_sheets)
    bad = Counter((r.rule_id, r.reason_code) for i in investor_sheets for r in i.report.results if r.status in PROBLEMS)
    assert bad == Counter({("batch.pairing", "reference_row_missing"): 6})


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
    ("underlyings", "underlyings"),
    ("strike_pct", "strike_pct"),
    ("ko_pct", "ko_pct"),
    ("tenor_months", "tenor_months"),
    ("trade_date", "trade_date"),
    ("issue_date", "issue_date"),
    ("final_valuation_date", "final_valuation_date"),
    ("maturity_date", "maturity_date"),
    ("monthly_coupon_shown_pct", "monthly_coupon_pct"),
    ("ko_observation", "ko_observation"),
    ("ko_memory", "ko_memory"),
    ("ki_type", "ki_type"),
)


def test_every_iis_field_is_read_and_matches_the_term_sheet():
    pairs = [(p, p.with_name(p.name.replace("_IIS", "_TS"))) for p in pdfs("082*_IIS.pdf")]
    assert len(pairs) == 6
    for iis_pdf, ts_pdf in pairs:
        sheet, ts = BNP.iis.read(_lines(iis_pdf)), BNP.read(_lines(ts_pdf))
        for name in sheet.provided:
            f = sheet.f(name)
            assert f.ok, (iis_pdf.name, name, f.note)
            if isinstance(f.value, tuple) and f.value and isinstance(f.value[0], Occurrence):
                assert all(o.value.ok for o in f.value), (iis_pdf.name, name)
        for mine, theirs in SAME_AS_TERM_SHEET:
            assert sheet.f(mine).value == ts.f(theirs).value, (iis_pdf.name, mine)
        assert squash(sheet.f("name_zh").value) == squash(ts.f("name_zh").value)
        assert squash(sheet.f("name_en").value) == squash(ts.f("name_en").value)
        assert [r.prices for r in sheet.f("underlying_prices").value] == [
            r.prices for r in ts.f("underlying_prices").value
        ]
        if sheet.provides("first_callable_period"):  # 只有 D 型寫出
            assert sheet.f("first_callable_period").value == ts.f("first_callable_period").value
        if sheet.provides("ki_pct"):  # 有 KI 時才有下限價格列
            assert sheet.f("ki_pct").value == ts.f("ki_pct").value


def test_no_other_sample_is_detected_as_a_bnp_investor_sheet():
    """BNP 說明書與其他上手的說明書、投資人須知都不符合 BNP 投資人須知範本。"""
    bnp_iis = {p.name for p in pdfs("082*_IIS.pdf")}
    others = [p for p in pdfs() if p.name not in bnp_iis]
    assert len(others) > 40
    for path in others:
        issuer, _ = detect_iis(_lines(path), (BNP,))
        assert issuer is None, path.name


def test_no_other_sample_is_detected_as_a_bnp_term_sheet(items):
    """BARC、HSBC、MS、SG、UBS 等說明書與所有投資人須知（含 BNP 投資人須知）都不符合 BNP 範本。"""
    term_sheets, _ = items
    bnp = {i.term_sheet.name for i in term_sheets}
    others = [p for p in pdfs() if p.name not in bnp]
    assert len(others) > 40
    for path in others:
        doc = fitz.open(path)
        try:
            lines = extract_lines(doc)
        finally:
            doc.close()
        issuer, _ = detect(lines, (BNP,))
        assert issuer is None, path.name
