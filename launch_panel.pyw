"""Windows 雙擊入口；設定路徑以專案位置為準，不依賴啟動工作目錄。"""

from tkinter import messagebox

try:
    from panel_bootstrap import run

    run(__file__)
except Exception as error:
    messagebox.showerror("PANEL 無法啟動", f"請確認已依 README 安裝專案與 Python Tkinter。\n\n{error}")
