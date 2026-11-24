from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from brainlearn_server import launcher


def _checkout(tmp_path: Path) -> Path:
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    web_root = tmp_path / "apps" / "web"
    vite_entry = web_root / "node_modules" / "vite" / "bin" / "vite.js"
    vite_entry.parent.mkdir(parents=True)
    vite_entry.write_text("", encoding="utf-8")
    return tmp_path


def test_build_services_keeps_api_and_interface_on_loopback(tmp_path: Path) -> None:
    root = _checkout(tmp_path)

    backend, frontend = launcher._build_services(root, "/usr/bin/node")

    assert backend.command == (sys.executable, "-m", "brainlearn_server")
    assert backend.cwd == root
    assert backend.ready_url == "http://127.0.0.1:8000/api/health"
    assert frontend.command[-3:] == ("--host", "127.0.0.1", "--strictPort")
    assert frontend.cwd == root / "apps" / "web"
    assert frontend.ready_url == "http://127.0.0.1:5173"


def test_build_services_explains_missing_frontend_dependencies(tmp_path: Path) -> None:
    (tmp_path / "apps" / "web").mkdir(parents=True)
    with pytest.raises(launcher.LauncherError, match="npm --prefix apps/web ci"):
        launcher._build_services(tmp_path, "/usr/bin/node")


def test_launcher_check_starts_both_services_and_stops_without_opening_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _checkout(tmp_path)
    started: list[tuple[tuple[str, ...], Path]] = []
    stdout_options: list[object] = []
    ready: list[str] = []
    stopped: list[object] = []
    processes = [object(), object()]

    def fake_popen(command: tuple[str, ...], **kwargs: object) -> object:
        cwd = kwargs["cwd"]
        assert isinstance(cwd, Path)
        started.append((command, cwd))
        stdout_options.append(kwargs["stdout"])
        return processes[len(started) - 1]

    monkeypatch.setattr(launcher, "_find_checkout_root", lambda _path: root)
    monkeypatch.setattr(launcher.shutil, "which", lambda _program: "/usr/bin/node")
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        launcher,
        "_wait_until_ready",
        lambda service, _process, _timeout: ready.append(service.ready_url),
    )
    monkeypatch.setattr(launcher, "_stop_processes", lambda items: stopped.extend(items))
    monkeypatch.setattr(
        launcher.webbrowser,
        "open",
        lambda _url: pytest.fail("check mode must not open a browser"),
    )

    assert launcher.main(["--check"]) == 0

    assert [url for url in ready] == [
        "http://127.0.0.1:8000/api/health",
        "http://127.0.0.1:5173",
    ]
    assert [cwd for _command, cwd in started] == [root, root / "apps" / "web"]
    assert stdout_options == [subprocess.DEVNULL, None]
    assert stopped == processes


def test_wait_until_ready_reports_a_child_that_exits_before_health() -> None:
    service = launcher._Service(
        name="Python API",
        command=("python",),
        cwd=Path("/tmp"),
        ready_url="http://127.0.0.1:8000/api/health",
    )

    class ExitedProcess:
        def poll(self) -> int:
            return 1

    with pytest.raises(launcher.LauncherError, match="port is already in use"):
        launcher._wait_until_ready(service, ExitedProcess(), 1.0)  # type: ignore[arg-type]


def test_stopping_services_kills_a_process_that_ignores_termination() -> None:
    calls: list[str] = []

    class StubbornProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            calls.append("terminate")

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                raise subprocess.TimeoutExpired("brainlearn", timeout)
            calls.append("wait")
            return -9

        def kill(self) -> None:
            calls.append("kill")

    launcher._stop_processes([StubbornProcess()])  # type: ignore[list-item]

    assert calls == ["terminate", "kill", "wait"]
