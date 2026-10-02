"""程式版本（核對紀錄用）：開發用 Git 工作目錄的 HEAD，或 setup_panel.cmd 安裝時記錄的版本。

更新方式：在專案資料夾 `git pull` 後重新執行 `setup_panel.cmd`（ADR 0003 已取代）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}")
PACKAGE_DIR = Path(__file__).resolve().parent
INSTALLED = Path(".local") / "installed.json"


def git_revision(root: Path) -> str | None:
    """root 本身是 Git 工作目錄時回傳 HEAD commit；不是 Git、沒有 git 或查詢失敗時為 None。"""
    root = root.resolve()
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        repository = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            creationflags=flags,
        )
        if repository.returncode == 0 and Path(repository.stdout.strip()).resolve() == root:
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                creationflags=flags,
            )
            if revision.returncode == 0 and SHA.fullmatch(revision.stdout.strip()):
                return revision.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def program_commit(root: Path, *, package: Path = PACKAGE_DIR) -> str | None:
    """正在執行的程式的 commit（核對紀錄用）；`package` 是 fcn_checker 套件所在資料夾。

    依序：開發用 Git 工作目錄（src/fcn_checker 的上兩層）的 HEAD；根目錄安裝時記錄的 .local/installed.json。
    都取不到時為 None。
    """
    revision = git_revision(package.parent.parent)
    if revision is not None:
        return revision
    try:
        sha = json.loads((root / INSTALLED).read_text(encoding="utf-8"))["sha"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return sha if isinstance(sha, str) and SHA.fullmatch(sha) else None


def record_installation(root: Path) -> None:
    """setup_panel.cmd 安裝成功後記錄版本；沒有 Git（例如 ZIP 安裝）時記為未知。"""
    root = root.resolve()
    sha = git_revision(root)
    target = root / INSTALLED
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f"installed-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps({"sha": sha}), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--record-installation", type=Path, required=True)
    record_installation(parser.parse_args().record_installation)
