"""穩定的 Windows 啟動入口；一律從專案根目錄啟動，不連網。"""

from pathlib import Path


def run(script: str, argv: list[str]) -> None:
    root = Path(script).resolve().parent
    from fcn_checker.panel import main

    main(
        [
            "--config-dir",
            str(root / "config"),
            "--builtin-config-dir",
            str(root / "config"),
            "--review-standard",
            str(root / "config/review_standard.toml"),
            "--install-root",
            str(root),
        ]
    )
