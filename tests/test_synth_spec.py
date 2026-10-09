"""測試切點：三家合成器共用的商品規格 `ProductSpec` 與唯一的參考條件表列 `reference_row`（Issue #145）。

只看規格與列的對應，不寫 PDF、不核對；各上手畫出的說明書與列一致另由各上手的黑箱測試保證。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import hsbc_synth
import ms_synth
import synth
from reference_synth import ProductSpec, issuer_value, reference_row


def test_each_issuer_spec_is_a_product_spec_with_its_own_defaults():
    barc, ms, hsbc = synth.Spec(), ms_synth.Spec(), hsbc_synth.Spec()

    assert all(isinstance(s, ProductSpec) for s in (barc, ms, hsbc))
    assert (barc.issuer, ms.issuer, hsbc.issuer) == ("BARC", "MS", "HSBC")
    assert [s.product_code[:3] for s in (barc, ms, hsbc)] == ["029", "147", "325"], "商品代號前三碼是上手編號"
    for s in (barc, ms, hsbc):
        row = reference_row(s)
        assert (row["發行機構"], row["TDCC Code"], row["Non-Call(月)"]) == (
            issuer_value(s.issuer),
            s.product_code,
            s.first_callable,
        )
        assert all(row.get(h) is None for h in ("ISIN Code", "發行日", "比價日_1", "TS", "IIS")), "回填欄位預設空白"


def test_reference_row_follows_the_spec_values_and_excel_overrides():
    spec = synth.Spec(product_code="029199990002", ki="AM", ki_pct=Decimal("55.00"), tenor=12, memory=False)

    row = reference_row(spec, **{"K(%)": 71})

    assert (row["KI(Freq)"], row["KI(%)"], row["天期(月)"], row["KO(memo)"]) == ("AM", 55.0, 12, "N")
    assert row["K(%)"] == 71 and row["UL_1_執行價"] == 86.415, "123.45 × 70%，四位小數"
    assert row["UL_1_下限價"] == 67.8975 and reference_row(synth.Spec())["UL_1_下限價"] == "-"
    assert row["UL_4"] == "-" and row["UL_4_進場價"] == "-"


def test_barc_non_call_is_derived_from_the_guaranteed_coupon_periods():
    assert synth.Spec().first_callable == 1 and synth.Spec(ko_obs="P").first_callable == 1
    assert synth.Spec(guaranteed=6).first_callable == 6, "D 型：第 G 期期末日起可提前出場"
    assert synth.Spec(ko_obs="P", guaranteed=2).first_callable == 3, "P 型：前 G 期不可提前出場"
    assert synth.Spec(guaranteed=2).with_(guaranteed=3).first_callable == 3


def test_ms_and_hsbc_dates_and_underlyings_follow_their_schedules():
    ms, hsbc = ms_synth.Spec(count=3, tenor=6), hsbc_synth.Spec(count=1)

    assert (ms.final_date, ms.maturity_date) == (ms.ends[-1], ms.payments[-1])
    assert len(ms.underlyings) == 3 and [u.ticker for u in ms.underlyings] == ["ZZ1 UW", "ZZ2 UW", "ZZ3 UW"]
    assert ms.with_(count=1).underlyings[0].initial == Decimal(100)
    assert (hsbc.final_date, hsbc.maturity_date) == (dt.date(2030, 7, 7), dt.date(2030, 7, 10))
    assert len(hsbc.underlyings) == 1 and hsbc.first_callable == 2
    assert reference_row(hsbc)["UL_1_進場價"] == 100.0 and reference_row(hsbc)["UL_2"] == "-"
