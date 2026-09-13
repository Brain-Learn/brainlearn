"""Step 4E.2: per-instance presentation stays outside computation identity."""

import json
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    PRESENTATION_ACCENTS,
    NodeInstance,
    NodePresentation,
    Workflow,
    migrate_workflow_dict,
    validate_workflow,
)
from brainlearn_server.app import app
from brainlearn_server.worker import build_run_record, workflow_identity
from fastapi.testclient import TestClient
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def _port(port_id: str, direction: str) -> dict[str, Any]:
    return {
        "id": port_id,
        "label": port_id,
        "direction": direction,
        "data_type": "raw_eeg",
        "required": True,
    }


def _node(node_id: str, presentation: dict[str, Any] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": node_id,
        "type": "demo.copy",
        "label": "Copy",
        "category": "Demo",
        "description": "Demonstration node.",
        "position": {"x": 0, "y": 0},
        "ports": [_port("output", "output")],
        "parameters": [{"id": "text", "label": "Text", "value": "hello", "required": False}],
        "pauses_for_review": False,
    }
    if presentation is not None:
        node["presentation"] = presentation
    return node


def _workflow(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": "presentation-flow",
        "metadata": {
            "name": "Presentation flow",
            "description": "",
            "created_with": "BrainLearn tests",
            "modality": "EEG",
            "status": "example",
        },
        "nodes": nodes,
        "edges": [],
    }


def test_presentation_defaults_to_manifest_values() -> None:
    assert NodePresentation().model_dump() == {
        "title": None,
        "accent": "teal",
        "compact": False,
        "notes": "",
    }


@pytest.mark.parametrize("accent", PRESENTATION_ACCENTS)
def test_every_allowlisted_accent_is_accepted(accent: str) -> None:
    assert NodePresentation(accent=accent).accent == accent


def test_title_bounds_are_enforced() -> None:
    assert NodePresentation(title="x" * 80).title == "x" * 80
    with pytest.raises(ValidationError):
        NodePresentation(title="")
    with pytest.raises(ValidationError):
        NodePresentation(title="x" * 81)


def test_notes_bound_is_enforced() -> None:
    assert NodePresentation(notes="n" * 2000).notes == "n" * 2000
    with pytest.raises(ValidationError):
        NodePresentation(notes="n" * 2001)


def test_unknown_accent_and_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        NodePresentation(accent="neon")
    with pytest.raises(ValidationError):
        NodePresentation.model_validate({"accent": "teal", "glow": True})


def test_node_without_presentation_still_loads() -> None:
    node = NodeInstance.model_validate(_node("plain"))
    assert node.presentation is None


def test_presentation_fixture_round_trips() -> None:
    source = json.loads((FIXTURES / "workflow-presentation-1.0.json").read_text(encoding="utf-8"))
    workflow = Workflow.model_validate(migrate_workflow_dict(source))
    restored = Workflow.model_validate_json(workflow.model_dump_json())
    assert restored == workflow
    assert workflow.nodes[0].presentation is not None
    assert workflow.nodes[0].presentation.title == "My copy step"  # type: ignore[union-attr]
    assert workflow.nodes[1].presentation is None


def test_presentation_changes_leave_workflow_identity_unchanged() -> None:
    plain = Workflow.model_validate(_workflow([_node("writer")]))
    customized = Workflow.model_validate(
        _workflow(
            [
                _node(
                    "writer",
                    {
                        "title": "Renamed step",
                        "accent": "rose",
                        "compact": True,
                        "notes": "Display only.",
                    },
                )
            ]
        )
    )
    assert validate_workflow(customized).valid
    assert workflow_identity(customized) == workflow_identity(plain)


def test_presentation_changes_leave_node_content_identity_unchanged() -> None:
    plain = Workflow.model_validate(_workflow([_node("writer")]))
    customized = Workflow.model_validate(
        _workflow([_node("writer", {"title": "Renamed", "accent": "amber"})])
    )
    plain_identities = [node.content_identity for node in build_run_record(plain).node_runs]
    custom_identities = [node.content_identity for node in build_run_record(customized).node_runs]
    assert plain_identities == custom_identities


def test_scientific_parameter_change_still_changes_identity() -> None:
    plain = Workflow.model_validate(_workflow([_node("writer")]))
    altered_dict = _workflow([_node("writer")])
    altered_dict["nodes"][0]["parameters"][0]["value"] = "different-bytes"
    altered = Workflow.model_validate(altered_dict)
    assert workflow_identity(altered) != workflow_identity(plain)


def test_validate_endpoint_echoes_presentation() -> None:
    client = TestClient(app)
    workflow = _workflow([_node("writer", {"title": "Echo", "accent": "blue"})])
    response = client.post("/api/workflows/validate", json=workflow)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["nodes"][0]["presentation"]["title"] == "Echo"
    assert body["workflow"]["nodes"][0]["presentation"]["accent"] == "blue"


def test_validate_endpoint_rejects_invalid_presentation() -> None:
    client = TestClient(app)
    workflow = _workflow([_node("writer", {"accent": "neon"}), _node("plain")])
    response = client.post("/api/workflows/validate", json=workflow)
    assert response.status_code == 422


def test_title_surrounding_whitespace_is_normalized() -> None:
    assert NodePresentation(title="  Padded step  ").title == "Padded step"


def test_whitespace_only_title_is_rejected() -> None:
    with pytest.raises(ValidationError):
        NodePresentation(title="   ")
    with pytest.raises(ValidationError):
        NodePresentation.model_validate({"title": "\t\n "})


def test_validate_endpoint_trims_titles_and_rejects_blank_ones() -> None:
    client = TestClient(app)
    padded = _workflow([_node("writer", {"title": "  Spaced  "})])
    response = client.post("/api/workflows/validate", json=padded)
    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["nodes"][0]["presentation"]["title"] == "Spaced"

    blank = _workflow([_node("writer", {"title": "   "})])
    assert client.post("/api/workflows/validate", json=blank).status_code == 422
