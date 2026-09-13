from pathlib import Path

import pytest
from brainlearn_core import (
    CanvasPosition,
    Edge,
    EdgeEndpoint,
    GraphMetadata,
    NodeManifest,
    PortDirection,
    ScientificType,
    Workflow,
    validate_workflow,
)
from brainlearn_server.demo_nodes import DEMO_NODES
from brainlearn_server.registry import (
    DEMO_MANIFESTS,
    NODE_REGISTRY,
    instantiate_registered_node,
)

EXAMPLE = Path(__file__).parents[1] / "examples" / "eeg-first-look.workflow.json"


def test_registry_manifests_round_trip_and_have_unique_identity() -> None:
    restored = [NodeManifest.model_validate_json(item.model_dump_json()) for item in NODE_REGISTRY]

    assert restored == list(NODE_REGISTRY)
    assert len({item.id for item in restored}) == len(restored) == 13


def test_registry_ports_and_parameters_match_example_instances() -> None:
    example_workflow = Workflow.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))
    manifests = {manifest.id: manifest for manifest in NODE_REGISTRY}

    for node in example_workflow.nodes:
        manifest = manifests[node.type]
        assert node.ports == manifest.ports
        assert {parameter.id for parameter in node.parameters} == {
            parameter.id for parameter in manifest.parameters
        }


def test_registry_instances_form_a_compatible_editable_graph() -> None:
    bids = instantiate_registered_node("input.bids_eeg", "bids", CanvasPosition(x=0, y=0))
    inspect = instantiate_registered_node("eeg.inspect", "inspect", CanvasPosition(x=200, y=0))
    workflow = Workflow(
        id="edited",
        metadata=GraphMetadata(name="Edited example"),
        nodes=[bids, inspect],
        edges=[
            Edge(
                id="edge",
                source=EdgeEndpoint(node_id="bids", port_id="dataset"),
                target=EdgeEndpoint(node_id="inspect", port_id="dataset"),
            )
        ],
    )

    assert workflow.nodes[0].parameters[0].value == "synthetic/example-bids"
    assert validate_workflow(workflow).valid


def test_demo_manifests_derive_ports_and_versions_from_worker_contract() -> None:
    assert {manifest.id for manifest in DEMO_MANIFESTS} == set(DEMO_NODES)
    for manifest in DEMO_MANIFESTS:
        worker = DEMO_NODES[manifest.id]
        assert manifest.node_version == worker.version
        assert [
            (port.id, port.direction, port.required)
            for port in manifest.ports
            if port.direction == PortDirection.INPUT
        ] == [
            (port_id, PortDirection.INPUT, required) for port_id, required in worker.inputs.items()
        ]
        assert [
            (port.id, port.direction, port.required)
            for port in manifest.ports
            if port.direction == PortDirection.OUTPUT
        ] == [
            (port_id, PortDirection.OUTPUT, required)
            for port_id, required in worker.outputs.items()
        ]
        assert {port.data_type for port in manifest.ports} == {ScientificType.RAW_EEG}
        assert manifest.category == "Demonstration"
        assert "Non-scientific" in manifest.description
        assert manifest.status == "example"
        assert manifest.citations == []
        assert manifest.license.spdx_id == "BSD-3-Clause"
    review = next(item for item in DEMO_MANIFESTS if item.id == "demo.review")
    assert review.review_behavior == "required"
    assert all(
        item.review_behavior == "none" for item in DEMO_MANIFESTS if item.id != "demo.review"
    )


@pytest.mark.parametrize("node_type", sorted(DEMO_NODES))
def test_demo_instances_carry_adapter_defaults(node_type: str) -> None:
    node = instantiate_registered_node(node_type, "probe", CanvasPosition(x=1, y=2))
    assert node.type == node_type
    assert node.position == CanvasPosition(x=1, y=2)


def test_demo_instance_parameter_defaults_match_adapters() -> None:
    assert {
        parameter.id: parameter.value
        for parameter in instantiate_registered_node(
            "demo.copy", "c", CanvasPosition(x=0, y=0)
        ).parameters
    } == {"text": "brainlearn-demo"}
    assert {
        parameter.id: parameter.value
        for parameter in instantiate_registered_node(
            "demo.delay", "d", CanvasPosition(x=0, y=0)
        ).parameters
    } == {"seconds": 0.1}
    assert {
        parameter.id: parameter.value
        for parameter in instantiate_registered_node(
            "demo.fail", "f", CanvasPosition(x=0, y=0)
        ).parameters
    } == {"message": "demonstration failure"}


def test_demo_parameter_contract_drives_handler_and_registry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from brainlearn_server.demo_nodes import (
        DEMO_NODES,
        DemoParameter,
        NodeContext,
        copy_adapter,
        delay_adapter,
    )
    from brainlearn_server.registry import demo_manifest

    altered = replace(
        DEMO_NODES["demo.copy"],
        parameters={
            "text": DemoParameter(
                id="text",
                label="Text",
                value_type="string",
                default="contract-bytes",
                description="Contract default.",
            )
        },
    )
    monkeypatch.setitem(DEMO_NODES, "demo.copy", altered)
    staged = copy_adapter(
        NodeContext(
            node_id="probe",
            node_type="demo.copy",
            parameters={},
            inputs={},
            staging_dir=tmp_path,
        )
    )
    assert staged[0].port_id == "output"
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "contract-bytes"
    assert demo_manifest("demo.copy").parameters[0].default == "contract-bytes"

    explicit = copy_adapter(
        NodeContext(
            node_id="probe",
            node_type="demo.copy",
            parameters={"text": "explicit-bytes"},
            inputs={},
            staging_dir=tmp_path,
        )
    )
    assert explicit
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "explicit-bytes"

    bounded = replace(
        DEMO_NODES["demo.delay"],
        parameters={
            "seconds": DemoParameter(
                id="seconds",
                label="Delay (seconds)",
                value_type="number",
                default=0.0,
                minimum=0.0,
                maximum=0.0,
            )
        },
    )
    monkeypatch.setitem(DEMO_NODES, "demo.delay", bounded)
    assert demo_manifest("demo.delay").parameters[0].maximum == 0.0
    assert (
        delay_adapter(
            NodeContext(
                node_id="probe",
                node_type="demo.delay",
                parameters={"seconds": 30.0},
                inputs={},
                staging_dir=tmp_path,
            )
        )
        == []
    )
