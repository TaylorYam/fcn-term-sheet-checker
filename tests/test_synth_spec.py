"""測試切點：三家合成器共用的商品規格 `ProductSpec`、唯一的參考條件表列 `reference_row`（Issue #145），
以及共用的改字 `Edit`（Issue #166）。

只看規格與列的對應、改字有沒有換到文字，不核對；各上手畫出的說明書與列一致另由各上手的黑箱測試保證。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

import hsbc_synth
import ms_synth
import synth
from pdf_writer import Edit
from reference_synth import ProductSpec, as_headers, issuer_value, reference_row


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
    assert reference_row(spec, **as_headers({"isin": "XS1"}))["ISIN Code"] == "XS1", "也可以用標準欄位名覆寫"


def test_barc_non_call_is_derived_from_the_guaranteed_coupon_periods():
    assert synth.Spec().first_callable == 1 and synth.Spec(ko_obs="P").first_callable == 1
    assert synth.Spec(guaranteed=6).first_callable == 6, "D 型：第 G 期期末日起可提前出場"
    assert synth.Spec(ko_obs="P", guaranteed=2).first_callable == 3, "P 型：前 G 期不可提前出場"
    assert synth.Spec(guaranteed=2).with_(guaranteed=3).first_callable == 3
    with pytest.raises(TypeError):
        synth.Spec(first_callable=3)  # 推得的欄位不能直接給
    with pytest.raises((TypeError, ValueError)):  # init=False 欄位：3.13 為 TypeError、3.11 為 ValueError
        synth.Spec().with_(first_callable=3)


def test_ms_and_hsbc_dates_and_underlyings_follow_their_schedules():
    ms, hsbc = ms_synth.Spec(count=3, tenor=6), hsbc_synth.Spec(count=1)

    assert (ms.final_date, ms.maturity_date) == (ms.ends[-1], ms.payments[-1])
    assert len(ms.underlyings) == 3 and [u.ticker for u in ms.underlyings] == ["ZZ1 UW", "ZZ2 UW", "ZZ3 UW"]
    assert ms.with_(count=1).underlyings[0].initial == Decimal(100)
    assert (hsbc.final_date, hsbc.maturity_date) == (dt.date(2030, 7, 7), dt.date(2030, 7, 10))
    assert len(hsbc.underlyings) == 1 and hsbc.first_callable == 2
    assert reference_row(hsbc)["UL_1_進場價"] == 100.0 and reference_row(hsbc)["UL_2"] == "-"
    with pytest.raises(TypeError):
        ms_synth.Spec(underlyings=())  # 標的由 count 產生，不能直接給
    with pytest.raises((TypeError, ValueError)):
        hsbc_synth.Spec().with_(final_date=dt.date(2030, 7, 8))


def test_hsbc_schedule_follows_the_trade_date_and_tenor():
    s = hsbc_synth.Spec(trade_date=dt.date(2030, 11, 30), tenor=3)

    assert s.ends == [dt.date(2030, 12, 30), dt.date(2031, 1, 30), dt.date(2031, 2, 28)], "同一天，沒有這天取月底"
    assert (s.final_date, s.maturity_date) == (dt.date(2031, 2, 28), dt.date(2031, 3, 3))
    assert hsbc_synth.Spec().ends[0] == dt.date(2030, 2, 7), "預設值同原本的版面：每月 7 日"
    row = reference_row(hsbc_synth.Spec(strike=Decimal("65.00")))
    assert row["K(%)"] == 65.0 and row["UL_1_執行價"] == 65.0, "規格值不再被拒絕"


@pytest.mark.parametrize(
    ("synth_module", "missed", "iis_only"),
    [
        # 段落代號打錯：負責人在第二章第 5 條
        (synth, Edit("林晋輝", "林晉輝", "ts.ch二.6"), Edit("風險程度：RR4", "風險程度：RR5", "iis.p2")),
        (ms_synth, Edit("不存在的原文", "改後", "ts"), Edit("International Plc", "International plc", "iis.cover")),
        (hsbc_synth, Edit("0%~5%", "0%~6%", "iis.p9"), Edit("0%~5%", "0%~6%", "iis.p3")),
    ],
    ids=["barc", "ms", "hsbc"],
)
def test_an_edit_that_replaces_nothing_is_an_error(tmp_path, synth_module, missed, iis_only):
    s = synth_module.Spec()
    pdf = tmp_path / f"{s.product_code}_TS.pdf"
    with pytest.raises(ValueError, match="改字沒有換到任何文字"):
        synth_module.build_pdf(pdf, s, edits=[missed])
    synth_module.build_pdf(pdf, s, edits=[iis_only])  # 只換到旁邊那份投資人須知也算
