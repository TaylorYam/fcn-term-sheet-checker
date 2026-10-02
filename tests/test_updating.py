"""更新服務公開入口；模擬 HTTP／程序邊界，不存取真實 GitHub。"""

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from fcn_checker.updating import PanelUpdater, UpdateError, record_installation

OLD = "1" * 40
NEW = "2" * 40


def archive(extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name in (
            "pyproject.toml",
            "constraints.txt",
            "launch_panel.pyw",
            "panel_bootstrap.py",
            "src/fcn_checker/panel.py",
        ):
            zipped.writestr("repo-2222222/" + name, "synthetic")
        if extra:
            entry = zipfile.ZipInfo("placeholder")
            entry.filename = extra
            zipped.writestr(entry, "unsafe")
    return stream.getvalue()


def environment(tmp_path, monkeypatch, payload=None):
    local = tmp_path / ".local"
    local.mkdir()
    (local / "installed.json").write_text(json.dumps({"sha": OLD}))
    requests = []
    commands = []

    def response(request, timeout):
        requests.append(request.full_url)
        assert timeout == 30
        assert not request.has_header("Authorization")
        data = json.dumps({"sha": NEW}).encode() if request.full_url.endswith("commits/main") else payload or archive()
        return io.BytesIO(data)

    def run(args, **kwargs):
        commands.append(args)
        if args[1:3] == ["-m", "venv"]:
            scripts = Path(args[3]) / ("Scripts" if sys.platform == "win32" else "bin")
            scripts.mkdir(parents=True)
            (scripts / "pythonw.exe").write_text("synthetic")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("urllib.request.urlopen", response)
    # module imports urlopen directly; HTTP is the external boundary under test.
    monkeypatch.setattr("fcn_checker.updating.urlopen", response)
    monkeypatch.setattr(subprocess, "run", run)
    return PanelUpdater(tmp_path), requests, commands


def test_update_pins_commit_installs_isolated_and_preserves_local_files(tmp_path, monkeypatch):
    updater, requests, commands = environment(tmp_path, monkeypatch)
    for name in ("config/review_standard.toml", "data/order.xlsx", "runtime/核對紀錄/20300203-040506.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("local original")
    info = updater.check()
    assert info.current == OLD and info.latest == NEW and info.available
    installed = updater.install(info)
    assert requests == [
        "https://api.github.com/repos/TaylorYam/fcn-term-sheet-checker/commits/main",
        "https://api.github.com/repos/TaylorYam/fcn-term-sheet-checker/zipball/" + NEW,
    ]
    pointer = json.loads((tmp_path / ".local/current.json").read_text())
    assert pointer["sha"] == NEW
    assert installed.release.is_relative_to(tmp_path / ".local/releases")
    assert not (tmp_path / ".venv").exists()
    assert any(command[-6:] == ["-m", "pip", "install", "-c", "constraints.txt", "."] for command in commands)
    for name in ("config/review_standard.toml", "data/order.xlsx", "runtime/核對紀錄/20300203-040506.json"):
        assert (tmp_path / name).read_text() == "local original"
    assert not (tmp_path / ".local/update.lock").exists()
    assert not PanelUpdater(tmp_path).check().available


@pytest.mark.parametrize("code, message", [(404, "公開"), (403, "限制"), (429, "限制"), (500, "HTTP 500")])
def test_github_unavailable_is_actionable(tmp_path, monkeypatch, code, message):
    def denied(*args, **kwargs):
        raise HTTPError("https://api.github.com", code, "denied", {}, None)

    monkeypatch.setattr("fcn_checker.updating.urlopen", denied)
    with pytest.raises(UpdateError, match=message):
        PanelUpdater(tmp_path).check()
    assert not list(tmp_path.iterdir())


def test_offline_keeps_installation(tmp_path, monkeypatch):
    updater, _, _ = environment(tmp_path, monkeypatch)

    def offline(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr("fcn_checker.updating.urlopen", offline)
    with pytest.raises(UpdateError, match="連線"):
        updater.check()
    assert json.loads((tmp_path / ".local/installed.json").read_text())["sha"] == OLD


@pytest.mark.parametrize(
    "extra", ["repo/../../escape.py", "repo/config/../../escape.py", "repo/C:/escape.py", "repo/\\escape.py"]
)
def test_unsafe_archive_does_not_switch_version(tmp_path, monkeypatch, extra):
    updater, _, _ = environment(tmp_path, monkeypatch, archive(extra))
    with pytest.raises(UpdateError, match="不安全|結構不正確"):
        updater.install(updater.check())
    assert not (tmp_path / ".local/current.json").exists()
    assert not (tmp_path.parent / "escape.py").exists()
    assert not (tmp_path / ".local/update.lock").exists()


def test_installation_failure_retains_previous_pointer(tmp_path, monkeypatch):
    updater, _, _ = environment(tmp_path, monkeypatch)
    pointer = tmp_path / ".local/current.json"
    previous = json.dumps({"sha": OLD, "release": ".local/releases/previous"}).encode()
    pointer.write_bytes(previous)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(UpdateError, match="舊版仍可使用"):
        updater.install(updater.check())
    assert pointer.read_bytes() == previous


def test_switch_failure_retains_old_version(tmp_path, monkeypatch):
    updater, _, _ = environment(tmp_path, monkeypatch)

    def denied(*args):
        raise PermissionError("synthetic")

    monkeypatch.setattr("os.replace", denied)
    with pytest.raises(UpdateError, match="無法寫入"):
        updater.install(updater.check())
    assert not (tmp_path / ".local/current.json").exists()
    assert not list((tmp_path / ".local").glob("pointer-*.tmp"))


def test_restart_failure_rolls_back_and_retains_release(tmp_path, monkeypatch):
    updater, _, _ = environment(tmp_path, monkeypatch)
    installed = updater.install(updater.check())

    def failed(*args, **kwargs):
        raise OSError("synthetic cannot start")

    monkeypatch.setattr(subprocess, "Popen", failed)
    with pytest.raises(UpdateError, match="已回復舊版"):
        updater.restart(installed)
    assert not (tmp_path / ".local/current.json").exists()
    assert installed.release.exists()
    assert PanelUpdater(tmp_path).current == OLD


def test_restart_success_uses_new_python_and_root_settings(tmp_path, monkeypatch):
    updater, _, _ = environment(tmp_path, monkeypatch)
    installed = updater.install(updater.check())
    launches = []

    class Running:
        def poll(self):
            return None

    def start(args, **kwargs):
        launches.append(args)
        (tmp_path / ".local" / f"ready-{args[-1]}").write_text("ready")
        return Running()

    monkeypatch.setattr(subprocess, "Popen", start)
    updater.restart(installed)
    assert Path(launches[0][0]).is_relative_to(installed.release)
    assert launches[0][2:4] == ["--managed-root", str(tmp_path)]
    assert json.loads((tmp_path / ".local/current.json").read_text())["sha"] == NEW


def test_new_process_exits_without_ready_restores_previous_release(tmp_path, monkeypatch):
    updater, _, _ = environment(tmp_path, monkeypatch)
    pointer = tmp_path / ".local/current.json"
    previous = json.dumps({"sha": OLD, "release": ".local/releases/previous"}).encode()
    pointer.write_bytes(previous)
    installed = updater.install(updater.check())
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: SimpleNamespace(poll=lambda: 7))
    with pytest.raises(UpdateError, match="已回復舊版"):
        updater.restart(installed)
    assert pointer.read_bytes() == previous
    assert installed.release.exists()


def test_corrupt_archive_never_activates(tmp_path, monkeypatch):
    updater, _, commands = environment(tmp_path, monkeypatch, b"not a zip")
    with pytest.raises(UpdateError, match="壓縮檔無法讀取"):
        updater.install(updater.check())
    assert not commands
    assert not (tmp_path / ".local/current.json").exists()


def test_existing_update_lock_is_not_overwritten(tmp_path, monkeypatch):
    updater, _, commands = environment(tmp_path, monkeypatch)
    lock = tmp_path / ".local/update.lock"
    lock.write_text("other process")
    with pytest.raises(UpdateError, match="更新正在執行"):
        updater.install(updater.check())
    assert lock.read_text() == "other process"
    assert not commands


def test_fresh_zip_install_records_unknown_and_resets_active_release(tmp_path, monkeypatch):
    updater, _, _ = environment(tmp_path, monkeypatch)
    installed = updater.install(updater.check())

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(subprocess, "run", no_git)
    record_installation(tmp_path)
    assert PanelUpdater(tmp_path).current is None
    assert not (tmp_path / ".local/current.json").exists()
    assert installed.release.exists()


def test_installation_revision_supports_chinese_windows_path(tmp_path, monkeypatch):
    root = tmp_path / "中文安裝目錄"
    root.mkdir()

    def git(args, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        return SimpleNamespace(returncode=0, stdout=str(root) if args[-1] == "--show-toplevel" else OLD)

    monkeypatch.setattr(subprocess, "run", git)
    record_installation(root)
    assert PanelUpdater(root).current == OLD


def test_invalid_version_does_not_download_or_install(tmp_path, monkeypatch):
    monkeypatch.setattr("fcn_checker.updating.urlopen", lambda *a, **kw: io.BytesIO(b'{"sha":"../evil"}'))
    with pytest.raises(UpdateError, match="版本資料不正確"):
        PanelUpdater(tmp_path).check()
    assert not list(tmp_path.iterdir())


def test_bootstrap_refuses_pointer_outside_release_directory(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bootstrap", Path(__file__).resolve().parents[1] / "panel_bootstrap.py"
    )
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    local = tmp_path / ".local"
    local.mkdir()
    (local / "current.json").write_text(json.dumps({"sha": NEW, "release": "../outside"}))
    with pytest.raises(ValueError, match="路徑不正確"):
        bootstrap.run(str(tmp_path / "launch_panel.pyw"), [])
