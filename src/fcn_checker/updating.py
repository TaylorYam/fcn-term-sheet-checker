"""公開 GitHub main 更新：隔離安裝、原子切換，保留本機設定與舊版。"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPOSITORY = "TaylorYam/fcn-term-sheet-checker"
API = f"https://api.github.com/repos/{REPOSITORY}"
SHA = re.compile(r"[0-9a-f]{40}")
MAX_ARCHIVE = 50 * 1024 * 1024
MAX_EXTRACTED = 100 * 1024 * 1024


class UpdateError(Exception):
    """可以直接呈現給使用者的更新失敗原因。"""


@dataclass(frozen=True)
class UpdateInfo:
    current: str | None
    latest: str

    @property
    def available(self) -> bool:
        return self.current != self.latest


@dataclass(frozen=True)
class InstalledUpdate:
    sha: str
    release: Path
    previous: bytes | None


def _download(url: str, limit: int) -> bytes:
    request = Request(url, headers={"User-Agent": "FCN-Panel-Updater", "Accept": "application/vnd.github+json"})
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read(limit + 1)
        if len(content) > limit:
            raise UpdateError("GitHub 回應超過大小限制，已停止更新。")
        return content
    except HTTPError as error:
        if error.code == 404:
            raise UpdateError("無法讀取 GitHub 專案；請確認 Repo 已公開且 main 存在。") from error
        if error.code in (403, 429):
            raise UpdateError("GitHub 暫時限制存取，請稍後重試。") from error
        raise UpdateError(f"GitHub 回應錯誤（HTTP {error.code}），請稍後重試。") from error
    except (URLError, TimeoutError, OSError) as error:
        raise UpdateError("無法連線或連線逾時，請確認網路與公司 GitHub 存取設定。") from error


def _extract(content: bytes, destination: Path) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files = archive.infolist()
            if sum(f.file_size for f in files) > MAX_EXTRACTED or len(files) > 10000:
                raise UpdateError("更新檔案超過解壓限制。")
            roots = set()
            selected = []
            for entry in files:
                name = PurePosixPath(entry.filename)
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or "\\" in entry.filename
                    or ":" in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or not name.parts
                ):
                    raise UpdateError("更新壓縮檔包含不安全路徑。")
                roots.add(name.parts[0])
                relative = Path(*name.parts[1:])
                if len(name.parts) < 2 or entry.is_dir():
                    continue
                if relative.parts[0] in ("src", "config") or relative.as_posix() in (
                    "pyproject.toml",
                    "constraints.txt",
                    "launch_panel.pyw",
                    "panel_bootstrap.py",
                ):
                    selected.append((entry, relative))
            if len(roots) != 1:
                raise UpdateError("更新壓縮檔結構不正確。")
            for entry, relative in selected:
                path = destination / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(archive.read(entry))
        for required in (
            "pyproject.toml",
            "constraints.txt",
            "launch_panel.pyw",
            "panel_bootstrap.py",
            "src/fcn_checker/panel.py",
        ):
            if not (destination / required).is_file():
                raise UpdateError("最新版缺少必要的 PANEL 安裝檔案。")
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise UpdateError("GitHub 更新壓縮檔無法讀取。") from error


def _run(args: list[str], cwd: Path) -> None:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode:
            # pip 輸出可能含公司套件來源憑證，不寫入日誌或畫面。
            raise UpdateError("新版安裝或驗證失敗；請確認 Python、資料夾權限及公司套件來源。舊版仍可使用。")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("新版安裝無法執行或已逾時；舊版仍可使用。") from error


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


def program_commit(root: Path) -> str | None:
    """執行中程式的 commit：套件所在的 Git 工作目錄優先；PANEL 安裝版沒有 Git，改讀根目錄的版本紀錄；都沒有時為 None。"""
    source = git_revision(Path(__file__).resolve().parents[2])
    if source is not None:
        return source
    try:
        return PanelUpdater(root).current
    except UpdateError:
        return None


def record_installation(root: Path) -> None:
    """首次／重新安裝成功後記錄版本；ZIP 安裝若無 Git，版本顯示未知。"""
    root = root.resolve()
    sha = git_revision(root)
    local = root / ".local"
    local.mkdir(parents=True, exist_ok=True)
    temporary = local / f"installed-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps({"sha": sha}), encoding="utf-8")
        os.replace(temporary, local / "installed.json")
        (local / "current.json").unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)


class PanelUpdater:
    def __init__(self, root: Path, progress: Callable[[str], None] = lambda _: None):
        self.root = root.resolve()
        self.local = self.root / ".local"
        self.pointer = self.local / "current.json"
        self.progress = progress
        self.current = self._revision()

    def _revision(self) -> str | None:
        path = self.pointer if self.pointer.exists() else self.local / "installed.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))["sha"]
            return value if isinstance(value, str) and SHA.fullmatch(value) else None
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise UpdateError("本機版本紀錄無法讀取，請重新安裝或聯絡維護人員。") from error

    def check(self) -> UpdateInfo:
        try:
            sha = json.loads(_download(f"{API}/commits/main", 1024 * 1024))["sha"]
            if not isinstance(sha, str) or not SHA.fullmatch(sha):
                raise ValueError("invalid commit")
            return UpdateInfo(self.current, sha)
        except (ValueError, KeyError, TypeError) as error:
            raise UpdateError("GitHub 版本資料不正確，已停止更新。") from error

    def install(self, info: UpdateInfo) -> InstalledUpdate:
        if not SHA.fullmatch(info.latest) or not info.available:
            raise UpdateError("沒有可安裝的新版。")
        lock = self.local / "update.lock"
        try:
            self.local.mkdir(parents=True, exist_ok=True)
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
        except FileExistsError as error:
            raise UpdateError(
                "此安裝資料夾已有更新正在執行；若曾非正常中斷，請由維護人員確認更新程序已停止。"
            ) from error
        except OSError as error:
            raise UpdateError("更新資料夾無法寫入，請確認磁碟空間與資料夾權限。") from error
        try:
            previous = self.pointer.read_bytes() if self.pointer.exists() else None
            release = self.local / "releases" / f"{info.latest}-{uuid.uuid4().hex}"
            release.mkdir(parents=True)
            self.progress("正在下載 GitHub 最新版…")
            _extract(_download(f"{API}/zipball/{info.latest}", MAX_ARCHIVE), release)
            self.progress("正在建立新版獨立環境…")
            _run([sys.executable, "-m", "venv", str(release / ".venv")], release)
            python = release / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            self.progress("正在安裝與驗證新版，可能需要數分鐘…")
            _run([str(python), "-m", "pip", "install", "-c", "constraints.txt", "."], release)
            _run([str(python), "-c", "import tkinter, fcn_checker.panel; import panel_bootstrap"], release)
            if (self.pointer.read_bytes() if self.pointer.exists() else None) != previous:
                raise UpdateError("其他程序已變更啟動版本，已停止切換。")
            self._write_pointer(
                json.dumps({"sha": info.latest, "release": str(release.relative_to(self.root))}).encode()
            )
            return InstalledUpdate(info.latest, release, previous)
        except OSError as error:
            raise UpdateError("更新檔案無法寫入；請確認磁碟空間與資料夾權限。舊版仍可使用。") from error
        finally:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                self.progress("更新鎖無法移除，下次更新前請聯絡維護人員確認。")

    def _write_pointer(self, content: bytes) -> None:
        temporary = self.local / f"pointer-{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(content)
            os.replace(temporary, self.pointer)
        finally:
            temporary.unlink(missing_ok=True)

    def restart(self, update: InstalledUpdate) -> None:
        python = update.release / ".venv" / ("Scripts/pythonw.exe" if sys.platform == "win32" else "bin/python")
        token = uuid.uuid4().hex
        ready = self.local / f"ready-{token}"
        process = None
        try:
            process = subprocess.Popen(
                [
                    str(python),
                    str(update.release / "launch_panel.pyw"),
                    "--managed-root",
                    str(self.root),
                    "--startup-token",
                    token,
                ],
                cwd=self.root,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if ready.exists() and process.poll() is None:
                    return
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            raise OSError("新版未完成視窗啟動")
        except OSError as error:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            # 只回復本次切換，避免覆蓋其他視窗後續更新。
            try:
                if json.loads(self.pointer.read_text(encoding="utf-8")).get("release") == str(
                    update.release.relative_to(self.root)
                ):
                    if update.previous is None:
                        self.pointer.unlink()
                    else:
                        self._write_pointer(update.previous)
            except (OSError, ValueError) as rollback_error:
                raise UpdateError(
                    "新版無法啟動且啟動版本無法回復；原視窗仍可使用，請聯絡維護人員。"
                ) from rollback_error
            raise UpdateError("新版無法啟動，已回復舊版；原視窗與核對結果仍可使用。") from error
        finally:
            try:
                ready.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--record-installation", type=Path, required=True)
    record_installation(parser.parse_args().record_installation)
