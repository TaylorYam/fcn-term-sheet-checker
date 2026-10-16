"""本機 HSBC 真實樣本；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。"""

from collections import Counter

import pytest

from fcn_checker.schema import CheckStatus as S
from harness import check_all
from real_data import CHECK_FORMAT, TERM_SHEETS, requires, term_sheets

PDFS = term_sheets("325*.pdf")
pytestmark = [
    pytest.mark.real_samples,
    requires(TERM_SHEETS / "325*_TS.pdf", CHECK_FORMAT),
]


def test_real_hsbc_samples_match_exploration():
    outcome = check_all(CHECK_FORMAT, PDFS)
    reports = [i.report for i in outcome.items if i.term_sheet.name.endswith("_TS.pdf")]  # 投資人須知另見批量測試
    assert len(reports) == 7 and all(r.template == "hsbc-zh-pd" for r in reports)
    bad = Counter(
        (x.rule_id, x.reason_code) for r in reports for x in r.results if x.status not in (S.PASS, S.NOT_APPLICABLE)
    )
    # 1 份參考條件表的標的代碼與說明書的交易所代碼不同（同一檔股票），待查（#310）
    assert bad == Counter({("field.underlyings", "value_mismatch"): 1})
    # 其餘全部通過
    assert sum(r.status == S.PASS for r in reports) == 6
