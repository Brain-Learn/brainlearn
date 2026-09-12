"""Step 4A: execution-contract round-trips, migrations, identities, invariants."""

import json
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    RUN_TERMINAL_STATES,
    ArtifactRecord,
    CacheEntry,
    EnvironmentRecord,
    FailureRecord,
    NodeRunRecord,
    NodeRunState,
    ReviewPauseRecord,
    RunEvent,
    RunRecord,
    RunState,
    counts_as_execution,
    migrate_artifact_dict,
    migrate_cache_entry_dict,
    migrate_environment_dict,
    migrate_event_dict,
    migrate_failure_dict,
    migrate_node_run_dict,
    migrate_review_pause_dict,
    migrate_run_dict,
    node_content_identity,
)
from brainlearn_core.identity import (
    canonical_json_bytes,
    content_identity,
    environment_identity,
)
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _node_payload() -> dict[str, Any]:
    return {
        "node_type": "eeg.filter",
        "node_version": "0.1.0",
        "inputs": {"raw": "brainlearn-v1:node:abc"},
        "parameters": {"low_hz": 1.0, "high_hz": 40.0},
        "environment_identity": "brainlearn-v1:environment:def",
        "seed": 7,
        "settings": {"timeout_seconds": 600},
    }


def _node_record(state: str, **overrides: Any) -> dict[str, Any]:
    record = _load("node-run-1.0.json")
    record["id"] = f"node-run-{state}"
    record["state"] = state
    if state in {"queued", "dependency_skipped", "cache_reused"}:
        record["attempt"] = 0
        record["started_at"] = None
        record["finished_at"] = None
        record["artifacts"] = []
    for artifact in record["artifacts"]:
        artifact["produced_by_node"] = record["id"]
    record.update(overrides)
    return record


def _run_with_nodes(nodes: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    raw = _load("run-1.0.json")
    raw["node_runs"] = nodes
    raw["events"] = []
    raw.update(overrides)
    return raw


def _bare_node(node_id: str, state: str = "succeeded") -> dict[str, Any]:
    record = _node_record(state, started_at=None, finished_at=None, artifacts=[])
    record["id"] = node_id
    record["node_id"] = node_id
    record["dependencies"] = []
    if state == "succeeded":
        record["started_at"] = "2026-09-12T09:01:00+00:00"
        record["finished_at"] = "2026-09-12T09:02:00+00:00"
    return record


# -- round-trips -------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "model"),
    [
        ("run-1.0.json", RunRecord),
        ("node-run-1.0.json", NodeRunRecord),
        ("artifact-1.0.json", ArtifactRecord),
        ("environment-1.0.json", EnvironmentRecord),
        ("run-event-1.0.json", RunEvent),
        ("failure-1.0.json", FailureRecord),
        ("review-pause-1.0.json", ReviewPauseRecord),
        ("cache-entry-1.0.json", CacheEntry),
    ],
)
def test_execution_fixtures_round_trip(fixture: str, model: Any) -> None:
    raw = _load(fixture)
    record = model.model_validate(raw)
    assert record.schema_version == "1.0"
    assert model.model_validate_json(record.model_dump_json()) == record


# -- migrations --------------------------------------------------------


@pytest.mark.parametrize(
    "migrate",
    [
        migrate_run_dict,
        migrate_node_run_dict,
        migrate_artifact_dict,
        migrate_environment_dict,
        migrate_event_dict,
        migrate_failure_dict,
        migrate_review_pause_dict,
        migrate_cache_entry_dict,
    ],
)
def test_migrations_reject_unknown_versions(migrate: Any) -> None:
    with pytest.raises(ValueError, match="Unsupported schema_version"):
        migrate({"schema_version": "9.9"})


# -- identity stability -------------------------------------------------


def test_identical_payloads_produce_identical_identities() -> None:
    assert node_content_identity(**_node_payload()) == node_content_identity(**_node_payload())


