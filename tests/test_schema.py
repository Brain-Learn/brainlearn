import json
from pathlib import Path

from brainlearn_core import Workflow

EXAMPLE = Path(__file__).parents[1] / "examples" / "eeg-first-look.workflow.json"


def test_example_workflow_round_trips_without_loss() -> None:
    source = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    workflow = Workflow.model_validate(source)
    serialized = workflow.model_dump_json()
    restored = Workflow.model_validate_json(serialized)

    assert workflow.schema_version == "1.0"
    assert restored == workflow
