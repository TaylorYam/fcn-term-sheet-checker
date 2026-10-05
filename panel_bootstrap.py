"""穩定的 Windows 啟動入口；一律從專案根目錄啟動，不連網。"""

from pathlib import Path


def run(script: str) -> None:
    root = Path(script).resolve().parent
    from fcn_checker.check_config import CONFIG_DIR, DEFAULTS
    from fcn_checker.panel import main

    config = root / CONFIG_DIR

    main(
        [
            "--config-dir",
            str(config),
            "--builtin-config-dir",
            str(config),
            "--review-standard",
            str(root / DEFAULTS.review_standard),
            "--install-root",
            str(root),
        ]
    )