def test_mapping_insertion_order_does_not_change_identity() -> None:
    payload = _node_payload()
    reordered = {
        "settings": dict(reversed(list(payload["settings"].items()))),
        "seed": payload["seed"],
        "environment_identity": payload["environment_identity"],
        "parameters": dict(reversed(list(payload["parameters"].items()))),
        "inputs": dict(reversed(list(payload["inputs"].items()))),
        "node_version": payload["node_version"],
        "node_type": payload["node_type"],
    }
    assert node_content_identity(**payload) == node_content_identity(**reordered)
    assert canonical_json_bytes({"b": 1, "a": 2}) == canonical_json_bytes({"a": 2, "b": 1})


@pytest.mark.parametrize(
    "mutation",
    [
        {"node_type": "eeg.epochs"},
        {"node_version": "0.2.0"},
        {"inputs": {"raw": "brainlearn-v1:node:other"}},
        {"parameters": {"low_hz": 2.0, "high_hz": 40.0}},
        {"environment_identity": "brainlearn-v1:environment:other"},
        {"seed": 8},
        {"seed": None},
        {"settings": {"timeout_seconds": 300}},
    ],
)
def test_changing_any_declared_input_changes_identity(mutation: dict[str, Any]) -> None:
    baseline = node_content_identity(**_node_payload())
    assert node_content_identity(**(_node_payload() | mutation)) != baseline


def test_cache_entry_fixture_recomputes_stable_identity() -> None:
    raw = _load("cache-entry-1.0.json")
    entry = CacheEntry.model_validate(migrate_cache_entry_dict(raw))
    recomputed = node_content_identity(
        node_type=entry.node_type,
        node_version=entry.node_version,
        inputs=entry.inputs,
        parameters=entry.parameters,
        environment_identity=entry.environment_identity,
        seed=entry.seed,
        settings=entry.settings,
    )
    assert recomputed == entry.content_identity
    assert CacheEntry.model_validate_json(entry.model_dump_json()) == entry


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("node_type", "demo.relay"),
        ("node_version", "9.9.9"),
        ("inputs", {"in": "brainlearn-v1:node:" + "d" * 64}),
        ("parameters", {"text": "mutated"}),
        ("environment_identity", "brainlearn-v1:environment:" + "e" * 64),
        ("seed", 1),
        ("settings", {"input_sources": {}, "declared_outputs": ["output", "extra"]}),
    ],
)
def test_cache_entry_rejects_stale_identity_per_field(field: str, value: Any) -> None:
    raw = _load("cache-entry-1.0.json")
    raw[field] = value
    with pytest.raises(ValidationError, match="content_identity"):
        CacheEntry.model_validate(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        {"node_type": "eeg.epochs"},
        {"node_version": "0.2.0"},
        {"inputs": {"raw": "brainlearn-v1:node:" + "b" * 64}},
        {"parameters": {"low_hz": 2.0, "high_hz": 40.0}},
        {
            "environment_identity": "brainlearn-v1:environment:" + "c" * 64,
            "seed": 7,
        },
        {"seed": 8},
        {"settings": {"timeout_seconds": 300}},
    ],
)
def test_stale_content_identity_is_rejected(mutation: dict[str, Any]) -> None:
    record = _node_record("succeeded", **mutation)
    with pytest.raises(ValidationError, match="does not match"):
        NodeRunRecord.model_validate(record)


def test_substituted_environment_identity_is_rejected() -> None:
    other_env = environment_identity({"os": "OtherOS"})
    record = _node_record("succeeded", environment_identity=other_env)
    with pytest.raises(ValidationError, match="does not match"):
        NodeRunRecord.model_validate(record)


def test_edited_run_environment_is_rejected() -> None:
    raw = _load("run-1.0.json")
    raw["environment"] = dict(raw["environment"], python_version="3.11.0")
    with pytest.raises(ValidationError, match="does not match the run environment"):
        RunRecord.model_validate(raw)


