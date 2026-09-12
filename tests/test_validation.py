import json
from pathlib import Path

import pytest
from brainlearn_core import Edge, Workflow, validate_workflow

EXAMPLE = Path(__file__).parents[1] / "examples" / "eeg-first-look.workflow.json"


def load_workflow() -> Workflow:
    return Workflow.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))


def test_example_workflow_is_valid() -> None:
    assert validate_workflow(load_workflow()).valid


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda data: data["edges"][0]["source"].update(node_id="absent"), "missing_node"),
        (lambda data: data["edges"][0]["source"].update(port_id="absent"), "unknown_port"),
        (
            lambda data: data["edges"][0]["target"].update(node_id="filter", port_id="raw"),
            "incompatible_port_type",
        ),
        (
            lambda data: data["nodes"][0]["parameters"][0].update(value=None),
            "missing_required_parameter",
        ),
    ],
)
def test_validation_reports_actionable_graph_errors(mutation, expected_code: str) -> None:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    mutation(data)

    result = validate_workflow(Workflow.model_validate(data))

    assert not result.valid
    assert expected_code in {issue.code for issue in result.issues}


def test_validation_rejects_cycles() -> None:
    workflow = load_workflow()
    workflow.edges.append(
        Edge.model_validate(
            {
                "id": "cycle",
                "source": {"node_id": "report", "port_id": "report"},
                "target": {"node_id": "inspect", "port_id": "dataset"},
            }
        )
    )

    result = validate_workflow(workflow)

    assert "cycle" in {issue.code for issue in result.issues}
