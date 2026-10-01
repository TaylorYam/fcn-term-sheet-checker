"""PANEL 工作流程入口：只用合成檔案，不依賴桌面或 parser 內部。"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import fitz
import pytest

from fcn_checker.ingestion import IngestionError
from fcn_checker.panel_workflow import PanelSession
from synth import ORDER_FORMAT, REVIEW_STANDARD, Spec, build_inquiry, build_not_barc_pdf, build_pdf


def test_confirmed_preview_can_be_checked_without_writing_report(tmp_path):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec())
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    session = PanelSession(ORDER_FORMAT, REVIEW_STANDARD)
    session.select(pdf, excel)
    with pytest.raises(IngestionError, match="預覽"):
        session.start_check()
    session.load_preview()
    outcome = session.start_check()
    assert not outcome.stopped
    assert "已核對項目一致" in outcome.headline
    assert "人工" in outcome.headline
    assert {n["rule_id"] for n in outcome.report.not_covered} == {
        "field.monthly_ki",
        "doc.underlying_names",
        "field.isin",
    }
    assert session.outcome is outcome
    assert set(tmp_path.iterdir()) == {pdf, excel}


@pytest.mark.parametrize("kind", ["mismatch", "missing_pdf", "missing_excel", "ambiguous"])
def test_pairing_failure_stops_panel_before_general_checks(tmp_path, kind):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec())
    excel = build_inquiry(
        tmp_path / "inquiry.xlsx", Spec(product_code="029199990002") if kind == "mismatch" else Spec()
    )
    if kind == "missing_excel":
        import openpyxl

        with_excel = openpyxl.load_workbook(excel)
        with_excel["詢價表格"]["B3"] = None
        with_excel.save(excel)
        with_excel.close()
    elif kind in ("missing_pdf", "ambiguous"):
        with fitz.open(pdf) as doc:
            page = doc[0]
            if kind == "missing_pdf":
                page.add_redact_annot(page.search_for("029199990001")[0])
                page.apply_redactions()
            else:
                page.insert_text((41, 820), "商品代號:", fontname="china-t", fontsize=10)
                page.insert_text((301, 820), "029199990003", fontsize=10)
            doc.saveIncr()
    session = PanelSession(ORDER_FORMAT, REVIEW_STANDARD)
    session.select(pdf, excel)
    session.load_preview()
    outcome = session.start_check()
    assert outcome.stopped
    assert "停止" in outcome.headline
    assert not any(r.rule_id == "field.currency" for r in outcome.report.results)
    assert outcome.report.status.value != "PASS"


def test_missing_general_field_continues_and_preserves_other_differences(tmp_path):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec(omit=frozenset({"trade_date"})))
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec(), overrides={"Coupon p.a. (%)": 9})
    session = PanelSession(ORDER_FORMAT, REVIEW_STANDARD)
    session.select(pdf, excel)
    session.load_preview()
    outcome = session.start_check()
    assert not outcome.stopped
    assert outcome.report.status.value == "REVIEW_REQUIRED"
    trade = next(r for r in outcome.report.results if r.rule_id == "field.trade_date")
    assert trade.status.value == "REVIEW_REQUIRED" and not trade.document_evidence
    coupon = next(r for r in outcome.report.results if r.rule_id == "field.coupon_pa_pct")
    assert coupon.status.value == "MISMATCH" and coupon.document_evidence and coupon.order_source
    assert outcome.ordered_results[0].status.value == "MISMATCH"


@pytest.mark.parametrize("changed", ["selection", "pdf", "standard"])
def test_changing_sources_clears_check_result_and_requires_new_preview(tmp_path, changed):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec())
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    standard = tmp_path / "standard.toml"
    standard.write_bytes(REVIEW_STANDARD.read_bytes())
    session = PanelSession(ORDER_FORMAT, standard)
    session.select(pdf, excel)
    session.load_preview()
    session.start_check()
    if changed == "selection":
        session.select(pdf, excel)
    elif changed == "pdf":
        build_pdf(pdf, Spec(tenor=7))
    else:
        standard.write_text(standard.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")
    assert session.outcome is None
    with pytest.raises(IngestionError, match="預覽"):
        session.start_check()


def test_preview_shows_pdf_product_code_and_readonly_conditions(tmp_path):
    spec = Spec(currency_zh="日幣", tenor=7)
    pdf = build_pdf(tmp_path / "ts.pdf", spec)
    excel = build_inquiry(tmp_path / "inquiry.xlsx", spec)
    session = PanelSession(ORDER_FORMAT)
    session.select(pdf, excel)
    preview = session.load_preview()

    assert preview.product_code_evidence[0].page == 1
    assert preview.product_code == "029199990001"
    coupon = next(row for row in preview.conditions if row.label == "Coupon p.a. (%)")
    assert coupon.value == Decimal("12")
    assert coupon.source == "詢價表格!M5"
    assert session.preview == preview
    assert set(tmp_path.iterdir()) == {pdf, excel}
    with pytest.raises(FrozenInstanceError):
        coupon.value = "modified"


def test_changing_selection_clears_previous_preview(tmp_path):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec())
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    session = PanelSession(ORDER_FORMAT)
    session.select(pdf, excel)
    session.load_preview()
    session.select(pdf, None)
    assert session.preview is None
    with pytest.raises(IngestionError, match="請先選取"):
        session.load_preview()


@pytest.mark.parametrize("changed", ["pdf", "excel", "format"])
def test_changed_source_invalidates_preview_and_can_be_reloaded(tmp_path, changed):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec())
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    fmt = tmp_path / "barc.toml"
    fmt.write_bytes(ORDER_FORMAT.read_bytes())
    session = PanelSession(fmt)
    session.select(pdf, excel)
    session.load_preview()
    if changed == "pdf":
        build_pdf(pdf, Spec(tenor=7))
    elif changed == "excel":
        build_inquiry(excel, Spec(annual=Decimal("9")))
    else:
        fmt.write_text(fmt.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    assert session.preview is None
    assert "重新載入" in session.message
    assert session.load_preview() is not None


@pytest.mark.parametrize("kind", ["unknown_pdf", "broken_pdf", "broken_excel", "unsupported", "wrong_format"])
def test_invalid_inputs_never_leave_valid_preview(tmp_path, kind):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec())
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    fmt = tmp_path / "format.toml"
    fmt.write_bytes(ORDER_FORMAT.read_bytes())
    session = PanelSession(fmt)
    session.select(pdf, excel)
    session.load_preview()
    if kind == "unknown_pdf":
        build_not_barc_pdf(pdf)
    elif kind == "broken_pdf":
        pdf.write_bytes(b"not a PDF")
    elif kind == "broken_excel":
        excel.write_bytes(b"not an Excel")
    elif kind == "unsupported":
        session.select(pdf, excel, issuer="OTHER", template="other")
    else:
        fmt.write_text(fmt.read_text(encoding="utf-8").replace('issuer = "BARC"', 'issuer = "OTHER"'), encoding="utf-8")
    with pytest.raises(IngestionError):
        session.load_preview()
    assert session.preview is None
    assert session.message


def test_heading_is_not_used_for_product_code(tmp_path):
    pdf = build_pdf(tmp_path / "misleading-title.pdf", Spec())
    with fitz.open(pdf) as doc:
        page = doc[0]
        rect = page.search_for("英商巴克萊銀行")[0]
        page.add_redact_annot(rect)
        page.apply_redactions()
        doc.saveIncr()
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    session = PanelSession(ORDER_FORMAT)
    session.select(pdf, excel)
    preview = session.load_preview()
    assert preview.product_code == "029199990001"


def test_pdf_code_is_not_replaced_by_excel_code(tmp_path):
    pdf = build_pdf(tmp_path / "ts.pdf", Spec())
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec(product_code="029199990002"))
    session = PanelSession(ORDER_FORMAT)
    session.select(pdf, excel)
    preview = session.load_preview()
    assert preview.product_code == "029199990001"
    assert preview.conditions[0].value == "029199990002"


@pytest.mark.parametrize("kind", ["missing", "ambiguous"])
def test_unreliable_pdf_code_is_not_guessed(tmp_path, kind):
    pdf = build_pdf(tmp_path / "029199990003.pdf", Spec())
    with fitz.open(pdf) as doc:
        page = doc[0]
        if kind == "missing":
            page.add_redact_annot(page.search_for("029199990001")[0])
            page.apply_redactions()
        else:
            page.insert_text((41, 820), "商品代號:", fontname="china-t", fontsize=10)
            page.insert_text((301, 820), "029199990004", fontsize=10)
        doc.saveIncr()
    excel = build_inquiry(tmp_path / "inquiry.xlsx", Spec())
    session = PanelSession(ORDER_FORMAT)
    session.select(pdf, excel)
    preview = session.load_preview()
    assert preview.product_code is None
    assert preview.product_code_note
