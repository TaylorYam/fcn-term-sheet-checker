"""Tkinter 本機桌面 PANEL：參考條件表＋多份說明書的預覽、核對、逐份結果與手動儲存。"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk

from .batch import BatchItem, BatchPreview
from .ingestion import IngestionError
from .messages import STATUS_ZH, problem_message, subject
from .panel_workflow import PanelOutcome, PanelSession, SaveReceipt
from .schema import CheckResult, CheckStatus
from .updating import PanelUpdater, UpdateError


def display_value(value: object) -> str:
    return "未提供" if value is None else str(value)


_DETAIL_DEFAULTS = {
    CheckStatus.PASS: "此已核對項目一致。",
    CheckStatus.NOT_APPLICABLE: "依明確規則，此項目不適用。",
}


def result_detail(row: CheckResult) -> str:
    """結果明細：有問題的項目用共用錯訊（不含 rule_id），再列雙方值、來源、容差與 PDF 原文。"""
    reason = problem_message(row) if row.status.is_problem else row.message or _DETAIL_DEFAULTS[row.status]
    evidence = (
        "\n".join(f"第 {e.page} 頁：{e.text}" for e in row.document_evidence) or "無法定位：沒有可用的 PDF 原文證據。"
    )
    return (
        f"{subject(row)}｜{STATUS_ZH[row.status]}\n"
        f"原因：{reason}\n"
        f"參考條件表／標準值：{display_value(row.expected)}\nPDF 值：{display_value(row.actual)}\n"
        f"參考條件表來源：{'、'.join(row.order_source) or '非參考條件表欄位，依審查標準或文件內部規則核對。'}\n"
        f"容差：{row.tolerance or '未設定'}\nPDF 原文：\n{evidence}"
    )


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


def _table(parent, columns: tuple[tuple[str, str, int], ...], pixels, height: int = 6) -> ttk.Treeview:
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    table = ttk.Treeview(frame, columns=[c for c, _, _ in columns], show="headings", selectmode="browse", height=height)
    for column, label, width in columns:
        table.heading(column, text=label)
        table.column(column, width=pixels(width), minwidth=pixels(80), anchor="w")
    table.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(frame, command=table.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
    horizontal.grid(row=1, column=0, sticky="ew")
    table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
    table.frame = frame
    return table


def _text(parent, height: int) -> tk.Text:
    return tk.Text(
        parent,
        height=height,
        wrap="word",
        font=("Microsoft JhengHei UI", 11),
        relief="flat",
        padx=8,
        pady=6,
        state="disabled",
    )


def _write(widget: tk.Text, text: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    widget.configure(state="disabled")


class ResultPane(ttk.Frame):
    """左側逐份說明書清單（問題優先）；右側選取那份的逐欄結果，選取列可查看完整值與原文證據。"""

    def __init__(self, parent, pixels):
        super().__init__(parent, padding=8)
        self.items: dict[str, BatchItem] = {}
        self.rows: dict[str, CheckResult] = {}
        self.outcome: PanelOutcome | None = None
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self.summary = tk.StringVar(value="尚未執行核對。")
        ttk.Label(self, textvariable=self.summary, wraplength=pixels(1000)).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )
        self.item_table = _table(
            self, (("status", "狀態", 150), ("file", "PDF 檔名", 220), ("problems", "問題數", 70)), pixels, height=8
        )
        self.item_table.frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.table = _table(
            self,
            (
                ("status", "狀態", 110),
                ("field", "欄位", 190),
                ("expected", "參考條件表／標準值", 190),
                ("actual", "PDF 值", 190),
                ("pages", "PDF 頁碼", 110),
            ),
            pixels,
            height=8,
        )
        self.table.frame.grid(row=1, column=1, sticky="nsew")
        ttk.Label(self, text="選取項目查看原因、參考條件表儲存格與 PDF 原文；下方另列回填欄位：").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 4)
        )
        self.detail = _text(self, 6)
        self.detail.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.item_table.bind("<<TreeviewSelect>>", self._item_selected)
        self.table.bind("<<TreeviewSelect>>", self._row_selected)

    def clear(self):
        self.items.clear()
        self.rows.clear()
        self.outcome = None
        self.item_table.delete(*self.item_table.get_children())
        self.table.delete(*self.table.get_children())
        self.summary.set("尚未執行核對。")
        _write(self.detail, "")

    def show(self, outcome: PanelOutcome):
        self.clear()
        self.outcome = outcome
        self.summary.set(outcome.headline)
        for item in outcome.ordered_items:
            problems = sum(r.status.is_problem for r in item.report.results)
            key = self.item_table.insert("", "end", values=(item.status_label, item.term_sheet.name, problems))
            self.items[key] = item
        if self.items:
            self.item_table.selection_set(next(iter(self.items)))
            self._item_selected(None)

    def selected_item(self) -> BatchItem | None:
        selection = self.item_table.selection()
        return self.items.get(selection[0]) if selection else None

    def _item_selected(self, _):
        item = self.selected_item()
        if item is None or self.outcome is None:
            return
        self.rows.clear()
        self.table.delete(*self.table.get_children())
        for row in self.outcome.ordered_results(item):
            pages = (
                "第 " + "、".join(str(p) for p in sorted({e.page for e in row.document_evidence})) + " 頁"
                if row.document_evidence
                else "無法定位"
            )
            key = self.table.insert(
                "",
                "end",
                values=(
                    STATUS_ZH[row.status],
                    subject(row),
                    display_value(row.expected),
                    display_value(row.actual),
                    pages,
                ),
            )
            self.rows[key] = row
        _write(self.detail, self._backfill_text(item))

    @staticmethod
    def _backfill_text(item: BatchItem) -> str:
        if not item.report.backfill:
            return "這份說明書沒有回填決策（未配對到參考條件表或無法核對）。"
        head = "回填欄位（整份通過才會回填；按「儲存核對結果」後寫入核對結果檔）："
        lines = [
            f"{d.column}（{d.cell}）：表上 {display_value(d.sheet_value)}／說明書 {display_value(d.expected)} → {d.action.label}"
            for d in item.report.backfill
        ]
        return "\n".join([head, *lines])

    def _row_selected(self, _):
        selection = self.table.selection()
        if not selection or selection[0] not in self.rows:
            return
        _write(self.detail, result_detail(self.rows[selection[0]]))


class PanelWindow:
    def __init__(self, root: tk.Tk, session: PanelSession, install_root: Path | None = None):
        self.root, self.session = root, session
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending: Future[BatchPreview] | None = None
        self.check_pending: Future[PanelOutcome] | None = None
        self.save_pending: Future[SaveReceipt] | None = None
        self.has_result = False
        self.update_pending = None
        self.update_phase = ""
        self.update_progress = SimpleQueue()
        self.updater = None
        self.update_error = ""
        if install_root is not None:
            try:
                self.updater = PanelUpdater(install_root, self.update_progress.put)
            except UpdateError as error:
                self.update_error = str(error)
        self.validation: Future[BatchPreview | None] | None = None
        self.checking_for: BatchPreview | None = None
        self.closed = False
        self.shown: BatchPreview | None = None
        self.sheet_path: Path | None = None
        self.pdf_paths: tuple[Path, ...] = ()
        root.title("FCN Term Sheet 核對")
        scale = root.winfo_fpixels("1i") / 96

        def pixels(value):
            return round(value * scale)

        width = min(pixels(1180), root.winfo_screenwidth() - pixels(40))
        height = min(pixels(820), root.winfo_screenheight() - pixels(80))
        root.geometry(f"{width}x{height}")
        root.minsize(min(pixels(820), width), min(pixels(720), height))
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
        style.configure("Small.TLabel", font=("Microsoft JhengHei UI", 9), foreground="#4a5a68")
        style.configure("TButton", font=("Microsoft JhengHei UI", 11), padding=(12, 7))
        style.configure("Treeview", font=("Microsoft JhengHei UI", 11), rowheight=pixels(30))
        style.configure("Treeview.Heading", font=("Microsoft JhengHei UI", 11, "bold"))
        style.map("Treeview", background=[("selected", "#d9e9f2")], foreground=[("selected", "#172b3a")])
        frame = ttk.Frame(root, padding=pixels(20))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="Term Sheet 核對", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            frame,
            text="選取參考條件表與說明書 PDF，先載入預覽確認每份對到的列，再按「開始核對」。上手由 PDF 檔名前三碼決定。",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))
        self.sheet_text = tk.StringVar()
        self.pdf_text = tk.StringVar()
        self.controls: list[ttk.Widget] = []
        for row, label, variable, kind in (
            (2, "參考條件表", self.sheet_text, "sheet"),
            (3, "說明書 PDF", self.pdf_text, "pdf"),
        ):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=5)
            ttk.Entry(frame, textvariable=variable, state="readonly").grid(
                row=row, column=1, sticky="ew", padx=(0, 12), pady=5
            )
            button = ttk.Button(frame, text="選取檔案…", command=lambda k=kind: self.choose_files(k))
            button.grid(row=row, column=2, pady=5)
            self.controls.append(button)
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 6))
        self.reload = ttk.Button(actions, text="載入／重新載入預覽", command=self.load)
        self.reload.pack(side="left", padx=(0, 12))
        self.check_button = ttk.Button(actions, text="開始核對", command=self.start_check, state="disabled")
        self.check_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="儲存核對結果…", command=self.save, state="disabled")
        self.save_button.pack(side="left", padx=(12, 0))
        self.update_button = ttk.Button(actions, text="更新 GitHub 最新版", command=self.update_app)
        self.update_button.pack(side="left", padx=(12, 0))
        self.controls += [self.update_button, self.reload]
        configs = "；".join(f"{label}：{path}" for label, path in session.config_paths)
        ttk.Label(frame, text="使用的設定檔：" + configs, style="Small.TLabel", wraplength=pixels(1100)).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(frame, text="預覽與核對結果", style="Section.TLabel").grid(row=6, column=0, columnspan=3, sticky="w")
        self.tabs = ttk.Notebook(frame)
        self.tabs.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(6, 10))
        preview_frame = ttk.Frame(self.tabs, padding=8)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.tabs.add(preview_frame, text="預覽（唯讀）")
        self.preview_table = _table(
            preview_frame,
            (
                ("file", "PDF 檔名", 230),
                ("issuer", "上手", 90),
                ("code", "PDF 商品代號", 150),
                ("row", "參考條件表列", 110),
                ("problem", "狀態", 420),
            ),
            pixels,
        )
        self.preview_table.frame.grid(row=0, column=0, sticky="nsew")
        self.preview_warnings = tk.StringVar(value="")
        ttk.Label(preview_frame, textvariable=self.preview_warnings, wraplength=pixels(1000)).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        self.results = ResultPane(self.tabs, pixels)
        self.tabs.add(self.results, text="核對結果")
        self.not_covered = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.not_covered, text="待處理")
        self.not_covered.columnconfigure(0, weight=1)
        self.not_covered.rowconfigure(1, weight=1)
        ttk.Label(self.not_covered, text="以下項目未涵蓋，不代表通過，仍需人工核對。").grid(row=0, column=0, sticky="w")
        self.pending_text = _text(self.not_covered, 6)
        self.pending_text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.status = tk.StringVar(value=session.message)
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=960)
        self.status_label.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 10))
        ttk.Label(
            frame,
            text="按「儲存核對結果」才會寫出核對結果檔（原參考條件表不動）；錯誤清單上的說明書請交由人工核對。",
        ).grid(row=9, column=0, columnspan=3, sticky="w")
        root.bind("<Configure>", self._resize)
        root.after(1000, self._watch_sources)

    def _resize(self, event):
        if event.widget == self.root:
            self.status_label.configure(wraplength=max(300, event.width - 60))

    def _busy_any(self) -> bool:
        return any(f is not None for f in (self.pending, self.check_pending, self.save_pending, self.update_pending))

    def _clear(self):
        self.shown = None
        self.preview_table.delete(*self.preview_table.get_children())
        self.preview_warnings.set("")
        self.check_button.configure(state="disabled")
        self._clear_results()

    def _clear_results(self):
        self.has_result = False
        self.save_button.configure(state="disabled")
        self.results.clear()
        self.tabs.tab(self.not_covered, text="待處理")
        _write(self.pending_text, "")

    def _selection_changed(self):
        self.session.select(self.sheet_path, self.pdf_paths)
        self._clear()
        self.status.set(self.session.message)

    def choose_files(self, kind):
        if kind == "sheet":
            selected = filedialog.askopenfilename(
                parent=self.root, title="選取參考條件表", filetypes=[("Excel 參考條件表", "*.xlsx *.xlsm")]
            )
            if selected:
                self.sheet_path = Path(selected)
                self.sheet_text.set(selected)
                self._selection_changed()
            return
        selected = filedialog.askopenfilenames(
            parent=self.root, title="選取說明書 PDF（可多選）", filetypes=[("PDF 說明書", "*.pdf")]
        )
        if selected:
            self.pdf_paths = tuple(Path(p) for p in selected)
            names = "、".join(p.name for p in self.pdf_paths[:3])
            more = f" 等 {len(self.pdf_paths)} 份" if len(self.pdf_paths) > 3 else ""
            self.pdf_text.set(f"已選 {len(self.pdf_paths)} 份：{names}{more}")
            self._selection_changed()

    def _busy(self, busy):
        self.save_button.configure(state="disabled" if busy or not self.has_result else "normal")
        for control in self.controls:
            control.configure(state="disabled" if busy else "normal")
        self.check_button.configure(state="disabled" if busy or self.shown is None else "normal")

    def _after(self, name: str, finish: Callable[[object], None], failure: str) -> None:
        """背景工作完成後在主執行緒呼叫 finish；IngestionError 顯示原因並清除結果。"""
        if self.closed or getattr(self, name) is None:
            return
        future = getattr(self, name)
        if not future.done():
            self.root.after(80, lambda: self._after(name, finish, failure))
            return
        setattr(self, name, None)
        try:
            finish(future.result())
        except IngestionError as e:
            self._clear()
            self.status.set(str(e))
        except Exception as e:
            self.status.set(f"{failure}：{e}")
        self._busy(False)

    def load(self):
        if self._busy_any():
            return
        self._clear()
        self._busy(True)
        self.status.set("正在讀取參考條件表與說明書，請稍候…")
        self.pending = self.executor.submit(self.session.load_preview)
        self.root.after(
            80, lambda: self._after("pending", self._show_preview, "預覽讀取失敗，請確認檔案與設定後重新載入")
        )

    def _show_preview(self, preview: BatchPreview):
        self.shown = preview
        for row in preview.rows:
            self.preview_table.insert(
                "",
                "end",
                values=(
                    row.term_sheet.name,
                    "未支援上手" if row.unsupported else display_value(row.issuer),
                    display_value(row.product_code),
                    f"第 {row.reference_row} 列" if row.reference_row else "—",
                    row.problem or "可以核對",
                ),
            )
        self.preview_warnings.set("參考條件表欄名問題：" + "；".join(preview.warnings) if preview.warnings else "")
        self.status.set(self.session.message)
        self.tabs.select(0)

    def start_check(self):
        if self._busy_any() or self.shown is None:
            return
        self._clear_results()
        self._busy(True)
        self.status.set("正在核對，請稍候…")
        self.tabs.select(self.results)
        self.check_pending = self.executor.submit(self.session.start_check)
        self.root.after(
            80, lambda: self._after("check_pending", self._show_outcome, "核對失敗，請確認檔案與設定後重新載入預覽")
        )

    def _show_outcome(self, outcome: PanelOutcome):
        self.results.show(outcome)
        self.has_result = True
        not_covered = {n["rule_id"]: n["description"] for i in outcome.batch.items for n in i.report.not_covered}
        _write(self.pending_text, "\n\n".join(not_covered.values()))
        self.tabs.tab(self.not_covered, text=f"待處理（{len(not_covered)}）")
        self.status.set(outcome.headline)

    def save(self):
        if not self.has_result or self._busy_any():
            return
        self._busy(True)
        destination = filedialog.askdirectory(parent=self.root, title="選取核對結果檔的保存資料夾")
        if not destination:
            self.status.set(self.session.save(None).summary)
            self._busy(False)
            return
        self.status.set("儲存中…")
        self.save_pending = self.executor.submit(self.session.save, Path(destination))
        self.root.after(80, lambda: self._after("save_pending", self._show_receipt, "儲存失敗，核對結果仍保留"))

    def _show_receipt(self, receipt: SaveReceipt):
        if self.session.outcome is None:
            self._clear()
        elif self.results.outcome is not None:
            self.results.show(self.results.outcome)  # 更新「已回填」狀態
        self.status.set(receipt.summary)

    def update_app(self):
        if self._busy_any():
            return
        if self.updater is None:
            self.status.set(self.update_error or "請從 launch_panel.cmd 開啟 PANEL，才能更新安裝版本。")
            return
        self._busy(True)
        self.status.set("正在檢查 GitHub main 最新版…")
        self.update_phase = "check"
        self.update_pending = self.executor.submit(self.updater.check)
        self.root.after(80, self._finish_update)

    def _install_and_restart(self, info):
        update = self.updater.install(info)
        self.update_progress.put("安裝驗證完成，正在重新啟動 PANEL…")
        self.updater.restart(update)

    def _finish_update(self):
        if self.closed or self.update_pending is None:
            return
        try:
            while True:
                self.status.set(self.update_progress.get_nowait())
        except Empty:
            pass
        if not self.update_pending.done():
            self.root.after(80, self._finish_update)
            return
        future, self.update_pending = self.update_pending, None
        try:
            result = future.result()
            if self.update_phase == "install":
                self.close()
                return
            versions = (
                f"目前：{result.current[:12] if result.current else '尚無版本紀錄'}\nGitHub main：{result.latest[:12]}"
            )
            if not result.available:
                self.status.set("已是 GitHub 最新版。\n" + versions)
            elif messagebox.askyesno(
                "更新 GitHub 最新版",
                versions + "\n\n更新完成會重新啟動，未儲存核對結果將消失。\n請先儲存。要現在更新嗎？",
                parent=self.root,
            ):
                self.update_phase = "install"
                self.status.set("正在下載與安裝新版，可能需要數分鐘…")
                self.update_pending = self.executor.submit(self._install_and_restart, result)
                self.root.after(80, self._finish_update)
                return
            else:
                self.status.set("已取消更新，核對結果仍保留。\n" + versions)
        except UpdateError as error:
            self.status.set(str(error))
        except Exception:
            self.status.set("更新未完成，原視窗與核對結果仍可使用。請重試或聯絡維護人員。")
        self._busy(False)

    def _watch_sources(self):
        if self.closed:
            return
        if self.validation is not None and self.validation.done():
            try:
                valid = self.validation.result() is not None
            except Exception:
                valid = False
            if not self._busy_any() and self.shown is self.checking_for and not valid:
                self._clear()
                self.status.set(self.session.message)
            self.validation = None
            self.checking_for = None
            self.root.after(1500, self._watch_sources)
            return
        if not self._busy_any() and self.shown is not None and self.validation is None:
            self.checking_for = self.shown
            self.validation = self.executor.submit(lambda: self.session.preview)
        self.root.after(100 if self.validation is not None else 1500, self._watch_sources)

    def close(self):
        if self.update_pending is not None:
            self.status.set("更新正在執行，請等待完成後再關閉 PANEL。")
            return
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FCN 本機 PANEL：以參考條件表預覽並核對多份說明書 PDF，按儲存才寫出核對結果檔。"
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="設定檔資料夾（預設 config）；缺少參考條件表格式或上手編號對照時改用程式內建設定",
    )
    parser.add_argument(
        "--builtin-config-dir", type=Path, default=None, help="程式內建設定資料夾（啟動器傳入版本資料夾的 config）"
    )
    parser.add_argument("--order-formats-dir", type=Path, help=argparse.SUPPRESS)  # 舊版啟動器仍會傳入
    parser.add_argument(
        "--review-standard", type=Path, default=Path("config/review_standard.toml"), help="審查標準設定檔"
    )
    parser.add_argument("--install-root", type=Path, help="雙擊入口提供的安裝目錄")
    args = parser.parse_args(argv)
    if args.config_dir is None:
        args.config_dir = args.order_formats_dir.parent if args.order_formats_dir else Path("config")
    return args


def session_from_args(args: argparse.Namespace) -> PanelSession:
    """根目錄與設定檔一致：雙擊入口給的安裝根目錄（不是版本資料夾），直接啟動時為執行目錄。"""
    return PanelSession(
        args.review_standard,
        args.config_dir,
        builtin_config_dir=args.builtin_config_dir,
        install_root=args.install_root,
    )


def main(argv: list[str] | None = None, on_ready: Callable[[], None] | None = None) -> int:
    args = parse_args(argv)
    enable_windows_dpi_awareness()
    root = tk.Tk()
    session = session_from_args(args)
    PanelWindow(root, session, args.install_root)
    if on_ready is not None:
        root.after_idle(on_ready)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
