"""Host and Origin validation for the local-only BrainLearn service."""

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
# ``testserver`` is Starlette's TestClient default host. It is allowed so the
# backend contract tests can exercise this middleware without disabling it.
ALLOWED_TEST_HOSTS = frozenset({"testserver"})
ALLOWED_HOSTS = ALLOWED_LOOPBACK_HOSTS | ALLOWED_TEST_HOSTS


def _hostname(host_header: str) -> str:
    value = host_header.strip().lower()
    if value.startswith("["):
        bracketed = value.split("]", maxsplit=1)
        return bracketed[0] + "]"
    return value.split(":", maxsplit=1)[0]


def is_allowed_host(host_header: str | None) -> bool:
    if not host_header:
        return False
    return _hostname(host_header) in ALLOWED_HOSTS


def is_allowed_origin(origin: str | None) -> bool:
    if origin is None or origin == "":
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in ALLOWED_HOSTS


class HostOriginValidationMiddleware(BaseHTTPMiddleware):
    """Reject non-loopback Host/Origin values before requests reach handlers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.headers.get("host")
        if not is_allowed_host(host):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "BrainLearn binds to loopback only. Send a Host of "
                        "127.0.0.1 or localhost; remote hosts are rejected."
                    )
                },
            )
        origin = request.headers.get("origin")
        if not is_allowed_origin(origin):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "The Origin header is not a trusted loopback origin. "
                        "Open BrainLearn from http://127.0.0.1:5173 or "
                        "http://localhost:5173."
                    )
                },
            )
        return await call_next(request)
