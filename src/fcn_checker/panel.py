"""Tkinter 本機桌面 PANEL：參考條件表＋多份說明書的預覽、核對、逐份結果與手動儲存。"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .approval_dates import parse_input_date
from .batch import BatchItem, BatchPreview
from .check_config import CONFIG_DIR, DEFAULTS
from .ingestion import IngestionError, SourceSnapshot
from .messages import STATUS_ZH, problem_message
from .panel_workflow import PanelOutcome, PanelSession, ReleaseState
from .saving import SaveReceipt
from .schema import CheckResult, CheckStatus


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
        f"{row.item.name}｜{STATUS_ZH[row.status]}\n"
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


PANEL_ICON = Path(__file__).parent / "assets" / "panel.ico"
APP_USER_MODEL_ID = "TaylorYam.FcnTermSheetChecker.Panel"


def set_windows_app_id() -> None:
    """在建立 Tk 視窗前設定專屬 AppUserModelID，工作列才顯示 PANEL 圖示，不與其他 Python 程式合併成一組。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def apply_window_icon(root: tk.Misc) -> None:
    """標題列、Alt-Tab、工作列與之後開啟的對話框都使用 PANEL 圖示；找不到或無法載入時沿用 Tk 預設圖示。"""
    try:
        root.iconbitmap(default=str(PANEL_ICON))
    except tk.TclError:
        return
    if sys.platform == "win32":
        root.update_idletasks()  # 建立外框視窗後 wm_frame 才是實際的視窗代碼
        _set_window_icons(int(root.wm_frame(), 16))


