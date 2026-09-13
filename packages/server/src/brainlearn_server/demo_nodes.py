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
from typing import Any, Literal


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

    spec = DEMO_NODES["demo.delay"].parameters["seconds"]
    raw = ctx.parameters.get("seconds", spec.default)
    lower = spec.minimum if spec.minimum is not None else 0.0
    upper = spec.maximum if spec.maximum is not None else MAX_DELAY_SECONDS
    total = max(lower, min(float(raw), upper))
    deadline = time.monotonic() + total
    while time.monotonic() < deadline:
        _require_cancel(ctx)
        time.sleep(0.02)
    _require_cancel(ctx)
    return []


def copy_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Write a small canned text output into staging."""

    _require_cancel(ctx)
    text = str(ctx.parameters.get("text", DEMO_NODES["demo.copy"].parameters["text"].default))
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    (ctx.staging_dir / "output.txt").write_text(text, encoding="utf-8")
    return [StagedOutput(port_id="output", relative_path="output.txt", media_type="text/plain")]


def fail_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Fail deterministically with a controlled message."""

    _require_cancel(ctx)
    raise ControlledFailure(
        str(ctx.parameters.get("message", DEMO_NODES["demo.fail"].parameters["message"].default))
    )


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
class DemoParameter:
    """One execution-relevant parameter of a demonstration node.

    The single contract for a parameter's name, type, default, and bounds:
    handlers interpret values through it and the registry derives its UI
    parameter schemas from it, so the two cannot drift apart. Parameters
    affect content identity and execution; they are not display metadata.
    """

    id: str
    label: str
    value_type: Literal["string", "number"]
    default: Any
    required: bool = False
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class DemoNodeManifest:
    """One authoritative execution manifest per demonstration node type.

    ``inputs`` and ``outputs`` map port IDs to required flags: True means the
    port is required (must be declared, and for inputs must be connected via
    an edge; for outputs must be emitted on success), False means optional
    (may be declared/emitted but is never required). ``parameters`` carries
    the execution-relevant parameter contract shared with the registry.
    """

    version: str
    inputs: dict[str, bool]
    outputs: dict[str, bool]
    handler: DemoAdapter
    parameters: dict[str, DemoParameter] = field(default_factory=dict)


DEMO_NODES: dict[str, DemoNodeManifest] = {
    "demo.delay": DemoNodeManifest(
        version="0.1.0",
        inputs={},
        outputs={"out": False},
        handler=delay_adapter,
        parameters={
            "seconds": DemoParameter(
                id="seconds",
                label="Delay (seconds)",
                value_type="number",
                default=0.1,
                description="Cooperative wait before completing. Clamped to a safe maximum.",
                minimum=0.0,
                maximum=MAX_DELAY_SECONDS,
            )
        },
    ),
    "demo.copy": DemoNodeManifest(
        version="0.1.0",
        inputs={"in": False},
        outputs={"output": True},
        handler=copy_adapter,
        parameters={
            "text": DemoParameter(
                id="text",
                label="Text",
                value_type="string",
                default="brainlearn-demo",
                description="Canned text written to the output file.",
            )
        },
    ),
    "demo.fail": DemoNodeManifest(
        version="0.1.0",
        inputs={},
        outputs={"out": False},
        handler=fail_adapter,
        parameters={
            "message": DemoParameter(
                id="message",
                label="Failure message",
                value_type="string",
                default="demonstration failure",
                description="Message reported when this node fails on purpose.",
            )
        },
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
