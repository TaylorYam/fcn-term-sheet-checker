"""測試切點：辨識 `identify(PDF 們, 參考條件表, 核對設定)` → 每份 PDF 一筆凍結的辨識結果，不經 check_batch、不跑任何條件規則。

只用合成說明書與投資人須知（tests/synth.py）；看的是辨識結果的事實（配對問題、對到的列、同商品另一份、規則會不會跑）
與辨識／配對結果的訊息。整批流程（預覽、核對、儲存）的同類行為見 test_batch.py。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from fcn_checker.identification import Identification, PairingProblem, identify
from fcn_checker.orders.reference import load_reference_sheet
from fcn_checker.result_file import ErrorRow
from fcn_checker.schema import CheckStatus, DocKind
from harness import CONFIG, iis_path, with_iis
from reference_synth import build_reference_sheet, reference_row
from synth import Spec, build_iis_pdf, build_pdf

PASS, REVIEW = CheckStatus.PASS, CheckStatus.REVIEW_REQUIRED
TS, IIS = DocKind.TERM_SHEET, DocKind.IIS
P = PairingProblem


def identified(tmp_path: Path, pdfs: list[Path], rows: list[dict]) -> tuple[Identification, ...]:
    """辨識 pdfs（說明書旁的同商品投資人須知一起）對合成參考條件表。"""
    sheet = load_reference_sheet(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), CONFIG.reference_format)
    return identify(with_iis(pdfs), sheet, CONFIG)


def only(ident: Identification, rule_id: str):
    out = [r for r in ident.results if r.rule_id == rule_id]
    assert len(out) == 1, f"{rule_id} 應恰好一筆：{out}"
    return out[0]


def test_term_sheet_and_investor_sheet_of_one_product_are_frozen_and_reference_each_other(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)

    ts, iis = identified(tmp_path, [pdf], [reference_row(spec)])

    assert (ts.kind, iis.kind) == (TS, IIS) and (ts.pdf, iis.pdf) == (pdf, iis_path(pdf))
    assert ts.partner is iis and iis.partner is ts
    assert (ts.issuer, ts.product_code, ts.reference_row) == ("BARC", spec.product_code, 4)
    assert (iis.issuer, iis.product_code, iis.reference_row) == ("BARC", spec.product_code, 4)
    assert ts.problem is None and iis.problem is None and ts.checked and iis.checked
    assert ts.product_code_evidence[0].page == 1, "封面商品代號的證據跟著辨識結果走"
    for ident in (ts, iis):
        r = only(ident, "batch.pairing")
        assert (r.status, r.expected, r.actual, r.message) == (
            PASS,
            spec.product_code,
            spec.product_code,
            "對應參考條件表第 4 列",
        )
        assert isinstance(ident.results, tuple)
    with pytest.raises(FrozenInstanceError):
        ts.problem = P.ROW_MISSING


def test_reference_row_missing_stops_at_pairing_and_still_links_the_pair(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)

    ts, iis = identified(tmp_path, [pdf], [reference_row(Spec(product_code="029199990002"))])

    for ident in (ts, iis):
        assert ident.problem == P.ROW_MISSING and ident.reference_row is None and not ident.checked
        r = only(ident, "batch.pairing")
        assert (r.status, r.reason_code) == (REVIEW, "reference_row_missing")
        assert r.message == f"參考條件表找不到 TDCC Code {spec.product_code} 的列"
    assert ts.partner is iis and iis.partner is ts, "同商品的兩份仍互相引用，只是都沒對到列"
    assert ts.issuer == "BARC" and ts.product_code == spec.product_code, "停在配對之前的事實都保留"


def test_duplicate_reference_rows_name_both_sources(tmp_path):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, iis=False)

    [ts] = identified(tmp_path, [pdf], [reference_row(spec), reference_row(spec)])

    assert ts.problem == P.ROW_DUPLICATE and ts.reference_row is None and not ts.checked
    r = only(ts, "batch.pairing")
    assert (r.status, r.reason_code) == (REVIEW, "reference_row_duplicate")
    assert r.message == f"參考條件表有 2 列 TDCC Code 為 {spec.product_code}"
    assert len(r.order_source) == 2
    assert not any(x.rule_id == "batch.counterpart" for x in ts.results), "沒對到列就不算缺另一份"


def test_several_documents_of_one_kind_on_one_row_all_share_the_row_and_pair_with_nobody(tmp_path):
    spec = Spec()
    (tmp_path / "old").mkdir()
    old = build_pdf(tmp_path / "old" / f"{spec.product_code}_TS.pdf", spec, iis=False)
    new = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)

    first, second, iis = identified(tmp_path, [old, new], [reference_row(spec)])

    for ident, other in ((first, new), (second, old)):
        assert ident.problem == P.SHARED_ROW and ident.reference_row == 4 and not ident.checked
        r = only(ident, "batch.pairing")
        assert (r.status, r.reason_code, r.expected, r.actual) == (
            REVIEW,
            "reference_row_shared",
            spec.product_code,
            spec.product_code,
        )
        others = f"{other}、{iis.pdf.name}"  # 同名的說明書列完整路徑；同一組的其他文件（投資人須知）也列出
        assert r.message == (
            f"同一批有多份說明書對到同一個 TDCC Code {spec.product_code}（參考條件表第 4 列），其他文件：{others}"
        )
    assert iis.problem == P.SHARED_ROW and not iis.checked, "同一列的投資人須知也無法確定配哪一份說明書"
    assert first.partner is None and second.partner is None and iis.partner is None


@pytest.mark.parametrize(
    ("kind", "problem", "missing", "suffix"),
    [(TS, P.MISSING_IIS, "投資人須知", "_IIS"), (IIS, P.MISSING_TS, "說明書", "_TS")],
)
def test_only_one_kind_in_the_batch_is_a_missing_counterpart_but_rules_still_run(
    tmp_path, kind, problem, missing, suffix
):
    spec = Spec()
    ts = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    pdf = ts if kind == TS else iis_path(ts)

    sheet = load_reference_sheet(
        build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec)]), CONFIG.reference_format
    )
    [alone] = identify([pdf], sheet, CONFIG)

    assert alone.kind == kind and alone.problem == problem and alone.partner is None
    assert alone.reference_row == 4 and alone.checked, "缺另一份時規則照常執行，方便先看這份的問題"
    assert only(alone, "batch.pairing").status == PASS
    r = only(alone, "batch.counterpart")
    assert (r.status, r.reason_code) == (REVIEW, problem.value)
    assert (
        r.message
        == f"這批沒有同商品的{missing}（{spec.product_code}{suffix}.pdf）；說明書與投資人須知要一起選取、一起核對"
    )


def test_unrecognized_file_name_has_no_kind_and_is_nobody_s_counterpart(tmp_path):
    spec = Spec()
    name = f"{spec.product_code}_ts.pdf"
    pdf = build_pdf(tmp_path / name, spec, iis=False)
    iis = build_iis_pdf(tmp_path / f"{spec.product_code}_IIS.pdf", spec)

    bad, sheet = identified(tmp_path, [pdf, iis], [reference_row(spec)])

    assert bad.kind is None and bad.problem == P.NAME_UNRECOGNIZED and bad.partner is None
    assert (bad.issuer, bad.product_code, bad.reference_row, bad.checked) == (None, None, None, False)
    [r] = bad.results
    assert (r.rule_id, r.status, r.reason_code) == ("batch.file_name", REVIEW, "file_name_unrecognized")
    assert sheet.problem == P.MISSING_TS and sheet.partner is None, "辨識不了的檔案不算同商品的說明書"
    assert ErrorRow.of(bad, ("錯訊",)) == ErrorRow(spec.product_code, name, "錯訊"), (
        "錯誤清單的 TDCC Code 用檔名前 12 碼"
    )
    assert ErrorRow.of(sheet, ("甲", "乙")) == ErrorRow(spec.product_code, iis.name, "甲\n乙")


def test_product_code_prefix_differing_from_the_file_name_prefix_is_an_issuer_prefix_mismatch(tmp_path):
    spec = Spec(product_code="028199990001")  # 檔名 029、說明書封面 028
    pdf = build_pdf(tmp_path / "029199990001_TS.pdf", spec, iis=False)

    [ts] = identified(tmp_path, [pdf], [reference_row(spec)])

    assert ts.problem == P.PREFIX_MISMATCH and ts.reference_row is None and not ts.checked
    assert ts.issuer == "BARC" and ts.product_code == "028199990001"
    r = only(ts, "batch.issuer_prefix")
    assert (r.status, r.reason_code, r.actual) == (REVIEW, "issuer_prefix_mismatch", "028199990001")
    assert r.message == "說明書商品代號 028199990001 的前三碼與檔名上手編號 029 不同"
    assert [x.status for x in ts.results] == [PASS, REVIEW], "範本辨識命中的結果在前、停下的配對問題在後"
