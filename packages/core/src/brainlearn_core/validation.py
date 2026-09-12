"""Semantic validation for workflow connections and required configuration."""

from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from brainlearn_core.schema import (
    NodeInstance,
    NodeManifest,
    ParameterSchema,
    PortDirection,
    Workflow,
)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "duplicate_node",
        "missing_node",
        "unknown_port",
        "wrong_port_direction",
        "incompatible_port_type",
        "missing_required_parameter",
        "unknown_node_type",
        "manifest_mismatch",
        "invalid_parameter",
        "cycle",
    ]
    message: str
    node_id: str | None = None
    edge_id: str | None = None


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: list[ValidationIssue]


def _node_map(workflow: Workflow, issues: list[ValidationIssue]) -> dict[str, NodeInstance]:
    nodes: dict[str, NodeInstance] = {}
    for node in workflow.nodes:
        if node.id in nodes:
            issues.append(
                ValidationIssue(
                    code="duplicate_node",
                    node_id=node.id,
                    message=(
                        f"Node ID '{node.id}' is used more than once. Give each node a unique ID."
                    ),
                )
            )
        else:
            nodes[node.id] = node
    return nodes


def _matches_parameter_type(value: Any, schema: ParameterSchema) -> bool:
    if value is None:
        return not schema.required
    if schema.value_type == "string":
        return isinstance(value, str)
    if schema.value_type == "boolean":
        return isinstance(value, bool)
    if schema.value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_against_registry(
    workflow: Workflow,
    manifests: Mapping[str, NodeManifest],
    issues: list[ValidationIssue],
) -> None:
    for node in workflow.nodes:
        manifest = manifests.get(node.type)
        if manifest is None:
            issues.append(
                ValidationIssue(
                    code="unknown_node_type",
                    node_id=node.id,
                    message=f"'{node.label}' uses unregistered node type '{node.type}'.",
                )
            )
            continue
        expected_review = manifest.review_behavior == "required"
        metadata_matches = (
            node.label == manifest.label
            and node.category == manifest.category
            and node.description == manifest.description
            and node.pauses_for_review == expected_review
        )
        if not metadata_matches:
            issues.append(
                ValidationIssue(
                    code="manifest_mismatch",
                    node_id=node.id,
                    message=(
                        f"'{node.id}' display or review metadata does not match registry manifest "
                        f"{manifest.node_version}. Remove and add the node again."
                    ),
                )
            )
        if node.ports != manifest.ports:
            issues.append(
                ValidationIssue(
                    code="manifest_mismatch",
                    node_id=node.id,
                    message=(
                        f"'{node.label}' ports do not match registry manifest "
                        f"{manifest.node_version}. "
                        "Remove and add the node again."
                    ),
                )
            )
        instance_parameters = {parameter.id: parameter for parameter in node.parameters}
        if set(instance_parameters) != {schema.id for schema in manifest.parameters}:
            issues.append(
                ValidationIssue(
                    code="manifest_mismatch",
                    node_id=node.id,
                    message=(
                        f"'{node.label}' parameters do not match registry manifest "
                        f"{manifest.node_version}. Remove and add the node again."
                    ),
                )
            )
            continue
        for schema in manifest.parameters:
            parameter = instance_parameters[schema.id]
            if (
                parameter.label != schema.label
                or parameter.required != schema.required
                or parameter.description != schema.description
            ):
                issues.append(
                    ValidationIssue(
                        code="manifest_mismatch",
                        node_id=node.id,
                        message=(
                            f"'{node.label}' metadata for parameter '{schema.id}' does not match "
                            f"registry manifest {manifest.node_version}. "
                            "Remove and add the node again."
                        ),
                    )
                )
            value = parameter.value
            number = float(value) if isinstance(value, (int, float)) else None
            valid_range = (
                schema.minimum is None or (number is not None and number >= schema.minimum)
            ) and (schema.maximum is None or (number is not None and number <= schema.maximum))
            valid_option = schema.options is None or value in schema.options
            if not _matches_parameter_type(value, schema) or not valid_range or not valid_option:
                issues.append(
                    ValidationIssue(
                        code="invalid_parameter",
                        node_id=node.id,
                        message=f"'{node.label}' has an invalid value for '{schema.label}'.",
                    )
                )


def validate_workflow(
    workflow: Workflow, manifests: Mapping[str, NodeManifest] | None = None
) -> ValidationResult:
    """Return all graph errors that can be explained before execution."""

    issues: list[ValidationIssue] = []
    nodes = _node_map(workflow, issues)
    if manifests is not None:
        _validate_against_registry(workflow, manifests, issues)

    for node in workflow.nodes:
        for parameter in node.parameters:
            if parameter.required and parameter.value is None:
                issues.append(
                    ValidationIssue(
                        code="missing_required_parameter",
                        node_id=node.id,
                        message=(
                            f"'{node.label}' requires parameter '{parameter.label}'. "
                            "Choose a value before running the workflow."
                        ),
                    )
                )

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in workflow.edges:
        source = nodes.get(edge.source.node_id)
        target = nodes.get(edge.target.node_id)
        if source is None:
            issues.append(
                ValidationIssue(
                    code="missing_node",
                    edge_id=edge.id,
                    message=(
                        f"Edge '{edge.id}' refers to missing source node '{edge.source.node_id}'."
                    ),
                )
            )
        if target is None:
            issues.append(
                ValidationIssue(
                    code="missing_node",
                    edge_id=edge.id,
                    message=(
                        f"Edge '{edge.id}' refers to missing target node '{edge.target.node_id}'."
                    ),
                )
            )
        if source is None or target is None:
            continue

        source_port = next((port for port in source.ports if port.id == edge.source.port_id), None)
        target_port = next((port for port in target.ports if port.id == edge.target.port_id), None)
        if source_port is None:
            issues.append(
                ValidationIssue(
                    code="unknown_port",
                    edge_id=edge.id,
                    node_id=source.id,
                    message=f"'{source.label}' has no port named '{edge.source.port_id}'.",
                )
            )
        if target_port is None:
            issues.append(
                ValidationIssue(
                    code="unknown_port",
                    edge_id=edge.id,
                    node_id=target.id,
                    message=f"'{target.label}' has no port named '{edge.target.port_id}'.",
                )
            )
        if source_port is None or target_port is None:
            continue

        if source_port.direction != PortDirection.OUTPUT:
            issues.append(
                ValidationIssue(
                    code="wrong_port_direction",
                    edge_id=edge.id,
                    node_id=source.id,
                    message=f"Source port '{source_port.label}' must be an output.",
                )
            )
        if target_port.direction != PortDirection.INPUT:
            issues.append(
                ValidationIssue(
                    code="wrong_port_direction",
                    edge_id=edge.id,
                    node_id=target.id,
                    message=f"Target port '{target_port.label}' must be an input.",
                )
            )
        if source_port.data_type != target_port.data_type:
            issues.append(
                ValidationIssue(
                    code="incompatible_port_type",
                    edge_id=edge.id,
                    message=(
                        f"Cannot connect {source_port.data_type.value} to "
                        f"{target_port.data_type.value}. Add an explicit conversion node."
                    ),
                )
            )
        adjacency[source.id].add(target.id)

    indegree = {node_id: 0 for node_id in nodes}
    for targets in adjacency.values():
        for target_id in targets:
            indegree[target_id] += 1
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target_id in adjacency[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                queue.append(target_id)
    if visited != len(nodes):
        issues.append(
            ValidationIssue(
                code="cycle",
                message=(
                    "The workflow contains a cycle. Put repeated work inside a structured node."
                ),
            )
        )

    return ValidationResult(valid=not issues, issues=issues)
