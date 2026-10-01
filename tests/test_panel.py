"""PANEL 工作流程入口：只用合成檔案，不依賴桌面或 parser 內部。"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import fitz
import pytest

from fcn_checker.ingestion import IngestionError
from fcn_checker.panel_workflow import PanelSession
from synth import ORDER_FORMAT, Spec, build_inquiry, build_not_barc_pdf, build_pdf


def test_preview_shows_cover_title_and_readonly_conditions(tmp_path):
    spec = Spec(currency_zh="日幣", tenor=7)
    pdf = build_pdf(tmp_path / "ts.pdf", spec)
    excel = build_inquiry(tmp_path / "inquiry.xlsx", spec)
    session = PanelSession(ORDER_FORMAT)
    session.select(pdf, excel)
    preview = session.load_preview()

    assert preview.title == (
        "英商巴克萊銀行7個月日幣計價連結股權記憶式自動提前出場結構型商品（不保本）（無擔保及無保證機構）"
    )
    assert preview.title_evidence[0].page == 1
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


def test_missing_title_is_explicit_and_never_replaced_by_filename(tmp_path):
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
    assert preview.title is None
    assert "未找到" in preview.title_note
