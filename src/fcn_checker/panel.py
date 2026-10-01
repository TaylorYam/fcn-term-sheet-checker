"""Tkinter 本機桌面 PANEL：來源預覽、核對及人工處理清單。"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, ttk

from .ingestion import IngestionError
from .panel_workflow import SUPPORTED_TEMPLATES, PanelOutcome, PanelSession, Preview
from .reporting import FIELD_ZH, STATUS_ZH
from .schema import CheckResult, CheckStatus


def display_value(value: object) -> str:
    return "未提供" if value is None else str(value)


def enable_windows_dpi_awareness() -> None:
    """在建立 Tk 視窗前啟用 system DPI awareness，避免 Windows 點陣放大。

    Tk 8.6 不在此處承諾跨螢幕動態 DPI 排版；已由宿主設定 awareness 時維持其設定。
    """
    if sys.platform != "win32":
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        set_awareness = user32.SetProcessDpiAwarenessContext
    except AttributeError:
        user32.SetProcessDPIAware()
        return
    set_awareness.argtypes = [ctypes.c_void_p]
    set_awareness.restype = ctypes.c_bool
    set_awareness(ctypes.c_void_p(-2))  # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE


class ResultPane(ttk.Frame):
    """問題優先的結果表；選取列後可查看完整值與原文證據。"""

    def __init__(self, parent, pixels):
        super().__init__(parent, padding=8)
        self.rows: dict[str, CheckResult] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.summary = tk.StringVar(value="尚未執行核對。")
        ttk.Label(self, textvariable=self.summary, wraplength=pixels(900)).grid(
            row=0, column=0, sticky="ew", pady=(0, 8)
        )
        columns = ("status", "field", "expected", "actual", "pages")
        self.table = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse", height=4)
        for column, label, width in zip(
            columns, ("狀態", "欄位", "Excel／標準值", "PDF 值", "PDF 頁碼"), (130, 210, 220, 220, 160), strict=True
        ):
            self.table.heading(column, text=label)
            self.table.column(column, width=pixels(width), minwidth=pixels(100), anchor="w")
        self.table.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, command=self.table.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        ttk.Label(self, text="選取項目查看原因、來源儲存格與 PDF 原文：").grid(row=3, column=0, sticky="w", pady=(8, 4))
        self.detail = tk.Text(
            self,
            height=3,
            wrap="word",
            font=("Microsoft JhengHei UI", 11),
            relief="flat",
            padx=8,
            pady=6,
            state="disabled",
        )
        self.detail.grid(row=4, column=0, sticky="ew")
        detail_scroll = ttk.Scrollbar(self, command=self.detail.yview)
        detail_scroll.grid(row=4, column=1, sticky="ns")
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.table.bind("<<TreeviewSelect>>", self._selected)

    def _write_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def clear(self):
        self.rows.clear()
        self.table.delete(*self.table.get_children())
        self.summary.set("尚未執行核對。")
        self._write_detail("")

    def show(self, outcome: PanelOutcome):
        self.clear()
        counts = {status: sum(r.status == status for r in outcome.report.results) for status in STATUS_ZH}
        self.summary.set(
            outcome.headline + "\n" + "、".join(f"{label} {counts[status]}" for status, label in STATUS_ZH.items())
        )
        for row in outcome.ordered_results:
            pages = (
                "第 " + "、".join(str(p) for p in sorted({e.page for e in row.document_evidence})) + " 頁"
                if row.document_evidence
                else "無法定位"
            )
            item = self.table.insert(
                "",
                "end",
                values=(
                    STATUS_ZH[row.status],
                    FIELD_ZH.get(row.field, row.field),
                    display_value(row.expected),
                    display_value(row.actual),
                    pages,
                ),
            )
            self.rows[item] = row
        if self.rows:
            first = next(iter(self.rows))
            self.table.selection_set(first)
            self._selected(None)

    def _selected(self, _):
        selection = self.table.selection()
        if not selection or selection[0] not in self.rows:
            return
        row = self.rows[selection[0]]
        defaults = {
            CheckStatus.PASS: "此已核對項目一致。",
            CheckStatus.MISMATCH: "兩份來源值不一致，依明定容差比對。",
            CheckStatus.REVIEW_REQUIRED: "無法可靠判定，請人工覆核。",
            CheckStatus.NOT_APPLICABLE: "依明確規則，此項目不適用。",
            CheckStatus.ERROR: "執行失敗，請確認來源與設定。",
        }
        evidence = (
            "\n".join(f"第 {e.page} 頁：{e.text}" for e in row.document_evidence)
            or "無法定位：沒有可用的 PDF 原文證據。"
        )
        self._write_detail(
            f"{FIELD_ZH.get(row.field, row.field)}｜{STATUS_ZH[row.status]}\n"
            f"原因：{row.message or defaults[row.status]}\n"
            f"Excel／標準值：{display_value(row.expected)}\nPDF 值：{display_value(row.actual)}\n"
            f"Excel 來源：{'、'.join(row.order_source) or '非 Excel 欄位，依審查標準或文件內部規則核對。'}\n"
            f"規則：{row.rule_id}；容差：{row.tolerance or '未設定'}\nPDF 原文：\n{evidence}"
        )


class PanelWindow:
    def __init__(self, root: tk.Tk, session: PanelSession):
        self.root, self.session = root, session
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending: Future[Preview] | None = None
        self.check_pending: Future[PanelOutcome] | None = None
        self.save_pending = None
        self.has_result = False
        self.validation: Future[Preview | None] | None = None
        self.checking_for: Preview | None = None
        self.closed = False
        self.shown: Preview | None = None
        root.title("FCN Term Sheet 核對")
        scale = root.winfo_fpixels("1i") / 96

        def pixels(value):
            return round(value * scale)

        width = min(pixels(1080), root.winfo_screenwidth() - pixels(40))
        height = min(pixels(780), root.winfo_screenheight() - pixels(80))
        root.geometry(f"{width}x{height}")
        root.minsize(min(pixels(780), width), min(pixels(720), height))
        if sys.platform == "win32":
            root.state("zoomed")
        root.configure(background="#f4f6f8")
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6f8")
        style.configure("TLabel", background="#f4f6f8", foreground="#172b3a", font=("Microsoft JhengHei UI", 11))
        style.configure("Heading.TLabel", font=("Microsoft JhengHei UI", 20, "bold"))
        style.configure("Section.TLabel", font=("Microsoft JhengHei UI", 12, "bold"))
        style.configure("TButton", font=("Microsoft JhengHei UI", 11), padding=(12, 7))
        style.configure("Treeview", font=("Microsoft JhengHei UI", 11), rowheight=pixels(30))
        style.configure("Treeview.Heading", font=("Microsoft JhengHei UI", 11, "bold"))
        style.map("Treeview", background=[("selected", "#d9e9f2")], foreground=[("selected", "#172b3a")])
        frame = ttk.Frame(root, padding=pixels(20))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(9, weight=1)
        ttk.Label(frame, text="Term Sheet 核對", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="先載入預覽，確認商品代號與 Excel 條件，再按「開始核對」。").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 10)
        )
        selectors = ttk.Frame(frame)
        selectors.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(selectors, text="Issuer").pack(side="left", padx=(0, 10))
        self.issuer = ttk.Combobox(
            selectors, values=sorted({c.issuer for c in SUPPORTED_TEMPLATES}), state="readonly", width=12
        )
        self.issuer.set(session.issuer)
        self.issuer.pack(side="left", padx=(0, 24))
        ttk.Label(selectors, text="TS 模板").pack(side="left", padx=(0, 10))
        self.template = ttk.Combobox(selectors, state="readonly", width=30)
        self.template.pack(side="left")
        self._set_templates()
        self.issuer.bind("<<ComboboxSelected>>", self._issuer_changed)
        self.template.bind("<<ComboboxSelected>>", lambda _: self._selection_changed())
        self.pdf_path = tk.StringVar()
        self.excel_path = tk.StringVar()
        self.controls: list[ttk.Widget] = [self.issuer, self.template]
        for row, label, variable, kind in (
            (3, "TS PDF", self.pdf_path, "pdf"),
            (4, "Excel 詢價表", self.excel_path, "excel"),
        ):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=5)
            entry = ttk.Entry(frame, textvariable=variable, state="readonly")
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=5)
            button = ttk.Button(frame, text="選取檔案…", command=lambda k=kind: self.choose_file(k))
            button.grid(row=row, column=2, pady=5)
            self.controls.append(button)
        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 10))
        self.reload = ttk.Button(actions, text="載入／重新載入預覽", command=self.load)
        self.reload.pack(side="left", padx=(0, 12))
        self.check_button = ttk.Button(actions, text="開始核對", command=self.start_check, state="disabled")
        self.check_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="儲存報告…", command=self.save_report, state="disabled")
        self.save_button.pack(side="left", padx=(12, 0))
        self.controls.append(self.reload)
        ttk.Label(frame, text="PDF 商品代號", style="Section.TLabel").grid(row=6, column=0, columnspan=3, sticky="w")
        self.product_code_text = tk.Text(
            frame,
            height=1,
            wrap="word",
            font=("Microsoft JhengHei UI", 12),
            background="white",
            foreground="#172b3a",
            relief="flat",
            padx=12,
            pady=8,
        )
        self.product_code_text.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 10))
        self._show_product_code("選取 PDF 與 Excel 後，載入預覽以查看完整商品代號。")
        ttk.Label(frame, text="條件與核對結果", style="Section.TLabel").grid(row=8, column=0, columnspan=3, sticky="w")
        self.tabs = ttk.Notebook(frame)
        self.tabs.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(6, 10))
        table_frame = ttk.Frame(self.tabs)
        self.tabs.add(table_frame, text="Excel 條件（唯讀）")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(
            table_frame, columns=("condition", "value", "source"), show="headings", selectmode="browse"
        )
        for column, label, width in (
            ("condition", "條件", 320),
            ("value", "Excel 值", 240),
            ("source", "來源儲存格", 260),
        ):
            self.table.heading(column, text=label)
            self.table.column(column, width=pixels(width), minwidth=pixels(120), anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.results = ResultPane(self.tabs, pixels)
        self.tabs.add(self.results, text="核對結果")
        self.not_covered = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.not_covered, text="待處理")
        self.not_covered.columnconfigure(0, weight=1)
        self.not_covered.rowconfigure(1, weight=1)
        ttk.Label(self.not_covered, text="以下項目未涵蓋，不代表通過，仍需人工核對。").grid(row=0, column=0, sticky="w")
        self.pending_text = tk.Text(
            self.not_covered, wrap="word", font=("Microsoft JhengHei UI", 11), relief="flat", state="disabled"
        )
        self.pending_text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        pending_scroll = ttk.Scrollbar(self.not_covered, command=self.pending_text.yview)
        pending_scroll.grid(row=1, column=1, sticky="ns")
        self.pending_text.configure(yscrollcommand=pending_scroll.set)
        self.details = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.details, wraplength=960).grid(row=10, column=0, columnspan=3, sticky="w")
        self.table.bind("<<TreeviewSelect>>", self._condition_selected)
        self.status = tk.StringVar(value=session.message)
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=960)
        self.status_label.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(8, 10))
        ttk.Label(frame, text="按「儲存報告」才會保存到本機；有差異或待處理項目請交由人工核對。").grid(
            row=12, column=0, columnspan=3, sticky="w"
        )
        root.bind("<Configure>", self._resize)
        root.after(1000, self._watch_sources)

    def _resize(self, event):
        if event.widget == self.root:
            self.status_label.configure(wraplength=max(300, event.width - 60))

    def _set_templates(self):
        choices = [c.label for c in SUPPORTED_TEMPLATES if c.issuer == self.issuer.get()]
        self.template.configure(values=choices)
        self.template.set(choices[0] if choices else "")

    def _issuer_changed(self, _):
        self._set_templates()
        self._selection_changed()

    def _show_product_code(self, text):
        self.product_code_text.configure(state="normal")
        self.product_code_text.delete("1.0", "end")
        self.product_code_text.insert("1.0", text)
        self.product_code_text.configure(state="disabled")

    def _clear(self):
        self.shown = None
        self._show_product_code("尚未載入有效預覽。")
        self.table.delete(*self.table.get_children())
        self.details.set("")
        self.check_button.configure(state="disabled")
        self._clear_results()

    def _clear_results(self):
        self.has_result = False
        self.save_button.configure(state="disabled")
        self.results.clear()
        self.tabs.tab(self.not_covered, text="待處理")
        self.pending_text.configure(state="normal")
        self.pending_text.delete("1.0", "end")
        self.pending_text.configure(state="disabled")

    def _selection_changed(self):
        choice = next(
            (c for c in SUPPORTED_TEMPLATES if c.issuer == self.issuer.get() and c.label == self.template.get()), None
        )
        self.session.select(
            Path(self.pdf_path.get()) if self.pdf_path.get() else None,
            Path(self.excel_path.get()) if self.excel_path.get() else None,
            issuer=self.issuer.get(),
            template=choice.template if choice else "",
        )
        self._clear()
        self.status.set(self.session.message)

    def choose_file(self, kind):
        types = [("PDF 說明書", "*.pdf")] if kind == "pdf" else [("Excel 詢價表", "*.xlsx *.xlsm")]
        selected = filedialog.askopenfilename(
            parent=self.root, title="選取 TS PDF" if kind == "pdf" else "選取 Excel 詢價表", filetypes=types
        )
        if selected:
            (self.pdf_path if kind == "pdf" else self.excel_path).set(selected)
            self._selection_changed()

    def _busy(self, busy):
        self.save_button.configure(state="disabled" if busy or not self.has_result else "normal")
        for control in self.controls:
            control.configure(
                state="disabled" if busy else "readonly" if isinstance(control, ttk.Combobox) else "normal"
            )
        self.check_button.configure(state="disabled" if busy or self.shown is None else "normal")

    def load(self):
        if self.pending is not None or self.check_pending is not None or self.save_pending is not None:
            return
        self._clear()
        self._busy(True)
        self.status.set("正在讀取 PDF 與 Excel，請稍候…")
        self.pending = self.executor.submit(self.session.load_preview)
        self.root.after(80, self._finish_load)

    def _finish_load(self):
        if self.closed or self.pending is None:
            return
        if not self.pending.done():
            self.root.after(80, self._finish_load)
            return
        future, self.pending = self.pending, None
        self._busy(False)
        try:
            preview = future.result()
            self.shown = preview
            self._show_product_code(
                preview.product_code or "無法可靠擷取商品代號，請人工確認。" + preview.product_code_note
            )
            for condition in preview.conditions:
                self.table.insert("", "end", values=(condition.label, display_value(condition.value), condition.source))
            self.status.set(self.session.message + ("\n" + "；".join(preview.warnings) if preview.warnings else ""))
            self.check_button.configure(state="normal")
            self.tabs.select(0)
        except IngestionError as e:
            self.status.set(str(e))
        except Exception:
            self.status.set("預覽讀取失敗，請確認檔案與格式設定後重新載入。")

    def start_check(self):
        if (
            self.pending is not None
            or self.check_pending is not None
            or self.save_pending is not None
            or self.shown is None
        ):
            return
        self._clear_results()
        self.details.set("")
        self._busy(True)
        self.status.set("正在核對，請稍候…")
        self.tabs.select(self.results)
        self.check_pending = self.executor.submit(self.session.start_check)
        self.root.after(80, self._finish_check)

    def _finish_check(self):
        if self.closed or self.check_pending is None:
            return
        if not self.check_pending.done():
            self.root.after(80, self._finish_check)
            return
        future, self.check_pending = self.check_pending, None
        try:
            outcome = future.result()
            self.results.show(outcome)
            self.has_result = True
            self.pending_text.configure(state="normal")
            self.pending_text.insert("1.0", "\n\n".join(n["description"] for n in outcome.report.not_covered))
            self.pending_text.configure(state="disabled")
            self.tabs.tab(self.not_covered, text=f"待處理（{len(outcome.report.not_covered)}）")
            self.status.set(outcome.headline)
        except IngestionError as e:
            self._clear()
            self.status.set(str(e))
        except Exception:
            self.status.set("核對失敗，請確認檔案與設定後重新載入預覽。")
        self._busy(False)

    def save_report(self):
        if not self.has_result or self.save_pending is not None or self.check_pending is not None:
            return
        self._busy(True)
        destination = filedialog.askdirectory(parent=self.root, title="選取報告保存資料夾")
        if not destination:
            self.status.set("已取消儲存，核對結果仍保留。")
            self._busy(False)
            return
        self.status.set("儲存報告中…")
        self.save_pending = self.executor.submit(self.session.save_report, Path(destination))
        self.root.after(80, self._finish_save)

    def _finish_save(self):
        if self.closed or self.save_pending is None:
            return
        if not self.save_pending.done():
            self.root.after(80, self._finish_save)
            return
        future, self.save_pending = self.save_pending, None
        try:
            receipt = future.result()
            if receipt.source_changed:
                self._clear()
            self.status.set(receipt.summary)
        except IngestionError as error:
            self._clear()
            self.status.set(str(error))
        except Exception as error:
            self.status.set(f"儲存失敗，核對結果仍保留：{error}")
        self._busy(False)

    def _condition_selected(self, _):
        selected = self.table.selection()
        if selected:
            values = self.table.item(selected[0], "values")
            self.details.set(f"{values[0]}：{values[1]}　來源：{values[2]}")

    def _watch_sources(self):
        if self.closed:
            return
        if self.validation is not None and self.validation.done():
            try:
                valid = self.validation.result() is not None
            except Exception:
                valid = False
            if (
                self.pending is None
                and self.check_pending is None
                and self.save_pending is None
                and self.shown is self.checking_for
                and not valid
            ):
                self._clear()
                self.status.set(self.session.message)
            self.validation = None
            self.checking_for = None
            self.root.after(1500, self._watch_sources)
            return
        if (
            self.pending is None
            and self.check_pending is None
            and self.save_pending is None
            and self.shown is not None
            and self.validation is None
        ):
            self.checking_for = self.shown
            self.validation = self.executor.submit(lambda: self.session.preview)
        self.root.after(100 if self.validation is not None else 1500, self._watch_sources)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BARC 本機 PANEL：預覽並核對 TS PDF 與 Excel，不自動保存。")
    parser.add_argument(
        "--order-format", type=Path, default=Path("config/order_formats/barc.toml"), help="BARC 詢價格式設定檔"
    )
    parser.add_argument(
        "--review-standard", type=Path, default=Path("config/review_standard.toml"), help="審查標準設定檔"
    )
    args = parser.parse_args(argv)
    enable_windows_dpi_awareness()
    root = tk.Tk()
    PanelWindow(root, PanelSession(args.order_format, args.review_standard))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
