"""穩定的 Windows 啟動入口；僅讀本機啟動指標，不連網。"""

import argparse
import json
import re
import subprocess
from pathlib import Path


def run(script: str, argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--managed-root", type=Path)
    parser.add_argument("--startup-token")
    args = parser.parse_args(argv)
    root = (args.managed_root or Path(script).resolve().parent).resolve()
    pointer = root / ".local/current.json"
    if args.managed_root is None and pointer.exists():
        value = json.loads(pointer.read_text(encoding="utf-8"))
        release = (root / value["release"]).resolve()
        releases = (root / ".local/releases").resolve()
        if (
            not release.is_relative_to(releases)
            or release == releases
            or not re.fullmatch(r"[0-9a-f]{40}", value["sha"])
        ):
            raise ValueError("本機更新版本路徑不正確")
        python = release / ".venv/Scripts/pythonw.exe"
        if not python.is_file() or not (release / "launch_panel.pyw").is_file():
            raise ValueError("本機更新版本缺少啟動檔案，請聯絡維護人員")
        subprocess.Popen([str(python), str(release / "launch_panel.pyw"), "--managed-root", str(root)], cwd=root)
        return
    from fcn_checker.panel import main

    def ready():
        if args.startup_token:
            if not re.fullmatch(r"[0-9a-f]{32}", args.startup_token):
                raise ValueError("啟動回報代碼不正確")
            (root / ".local" / f"ready-{args.startup_token}").write_text("ready", encoding="utf-8")

    main(
        [
            "--config-dir",
            str(root / "config"),
            "--builtin-config-dir",
            str(Path(script).resolve().parent / "config"),
            "--review-standard",
            str(root / "config/review_standard.toml"),
            "--install-root",
            str(root),
        ],
        on_ready=ready if args.startup_token else None,
    )
