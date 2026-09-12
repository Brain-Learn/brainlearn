from pathlib import Path

from brainlearn_core import (
    CanvasPosition,
    Edge,
    EdgeEndpoint,
    GraphMetadata,
    NodeManifest,
    Workflow,
    validate_workflow,
)
from brainlearn_server.registry import NODE_REGISTRY, instantiate_registered_node

EXAMPLE = Path(__file__).parents[1] / "examples" / "eeg-first-look.workflow.json"


def test_registry_manifests_round_trip_and_have_unique_identity() -> None:
    restored = [NodeManifest.model_validate_json(item.model_dump_json()) for item in NODE_REGISTRY]

    assert restored == list(NODE_REGISTRY)
    assert len({item.id for item in restored}) == len(restored) == 8


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
