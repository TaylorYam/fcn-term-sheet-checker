"""提前出場期始日用一籃子預定交易日：跳過週末與標的交易所休市日（Issue #208）。

測試切點：交易所假日表（`load_exchange_holidays`、`TradingCalendar.next_trading_day`），以及經公開批量入口的
BARC／HSBC 期始日規則。合成說明書的日期在 2030 年；要測假日時另給一個加了休市日的核對設定。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import hsbc_synth
from fcn_checker.check_config import ConfigPaths
from fcn_checker.config import load_exchange_holidays
from fcn_checker.ingestion import IngestionError
from fcn_checker.schema import CheckStatus as S
from harness import (
    CONFIG,
    EXCHANGE_HOLIDAYS,
    ISSUER_PREFIXES,
    REFERENCE_FORMAT,
    REVIEW_STANDARD,
    check_sheet,
    with_markets,
)
from pdf_writer import Edit, zh_date
from reference_synth import UL, build_reference_sheet, reference_row
from synth import Spec, build_pdf, next_weekday

REPO = load_exchange_holidays(EXCHANGE_HOLIDAYS)
PATHS = ConfigPaths(REVIEW_STANDARD, REFERENCE_FORMAT, ISSUER_PREFIXES, EXCHANGE_HOLIDAYS)
REPO_CONFIG = PATHS.load()  # 不延長涵蓋範圍：合成說明書的 2030 年超出假日表
D = dt.date


def with_holidays(*days: dt.date):
    """核對設定：美國市場另加休市日（合成說明書的年份）。"""
    return with_markets(CONFIG, lambda m: replace(m, holidays=m.holidays | set(days)) if m.code == "US" else m)


def report(tmp_path: Path, spec, *, edits=(), config=CONFIG, builder=build_pdf):
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = builder(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    return check_sheet(pdf, sheet, config)


def starts(r):
    return [x for x in r.results if x.rule_id == "schedule.autocall_dates" and x.field in ("start", "daily")]


def problems(r):
    return {(x.rule_id, x.status) for x in r.results if x.status not in (S.PASS, S.NOT_APPLICABLE)}


# ---------------------------------------------------------------- 交易所假日表


def test_us_holidays_are_skipped_and_uk_holidays_are_not():
    us = ["GOOGL UW", "AAPL UQ"]
    assert REPO.next_trading_day(D(2027, 1, 15), us).day == D(2027, 1, 19), "1/18 Martin Luther King Day"
    assert REPO.next_trading_day(D(2026, 11, 25), us).day == D(2026, 11, 27), "11/26 感恩節"
    assert REPO.next_trading_day(D(2027, 3, 25), us).day == D(2027, 3, 29), "3/26 Good Friday；3/29 復活節星期一照常"
    assert REPO.next_trading_day(D(2027, 5, 28), us).day == D(2027, 6, 1), "5/31 Memorial Day"
    assert REPO.next_trading_day(D(2026, 12, 24), us).day == D(2026, 12, 28), "12/25 聖誕節＋週末；12/28 節禮日補假照常"
    assert all(not REPO.next_trading_day(d, us).uncertain for d in (D(2027, 1, 15), D(2027, 5, 28)))


def test_ticker_suffix_decides_the_market():
    assert REPO.market_of("ABC UN Equity").code == "US"
    assert REPO.market_of("abc us").code == "US"
    assert REPO.market_of("700 HK") is None
    assert REPO.market_of("GOOGL") is None


def test_unknown_exchange_or_dates_outside_the_table_are_uncertain():
    hk = REPO.next_trading_day(D(2027, 1, 15), ["GOOGL UW", "700 HK"])
    assert hk.day == D(2027, 1, 19), "已知市場的休市日照樣跳過"
    assert "700 HK" in hk.uncertain and "不在交易所假日表" in hk.uncertain
    late = REPO.next_trading_day(D(2030, 3, 8), ["GOOGL UW"])
    assert late.day == D(2030, 3, 11) and "涵蓋範圍" in late.uncertain and "2028-12-31" in late.uncertain
    early = REPO.next_trading_day(D(2025, 12, 30), ["GOOGL UW"])
    assert "涵蓋範圍" in early.uncertain and "2026-01-01" in early.uncertain
    assert "讀不到標的" in REPO.next_trading_day(D(2027, 1, 15), None).uncertain


def write(tmp_path: Path, market: str) -> Path:
    p = tmp_path / "exchange_holidays.toml"
    p.write_text(f"version = 1\n\n[markets.US]\n{market}\n", encoding="utf-8")
    return p


GOOD = 'name = "美國"\nbloomberg_codes = ["UN"]\ncovered_from = 2026-01-01\ncovered_through = 2026-12-31\n'


@pytest.mark.parametrize(
    ("market", "message"),
    [
        (GOOD, "holidays"),
        (GOOD + "holidays = [2027-01-01]", "不在涵蓋範圍"),
        (GOOD + 'holidays = ["2026-01-01"]', "必須是日期"),
        (GOOD.replace("covered_through = 2026-12-31", "covered_through = 2025-12-31") + "holidays = []", "早於起日"),
        (
            GOOD + 'holidays = []\n\n[markets.NY]\nname = "紐約"\nbloomberg_codes = ["un"]\n'
            "covered_from = 2026-01-01\ncovered_through = 2026-12-31\nholidays = []",
            "交易所碼 UN 同時出現",
        ),
    ],
    ids=["missing-holidays", "holiday-outside", "not-a-date", "range-reversed", "duplicate-code"],
)
def test_invalid_table_stops_loading(tmp_path, market, message):
    with pytest.raises(IngestionError, match=message):
        load_exchange_holidays(write(tmp_path, market))


def test_config_records_the_table_and_panel_falls_back_to_the_builtin_one(tmp_path):
    record = REPO_CONFIG.record()["exchange_holidays"]
    assert record["version"] == REPO.version and record["sha256"]
    assert ("交易所假日表", EXCHANGE_HOLIDAYS) in PATHS.labelled
    (tmp_path / "config").mkdir()  # 舊安裝的根目錄 config 沒有交易所假日表：用程式內建的
    fallback = ConfigPaths.with_fallback(REVIEW_STANDARD, tmp_path / "config", EXCHANGE_HOLIDAYS.parent)
    assert fallback.exchange_holidays == EXCHANGE_HOLIDAYS.resolve()
    with pytest.raises(IngestionError, match="交易所假日表"):
        replace(PATHS, exchange_holidays=tmp_path / "沒有.toml").load()


# ---------------------------------------------------------------- BARC：第 3 期期始日 2030-03-11（週一），前期期末 03-08


START = Edit("2030 年3 月11 日", "2030 年3 月12 日", "ts.art13.ko.3.start")


def test_barc_start_after_an_exchange_holiday_passes(tmp_path):
    r = report(tmp_path, Spec(), edits=[START], config=with_holidays(D(2030, 3, 11)))
    assert problems(r) == set()
    assert [x.status for x in starts(r)] == [S.PASS]


def test_barc_start_that_skips_a_trading_day_or_lands_on_a_holiday_is_mismatch(tmp_path):
    late = report(tmp_path, Spec(), edits=[START])
    (result,) = starts(late)
    assert result.status == S.MISMATCH and result.actual == "第 3 期"
    assert "第 3 期應為 2030-03-11" in result.message
    on_holiday = report(tmp_path / "h", Spec(), config=with_holidays(D(2030, 3, 11)))
    assert [(x.status, x.actual) for x in starts(on_holiday)] == [(S.MISMATCH, "第 3 期")]
    assert "第 3 期應為 2030-03-12" in starts(on_holiday)[0].message


HK = Spec(underlyings=(UL("甲乙丙控股", "香港交易所", "ABC HK", Decimal("50.0000")),))


def test_barc_start_with_an_unknown_exchange_requires_review_only_when_later(tmp_path):
    assert [x.status for x in starts(report(tmp_path, HK))] == [S.PASS], "剛好是下一個平日：照舊通過"
    (result,) = starts(report(tmp_path / "s", HK, edits=[START]))
    assert result.status == S.REVIEW_REQUIRED and result.reason_code == "trading_calendar_uncertain"
    assert "ABC HK" in result.message and "第 3 期應為 2030-03-11" in result.message


@pytest.mark.parametrize(
    "new",
    ["2030 年3 月10 日", "2030 年3 月16 日", "2030 年3 月8 日"],
    ids=["sunday-before", "saturday-after", "same-as-previous-end"],
)
def test_barc_start_that_cannot_be_a_holiday_shift_is_mismatch_even_when_uncertain(tmp_path, new):
    """假日只會讓期始日往後延：比推算結果早、或落在週末，不論假日表確不確定都是不一致。"""
    (result,) = starts(report(tmp_path, HK, edits=[Edit("2030 年3 月11 日", new, "ts.art13.ko.3.start")]))
    assert result.status == S.MISMATCH and "第 3 期應為 2030-03-11" in result.message


def test_barc_mixed_basket_skips_known_holidays_and_reviews_the_rest(tmp_path):
    """美股＋港股：美股休市日照樣跳過（落在美股休市日一定不一致），晚於推算結果才轉人工覆核。"""
    mixed = Spec(underlyings=(*Spec().underlyings[:1], *HK.underlyings))
    config = with_holidays(D(2030, 3, 11))
    shifted = report(tmp_path, mixed, edits=[START], config=config)
    assert [x.status for x in starts(shifted)] == [S.PASS], "跳過美股休市日：期始日 = 推算結果"
    (on_holiday,) = starts(report(tmp_path / "h", mixed, config=config))
    assert on_holiday.status == S.MISMATCH and "第 3 期應為 2030-03-12" in on_holiday.message
    later = Edit("2030 年3 月11 日", "2030 年3 月13 日", "ts.art13.ko.3.start")
    (unsure,) = starts(report(tmp_path / "l", mixed, edits=[later], config=config))
    assert unsure.status == S.REVIEW_REQUIRED and "ABC HK" in unsure.message


def test_barc_start_outside_the_table_requires_review(tmp_path):
    (result,) = starts(report(tmp_path, Spec(), edits=[START], config=REPO_CONFIG))
    assert result.status == S.REVIEW_REQUIRED and "涵蓋範圍" in result.message


# ---------------------------------------------------------------- HSBC：期間每日觀察的期始日


def hsbc_shift():
    s = hsbc_synth.Spec(ko_obs="D")
    i = s.first_callable + 1  # 第一個有期始日的期別
    start = next_weekday(s.ends[i - 2])
    return s, start, Edit(zh_date(start), zh_date(next_weekday(start)), "ts.art11")


def test_hsbc_daily_start_after_an_exchange_holiday_passes(tmp_path):
    s, start, edit = hsbc_shift()
    r = report(tmp_path, s, edits=[edit], config=with_holidays(start), builder=hsbc_synth.build_pdf)
    assert [x.status for x in starts(r)] == [S.PASS]
    assert problems(r) == set()


def test_hsbc_daily_start_without_a_holiday_is_mismatch_or_review_outside_the_table(tmp_path):
    s, _, edit = hsbc_shift()
    (mismatch,) = starts(report(tmp_path, s, edits=[edit], builder=hsbc_synth.build_pdf))
    assert mismatch.status == S.MISMATCH and "期始日應為" in mismatch.message, "錯訊寫出應為哪一天"
    (result,) = starts(report(tmp_path / "r", s, edits=[edit], config=REPO_CONFIG, builder=hsbc_synth.build_pdf))
    assert result.status == S.REVIEW_REQUIRED and "涵蓋範圍" in result.message
