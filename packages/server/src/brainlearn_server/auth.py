"""Per-session token authentication for the loopback BrainLearn service."""

import os
import secrets

from fastapi import Header, HTTPException

_ENV_VAR = "BRAINLEARN_SESSION_TOKEN"


def _initial_token() -> str:
    configured = os.environ.get(_ENV_VAR, "").strip()
    if configured:
        return configured
    return secrets.token_urlsafe(32)


SESSION_TOKEN: str = _initial_token()


def get_session_token() -> str:
    return SESSION_TOKEN


def reset_session_token_for_tests(token: str) -> None:
    """Override the session token inside tests. Not used by the running service."""

    global SESSION_TOKEN
    SESSION_TOKEN = token


async def require_session_token(
    authorization: str | None = Header(default=None),
    x_brainlearn_token: str | None = Header(default=None),
) -> None:
    """Require the per-process session token on filesystem-touching endpoints."""

    candidate: str | None = None
    if authorization is not None and authorization.startswith("Bearer "):
        candidate = authorization[len("Bearer ") :].strip()
    elif x_brainlearn_token is not None:
        candidate = x_brainlearn_token.strip()
    if not candidate or secrets.compare_digest(candidate, SESSION_TOKEN) is False:
        raise HTTPException(
            status_code=401,
            detail=(
                "A valid session token is required. "
                "Start the BrainLearn service, copy the session token from its "
                "startup log, and send it as "
                "'Authorization: Bearer <token>'."
            ),
        )
