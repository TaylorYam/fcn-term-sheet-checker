"""Windows 雙擊入口；設定路徑以專案位置為準，不依賴啟動工作目錄。"""

from pathlib import Path
from tkinter import messagebox

try:
    from fcn_checker.panel import main

    config = Path(__file__).resolve().parent / "config"
    main(
        [
            "--order-format",
            str(config / "order_formats/barc.toml"),
            "--review-standard",
            str(config / "review_standard.toml"),
        ]
    )
except Exception as error:
    messagebox.showerror("PANEL 無法啟動", f"請確認已依 README 安裝專案與 Python Tkinter。\n\n{error}")
