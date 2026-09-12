"""Deterministic content identities for execution caching and replay.

An identity names *what was computed*, never *when, where on screen, or by
which absolute path*. Only the declared computation inputs enter the hash:
input artifact/content identities keyed by port, parameter values, node
implementation identity, environment identity, seeds, and execution settings.
Timestamps, canvas positions, display labels, and absolute project paths are
excluded by construction: the helpers below accept nothing else.

Canonical form is compact JSON with sorted mapping keys and fixed separators,
encoded as UTF-8, then hashed with SHA-256 under a ``brainlearn-v1`` domain
prefix. Lists keep caller-defined order (dependency lists, port sequences);
mappings hash identically regardless of insertion order. Numerically equal
numbers share one representation: integral floats become ints (``1.0`` and
``1`` hash identically, as do ``-0.0`` and ``0``); non-integral floats keep
their value; non-finite floats are rejected.
"""

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

IDENTITY_PREFIX = "brainlearn-v1"
SCHEMA_VERSION = "1.0"


def _normalize_numbers(value: Any) -> Any:
    """Map numerically equal JSON numbers to one representative.

    Integral floats become ints (``1.0`` -> ``1``, ``-0.0`` -> ``0``) so the
    same parameter hashes identically whether it arrived as JSON, Python, or
    generated code. Non-integral floats keep their value; non-finite floats
    are rejected as non-JSON. Booleans are not numbers here and pass through.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Identity payloads must not contain non-finite numbers.")
        return int(value) if value.is_integer() else value
    if isinstance(value, Mapping):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_numbers(item) for item in value]
    return value


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialize a payload deterministically, rejecting non-JSON values."""

    _reject_non_json(payload)
    normalized = _normalize_numbers(payload)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _reject_non_json(value: Any) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Identity payloads must not contain non-finite numbers.")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"Identity payload mapping keys must be strings, got {type(key).__name__}."
                )
            _reject_non_json(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_json(item)
        return
    raise TypeError(
        "Identity payloads must contain only JSON values "
        f"(null, boolean, number, string, list, mapping), got {type(value).__name__}."
    )


def content_identity(domain: str, payload: Mapping[str, Any]) -> str:
    """Hash a canonical payload under a domain prefix.

    ``domain`` is one of ``workflow``, ``node``, ``environment``, or
    ``artifact``. The returned string is ``brainlearn-v1:<domain>:<sha256>``.
    """

    if not domain or "/" in domain or ":" in domain or " " in domain:
        raise ValueError(f"Identity domain must be a simple name, got {domain!r}.")
    digest = hashlib.sha256(
        f"{IDENTITY_PREFIX}:{domain}:".encode() + canonical_json_bytes(payload)
    ).hexdigest()
    return f"{IDENTITY_PREFIX}:{domain}:{digest}"


def node_content_identity(
    *,
    node_type: str,
    node_version: str,
    inputs: Mapping[str, str],
    parameters: Mapping[str, Any],
    environment_identity: str,
    seed: int | None,
    settings: Mapping[str, Any],
) -> str:
    """Identity for one node execution attempt.

    Only computation-changing inputs are accepted. There is deliberately no
    parameter for timestamps, canvas positions, display labels, or absolute
    paths, so those values cannot affect cache reuse.
    """

    if not node_type:
        raise ValueError("node_type must be a non-empty string.")
    if not node_version:
        raise ValueError("node_version must be a non-empty string.")
    if not environment_identity:
        raise ValueError("environment_identity must be a non-empty string.")
    return content_identity(
        "node",
        {
            "schema_version": SCHEMA_VERSION,
            "node_type": node_type,
            "node_version": node_version,
            "inputs": dict(inputs),
            "parameters": dict(parameters),
            "environment": environment_identity,
            "seed": seed,
            "settings": dict(settings),
        },
    )


def environment_identity(payload: Mapping[str, Any]) -> str:
    """Identity for the environment a run executed in.

    The embedded schema marker always wins: a caller-provided
    ``schema_version`` is ignored so the marker cannot be spoofed while
    keeping the trusted prefix.
    """

    merged = {**dict(payload), "schema_version": SCHEMA_VERSION}
    return content_identity("environment", merged)