def test_run_rejects_duplicate_dependencies() -> None:
    first, second = _bare_node("upstream"), _bare_node("downstream")
    second["dependencies"] = ["upstream", "upstream"]
    with pytest.raises(ValidationError, match="duplicate dependencies"):
        RunRecord.model_validate(_run_with_nodes([first, second]))


def test_queued_nodes_require_attempt_zero() -> None:
    with pytest.raises(ValidationError, match="attempt 0"):
        NodeRunRecord.model_validate(_node_record("queued", attempt=1))
    NodeRunRecord.model_validate(_node_record("queued"))


def test_default_queued_state_uses_zero_attempt() -> None:
    record = _node_record("queued")
    record.pop("state")
    record.pop("attempt")
    validated = NodeRunRecord.model_validate(record)
    assert validated.state == NodeRunState.QUEUED
    assert validated.attempt == 0


def test_running_nodes_require_positive_attempt() -> None:
    record = _node_record("running", finished_at=None, artifacts=[], attempt=0)
    with pytest.raises(ValidationError, match="positive attempt"):
        NodeRunRecord.model_validate(record)


def test_cancelled_run_finish_is_compared_with_creation() -> None:
    raw = _load("run-1.0.json")
    reversed_cancel = dict(
        raw,
        state="cancelled",
        started_at=None,
        finished_at="2026-09-11T09:00:00+00:00",
        node_runs=[],
        events=[],
        failure=None,
    )
    with pytest.raises(ValidationError, match="must not precede"):
        RunRecord.model_validate(reversed_cancel)

    same_instant = dict(
        raw,
        state="cancelled",
        started_at=None,
        finished_at="2026-09-12T09:00:00+00:00",
        node_runs=[],
        events=[],
        failure=None,
    )
    assert RunRecord.model_validate(same_instant).state == RunState.CANCELLED


def test_identity_carries_schema_domain_prefix() -> None:
    identity = node_content_identity(**_node_payload())
    assert identity.startswith("brainlearn-v1:node:")


def test_identity_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        content_identity("node", {"value": float("nan")})
    with pytest.raises(ValueError, match="non-finite"):
        node_content_identity(**(_node_payload() | {"parameters": {"x": float("inf")}}))


def test_identity_rejects_non_json_values() -> None:
    with pytest.raises(TypeError, match="only JSON values"):
        content_identity("node", {"value": {1, 2}})
    with pytest.raises(TypeError, match="must be strings"):
        content_identity("node", {1: "one"})  # type: ignore[dict-item]


# -- lifecycle invariants -------------------------------------------------


def test_queued_node_run_must_not_carry_timestamps() -> None:
    record = _node_record("queued", started_at="2026-09-12T09:00:00+00:00")
    with pytest.raises(ValidationError, match="queued"):
        NodeRunRecord.model_validate(record)


def test_succeeded_node_run_requires_timestamps() -> None:
    record = _node_record("succeeded", finished_at=None)
    with pytest.raises(ValidationError, match="started_at"):
        NodeRunRecord.model_validate(record)


def test_failed_node_run_requires_failure_and_no_artifacts() -> None:
    without_failure = _node_record("failed", failure=None)
    with pytest.raises(ValidationError, match="failure"):
        NodeRunRecord.model_validate(without_failure)

    failure = _load("failure-1.0.json")
    with_artifacts = _node_record("failed", failure=failure)
    with pytest.raises(ValidationError, match="successful artifacts"):
        NodeRunRecord.model_validate(with_artifacts)


def test_non_failed_node_run_must_not_carry_failure() -> None:
    record = _node_record("succeeded", failure=_load("failure-1.0.json"))
    with pytest.raises(ValidationError, match="Only a failed node run"):
        NodeRunRecord.model_validate(record)


