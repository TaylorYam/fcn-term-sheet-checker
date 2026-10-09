"""辨識：每份 PDF 從檔名到對到的參考條件表列算出的事實，凍結成辨識結果 `Identification`（見 CONTEXT.md）；
預覽列、核對項目、核對紀錄與錯誤清單都直接讀它，不各自複製欄位（Issue #147）。

辨識流程（每份 PDF）：
0. 檔名（不含副檔名）須為 `<12 位商品代號>_TS`（說明書）或 `<12 位商品代號>_IIS`（投資人須知）；
   其他 → 檔名無法辨識（人工覆核、不能放行）。
1. 檔名前三碼（上手編號）查上手編號對照 → 上手；不在對照表或上手沒有該種文件的範本 → 未支援上手。
2. 內容辨識出的上手必須與檔名一致；說明書封面商品代號也要等於檔名的商品代號，否則轉人工覆核。
3. 以商品代號找參考條件表的列（TDCC Code），該列發行機構必須是此上手的寫法。
4. 同一商品代號（檔名）的說明書與投資人須知配成一組；同種文件有多份時，對到列的全部轉人工覆核，不核對也不回填。
5. 這批只有說明書或只有投資人須知、且已對到列時，那一份轉人工覆核（這批缺另一份），不能人工放行。
   另一份在這批裡但本身配對失敗（例：未支援上手、範本不符）時不算缺，但那份沒通過，說明書就不回填。

辨識遇到問題就停，每份 PDF 最多一個配對問題。`identify` 分兩段：先對每份 PDF 累積事實（種類、上手、商品代號、讀出結果、
對到的列、遇到就停的配對問題），全部 PDF 都辨識並依檔名的商品代號分組（多份對到同一列、缺另一份、同商品另一份的引用）後，
才由事實產生每份的辨識／配對結果（`CheckResult`）並凍結；之後沒有任何結果被改寫。
範本辨識與讀出每份只做一次（在預覽，ADR 0005）；單份非預期錯誤不中斷整批。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .check_config import CheckConfig
from .extraction import extract_lines
from .ingestion import IngestionError, error_result, open_pdf
from .investor_sheet import IisSheet
from .issuers import Issuer, by_code, detect, detect_iis
from .orders.reference import OrderRecord, ReferenceSheet
from .rules.kit import doc_review, read_standard
from .schema import CheckResult, CheckStatus, DocKind, Evidence, Item, ParsedField
from .standard_fields import TermSheet


class PairingProblem(StrEnum):
    """配對問題：辨識結果裡讓這份 PDF 不能正常核對或不能放行的那一個原因（每份最多一個，見 CONTEXT.md）。

    值是對應結果的原因碼；原因碼由例外或範本辨識決定的（PDF 讀不到、範本、封面商品代號）取其中一個代表。
    """

    NAME_UNRECOGNIZED = "file_name_unrecognized"
    PDF_UNREADABLE = "pdf_unreadable"  # 找不到、無法開啟、加密或沒有頁面
    UNSUPPORTED = "issuer_unsupported"
    TEMPLATE_UNKNOWN = "template_unknown"
    TEMPLATE_AMBIGUOUS = "template_ambiguous"
    PREFIX_MISMATCH = "issuer_prefix_mismatch"
    UNEXPECTED = "unexpected_error"  # 上手讀出或辨識時的非預期錯誤
    PRODUCT_CODE_UNREADABLE = "product_code_unreadable"  # 說明書封面商品代號讀不到
    CODE_MISMATCH = "file_code_mismatch"  # 說明書封面商品代號與檔名的商品代號不同
    ROW_MISSING = "reference_row_missing"
    ROW_DUPLICATE = "reference_row_duplicate"
    ISSUER_MISMATCH = "reference_issuer_mismatch"
    SHARED_ROW = "reference_row_shared"
    MISSING_IIS = "counterpart_missing_iis"  # 這批有說明書、沒有同商品的投資人須知
    MISSING_TS = "counterpart_missing_ts"  # 這批有投資人須知、沒有同商品的說明書


_SUFFIXES = {"_TS": DocKind.TERM_SHEET, "_IIS": DocKind.IIS}
_CODE = re.compile(r"[0-9]{12}")


def doc_kind(pdf: Path) -> DocKind | None:
    """檔名（不含副檔名）結尾剛好是 `_TS` 或 `_IIS` 才算；不容忍空白、大小寫或其他寫法。"""
    return next((kind for suffix, kind in _SUFFIXES.items() if pdf.stem.endswith(suffix)), None)


def file_code(pdf: Path) -> str | None:
    """檔名結尾前的部分是 12 位數字時為商品代號（例：029199990001_IIS.pdf）。"""
    kind = doc_kind(pdf)
    head = pdf.stem[: pdf.stem.rfind("_")] if kind else ""
    return head if _CODE.fullmatch(head) else None


def name_code(pdf: Path) -> str | None:
    """檔名前 12 碼（12 位數字才算）：錯誤清單的 TDCC Code 在商品代號取不到時用它，檔名無法辨識的檔案也適用。"""
    head = pdf.name[:12]
    return head if _CODE.fullmatch(head) else None


@dataclass(frozen=True, eq=False)
class Identification:
    """辨識結果：預覽階段對每份 PDF 算出的事實（見 CONTEXT.md），由 `identify` 建立後不再改變；
    類別、狀態標籤、能否回填與放行都只看它與核對報告。

    前六欄是判定要看的事實；其餘只有 `identify` 會填（沒有檔案的手刻辨識結果留預設值）。
    一份 PDF 一筆，比較與雜湊依身分（`eq=False`）：兩份內容相同的 PDF 仍是兩筆，同商品的兩份互相引用也不會比到自己。
    """

    kind: DocKind | None  # None → 檔名無法辨識
    issuer: str | None = None  # 上手代號（檔名上手編號查對照表）
    product_code: str | None = None  # 說明書封面商品代號；投資人須知是檔名的商品代號
    reference_row: int | None = None  # 對到的參考條件表列號
    problem: PairingProblem | None = None  # 唯一的配對問題；None 表示配對乾淨
    checked: bool = False  # 規則有沒有跑：有對到列、且不是多份對到同一列（缺另一份時仍為 True）
    pdf: Path | None = field(default=None, kw_only=True)  # 這份 PDF（說明書或投資人須知）
    product_code_evidence: tuple[Evidence, ...] = field(default=(), kw_only=True)
    results: tuple[CheckResult, ...] = field(
        default=(), kw_only=True, repr=False
    )  # 辨識與配對結果：範本、配對、缺另一份
    partner: Identification | None = field(
        default=None, kw_only=True, repr=False
    )  # 同商品的另一份（說明書 ↔ 投資人須知）
    adapter: Issuer | None = field(default=None, kw_only=True, repr=False)  # 上手 adapter：範本命中且與檔名一致才有
    row: OrderRecord | None = field(default=None, kw_only=True, repr=False)  # 對到的參考條件表列（單份核對用）
    term_sheet: TermSheet | None = field(default=None, kw_only=True, repr=False)  # 說明書讀出結果：同一份只讀一次
    investor_sheet: IisSheet | None = field(default=None, kw_only=True, repr=False)  # 投資人須知讀出結果
    pages: int | None = field(default=None, kw_only=True)

    @property
    def unsupported(self) -> bool:
        return self.problem == PairingProblem.UNSUPPORTED


# 辨識與配對結果的項目：只寫說明，不附雙方值
ISSUER_ITEM, PRODUCT_CODE_ITEM, FILE_NAME_ITEM = Item.note("上手"), Item.note("商品代號"), Item.note("檔名")


def unexpected_result(e: Exception, kind: DocKind | None) -> CheckResult:
    """這份 PDF 的非預期錯誤；項目是文件本身（檔名無法辨識時以說明書稱呼）。"""
    document = kind or DocKind.TERM_SHEET
    return CheckResult(
        rule_id="batch.unexpected",
        field=document.value,
        status=CheckStatus.ERROR,
        reason_code="unexpected_error",
        message=f"{type(e).__name__}: {e}",
        item=Item.note(document.value),
        document=document,
    )


def _review(
    rule_id: str, field_: str, item: Item, reason: PairingProblem, message: str, actual: Any = None
) -> CheckResult:
    return CheckResult(
        rule_id=rule_id,
        field=field_,
        status=CheckStatus.REVIEW_REQUIRED,
        actual=actual,
        reason_code=reason.value,
        message=message,
        item=item,
    )


# ---------------------------------------------------------------- 第一段：逐份累積事實


@dataclass(eq=False)
class _Draft:
    """辨識途中累積的事實（可變，只在本模組內）；分組後由 `_freeze` 變成辨識結果。"""

    pdf: Path
    kind: DocKind | None
    issuer: str | None = None
    adapter: Issuer | None = None
    product_code: ParsedField | None = None
    row: OrderRecord | None = None
    term_sheet: TermSheet | None = None
    investor_sheet: IisSheet | None = None
    pages: int | None = None
    template: CheckResult | None = None  # 範本辨識命中的結果
    problem: PairingProblem | None = None  # 遇到就停的配對問題
    stopped: CheckResult | None = None  # 該問題對應的結果
    shared: tuple[str, str] | None = None  # 多份對到同一列：（哪種文件有多份, 其他文件的檔名）
    alone: bool = False  # 這批缺同商品的另一份
    partner: _Draft | None = None  # 同商品的另一份

    def stop(self, problem: PairingProblem, result: CheckResult) -> _Draft:
        """辨識遇到配對問題：記下問題與對應的結果，辨識到此為止。"""
        self.problem, self.stopped = problem, result
        return self


def _draft(pdf: Path, sheet: ReferenceSheet, config: CheckConfig) -> _Draft:
    registry = config.registry
    out = _Draft(pdf, doc_kind(pdf))
    if out.kind is None or file_code(pdf) is None:
        need = "「<12 位商品代號>_TS」（說明書）或「<12 位商品代號>_IIS」（投資人須知）"
        msg = f"檔名須為{need}，無法辨識；請修正檔名後重新載入"
        problem = PairingProblem.NAME_UNRECOGNIZED
        return out.stop(problem, _review("batch.file_name", "file_name", FILE_NAME_ITEM, problem, msg))
    what = out.kind.value
    try:
        doc = open_pdf(pdf)
        try:
            lines = extract_lines(doc)
            out.pages = doc.page_count
        finally:
            doc.close()
    except IngestionError as e:
        return out.stop(PairingProblem.PDF_UNREADABLE, error_result("input.term_sheet", what, e))

    prefix = pdf.name[:3]
    out.issuer = config.issuer_prefixes.get(prefix)
    if out.issuer is None:
        msg = f"檔名上手編號「{prefix}」不在上手編號對照表，未支援上手"
        problem = PairingProblem.UNSUPPORTED
        return out.stop(problem, _review("batch.issuer_prefix", "issuer", ISSUER_ITEM, problem, msg))
    issuer = by_code(out.issuer, registry)
    if issuer is None or (out.kind == DocKind.IIS and issuer.iis is None):
        code = out.issuer
        msg = f"上手編號 {prefix} 對應 {code}，但 {code} 還沒有{what}範本，未支援上手"
        problem = PairingProblem.UNSUPPORTED
        return out.stop(problem, _review("batch.issuer_prefix", "issuer", ISSUER_ITEM, problem, msg))

    detected, template_result = (detect if out.kind == DocKind.TERM_SHEET else detect_iis)(lines, registry)
    if detected is None:  # 範本不符或多重命中：原因碼就是配對問題的值
        return out.stop(PairingProblem(template_result.reason_code), template_result)
    out.template = template_result
    if detected is not issuer:
        problem = PairingProblem.PREFIX_MISMATCH
        msg = f"檔名上手編號 {prefix} 對應 {issuer.code}，但{what}內容是 {detected.code} 範本，可能檔名取錯或檔案放錯"
        return out.stop(problem, _review("batch.issuer_prefix", "issuer", ISSUER_ITEM, problem, msg, detected.code))
    out.adapter = issuer
    try:
        if issuer.iis is not None and out.kind == DocKind.IIS:
            out.investor_sheet = issuer.iis.read(lines)
        else:
            out.term_sheet = issuer.read(lines)
    except Exception as e:  # 上手讀出失敗：這份轉執行錯誤，不中斷整批（預覽也一樣）
        return out.stop(PairingProblem.UNEXPECTED, unexpected_result(e, out.kind))

    if out.kind == DocKind.IIS:  # 投資人須知以檔名前 12 碼配對（HSBC 範本沒有商品代號；封面有的另由規則核對）
        pc = out.product_code = ParsedField.present("product_code", file_code(pdf), [])
    else:
        pc = out.product_code = read_standard(out.term_sheet, "product_code")
        if not pc.ok:
            problem = PairingProblem.PRODUCT_CODE_UNREADABLE
            return out.stop(problem, doc_review("batch.pairing", "product_code", pc, item=PRODUCT_CODE_ITEM))
        if not str(pc.value).startswith(prefix):
            problem = PairingProblem.PREFIX_MISMATCH
            msg = f"說明書商品代號 {pc.value} 的前三碼與檔名上手編號 {prefix} 不同"
            r = _review("batch.issuer_prefix", "product_code", PRODUCT_CODE_ITEM, problem, msg, actual=pc.value)
            return out.stop(problem, r)
        if pc.value != file_code(pdf):  # 同商品的兩份以檔名的商品代號配成一組，說明書檔名不能和封面不同
            problem = PairingProblem.CODE_MISMATCH
            msg = f"說明書封面商品代號 {pc.value} 與檔名的商品代號 {file_code(pdf)} 不同，可能檔名取錯或檔案放錯"
            r = _review("batch.file_name", "product_code", PRODUCT_CODE_ITEM, problem, msg, actual=pc.value)
            return out.stop(problem, r)

    rows = sheet.find(pc.value)
    if not rows:
        problem = PairingProblem.ROW_MISSING
        msg = f"參考條件表找不到 TDCC Code {pc.value} 的列"
        return out.stop(problem, _review("batch.pairing", "product_code", PRODUCT_CODE_ITEM, problem, msg))
    if len(rows) > 1:
        problem = PairingProblem.ROW_DUPLICATE
        msg = f"參考條件表有 {len(rows)} 列 TDCC Code 為 {pc.value}"
        r = _review("batch.pairing", "product_code", PRODUCT_CODE_ITEM, problem, msg)
        r.order_source = [x.product_code.source for x in rows]
        return out.stop(problem, r)
    row = rows[0]
    expected_issuer = config.reference_format.issuer_values.get(issuer.code)
    if expected_issuer is None or row.issuer.value != expected_issuer:
        problem = PairingProblem.ISSUER_MISMATCH
        r = _review(
            "batch.pairing",
            "issuer",
            ISSUER_ITEM,
            problem,
            f"參考條件表該列發行機構是「{row.issuer.value}」，{issuer.code} 應為「{expected_issuer}」",
            actual=row.issuer.value,
        )
        r.expected, r.order_source = expected_issuer, [row.issuer.source]
        return out.stop(problem, r)
    out.row = row
    return out


# ---------------------------------------------------------------- 第二段：分組，再由事實產生結果


def _other_names(found: _Draft, group: Sequence[_Draft]) -> str:
    """同一列其他說明書的檔名；檔名相同時改列完整路徑，同一個檔案選了兩次時註明。"""
    names = [f.pdf.name for f in group]
    labels = []
    for f in group:
        if f is found:
            continue
        if f.pdf.resolve() == found.pdf.resolve():
            labels.append(f"{f.pdf.name}（同一個檔案重複選取）")
        else:
            labels.append(str(f.pdf) if names.count(f.pdf.name) > 1 else f.pdf.name)
    return "、".join(labels)


def _group(drafts: Sequence[_Draft]) -> None:
    """依檔名的商品代號配成一組（一份說明書＋一份投資人須知）。

    同一商品有多份同種文件 → 對到列的全部多份對到同一列；只有一種文件且已對到列 → 那一份缺另一份。
    """
    by_code: dict[str, list[_Draft]] = {}
    for found in drafts:
        code = file_code(found.pdf)
        if found.kind is not None and code is not None:
            by_code.setdefault(code, []).append(found)
    for group in by_code.values():
        kinds = [f.kind for f in group]
        duplicated = [k for k in DocKind if kinds.count(k) > 1]
        if duplicated:
            what = "、".join(k.value for k in duplicated)
            for found in group:
                if found.row is not None:
                    found.shared = (what, _other_names(found, group))
        elif len(group) == 2:
            first, second = group
            first.partner, second.partner = second, first
        elif group[0].row is not None:
            group[0].alone = True


def _pairing(d: _Draft) -> CheckResult:
    """對到列的配對結果：通過，或多份對到同一列時直接以人工覆核建立（兩者都帶雙方的商品代號與出處）。"""
    row, pc = d.row, d.product_code
    status, reason, message = CheckStatus.PASS, "", f"對應參考條件表第 {row.row} 列"
    if d.shared is not None:
        what, others = d.shared
        status, reason = CheckStatus.REVIEW_REQUIRED, PairingProblem.SHARED_ROW.value
        message = f"同一批有多份{what}對到同一個 TDCC Code {pc.value}（參考條件表第 {row.row} 列），其他文件：{others}"
    return CheckResult(
        rule_id="batch.pairing",
        field="product_code",
        status=status,
        expected=row.product_code.value,
        actual=pc.value,
        reason_code=reason,
        message=message,
        document_evidence=pc.evidence,
        order_source=[row.product_code.source],
        item=PRODUCT_CODE_ITEM,
    )


def _counterpart(d: _Draft) -> tuple[PairingProblem, CheckResult]:
    """這批沒有同商品的另一種文件：人工覆核、不能放行；規則照常執行，方便先看這份的問題。"""
    missing = DocKind.IIS if d.kind == DocKind.TERM_SHEET else DocKind.TERM_SHEET
    suffix = "_IIS" if missing == DocKind.IIS else "_TS"
    problem = PairingProblem.MISSING_IIS if missing == DocKind.IIS else PairingProblem.MISSING_TS
    msg = (
        f"這批沒有同商品的{missing.value}（{d.product_code.value}{suffix}.pdf）；說明書與投資人須知要一起選取、一起核對"
    )
    return problem, _review("batch.counterpart", "counterpart", Item.note(missing.value), problem, msg)


def _freeze(d: _Draft) -> Identification:
    """由累積的事實產生這份的辨識／配對結果並凍結（同商品另一份的引用由 `identify` 補上）。"""
    results = [d.template] if d.template is not None else []
    problem = d.problem
    if d.row is None:
        if d.stopped is not None:
            results.append(d.stopped)
    else:
        results.append(_pairing(d))
        if d.shared is not None:
            problem = PairingProblem.SHARED_ROW
        elif d.alone:
            problem, counterpart = _counterpart(d)
            results.append(counterpart)
    pc = d.product_code
    return Identification(
        d.kind,
        d.issuer,
        pc.value if pc is not None and pc.ok else None,
        d.row.row if d.row is not None else None,
        problem,
        d.row is not None and problem != PairingProblem.SHARED_ROW,
        pdf=d.pdf,
        product_code_evidence=tuple(pc.evidence) if pc is not None else (),
        results=tuple(results),
        adapter=d.adapter,
        row=d.row,
        term_sheet=d.term_sheet,
        investor_sheet=d.investor_sheet,
        pages=d.pages,
    )


def identify(pdfs: Sequence[Path], sheet: ReferenceSheet, config: CheckConfig) -> tuple[Identification, ...]:
    """辨識並讀出每份 PDF（順序同 pdfs），分組後回傳每份凍結的辨識結果；單份非預期錯誤不中斷整批。"""
    drafts = []
    for pdf in map(Path, pdfs):
        try:
            draft = _draft(pdf, sheet, config)
        except Exception as e:
            kind = doc_kind(pdf)
            draft = _Draft(pdf, kind).stop(PairingProblem.UNEXPECTED, unexpected_result(e, kind))
        drafts.append(draft)
    _group(drafts)
    frozen = {draft: _freeze(draft) for draft in drafts}
    for draft, ident in frozen.items():
        if draft.partner is not None:  # 同商品的兩份互相引用：凍結記錄只在建立時補上這一次
            object.__setattr__(ident, "partner", frozen[draft.partner])
    return tuple(frozen.values())
