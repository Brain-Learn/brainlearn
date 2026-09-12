"""File-based fault logging for the local BrainLearn service.

Unexpected failures (the ones that become HTTP 500) are appended to a
rolling set of plain-text log files so a researcher or maintainer can find
out what happened without rerunning the failure::

    <log-dir>/<YYYY-MM-DD>/<HH>.log

The directory defaults to ``error-log`` inside the service state directory
(``~/.cache/brainlearn`` unless ``BRAINLEARN_STATE_DIR`` is set) and can be
overridden with ``BRAINLEARN_ERROR_LOG_DIR``. Each entry records the UTC time,
the request line, and the full traceback. Session tokens and request bodies
are never written: bodies may contain research data, so only the content
length is recorded.
"""

import os
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

ENV_OVERRIDE = "BRAINLEARN_ERROR_LOG_DIR"
_DIRNAME = "error-log"
_LOG_GUARD = threading.Lock()


def error_log_dir() -> Path:
    """Return the base directory that holds per-day fault logs."""

    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser()
    state = os.environ.get("BRAINLEARN_STATE_DIR", "").strip()
    base = Path(state).expanduser() if state else Path.home() / ".cache" / "brainlearn"
    return base / _DIRNAME


def log_file_for(moment: datetime) -> Path:
    """Return the log file for a moment: ``<base>/<YYYY-MM-DD>/<HH>.log``."""

    day = moment.strftime("%Y-%m-%d")
    hour = moment.strftime("%H")
    return error_log_dir() / day / f"{hour}.log"


def log_fault(
    *,
    summary: str,
    method: str = "-",
    path: str = "-",
    client: str = "-",
    content_length: str = "-",
    exc: BaseException | None = None,
    moment: datetime | None = None,
) -> Path:
    """Append one fault entry and return the file it was written to."""

    at = moment or datetime.now(UTC)
    target = log_file_for(at)
    lines = [
        f"[{at.isoformat()}] FAULT {summary}",
        f"request: {method} {path} (client {client}, body {content_length} bytes)",
    ]
    if exc is not None:
        lines.append("".join(traceback.format_exception(exc)).rstrip())
    lines.append("")
    text = "\n".join(lines)
    with _LOG_GUARD:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return target


class ErrorLoggingMiddleware(BaseHTTPMiddleware):
    """Record unhandled request failures before they become HTTP 500."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except BaseException as exc:
            client = request.client.host if request.client else "-"
            log_fault(
                summary=f"{type(exc).__name__}: {exc}",
                method=request.method,
                path=request.url.path,
                client=client,
                content_length=request.headers.get("content-length", "-"),
                exc=exc,
            )
            raise