def _set_window_icons(hwnd: int) -> None:
    """Tk 只把圖示設在視窗類別上，Windows 11 工作列會改拿小圖示放大而變糊（#111）。

    依目前縮放從 .ico 載入剛好的大、小圖示尺寸，直接設給視窗。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    for kind, metric in ((1, 11), (0, 49)):  # (ICON_BIG, SM_CXICON)、(ICON_SMALL, SM_CXSMICON)
        size = user32.GetSystemMetrics(metric)
        icon = user32.LoadImageW(None, str(PANEL_ICON), 1, size, size, 0x10)  # IMAGE_ICON、LR_LOADFROMFILE
        if icon:
            user32.SendMessageW(hwnd, 0x80, kind, icon)  # WM_SETICON


FOLDERS_RECORD = Path(".local") / "panel_folders.json"  # 兩個選檔按鈕各自記住的上次資料夾（根目錄下）


class LastFolders:
    """選檔按鈕各自記住上一次選檔的資料夾；讀寫失敗時當作沒有記錄。"""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def initial(self, kind: str) -> str | None:
        """對話框的起始資料夾；沒有記錄或資料夾已不存在時回傳 None，交給 Windows 決定。"""
        folder = self._read().get(kind)
        return folder if isinstance(folder, str) and Path(folder).is_dir() else None

    def remember(self, kind: str, selected: Path) -> None:
        """記下選取檔案所在的資料夾；寫不進去時略過，下次照舊由 Windows 決定。"""
        data = self._read()
        data[kind] = str(selected.parent)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


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
    """左側逐份說明書清單（問題優先）；右側選取那份的逐欄結果，選取列可查看完整值與原文證據。

    下方「人工放行」按鈕對選取的說明書操作；能否放行與顯示的原因由 release_state 提供，按下時呼叫 on_release。
    """

    def __init__(
        self,
        parent,
        pixels,
        release_state: Callable[[BatchItem], ReleaseState],
        on_release: Callable[[BatchItem], None],
    ):
        super().__init__(parent, padding=8)
        self.release_state, self.on_release = release_state, on_release
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
        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.release_button = ttk.Button(actions, text="人工放行…", command=self._release, state="disabled")
        self.release_button.grid(row=0, column=0, sticky="w")
        self.release_reason = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.release_reason, wraplength=pixels(900)).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
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
        self._release_state(None)

    def show(self, outcome: PanelOutcome, select: BatchItem | None = None):
        """顯示結果並選取 select（沒給或不在結果中時選第一份）。"""
        self.clear()
        self.outcome = outcome
        self.summary.set(outcome.headline)
        chosen = None
        for item in outcome.ordered_items:
            problems = sum(r.status.is_problem for r in item.report.results)
            key = self.item_table.insert("", "end", values=(item.status_label, item.term_sheet.name, problems))
            self.items[key] = item
            if item is select:
                chosen = key
        if self.items:
            chosen = chosen or next(iter(self.items))
            self.item_table.selection_set(chosen)
            self.item_table.see(chosen)
            self._item_selected(None)

    def _release_state(self, item: BatchItem | None) -> None:
        if item is None:
            self.release_button.configure(text="人工放行…", state="disabled")
            self.release_reason.set("")
        elif item.released:
            self.release_button.configure(text="取消放行", state="normal")
            self.release_reason.set("已人工放行：儲存時視同通過並回填，不列入錯誤清單。")
        else:
            allowed, reason = self.release_state(item)
            self.release_button.configure(text="人工放行…", state="normal" if allowed else "disabled")
            self.release_reason.set(f"不能人工放行：{reason}" if reason else "")

    def _release(self):
        item = self.selected_item()
        if item is not None:
            self.on_release(item)

    def selected_item(self) -> BatchItem | None:
        selection = self.item_table.selection()
        return self.items.get(selection[0]) if selection else None

    def _item_selected(self, _):
        item = self.selected_item()
        if item is None or self.outcome is None:
            return
        self._release_state(item)
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
                    row.item.name,
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
        head = "回填欄位（整份通過或人工放行才會回填；按「儲存核對結果」後寫入核對結果檔）："
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


class ApprovalDatesDialog(tk.Toplevel):
    """審查通過日期：列出歷次日期，新增晚於最新一筆的日期，或修改／刪除最新一筆（Issue #120）。"""

    def __init__(self, parent: tk.Misc, session: PanelSession, pixels, changed: Callable[[], None]):
        dates = session.approval_dates()  # 設定檔有問題時由呼叫端顯示原因，不開視窗
        super().__init__(parent)
        self.session, self.changed = session, changed
        self.title("審查通過日期")
        self.transient(parent)
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=pixels(16))
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="每份說明書依交易日，核對當天或之前最近一次的審查通過日期。\n"
            "重新審查後新增新的日期；較早的日期不能修改，只能修改或刪除最新一筆。",
            wraplength=pixels(520),
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        self.listbox = tk.Listbox(frame, height=8, font=("Microsoft JhengHei UI", 11), activestyle="none")
        self.listbox.grid(row=1, column=0, columnspan=4, sticky="ew", pady=pixels(10))
        ttk.Label(frame, text="日期（YYYY-MM-DD）").grid(row=2, column=0, sticky="w")
        self.entry = ttk.Entry(frame, width=14, font=("Microsoft JhengHei UI", 11))
        self.entry.grid(row=2, column=1, sticky="w", padx=(pixels(8), 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=4, sticky="w", pady=(pixels(12), 0))
        for text, command in (
            ("新增", self._add),
            ("把最新一筆改成這個日期", self._change),
            ("刪除最新一筆", self._remove),
            ("關閉", self.destroy),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, pixels(8)))
        self._show(dates)
        self.update_idletasks()  # 置中在主視窗上
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.entry.focus_set()
        self.grab_set()

    def _show(self, dates: tuple) -> None:
        self.dates = dates
        self.listbox.delete(0, "end")
        for i, d in enumerate(dates):
            self.listbox.insert("end", f"{d}{'（最新）' if i == len(dates) - 1 else ''}")

    def _run(self, action: Callable[[], str], confirm: str | None = None) -> None:
        if confirm and not messagebox.askyesno("審查通過日期", confirm, parent=self):
            return
        try:
            message = action()
        except IngestionError as e:
            messagebox.showerror("審查通過日期", str(e), parent=self)
            return
        self.entry.delete(0, "end")
        self._show(self.session.approval_dates())
        self.changed()
        messagebox.showinfo("審查通過日期", message, parent=self)

    def _date(self):
        return parse_input_date(self.entry.get())

    def _add(self) -> None:
        self._run(lambda: self.session.add_approval_date(self._date()))

    def _change(self) -> None:
        self._run(lambda: self.session.change_latest_approval_date(self._date()))

    def _remove(self) -> None:
        self._run(self.session.remove_latest_approval_date, f"確定刪除最新一筆審查通過日期 {self.dates[-1]}？")


