from __future__ import annotations

import http.server
import socket
import subprocess
import sys
import threading
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
    assert backend.port == 8000
    assert frontend.command[-3:] == ("--host", "127.0.0.1", "--strictPort")
    assert frontend.cwd == root / "apps" / "web"
    assert frontend.ready_url == "http://127.0.0.1:5173"
    assert frontend.port == 5173


def test_build_services_explains_missing_frontend_dependencies(tmp_path: Path) -> None:
    (tmp_path / "apps" / "web").mkdir(parents=True)
    with pytest.raises(launcher.LauncherError, match="npm --prefix apps/web ci"):
        launcher._build_services(tmp_path, "/usr/bin/node")


@pytest.mark.parametrize(
    ("served_instance", "expected"),
    [("stale-instance", False), (None, False), ("current-instance", True)],
)
def test_http_ready_requires_the_current_launcher_instance(
    served_instance: str | None,
    expected: bool,
) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            if served_instance is not None:
                self.send_header(launcher.INSTANCE_ID_HEADER, served_instance)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/health"
        assert launcher._http_ready(url, "current-instance") is expected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_launcher_reports_ui_port_conflict_before_spawning_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path
    started: list[tuple[str, ...]] = []
    api = launcher._Service(
        name="Python API",
        command=("python", "-m", "brainlearn_server"),
        cwd=root,
        ready_url="http://127.0.0.1:8000/api/health",
        port=0,
    )

    def fake_popen(command: tuple[str, ...], **_kwargs: object) -> object:
        started.append(command)
        return object()

    monkeypatch.setattr(launcher, "_find_checkout_root", lambda _path: root)
    monkeypatch.setattr(launcher.shutil, "which", lambda _program: "/usr/bin/node")
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "_wait_until_ready", lambda *_args: None)
    monkeypatch.setattr(launcher, "_stop_processes", lambda _processes: None)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        ui_port = listener.getsockname()[1]
        ui = launcher._Service(
            name="React interface",
            command=("node", "vite.js"),
            cwd=root / "apps" / "web",
            ready_url=f"http://127.0.0.1:{ui_port}",
            port=ui_port,
        )
        monkeypatch.setattr(launcher, "_build_services", lambda _root, _node: (api, ui))

        assert launcher.main(["--check"]) == 1

    assert started == []
    error = capsys.readouterr().err
    assert "React interface" in error
    assert f"127.0.0.1:{ui_port} is already in use" in error


def test_launcher_check_starts_both_services_and_stops_without_opening_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _checkout(tmp_path)
    started: list[tuple[tuple[str, ...], Path]] = []
    stdout_options: list[object] = []
    instance_ids: list[str] = []
    ready: list[str] = []
    stopped: list[object] = []
    processes = [object(), object()]

    def fake_popen(command: tuple[str, ...], **kwargs: object) -> object:
        cwd = kwargs["cwd"]
        assert isinstance(cwd, Path)
        started.append((command, cwd))
        stdout_options.append(kwargs["stdout"])
        env = kwargs["env"]
        assert isinstance(env, dict)
        instance_ids.append(env[launcher.INSTANCE_ID_ENV])
        return processes[len(started) - 1]

    monkeypatch.setattr(launcher, "_find_checkout_root", lambda _path: root)
    monkeypatch.setattr(launcher.shutil, "which", lambda _program: "/usr/bin/node")
    monkeypatch.setattr(launcher, "_assert_port_available", lambda _service: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        launcher,
        "_wait_until_ready",
        lambda service, _process, _timeout, _instance: ready.append(service.ready_url),
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
    assert len(set(instance_ids)) == 1
    assert stopped == processes


def test_wait_until_ready_reports_a_child_that_exits_before_health() -> None:
    service = launcher._Service(
        name="Python API",
        command=("python",),
        cwd=Path("/tmp"),
        ready_url="http://127.0.0.1:8000/api/health",
        port=8000,
    )

    class ExitedProcess:
        def poll(self) -> int:
            return 1

    with pytest.raises(launcher.LauncherError, match="port is already in use"):
        launcher._wait_until_ready(service, ExitedProcess(), 1.0, "test-instance")  # type: ignore[arg-type]


def test_wait_until_ready_rechecks_child_after_http_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = launcher._Service(
        name="Python API",
        command=("python",),
        cwd=Path("/tmp"),
        ready_url="http://127.0.0.1:8000/api/health",
        port=8000,
    )

    class ExitsAfterProbe:
        calls = 0

        def poll(self) -> int | None:
            self.calls += 1
            return None if self.calls == 1 else 1

    monkeypatch.setattr(launcher, "_http_ready", lambda _url, _instance_id: True)
    with pytest.raises(launcher.LauncherError, match="exited with status 1 while becoming ready"):
        launcher._wait_until_ready(service, ExitsAfterProbe(), 1.0, "test-instance")  # type: ignore[arg-type]


def test_port_preflight_rejects_an_existing_loopback_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        service = launcher._Service(
            name="Python API",
            command=("python",),
            cwd=Path("/tmp"),
            ready_url=f"http://127.0.0.1:{port}/api/health",
            port=port,
        )

        with pytest.raises(launcher.LauncherError, match="already in use"):
            launcher._assert_port_available(service)


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
