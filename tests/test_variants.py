"""不支援樣態（Issue #207）：上手支援樣態表、樣態判斷，以及批量核對攔下不支援樣態（不跑規則、不能人工放行）。"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from fcn_checker.ingestion import IngestionError
from fcn_checker.saving import run_batch
from fcn_checker.schema import CheckStatus
from fcn_checker.variants import Terms, Variant, load_unsupported_variants, unsupported
from harness import CONFIG, ISSUER_PREFIXES, load_config, with_iis
from reference_synth import build_reference_sheet, reference_row
from synth import Spec, build_pdf

REVIEW = CheckStatus.REVIEW_REQUIRED
V = Variant


# ---------------------------------------------------------------- 樣態判斷


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (Terms(vwap=True), (V.VWAP,)),
        (Terms(vwap=False), ()),
        (Terms(ko_observation="D", ko_memory=False), (V.KO_DAILY,)),
        (Terms(ko_observation="D", ko_memory=True), ()),  # 表上沒有的組合：一律支援
        (Terms(ko_observation="P", ko_memory=True), (V.KO_PERIODIC_MEMORY,)),
        (Terms(ko_observation="P", ko_memory=False), (V.KO_PERIODIC,)),
        (Terms(ko_observation="P"), ()),  # 記憶式讀不出：不判斷 KO 樣態
        (Terms(ki_type="D"), (V.KI_DAILY,)),
        (Terms(ki_type="P"), (V.KI_PERIODIC,)),
        (Terms(ki_type="AM"), ()),
        (Terms(ki_type="none"), ()),
        (Terms(first_callable=6, tenor=6), (V.NON_CALL_EQUALS_TENOR,)),
        (Terms(first_callable=1, tenor=6), ()),
        (Terms(first_callable=6), ()),  # 天期讀不出：不判斷
    ],
)
def test_terms_map_to_variants(terms, expected):
    assert terms.variants() == expected


def test_unsupported_lists_each_banned_variant_with_every_source_in_variant_order():
    sources = [
        ("參考條件表", Terms(ki_type="P", first_callable=6, tenor=6)),
        ("說明書", Terms(ki_type="P", ko_observation="P", ko_memory=False)),
    ]
    banned = frozenset({V.KI_PERIODIC, V.NON_CALL_EQUALS_TENOR, V.KO_PERIODIC, V.VWAP})
    assert unsupported(sources, banned) == {
        V.KO_PERIODIC: ("說明書",),
        V.KI_PERIODIC: ("參考條件表", "說明書"),
        V.NON_CALL_EQUALS_TENOR: ("參考條件表",),
    }
    assert unsupported(sources, frozenset({V.KI_DAILY})) == {}


# ---------------------------------------------------------------- 上手支援樣態表


def test_repo_table_registers_every_issuer_with_the_operator_list():
    table = CONFIG.unsupported_variants
    assert set(table) == set(CONFIG.issuer_prefixes.values())
    for code in ("BARC", "HSBC", "MS", "CA", "JPM"):
        assert table[code] == {V.KI_PERIODIC}, code
    for code in ("BNP", "SG", "CITI", "NATIXIS", "NOMURA"):
        assert table[code] == frozenset(), code
    assert table["UBS"] == {V.VWAP, V.NON_CALL_EQUALS_TENOR}
    assert table["DBS"] == set(Variant)


def prefixes_with(tmp_path: Path, old: str, new: str) -> Path:
    text = ISSUER_PREFIXES.read_text(encoding="utf-8")
    assert old in text
    p = tmp_path / "prefixes.toml"
    p.write_text(text.replace(old, new), encoding="utf-8")
    return p


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("\nCITI = []\n", "\n", "沒有登記這些上手：CITI"),
        ("\nCITI = []\n", "\nCITI = []\nFAKE = []\n", "上手編號對照沒有的上手：FAKE"),
        ('\nBARC = ["ki_periodic"]\n', '\nBARC = ["ki_monthly"]\n', "BARC 有未知的樣態：ki_monthly"),
        ('\nBARC = ["ki_periodic"]\n', '\nBARC = "ki_periodic"\n', "BARC 須是樣態名稱的清單"),
        ("[unsupported_variants]", "[variants]", "[unsupported_variants]不存在"),
    ],
)
def test_table_problems_fail_config_load_naming_the_issuer(tmp_path, old, new, message):
    path = prefixes_with(tmp_path, old, new)
    with pytest.raises(IngestionError) as e:
        load_unsupported_variants(path, CONFIG.issuer_prefixes.values())
    assert message in str(e.value)
    with pytest.raises(IngestionError):
        load_config(issuer_prefixes=path)


# ---------------------------------------------------------------- 批量核對


def run(tmp_path: Path, spec: Spec, row: dict, config=CONFIG):
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row])
    return run_batch(config, sheet, with_iis([pdf]), tmp_path / "reports", root=tmp_path, now=None)


def variant_result(item):
    [r] = [r for r in item.report.results if r.rule_id == "batch.variant"]
    return r


def test_reference_sheet_variant_stops_both_documents_without_rules_or_release(tmp_path):
    spec = Spec(ki="D")
    outcome, receipt = run(tmp_path, spec, reference_row(spec, **{"KI(Freq)": "P"}))

    assert [i.kind.value for i in outcome.items] == ["說明書", "投資人須知"]
    for item in outcome.items:
        r = variant_result(item)
        assert (r.status, r.reason_code) == (REVIEW, "variant_unsupported")
        assert item.problem_messages == (
            "樣態：BARC 不支援「KI 每期觀察」（參考條件表）；這個樣態沒有範本可以核對，不能人工放行",
        )
        assert not item.identification.checked
        assert not any(r.rule_id.startswith(("field.", "doc.", "iis.")) for r in item.report.results)
        assert item.status_label == "不支援樣態"
        assert "不能人工放行" in item.release_problem
        with pytest.raises(IngestionError):
            outcome.release(item)
        assert not receipt.filled(item)
    ws = openpyxl.load_workbook(receipt.output)["錯誤清單"]
    assert [r[2] for r in ws.iter_rows(min_row=2, values_only=True)] == [
        "樣態：BARC 不支援「KI 每期觀察」（參考條件表）；這個樣態沒有範本可以核對，不能人工放行"
    ] * 2


def test_term_sheet_variant_stops_the_product_even_when_the_sheet_is_supported(tmp_path):
    spec = Spec(ki="D")
    config = load_config(
        issuer_prefixes=prefixes_with(tmp_path, '\nBARC = ["ki_periodic"]\n', '\nBARC = ["ki_daily"]\n')
    )
    outcome, _ = run(tmp_path, spec, reference_row(spec, **{"KI(Freq)": "AM"}), config)

    ts, iis = outcome.items
    assert variant_result(ts).message.startswith("BARC 不支援「KI 每日觀察」（說明書")
    assert variant_result(iis).message == variant_result(ts).message  # 同商品另一份也攔下
    assert all(i.status_label == "不支援樣態" for i in outcome.items)


def test_several_unsupported_variants_are_listed_in_one_message(tmp_path):
    spec = Spec(ki="D", ko_obs="P", memory=False)
    config = load_config(
        issuer_prefixes=prefixes_with(tmp_path, '\nBARC = ["ki_periodic"]\n', '\nBARC = ["ko_periodic", "ki_daily"]\n')
    )
    outcome, _ = run(tmp_path, spec, reference_row(spec), config)

    message = variant_result(outcome.items[0]).message
    assert message.startswith("BARC 不支援「KO 每期觀察（非記憶式）」（參考條件表、說明書")
    assert "、「KI 每日觀察」（參考條件表、說明書" in message


def test_supported_variants_are_checked_as_usual(tmp_path):
    spec = Spec(ki="D")
    outcome, receipt = run(tmp_path, spec, reference_row(spec))

    assert all(i.status == CheckStatus.PASS for i in outcome.items), [i.problem_messages for i in outcome.items]
    assert not any(r.rule_id == "batch.variant" for i in outcome.items for r in i.report.results)
    assert receipt.filled(outcome.items[0])