@pytest.mark.parametrize("state", ["cancelled", "dependency_skipped", "cache_reused"])
def test_unexecuted_or_unsuccessful_states_keep_no_artifacts(state: str) -> None:
    record = _node_record(state, finished_at="2026-09-12T09:05:00+00:00")
    if state == "cancelled":
        record = dict(record, artifacts=[])
        NodeRunRecord.model_validate(record)
        return
    with pytest.raises(ValidationError, match="must not carry timestamps"):
        NodeRunRecord.model_validate(record)

    clean = _node_record(state, started_at=None, finished_at=None, artifacts=[])
    NodeRunRecord.model_validate(clean)


def test_cache_reuse_and_skip_do_not_count_as_execution() -> None:
    assert counts_as_execution(NodeRunState.RUNNING, 1) is True
    assert counts_as_execution(NodeRunState.SUCCEEDED, 1) is True
    assert counts_as_execution(NodeRunState.CACHE_REUSED, 0) is False
    assert counts_as_execution(NodeRunState.DEPENDENCY_SKIPPED, 0) is False
    assert counts_as_execution(NodeRunState.QUEUED, 0) is False


def test_cancelled_counts_as_execution_only_with_positive_attempt() -> None:
    assert counts_as_execution(NodeRunState.CANCELLED, 0) is False
    assert counts_as_execution(NodeRunState.CANCELLED, 1) is True
    assert counts_as_execution(NodeRunState.WAITING_FOR_REVIEW, 1) is True
    with pytest.raises(TypeError):
        counts_as_execution(NodeRunState.CANCELLED)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="Attempt must be"):
        counts_as_execution(NodeRunState.CANCELLED, -1)


def test_waiting_node_run_requires_pending_review() -> None:
    record = _node_record("waiting_for_review", review_pause=None, finished_at=None)
    with pytest.raises(ValidationError, match="pending review"):
        NodeRunRecord.model_validate(record)

    decided = _load("review-pause-1.0.json")
    record = _node_record("waiting_for_review", review_pause=decided, finished_at=None)
    with pytest.raises(ValidationError, match="pending review"):
        NodeRunRecord.model_validate(record)


def test_failed_run_requires_failure_and_no_run_failure_when_succeeded() -> None:
    raw = _load("run-1.0.json")
    failed = dict(raw, state="failed", finished_at=raw["finished_at"], failure=None)
    with pytest.raises(ValidationError, match="failure"):
        RunRecord.model_validate(failed)

    succeeded_with_failure = dict(raw, failure=_load("failure-1.0.json"))
    with pytest.raises(ValidationError, match="Only a failed run"):
        RunRecord.model_validate(succeeded_with_failure)


def test_run_events_require_gapless_ordering() -> None:
    raw = _load("run-1.0.json")
    events = list(raw["events"])
    events[1] = dict(events[1], seq=5)
    with pytest.raises(ValidationError, match="seq 0..n-1"):
        RunRecord.model_validate(dict(raw, events=events))


@pytest.mark.parametrize("path", ["/absolute/path.fif", "../escape.fif", "a/../b.fif", ""])
def test_artifact_paths_must_be_project_relative(path: str) -> None:
    raw = _load("artifact-1.0.json")
    with pytest.raises(ValidationError, match="project-relative|segments|at least 1"):
        ArtifactRecord.model_validate(dict(raw, path=path))


@pytest.mark.parametrize(
    "path",
    [
        "C:\\outside\\result.bin",
        "C:/outside/result.bin",
        "C:result.bin",
        "c:\\outside\\result.bin",
        "\\\\server\\share\\result.bin",
        "//server/share/result.bin",
    ],
)
def test_artifact_paths_reject_windows_absolute_and_unc(path: str) -> None:
    raw = _load("artifact-1.0.json")
    with pytest.raises(ValidationError, match="project-relative"):
        ArtifactRecord.model_validate(dict(raw, path=path))


