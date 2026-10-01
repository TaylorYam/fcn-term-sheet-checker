"""Tkinter 本機桌面 PANEL；目前僅提供來源選取與唯讀預覽。"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, ttk

from .ingestion import IngestionError
from .panel_workflow import SUPPORTED_TEMPLATES, PanelSession, Preview


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


class PanelWindow:
    def __init__(self, root: tk.Tk, session: PanelSession):
        self.root, self.session = root, session
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending: Future[Preview] | None = None
        self.validation: Future[Preview | None] | None = None
        self.checking_for: Preview | None = None
        self.closed = False
        self.shown: Preview | None = None
        root.title("FCN Term Sheet 核對 — 來源預覽")
        scale = root.winfo_fpixels("1i") / 96

        def pixels(value):
            return round(value * scale)

        width = min(pixels(1080), root.winfo_screenwidth() - pixels(40))
        height = min(pixels(780), root.winfo_screenheight() - pixels(80))
        root.geometry(f"{width}x{height}")
        root.minsize(min(pixels(780), width), min(pixels(680), height))
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
        ttk.Label(frame, text="核對來源預覽", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="先確認商品代號與 Excel 條件，再進行後續核對。").grid(
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
        self.reload = ttk.Button(frame, text="載入／重新載入預覽", command=self.load)
        self.reload.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 10))
        self.controls.append(self.reload)
        ttk.Label(frame, text="PDF 商品代號", style="Section.TLabel").grid(row=6, column=0, columnspan=3, sticky="w")
        self.product_code_text = tk.Text(
            frame,
            height=2,
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
        ttk.Label(frame, text="Excel 條件（唯讀）", style="Section.TLabel").grid(
            row=8, column=0, columnspan=3, sticky="w"
        )
        table_frame = ttk.Frame(frame)
        table_frame.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(6, 10))
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
        self.details = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.details, wraplength=960).grid(row=10, column=0, columnspan=3, sticky="w")
        self.table.bind("<<TreeviewSelect>>", self._condition_selected)
        self.status = tk.StringVar(value=session.message)
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=960)
        self.status_label.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(8, 10))
        ttk.Label(frame, text="目前版本提供來源預覽；執行核對與儲存報告將於後續版本提供。").grid(
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
        for control in self.controls:
            control.configure(
                state="disabled" if busy else "readonly" if isinstance(control, ttk.Combobox) else "normal"
            )

    def load(self):
        if self.pending is not None:
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
        except IngestionError as e:
            self.status.set(str(e))
        except Exception:
            self.status.set("預覽讀取失敗，請確認檔案與格式設定後重新載入。")

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
            if self.pending is None and self.shown is self.checking_for and not valid:
                self._clear()
                self.status.set(self.session.message)
            self.validation = None
            self.checking_for = None
            self.root.after(1500, self._watch_sources)
            return
        if self.pending is None and self.shown is not None and self.validation is None:
            self.checking_for = self.shown
            self.validation = self.executor.submit(lambda: self.session.preview)
        self.root.after(100 if self.validation is not None else 1500, self._watch_sources)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BARC 本機 PANEL：選取 TS PDF 與 Excel，顯示唯讀預覽。")
    parser.add_argument(
        "--order-format", type=Path, default=Path("config/order_formats/barc.toml"), help="BARC 詢價格式設定檔"
    )
    args = parser.parse_args(argv)
    enable_windows_dpi_awareness()
    root = tk.Tk()
    PanelWindow(root, PanelSession(args.order_format))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