class PanelWindow:
    def __init__(self, root: tk.Tk, session: PanelSession):
        self.root, self.session = root, session
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending: Future[BatchPreview] | None = None
        self.check_pending: Future[PanelOutcome] | None = None
        self.save_pending: Future[SaveReceipt] | None = None
        self.release_pending: Future[IngestionError | None] | None = None
        self.has_result = False
        self.validation: Future[bool] | None = None  # 背景只計算來源快照是否仍一致，不改工作階段
        self.checking_for: SourceSnapshot | None = None
        self.closed = False
        self.shown: BatchPreview | None = None
        self.sheet_path: Path | None = None
        self.pdf_paths: tuple[Path, ...] = ()
        self.folders = LastFolders(session.install_root / FOLDERS_RECORD)
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
        self.controls += [self.reload]
        configs = "；".join(f"{label}：{path}" for label, path in session.config_paths)
        ttk.Label(frame, text="使用的設定檔：" + configs, style="Small.TLabel", wraplength=pixels(1000)).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self.pixels = pixels
        approval_button = ttk.Button(frame, text="審查通過日期…", command=self.edit_approval_dates)
        approval_button.grid(row=5, column=2, sticky="e", pady=(0, 8))
        self.controls.append(approval_button)
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
        self.results = ResultPane(self.tabs, pixels, self.session.release_state, self.toggle_release)
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
        return any(f is not None for f in (self.pending, self.check_pending, self.save_pending, self.release_pending))

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
                parent=self.root,
                title="選取參考條件表",
                filetypes=[("Excel 參考條件表", "*.xlsx *.xlsm")],
                initialdir=self.folders.initial(kind),
            )
            if selected:
                self.sheet_path = Path(selected)
                self.folders.remember(kind, self.sheet_path)
                self.sheet_text.set(selected)
                self._selection_changed()
            return
        selected = filedialog.askopenfilenames(
            parent=self.root,
            title="選取說明書 PDF（可多選）",
            filetypes=[("PDF 說明書", "*.pdf")],
            initialdir=self.folders.initial(kind),
        )
        if selected:
            self.pdf_paths = tuple(Path(p) for p in selected)
            self.folders.remember(kind, self.pdf_paths[0])
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
        groups = outcome.not_covered
        _write(self.pending_text, "\n\n".join(f"【{g.issuer}】\n" + "\n".join(g.descriptions) for g in groups))
        self.tabs.tab(self.not_covered, text=f"待處理（{outcome.not_covered_count}）")
        self.status.set(outcome.headline)

    def toggle_release(self, item: BatchItem):
        """人工放行（先確認全部錯訊）或取消放行；放行前的來源檢查在背景執行，結果失效時清空畫面並顯示原因。"""
        if self._busy_any():
            return
        action = self.session.cancel_release if item.released else self.session.release
        if not item.released:
            text = (
                f"{item.term_sheet.name}\n\n這份說明書的問題：\n"
                + "\n".join(f"・{m}" for m in item.problem_messages)
                + "\n\n確認人工放行？放行後視同通過：儲存時回填，不列入錯誤清單。"
            )
            if not messagebox.askyesno("人工放行", text, parent=self.root):
                return

        def run() -> IngestionError | None:
            try:
                action(item)
            except IngestionError as e:
                return e
            return None

        self._busy(True)
        self.status.set("確認來源中…")
        self.release_pending = self.executor.submit(run)
        self.root.after(
            80, lambda: self._after("release_pending", lambda e: self._show_release(item, e), "人工放行失敗")
        )

    def _show_release(self, item: BatchItem, error: IngestionError | None):
        if error is not None:
            if self.session.outcome is None:  # 來源已變更、結果失效：清空畫面，不顯示空結果
                self._clear()
            self.status.set(str(error))
            return
        outcome = self.session.outcome
        self.results.show(outcome, select=outcome.selection_after_release_change(item))
        self.status.set(self.session.message)

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
        self.status.set(receipt.summary)

    def edit_approval_dates(self):
        if self._busy_any():
            return
        try:
            ApprovalDatesDialog(self.root, self.session, self.pixels, self._approval_dates_changed)
        except IngestionError as e:
            messagebox.showerror("審查通過日期", str(e), parent=self.root)

    def _approval_dates_changed(self):
        """設定檔已改寫：工作階段已清除預覽與結果，畫面跟著清空。"""
        self._clear()
        self.status.set(self.session.message)
        self._busy(False)

    def _watch_sources(self):
        if self.closed:
            return
        if self.validation is not None and self.validation.done():
            try:
                valid = self.validation.result()
            except Exception:
                valid = False
            if not self._busy_any() and not valid:  # 回到主執行緒才清除；快照已換過（重新載入）就不動
                self.session.invalidate(self.checking_for)
                if self.session.preview is None and self.shown is not None:
                    self._clear()
                    self.status.set(self.session.message)
            self.validation = None
            self.checking_for = None
            self.root.after(1500, self._watch_sources)
            return
        snapshot = self.session.snapshot
        if not self._busy_any() and snapshot is not None and self.validation is None:
            self.checking_for = snapshot
            self.validation = self.executor.submit(snapshot.still_valid)
        self.root.after(100 if self.validation is not None else 1500, self._watch_sources)

    def close(self):
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
        "--builtin-config-dir", type=Path, default=None, help="程式內建設定資料夾（雙擊入口傳入專案的 config）"
    )
    parser.add_argument("--order-formats-dir", type=Path, help=argparse.SUPPRESS)  # 舊版啟動器仍會傳入
    parser.add_argument("--review-standard", type=Path, default=DEFAULTS.review_standard, help="審查標準設定檔")
    parser.add_argument("--install-root", type=Path, help="雙擊入口提供的安裝目錄")
    args = parser.parse_args(argv)
    if args.config_dir is None:
        args.config_dir = args.order_formats_dir.parent if args.order_formats_dir else CONFIG_DIR
    return args


def session_from_args(args: argparse.Namespace) -> PanelSession:
    """根目錄與設定檔一致：雙擊入口給的專案根目錄，直接啟動時為執行目錄。"""
    return PanelSession(
        args.review_standard,
        args.config_dir,
        builtin_config_dir=args.builtin_config_dir,
        install_root=args.install_root,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    enable_windows_dpi_awareness()
    set_windows_app_id()
    root = tk.Tk()
    session = session_from_args(args)
    PanelWindow(root, session)
    apply_window_icon(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