@pytest.mark.parametrize(
    ("raw_path", "canonical"),
    [
        ("artifacts/filter/out.fif", "artifacts/filter/out.fif"),
        ("artifacts\\filter\\out.fif", "artifacts/filter/out.fif"),
        ("artifacts\\filter/out.fif", "artifacts/filter/out.fif"),
    ],
)
def test_artifact_paths_normalize_to_forward_slashes(raw_path: str, canonical: str) -> None:
    record = ArtifactRecord.model_validate(dict(_load("artifact-1.0.json"), path=raw_path))
    assert record.path == canonical
    assert ArtifactRecord.model_validate_json(record.model_dump_json()) == record


def test_timestamps_must_be_iso() -> None:
    raw = _load("run-event-1.0.json")
    with pytest.raises(ValidationError, match="ISO-8601"):
        RunEvent.model_validate(dict(raw, at="not-a-timestamp"))


def test_run_fixture_embeds_valid_nested_records() -> None:
    run = RunRecord.model_validate(_load("run-1.0.json"))
    assert [node.id for node in run.node_runs] == [
        "node-run-inspect-001",
        "node-run-filter-001",
    ]
    assert run.node_runs[1].dependencies == ["node-run-inspect-001"]
    assert [event.seq for event in run.events] == [0, 1, 2, 3, 4, 5]
    assert run.failure is None
    assert run.state in RUN_TERMINAL_STATES


# -- referential integrity -------------------------------------------------


def test_run_rejects_dangling_dependencies() -> None:
    nodes = [_bare_node("a"), _bare_node("b")]
    nodes[1]["dependencies"] = ["node-run-ghost"]
    with pytest.raises(ValidationError, match="unknown node run"):
        RunRecord.model_validate(_run_with_nodes(nodes))


def test_run_rejects_duplicate_node_run_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        RunRecord.model_validate(_run_with_nodes([_bare_node("a"), _bare_node("a")]))


def test_run_rejects_self_dependencies() -> None:
    nodes = [_bare_node("a")]
    nodes[0]["dependencies"] = ["a"]
    with pytest.raises(ValidationError, match="itself"):
        RunRecord.model_validate(_run_with_nodes(nodes))


def test_run_rejects_cyclic_dependencies() -> None:
    first, second = _bare_node("a"), _bare_node("b")
    first["dependencies"] = ["b"]
    second["dependencies"] = ["a"]
    with pytest.raises(ValidationError, match="cycle"):
        RunRecord.model_validate(_run_with_nodes([first, second]))


def test_run_rejects_unknown_event_references() -> None:
    raw = _run_with_nodes([_bare_node("a")])
    raw["events"] = [dict(_load("run-event-1.0.json"), seq=0, node_run_id="node-run-ghost")]
    with pytest.raises(ValidationError, match="unknown node run"):
        RunRecord.model_validate(raw)


def test_run_rejects_unknown_failure_reference() -> None:
    raw = _run_with_nodes([_bare_node("a")], state="failed")
    raw["failure"] = dict(_load("failure-1.0.json"), node_run_id="node-run-ghost")
    with pytest.raises(ValidationError, match="unknown node run"):
        RunRecord.model_validate(raw)


def test_node_run_rejects_mismatched_contained_references() -> None:
    base = _node_record("succeeded")
    artifact = dict(_load("artifact-1.0.json"), produced_by_node="node-run-other")
    with pytest.raises(ValidationError, match="producer"):
        NodeRunRecord.model_validate(dict(base, artifacts=[artifact]))

    failure = dict(_load("failure-1.0.json"), node_run_id="node-run-other")
    failed = _node_record("failed", failure=failure, artifacts=[])
    with pytest.raises(ValidationError, match="Failure references"):
        NodeRunRecord.model_validate(failed)

    review = dict(
        _load("review-pause-1.0.json"),
        id="review-bad",
        node_run_id="node-run-other",
        decided_at=None,
        decision=None,
    )
    waiting = _node_record(
        "waiting_for_review", review_pause=review, finished_at=None, artifacts=[]
    )
    with pytest.raises(ValidationError, match="references node run"):
        NodeRunRecord.model_validate(waiting)


