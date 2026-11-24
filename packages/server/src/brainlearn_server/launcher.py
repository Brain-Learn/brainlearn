"""Source-checkout launcher for the local API and React development UI."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

API_URL = "http://127.0.0.1:8000"
UI_URL = "http://127.0.0.1:5173"
HEALTH_URL = f"{API_URL}/api/health"
INSTANCE_ID_ENV = "BRAINLEARN_LAUNCH_INSTANCE_ID"
INSTANCE_ID_HEADER = "X-BrainLearn-Launcher-Instance"


class LauncherError(RuntimeError):
    """An actionable problem prevented the local app from starting."""


@dataclass(frozen=True)
class _Service:
    name: str
    command: tuple[str, ...]
    cwd: Path
    ready_url: str
    port: int


def _find_checkout_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "uv.lock").is_file() and (candidate / "apps/web/package.json").is_file():
            return candidate
    raise LauncherError(
        "This launcher spike requires a BrainLearn source checkout. "
        "A packaged installer is not available yet."
    )


def _build_services(root: Path, node: str) -> tuple[_Service, _Service]:
    web_root = root / "apps" / "web"
    vite_entry = web_root / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_entry.is_file():
        raise LauncherError(
            "The React dependencies are missing. Run `npm --prefix apps/web ci` "
            "from the repository root, then launch BrainLearn again."
        )

    backend = _Service(
        name="Python API",
        command=(sys.executable, "-m", "brainlearn_server"),
        cwd=root,
        ready_url=HEALTH_URL,
        port=8000,
    )
    frontend = _Service(
        name="React interface",
        command=(node, str(vite_entry), "--host", "127.0.0.1", "--strictPort"),
        cwd=web_root,
        ready_url=UI_URL,
        port=5173,
    )
    return backend, frontend


def _http_ready(url: str, instance_id: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=0.5) as response:
            status = response.getcode()
            return (
                status is not None
                and 200 <= status < 400
                and response.headers.get(INSTANCE_ID_HEADER) == instance_id
            )
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def _assert_port_available(service: _Service) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", service.port))
    except OSError as exc:
        raise LauncherError(
            f"Cannot start the {service.name}: 127.0.0.1:{service.port} is already in use. "
            "Stop the other local service and try again."
        ) from exc


def _wait_until_ready(
    service: _Service,
    process: subprocess.Popen[bytes],
    timeout_seconds: float,
    instance_id: str,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        exit_code = process.poll()
        if exit_code is not None:
            raise LauncherError(
                f"The {service.name} exited with status {exit_code} before becoming ready. "
                f"Check whether its port is already in use ({service.ready_url})."
            )
        if _http_ready(service.ready_url, instance_id):
            exit_code = process.poll()
            if exit_code is None:
                return
            raise LauncherError(
                f"The {service.name} exited with status {exit_code} while becoming ready "
                f"at {service.ready_url}."
            )
        if time.monotonic() >= deadline:
            raise LauncherError(
                f"The {service.name} did not become ready within {timeout_seconds:g} seconds "
                f"at {service.ready_url}. Check the service output above and try again."
            )
        time.sleep(0.2)


def _stop_processes(processes: Sequence[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _positive_seconds(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number of seconds") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brainlearn-launch",
        description=(
            "Start BrainLearn's local Python API and React interface from a source checkout."
        ),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="start both services without opening a browser",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify both local services start, then stop them and exit",
    )
    parser.add_argument(
        "--startup-timeout",
        type=_positive_seconds,
        default=30.0,
        metavar="SECONDS",
        help="maximum time to wait for each service (default: 30)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = _find_checkout_root(Path(__file__).resolve())
        node = shutil.which("node")
        if node is None:
            raise LauncherError(
                "Node.js was not found. Install the Node.js version listed in "
                "website/installation.md, then try again."
            )
        services = _build_services(root, node)
    except LauncherError as exc:
        print(f"BrainLearn launcher: {exc}", file=sys.stderr)
        return 2

    processes: list[subprocess.Popen[bytes]] = []
    instance_id = uuid.uuid4().hex
    child_environment = os.environ.copy()
    child_environment[INSTANCE_ID_ENV] = instance_id
    try:
        for service in services:
            _assert_port_available(service)
            service_stdout = (
                subprocess.DEVNULL if args.check and service.name == "Python API" else None
            )
            process = subprocess.Popen(
                service.command,
                cwd=service.cwd,
                stdin=subprocess.DEVNULL,
                stdout=service_stdout,
                env=child_environment,
            )
            processes.append(process)
            _wait_until_ready(service, process, args.startup_timeout, instance_id)
            print(f"{service.name} ready at {service.ready_url}", flush=True)

        if args.check:
            print("BrainLearn launcher check passed; stopping both local services.", flush=True)
            return 0

        if not args.no_browser and not webbrowser.open(UI_URL):
            print(f"Open the BrainLearn interface in your browser: {UI_URL}", flush=True)
        print(
            "BrainLearn is running locally. The API terminal output contains the session token. "
            "Press Ctrl-C to stop both services.",
            flush=True,
        )
        while True:
            for service, process in zip(services, processes, strict=True):
                exit_code = process.poll()
                if exit_code is not None:
                    raise LauncherError(
                        f"The {service.name} stopped unexpectedly with status {exit_code}."
                    )
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\nStopping BrainLearn services.", flush=True)
        return 0
    except LauncherError as exc:
        print(f"BrainLearn launcher: {exc}", file=sys.stderr)
        return 1
    finally:
        _stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
