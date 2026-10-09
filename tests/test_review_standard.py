"""審查標準依上手解析：固定警語、商品名稱樣板、發行機構名稱、受託機構電話等價寫法。

測試切點是批量核對入口（預覽＋核對）；設定檔問題在載入核對設定時回報；BARC 與 HSBC 都用合成說明書。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hsbc_synth
import ms_synth
from fcn_checker.ingestion import IngestionError
from harness import MISMATCH, PASS, REVIEW, REVIEW_STANDARD, STANDARD, check_sheet, load_config, results
from reference_synth import build_reference_sheet, reference_row
from synth import Spec, build_pdf, check

STD = STANDARD
BARC_WARNING = STD["risk"]["fixed_warning"]
HSBC_WARNING = STD["risk"]["fixed_warning_by_issuer"]["hsbc"]
NAME, PHONE, ADDRESS = (STD["distributor"][k] for k in ("name", "phone", "address"))


def sub(tmp_path: Path, name: str) -> Path:
    (tmp_path / name).mkdir()
    return tmp_path / name


def standard_with(tmp_path: Path, old: str, new: str) -> Path:
    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "review_standard.toml"
    path.write_text(text.replace(old, new), encoding="utf-8")
    return path


def check_hsbc(tmp_path: Path, spec: hsbc_synth.Spec | None = None, standard: Path = REVIEW_STANDARD):
    s = spec or hsbc_synth.Spec()
    pdf = hsbc_synth.build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s)
    sheet = build_reference_sheet(tmp_path / "order.xlsx", [reference_row(s)])
    return check_sheet(pdf, sheet, load_config(review_standard=standard))


def check_barc(tmp_path: Path, spec: Spec, standard: Path):
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)])
    return check_sheet(pdf, sheet, load_config(review_standard=standard))


# ---------------------------------------------------------------- 固定警語


def test_each_issuer_uses_its_own_fixed_warning(tmp_path):
    assert results(check(sub(tmp_path, "barc")), "standard.fixed_warning")[0].status == PASS
    assert results(check_hsbc(sub(tmp_path, "hsbc")), "standard.fixed_warning")[0].status == PASS


def test_hsbc_document_with_barc_warning_is_mismatch(tmp_path):
    report = check_hsbc(tmp_path, hsbc_synth.Spec(replacements={HSBC_WARNING: BARC_WARNING}))
    r = results(report, "standard.fixed_warning")[0]
    assert (r.status, r.actual) == (MISMATCH, 0)


def test_barc_document_with_hsbc_warning_is_mismatch(tmp_path):
    r = results(check(tmp_path, Spec(warnings=(HSBC_WARNING,) * 3)), "standard.fixed_warning")[0]
    assert (r.status, r.actual) == (MISMATCH, 0)


# ---------------------------------------------------------------- 商品名稱樣板


def test_unknown_product_name_placeholder_is_a_batch_config_error(tmp_path):
    standard = standard_with(
        tmp_path,
        'en = "{maxi_en}{daily_en}{memory_en}Autocallable',
        'en = "{maxi_en}{daily_en}{memory_en}{quanto_en}Autocallable',
    )
    with pytest.raises(IngestionError) as raised:
        load_config(review_standard=standard)
    err = raised.value
    assert err.reason_code == "config_invalid"
    assert "quanto_en" in str(err) and "product_name.hsbc" in str(err)


def test_product_name_placeholders_are_filled_from_the_document_for_any_issuer(tmp_path):
    """名稱樣板的 maxi／daily 佔位符依說明書欄位填入，不依上手：BARC 樣板加上 {daily_en} 也照填。"""
    text = standard_with(
        tmp_path,
        'en = "{tenor} Months {ccy} {memory_en}Autocallable',
        'en = "{tenor} Months {ccy} {daily_en}{memory_en}Autocallable',
    ).read_text(encoding="utf-8")
    path = tmp_path / "with_daily.toml"
    text = text.replace('memory_en = "Memory "\n', 'memory_en = "Memory "\ndaily_en = "Daily "\n', 1)
    path.write_text(text, encoding="utf-8")
    spec = Spec()  # D 型、記憶式
    report = check_barc(tmp_path, spec, path)
    r = results(report, "standard.product_name", "name_en")[0]
    assert r.status == MISMATCH
    assert r.expected.startswith("6 Months USD Daily Memory Autocallable")


def test_missing_issuer_name_requires_review(tmp_path):
    standard = standard_with(tmp_path, '\nhsbc = "香港商', '\nfake = "香港商')
    report = check_hsbc(tmp_path, standard=standard)
    for r in results(report, "standard.issuer_name"):
        assert (r.status, r.reason_code) == (REVIEW, "standard_missing")


# ---------------------------------------------------------------- 受託機構電話


def test_international_phone_listed_as_equivalent_passes_for_every_issuer(tmp_path):
    assert (
        results(check_hsbc(sub(tmp_path, "hsbc")), "standard.distributor", "distributor_phone_cover")[0].status == PASS
    )
    spec = Spec(distributor_cover=(NAME, "+886-2-5556-1313", ADDRESS))
    r = results(check(sub(tmp_path, "barc"), spec), "standard.distributor", "distributor_phone_cover")[0]
    assert (r.status, r.actual) == (PASS, "+886-2-5556-1313")


def test_phone_form_not_listed_in_standard_is_mismatch(tmp_path):
    spec = Spec(distributor_cover=(NAME, "(02)5556-1313", ADDRESS))
    r = results(check(sub(tmp_path, "barc"), spec), "standard.distributor", "distributor_phone_cover")[0]
    assert r.status == MISMATCH

    standard = standard_with(
        tmp_path, 'phone_equivalents = ["+886-2-5556-1313", "+886 2 5556 1313"]', "phone_equivalents = []"
    )
    r = results(
        check_hsbc(sub(tmp_path, "hsbc"), standard=standard), "standard.distributor", "distributor_phone_cover"
    )[0]
    assert r.status == MISMATCH


# ---------------------------------------------------------------- MS 分節的設定檢查（Issue #135）


@pytest.mark.parametrize(
    "old,new,where",
    [
        ('basket_en = "Worst of Shares and/or ETFs"\n', "", "basket_en"),
        ('ms = ["風險程度等級為{level}"', 'ms = ["風險程度等級為RRn"', "risk.level_formats.ms"),
        ('ms = ["brackets", "trailing_period"]', 'ms = ["brackets", "commas"]', "issuer_name_ignore.ms"),
        ('ms = ["本商品風險程度為RR4。"]', 'ms = ["本商品風險程度為RR4"]', "risk.fixed_warning_openings.ms"),
        ('ms = ["本商品風險程度為RR4。"]', 'ms = ["本商品風險程度等級為RR4。"]', "risk.fixed_warning_openings.ms"),
    ],
)
def test_invalid_ms_review_standard_is_a_batch_config_error(tmp_path, old, new, where):
    with pytest.raises(IngestionError) as raised:
        load_config(review_standard=standard_with(tmp_path, old, new))
    assert raised.value.reason_code == "config_invalid" and where in str(raised.value)


def test_issuer_name_ignores_apply_only_to_the_configured_issuer(tmp_path):
    """BARC 沒有設定 issuer_name_ignore：第二章少了括號仍是不一致。"""
    plain = STD["issuer_name"]["barc"].replace("（", "").replace("）", "")
    r = results(check(tmp_path, Spec(issuer_ch2=plain)), "standard.issuer_name", "issuer_name_ch2")[0]
    assert r.status == MISMATCH


def test_address_form_not_listed_in_standard_is_mismatch(tmp_path):
    """MS 第二章沒有「松山區」的地址靠審查標準 address_equivalents 才算相符；清單拿掉就判不一致。"""
    variant = ms_synth.Spec(
        replace=[("ch二", "◎ 營業所在地：台北市松山區民生東路三段158號6樓", "◎ 營業所在地：台北市民生東路三段158號6樓")]
    )
    r = results(
        ms_synth.check(sub(tmp_path, "listed"), pdf_spec=variant), "standard.distributor", "distributor_address_ch2"
    )[0]
    assert r.status == PASS and "address_equivalents" in r.tolerance
    standard = standard_with(
        tmp_path, 'address_equivalents = ["台北市民生東路三段158號6樓"]', "address_equivalents = []"
    )
    r = results(
        ms_synth.check(sub(tmp_path, "unlisted"), pdf_spec=variant, config=load_config(review_standard=standard)),
        "standard.distributor",
        "distributor_address_ch2",
    )[0]
    assert r.status == MISMATCH