def test_valid_branched_dependency_graph_passes() -> None:
    root, left, right, join = (
        _bare_node("root"),
        _bare_node("left"),
        _bare_node("right"),
        _bare_node("join"),
    )
    left["dependencies"] = ["root"]
    right["dependencies"] = ["root"]
    join["dependencies"] = ["left", "right"]
    run = RunRecord.model_validate(_run_with_nodes([root, left, right, join]))
    assert [node.id for node in run.node_runs] == ["root", "left", "right", "join"]


# -- aggregate consistency ---------------------------------------------------


@pytest.mark.parametrize(
    "node_state", ["queued", "running", "waiting_for_review", "failed", "cancelled"]
)
def test_succeeded_run_rejects_unfinished_or_failed_nodes(node_state: str) -> None:
    nodes = [_bare_node("good")]
    bad = _node_record(node_state, artifacts=[])
    bad["id"] = "bad"
    bad["node_id"] = "bad"
    bad["dependencies"] = []
    if node_state == "queued":
        bad["started_at"] = None
        bad["finished_at"] = None
    elif node_state == "running":
        bad["finished_at"] = None
    elif node_state == "waiting_for_review":
        bad["finished_at"] = None
        bad["review_pause"] = {
            **_load("review-pause-1.0.json"),
            "id": "review-bad",
            "node_run_id": "bad",
            "decided_at": None,
            "decision": None,
        }
    elif node_state == "failed":
        bad["failure"] = dict(_load("failure-1.0.json"), node_run_id="bad")
    with pytest.raises(ValidationError, match="succeeded run"):
        RunRecord.model_validate(_run_with_nodes(nodes + [bad]))


def test_succeeded_run_rejects_skipped_nodes() -> None:
    nodes = [_bare_node("good"), _bare_node("skipped", state="dependency_skipped")]
    with pytest.raises(ValidationError, match="succeeded run"):
        RunRecord.model_validate(_run_with_nodes(nodes))


def test_queued_run_rejects_started_work() -> None:
    raw = _run_with_nodes([_bare_node("a")], state="queued", started_at=None)
    with pytest.raises(ValidationError, match="queued run"):
        RunRecord.model_validate(raw)


def test_terminal_run_rejects_nonterminal_nodes() -> None:
    running = _node_record("running", finished_at=None, artifacts=[])
    running["id"] = "live"
    running["node_id"] = "live"
    running["dependencies"] = []
    failure = dict(_load("failure-1.0.json"), node_run_id="done")
    raw = _run_with_nodes(
        [_bare_node("done"), running],
        state="failed",
        failure=failure,
    )
    with pytest.raises(ValidationError, match="nonterminal"):
        RunRecord.model_validate(raw)


# -- numeric canonicalization --------------------------------------------------


def test_equivalent_numbers_share_an_identity() -> None:
    from_int = node_content_identity(**(_node_payload() | {"parameters": {"cutoff": 1}}))
    from_float = node_content_identity(**(_node_payload() | {"parameters": {"cutoff": 1.0}}))
    assert from_int == from_float
    assert node_content_identity(
        **(_node_payload() | {"parameters": {"cutoff": -0.0}, "seed": 0})
    ) == node_content_identity(**(_node_payload() | {"parameters": {"cutoff": 0}, "seed": 0}))


def test_nested_numbers_canonicalize() -> None:
    first = node_content_identity(
        **(_node_payload() | {"settings": {"bands": [1.0, {"low": 2.0}]}})
    )
    second = node_content_identity(**(_node_payload() | {"settings": {"bands": [1, {"low": 2}]}}))
    assert first == second
    distinct = node_content_identity(**(_node_payload() | {"settings": {"bands": [1.5]}}))
    assert distinct != first


def test_json_round_trip_preserves_identity() -> None:
    payload = {"cutoff": 1.0, "nested": {"rate": 40.0, "name": "eeg"}}
    before = content_identity("node", payload)
    after = content_identity("node", json.loads(json.dumps(payload)))
    assert before == after


