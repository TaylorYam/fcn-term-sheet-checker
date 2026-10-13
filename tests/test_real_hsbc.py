"""本機 HSBC 真實樣本；交易資料不進 Git，CI 自動略過。"""

from collections import Counter

import pytest

from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import REFERENCE, TERM_SHEETS, requires, term_sheets

PDFS = term_sheets("325*.pdf")
pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "325*_TS.pdf", REFERENCE),
]


def test_real_hsbc_samples_match_exploration():
    outcome = check_all(REFERENCE, PDFS)
    items = [i for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")]  # 投資人須知另見批量測試
    reports = [i.report for i in items]
    # 含第 10 條起條號與標題同一行的 1 份（Issue #185）
    assert len(reports) == 14 and all(r.template == "hsbc-zh-pd" for r in reports)
    bad = Counter(
        (x.rule_id, x.reason_code) for r in reports for x in r.results if x.status not in (S.PASS, S.NOT_APPLICABLE)
    )
    assert bad == Counter(
        {
            ("batch.pairing", "reference_row_missing"): 1,
            # 2025 年英鎊商品（2026-10-13 使用者確認）：參考條件表到期日填成最後評價日；總配息以已進位月配息率累乘
            # （提前到期、到期情境的總配息與損益共 5 處，2026 年樣本以未進位年利率推算）；交易日早於最早的審查通過日期
            ("field.maturity_date", "value_mismatch"): 1,
            ("doc.scenario_calculations", "value_mismatch"): 5,
            ("standard.approval_date", "approval_date_not_configured"): 1,
            # 人民幣客製面額，最低交易／申購／加購金額仍是幣別預設值：文件錯誤（#182）；面額不是預設值轉人工確認
            ("field.min_amounts", "value_mismatch"): 3,
            ("doc.denomination", "denomination_non_default"): 1,
        }
    )
    # Issue #82: the issuer's unrounded-coupon total differs from the printed items by 0.01 and now passes.
    assert next(i.report for i in items if i.term_sheet.name.startswith("325000132929")).status == S.PASS
    paired = [r for r in reports if not any(x.reason_code == "reference_row_missing" for x in r.results)]
    assert len(paired) == 13
    # 上面兩份以外全部通過：含 1 份期初定價 VWAP（Issue #122）與日圓、歐元、澳幣計價連結美股（Issue #179、#185）
    assert sum(r.status == S.PASS for r in paired) == 11
