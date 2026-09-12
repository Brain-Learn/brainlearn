"""Allowlisted demonstration-node adapters for the Step 4C worker.

Only these internal functions may execute. The worker never accepts shell
commands, module paths, or arbitrary Python from API payloads: an unknown
node type fails deterministically with ``unsupported_node_type`` instead of
executing anything.

Adapters receive a :class:`NodeContext` scoped to one node attempt and either
return staged outputs or raise one of the control exceptions. Staging lives
beneath the run directory; promotion to successful artifacts happens only in
the worker after success.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ControlledFailure(Exception):
    """A demonstration node failed in a controlled, reportable way."""


class CancelledByUser(Exception):
    """Cooperative cancellation observed by an adapter."""


class ReviewPauseRequested(Exception):
    """A demonstration node requests human review before completing."""


@dataclass
class StagedOutput:
    port_id: str
    relative_path: str
    media_type: str = "application/octet-stream"


@dataclass
class NodeContext:
    node_id: str
    node_type: str
    parameters: dict[str, Any]
    inputs: dict[str, Path]
    staging_dir: Path
    cancel: threading.Event = field(default_factory=threading.Event)


DemoAdapter = Callable[[NodeContext], list[StagedOutput]]

MAX_DELAY_SECONDS = 30.0


def _require_cancel(ctx: NodeContext) -> None:
    if ctx.cancel.is_set():
        raise CancelledByUser(f"Node {ctx.node_id!r} observed cancellation.")


def delay_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Wait cooperatively; produces no artifacts."""

    raw = ctx.parameters.get("seconds", 0.1)
    total = max(0.0, min(float(raw), MAX_DELAY_SECONDS))
    deadline = time.monotonic() + total
    while time.monotonic() < deadline:
        _require_cancel(ctx)
        time.sleep(0.02)
    _require_cancel(ctx)
    return []


def copy_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Write a small canned text output into staging."""

    _require_cancel(ctx)
    text = str(ctx.parameters.get("text", "brainlearn-demo"))
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    (ctx.staging_dir / "output.txt").write_text(text, encoding="utf-8")
    return [StagedOutput(port_id="output", relative_path="output.txt", media_type="text/plain")]


def fail_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Fail deterministically with a controlled message."""

    _require_cancel(ctx)
    raise ControlledFailure(str(ctx.parameters.get("message", "demonstration failure")))


def review_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Pause for an explicit researcher decision."""

    _require_cancel(ctx)
    raise ReviewPauseRequested(f"Node {ctx.node_id!r} awaits review.")


def relay_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Forward one input's bytes, recording the exact source path consumed."""

    _require_cancel(ctx)
    try:
        source = ctx.inputs["in"]
    except KeyError:
        raise ControlledFailure(
            f"Node {ctx.node_id!r} requires input port 'in' but none resolved."
        ) from None
    data = source.read_bytes()
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    (ctx.staging_dir / "output.txt").write_text(
        f"{source}\n{data.decode('utf-8', errors='replace')}", encoding="utf-8"
    )
    return [StagedOutput(port_id="output", relative_path="output.txt", media_type="text/plain")]


@dataclass(frozen=True)
class DemoNodeManifest:
    """One authoritative execution manifest per demonstration node type.

    ``inputs`` and ``outputs`` map port IDs to required flags: True means the
    port is required (must be declared, and for inputs must be connected via
    an edge; for outputs must be emitted on success), False means optional
    (may be declared/emitted but is never required).
    """

    version: str
    inputs: dict[str, bool]
    outputs: dict[str, bool]
    handler: DemoAdapter


DEMO_NODES: dict[str, DemoNodeManifest] = {
    "demo.delay": DemoNodeManifest(
        version="0.1.0", inputs={}, outputs={"out": False}, handler=delay_adapter
    ),
    "demo.copy": DemoNodeManifest(
        version="0.1.0",
        inputs={"in": False},
        outputs={"output": True},
        handler=copy_adapter,
    ),
    "demo.fail": DemoNodeManifest(
        version="0.1.0", inputs={}, outputs={"out": False}, handler=fail_adapter
    ),
    "demo.review": DemoNodeManifest(
        version="0.1.0", inputs={}, outputs={"out": False}, handler=review_adapter
    ),
    "demo.relay": DemoNodeManifest(
        version="0.1.0",
        inputs={"in": True},
        outputs={"output": True},
        handler=relay_adapter,
    ),
}