# -- timezone and chronology -----------------------------------------------------


@pytest.mark.parametrize(
    "record",
    [
        dict(_load("run-event-1.0.json"), at="2026-09-12T09:05:00"),
        dict(_load("failure-1.0.json"), at="2026-09-12T09:15:00"),
    ],
)
def test_naive_timestamps_are_rejected(record: dict[str, Any]) -> None:
    model = RunEvent if "seq" in record else FailureRecord
    with pytest.raises(ValidationError, match="timezone offset"):
        model.model_validate(record)


def test_naive_run_timestamps_are_rejected() -> None:
    raw = _load("run-1.0.json")
    with pytest.raises(ValidationError, match="timezone offset"):
        RunRecord.model_validate(dict(raw, created_at="2026-09-12T09:00:00"))


def test_reversed_chronology_is_rejected() -> None:
    raw = _load("run-1.0.json")
    with pytest.raises(ValidationError, match="must not precede"):
        RunRecord.model_validate(dict(raw, started_at="2026-09-12T10:00:00+00:00"))

    node = _node_record(
        "succeeded",
        started_at="2026-09-12T09:05:00+00:00",
        finished_at="2026-09-12T09:04:00+00:00",
    )
    with pytest.raises(ValidationError, match="must not precede"):
        NodeRunRecord.model_validate(node)

    review = dict(_load("review-pause-1.0.json"), decided_at="2026-09-12T09:09:00+00:00")
    with pytest.raises(ValidationError, match="must not precede"):
        ReviewPauseRecord.model_validate(review)


def test_equal_instants_and_equivalent_offsets_pass() -> None:
    raw = _load("run-1.0.json")
    run = RunRecord.model_validate(
        dict(
            raw,
            created_at="2026-09-12T09:00:00+00:00",
            started_at="2026-09-12T10:00:00+01:00",
            finished_at="2026-09-12T09:06:00Z",
        )
    )
    assert run.state == RunState.SUCCEEDED


# -- identity format, schema marker, attempt ---------------------------------------


@pytest.mark.parametrize(
    "identity",
    [
        "not-an-identity",
        "brainlearn-v1:node:short",
        "brainlearn-v1:node:" + "g" * 64,
        "brainlearn-v1:node:" + "A" * 64,
        "other-prefix:node:" + "a" * 64,
    ],
)
def test_malformed_node_identities_are_rejected(identity: str) -> None:
    record = _node_record("succeeded", content_identity=identity)
    with pytest.raises(ValidationError, match="content_identity|pattern"):
        NodeRunRecord.model_validate(record)


def test_malformed_environment_and_workflow_identities_are_rejected() -> None:
    record = _node_record("succeeded", environment_identity="brainlearn-v1:node:" + "a" * 64)
    with pytest.raises(ValidationError, match="environment_identity|pattern"):
        NodeRunRecord.model_validate(record)

    raw = _run_with_nodes([_bare_node("a")], state="queued", workflow_identity="bogus")
    with pytest.raises(ValidationError, match="workflow_identity|pattern"):
        RunRecord.model_validate(raw)


def test_caller_schema_marker_cannot_override_identity_marker() -> None:
    from brainlearn_core.identity import environment_identity as env_identity

    assert env_identity({"schema_version": "9.9", "os": "Linux"}) == env_identity({"os": "Linux"})


@pytest.mark.parametrize("state", ["dependency_skipped", "cache_reused"])
def test_skipped_states_require_attempt_zero(state: str) -> None:
    record = _node_record(state, attempt=1)
    with pytest.raises(ValidationError, match="attempt 0"):
        NodeRunRecord.model_validate(record)
    NodeRunRecord.model_validate(_node_record(state))


def test_executed_states_require_positive_attempt() -> None:
    record = _node_record("succeeded", attempt=0)
    with pytest.raises(ValidationError, match="positive attempt"):
        NodeRunRecord.model_validate(record)
