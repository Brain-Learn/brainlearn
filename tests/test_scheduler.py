"""Step 4B: topological scheduling without execution."""

import pytest
from brainlearn_core import NodeRunState
from brainlearn_core.scheduler import downstream_ids, ready_node_ids, topological_order

DIAMOND = {
    "root": [],
    "left": ["root"],
    "right": ["root"],
    "join": ["left", "right"],
}

QUEUED = NodeRunState.QUEUED
RUNNING = NodeRunState.RUNNING
DONE = NodeRunState.SUCCEEDED
REUSED = NodeRunState.CACHE_REUSED


def _states(*names: str, default: NodeRunState = QUEUED) -> dict[str, NodeRunState]:
    return {name: default for name in DIAMOND}


def test_topological_order_respects_dependencies() -> None:
    order = topological_order(DIAMOND)
    assert order[0] == "root"
    assert order[-1] == "join"
    assert set(order) == set(DIAMOND)


def test_topological_order_is_deterministic() -> None:
    first = topological_order({"b": [], "a": [], "c": ["a", "b"]})
    assert first == topological_order({"c": ["b", "a"], "b": [], "a": []})
    assert first[:2] == ["a", "b"]


def test_topological_order_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="cycle.*a.*b"):
        topological_order({"a": ["b"], "b": ["a"]})


def test_topological_order_rejects_unknown_dependencies() -> None:
    with pytest.raises(ValueError, match="unknown node 'ghost'"):
        topological_order({"a": ["ghost"]})


def test_topological_order_rejects_duplicate_edges() -> None:
    with pytest.raises(ValueError, match="duplicate dependencies"):
        topological_order({"a": [], "b": ["a", "a"]})


@pytest.mark.parametrize("helper", ["ready", "downstream"])
def test_scheduler_helpers_agree_on_malformed_graphs(helper: str) -> None:
    cyclic = {"a": ["b"], "b": ["a"]}
    dangling = {"a": ["ghost"]}
    duplicated = {"a": [], "b": ["a", "a"]}
    states = {"a": QUEUED, "b": QUEUED, "ghost": DONE}
    if helper == "ready":
        with pytest.raises(ValueError, match="cycle"):
            ready_node_ids(states, cyclic)
        with pytest.raises(ValueError, match="unknown node"):
            ready_node_ids(states, dangling)
        with pytest.raises(ValueError, match="duplicate"):
            ready_node_ids(states, duplicated)
    else:
        with pytest.raises(ValueError, match="cycle"):
            downstream_ids({"a"}, cyclic)
        with pytest.raises(ValueError, match="unknown node"):
            downstream_ids({"a"}, dangling)
        with pytest.raises(ValueError, match="duplicate"):
            downstream_ids({"a"}, duplicated)


def test_ready_node_ids_ignore_extra_state_keys() -> None:
    states = {"root": DONE, "left": QUEUED, "right": QUEUED, "join": QUEUED, "stale": DONE}
    assert ready_node_ids(states, DIAMOND) == {"left", "right"}


def test_ready_nodes_require_completed_dependencies() -> None:
    states = _states("root", "left", "right", "join")
    states["root"] = DONE
    assert ready_node_ids(states, DIAMOND) == {"left", "right"}

    states["left"] = DONE
    states["right"] = REUSED
    assert ready_node_ids(states, DIAMOND) == {"join"}


def test_ready_nodes_exclude_non_queued_states() -> None:
    states = _states("root", "left", "right", "join")
    states["root"] = DONE
    states["left"] = RUNNING
    assert ready_node_ids(states, DIAMOND) == {"right"}


def test_ready_nodes_require_known_states() -> None:
    with pytest.raises(ValueError, match="no state"):
        ready_node_ids({"root": DONE}, DIAMOND)


def test_downstream_ids_cover_transitive_dependents() -> None:
    assert downstream_ids({"root"}, DIAMOND) == {"left", "right", "join"}
    assert downstream_ids({"left"}, DIAMOND) == {"join"}
    assert downstream_ids({"join"}, DIAMOND) == set()
    assert downstream_ids({"left", "right"}, DIAMOND) == {"join"}


def test_failed_subgraph_marks_skip_closure() -> None:
    # A failure at "left" can only ever strand its own downstream branch.
    assert downstream_ids({"left"}, DIAMOND) == {"join"}
    assert "right" not in downstream_ids({"left"}, DIAMOND)


def test_downstream_ids_reject_unknown_sources() -> None:
    with pytest.raises(ValueError, match=r"unknown node\(s\): ghost"):
        downstream_ids({"ghost"}, {"a": []})


def test_downstream_ids_reject_mixed_known_and_unknown_sources() -> None:
    with pytest.raises(ValueError, match=r"unknown node\(s\): ghost"):
        downstream_ids({"root", "ghost"}, DIAMOND)


def test_downstream_ids_preserve_empty_and_leaf_behavior() -> None:
    assert downstream_ids(set(), DIAMOND) == set()
    assert downstream_ids({"join"}, DIAMOND) == set()
