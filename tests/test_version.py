"""程式版本（核對紀錄用）：開發用 Git 的 HEAD，或 setup_panel.cmd 安裝時記錄的版本。"""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fcn_checker.version import program_commit, record_installation

OLD = "1" * 40


def test_program_commit_prefers_git_then_installed_record(tmp_path):
    local = tmp_path / ".local"
    local.mkdir()
    (local / "installed.json").write_text(json.dumps({"sha": OLD}))
    installed = tmp_path / ".venv" / "Lib" / "site-packages" / "fcn_checker"

    assert program_commit(tmp_path, package=installed) == OLD, "根目錄安裝：取安裝時記錄的 commit"
    assert program_commit(tmp_path / "elsewhere", package=installed) is None, "沒有版本紀錄時為 None"

    repo = Path(__file__).resolve().parents[1]
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode == 0 and (repo / ".git").exists():
        assert program_commit(tmp_path, package=repo / "src" / "fcn_checker") == head.stdout.strip(), "開發用 Git"


def test_installed_package_inside_a_git_checkout_uses_the_installed_record(tmp_path):
    """維護者的實際環境：專案是 Git，套件裝在 .venv 裡。commit 取安裝時記錄的版本，不是之後 git pull 的 HEAD。"""
    if shutil.which("git") is None:
        pytest.skip("沒有 git")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    local = tmp_path / ".local"
    local.mkdir()
    (local / "installed.json").write_text(json.dumps({"sha": OLD}))
    installed = tmp_path / ".venv" / "Lib" / "site-packages" / "fcn_checker"
    installed.mkdir(parents=True)
    assert program_commit(tmp_path, package=installed) == OLD


def test_invalid_installed_record_is_unknown(tmp_path):
    local = tmp_path / ".local"
    local.mkdir()
    (local / "installed.json").write_text(json.dumps({"sha": "../evil"}))
    assert program_commit(tmp_path, package=tmp_path / ".venv" / "fcn_checker") is None


def test_installation_without_git_records_unknown(tmp_path, monkeypatch):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(subprocess, "run", no_git)
    record_installation(tmp_path)
    assert json.loads((tmp_path / ".local" / "installed.json").read_text()) == {"sha": None}
    assert program_commit(tmp_path, package=tmp_path / ".venv" / "fcn_checker") is None


def test_installation_revision_supports_chinese_windows_path(tmp_path, monkeypatch):
    root = tmp_path / "中文安裝目錄"
    root.mkdir()

    def git(args, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        return SimpleNamespace(returncode=0, stdout=str(root) if args[-1] == "--show-toplevel" else OLD)

    monkeypatch.setattr(subprocess, "run", git)
    record_installation(root)
    monkeypatch.undo()
    assert program_commit(root, package=root / ".venv" / "fcn_checker") == OLD
