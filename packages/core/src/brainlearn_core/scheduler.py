"""Topological scheduling over workflow dependency graphs.

These helpers decide *what may run next* without running anything. They take
an explicit dependency mapping plus the current node-run states and answer
three questions: in which order can nodes execute, which queued nodes are
ready, and which nodes can no longer run because an ancestor failed. Worker
execution, retries, and caching belong to later slices.

Every entry point validates the graph the same way: dependency references
must name graph nodes, no node may list the same dependency twice, and the
graph must be acyclic. Callers pass node states separately: every graph node
must have a state, and additional state keys for unknown nodes are ignored.
"""

from collections.abc import Mapping

from brainlearn_core.execution import NodeRunState

COMPLETED_STATES = frozenset({NodeRunState.SUCCEEDED, NodeRunState.CACHE_REUSED})


def _validate_graph(dependencies: Mapping[str, list[str]]) -> None:
    """Reject unknown references, duplicate edges, and cycles."""

    for node, deps in dependencies.items():
        for dependency in deps:
            if dependency not in dependencies:
                raise ValueError(f"Node {node!r} depends on unknown node {dependency!r}.")
        if len(set(deps)) != len(deps):
            raise ValueError(f"Node {node!r} lists duplicate dependencies.")
    indegree = {node: 0 for node in dependencies}
    for node in sorted(dependencies):
        for dependency in set(dependencies[node]):
            indegree[node] += 1
    queue = sorted(node for node, degree in indegree.items() if degree == 0)
    seen = 0
    dependents: dict[str, list[str]] = {node: [] for node in dependencies}
    for node in sorted(dependencies):
        for dependency in set(dependencies[node]):
            dependents[dependency].append(node)
    while queue:
        node = queue.pop(0)
        seen += 1
        for dependent in sorted(dependents[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
        queue.sort()
    if seen != len(dependencies):
        cycle = sorted(node for node, degree in indegree.items() if degree > 0)
        raise ValueError("Dependency graph contains a cycle involving: " + ", ".join(cycle) + ".")


def topological_order(dependencies: Mapping[str, list[str]]) -> list[str]:
    """Return node IDs so every dependency precedes its dependents.

    Iteration is sorted so equivalent graphs always produce the same order.
    Unknown dependency references, duplicate edges, and cycles raise
    ``ValueError`` naming the offending IDs.
    """

    _validate_graph(dependencies)
    indegree = {node: 0 for node in dependencies}
    dependents: dict[str, list[str]] = {node: [] for node in dependencies}
    for node in sorted(dependencies):
        for dependency in dependencies[node]:
            indegree[node] += 1
            dependents[dependency].append(node)
    queue = sorted(node for node, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for dependent in sorted(dependents[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
        queue.sort()
    # _validate_graph already rejected cycles, so every node is ordered here.
    return order


def ready_node_ids(
    states: Mapping[str, NodeRunState],
    dependencies: Mapping[str, list[str]],
) -> set[str]:
    """Return queued nodes whose dependencies all completed.

    Completed means ``succeeded`` or ``cache_reused``. Nodes in any other
    state are never ready, even when their dependencies completed. The graph
    itself is validated exactly like :func:`topological_order`; every graph
    node must also have a state, while additional state keys are ignored.
    """

    _validate_graph(dependencies)
    unknown = [node for node in dependencies if node not in states]
    if unknown:
        raise ValueError(f"Scheduler has no state for node(s): {', '.join(sorted(unknown))}.")
    return {
        node
        for node, deps in dependencies.items()
        if states[node] == NodeRunState.QUEUED
        and all(states[dependency] in COMPLETED_STATES for dependency in deps)
    }


def downstream_ids(sources: set[str], dependencies: Mapping[str, list[str]]) -> set[str]:
    """Return all transitive dependents of ``sources``, excluding the sources.

    Every source ID must name a graph node: unknown IDs raise ``ValueError``
    so a misspelled failure cannot silently skip nothing. An empty source
    set returns an empty set, and a leaf source returns an empty set.
    """

    _validate_graph(dependencies)
    unknown = sorted(source for source in sources if source not in dependencies)
    if unknown:
        raise ValueError(f"Downstream query names unknown node(s): {', '.join(unknown)}.")
    dependents: dict[str, list[str]] = {node: [] for node in dependencies}
    for node in sorted(dependencies):
        for dependency in dependencies[node]:
            dependents[dependency].append(node)
    seen: set[str] = set()
    queue = sorted(sources & set(dependencies))
    while queue:
        node = queue.pop(0)
        for dependent in sorted(dependents[node]):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return seen - set(sources)
