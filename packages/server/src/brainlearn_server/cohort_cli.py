"""Interactive loopback CLI for explicit EEG cohort runs and QC decisions."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from brainlearn_core import (
    CohortRecordingState,
    Workflow,
    migrate_workflow_dict,
)
from brainlearn_core.schema import ParameterDefinition

POLL_SECONDS = 0.5
MAX_ARTIFACT_PREVIEW_BYTES = 10_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


_LOCAL_OPENER = build_opener(_NoRedirect())


class CohortCliError(RuntimeError):
    """A CLI input or local-service request cannot be completed safely."""


def _loopback_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CohortCliError("The service URL must use HTTP(S) and name a loopback host.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CohortCliError(
            "The service URL must not contain credentials, a query, or a fragment."
        )
    host = parsed.hostname
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback:
        raise CohortCliError("Cohort data can only be sent to a loopback BrainLearn service.")
    if parsed.path not in {"", "/"}:
        raise CohortCliError("The service URL must not include an API path prefix.")
    return value.rstrip("/")


class LocalApi:
    """Small stdlib-only HTTP client constrained to the local service."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = _loopback_base_url(base_url)
        if not token.strip():
            raise CohortCliError("A BrainLearn session token is required.")
        self.token = token.strip()

    def request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}{endpoint}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with _LOCAL_OPENER.open(request, timeout=30) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            try:
                body = json.loads(exc.read())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                body = None
            detail = body.get("detail") if isinstance(body, dict) else None
            raise CohortCliError(
                str(detail or f"BrainLearn service returned HTTP {exc.code}.")
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CohortCliError(
                f"Could not reach the loopback BrainLearn service at {self.base_url}: {exc}"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CohortCliError(
                "The BrainLearn service returned an invalid JSON response."
            ) from exc
        if not isinstance(result, dict):
            raise CohortCliError("The BrainLearn service returned an unexpected response.")
        return result

    def artifact(self, project_path: str, run_id: str, artifact_id: str) -> bytes:
        data = json.dumps(
            {"path": project_path, "run_id": run_id, "artifact_id": artifact_id}
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/artifacts/open",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with _LOCAL_OPENER.open(request, timeout=30) as response:
                value = response.read(MAX_ARTIFACT_PREVIEW_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise CohortCliError(f"Could not read a persisted QC diagnostic: {exc}") from exc
        if len(value) > MAX_ARTIFACT_PREVIEW_BYTES:
            raise CohortCliError("QC diagnostic exceeds the 10 MB CLI preview limit.")
        return cast(bytes, value)


def _read_workflow(path: Path, dataset_root: str) -> Workflow:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        workflow = Workflow.model_validate(migrate_workflow_dict(payload))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CohortCliError(f"Unable to read workflow JSON at {path}: {exc}") from exc
    nodes = []
    found = False
    for node in workflow.nodes:
        if node.type != "input.bids_eeg":
            nodes.append(node)
            continue
        parameters = []
        root_found = False
        for parameter in node.parameters:
            if parameter.id == "root":
                parameters.append(parameter.model_copy(update={"value": dataset_root}))
                root_found = True
            else:
                parameters.append(parameter)
        if not root_found:
            parameters.append(
                ParameterDefinition(
                    id="root",
                    label="BIDS dataset folder",
                    value=dataset_root,
                    required=True,
                )
            )
        nodes.append(node.model_copy(update={"parameters": parameters}))
        found = True
    if not found:
        raise CohortCliError("The selected workflow has no BIDS EEG input node.")
    return workflow.model_copy(update={"nodes": nodes})


def _choose_recordings(discovery: dict[str, Any], dataset_root: str) -> list[str]:
    recordings = discovery.get("recordings")
    if not isinstance(recordings, list):
        raise CohortCliError("BIDS discovery returned no recording list.")
    ready = [
        item for item in recordings if isinstance(item, dict) and item.get("status") == "ready"
    ]
    if not ready:
        raise CohortCliError(
            "No ready BIDS EEG recordings were found. Correct metadata and rescan."
        )
    print("Discovered BIDS EEG recordings:")
    for index, item in enumerate(ready, start=1):
        print(
            f"  {index}. sub-{item.get('subject')} {item.get('path')} · "
            f"{item.get('sampling_frequency_hz')} Hz · {item.get('eeg_channel_count')} channels"
        )
    defaults = [
        str(index)
        for index, item in enumerate(ready, start=1)
        if "ds005284" in dataset_root and item.get("subject") in {"001", "002", "003", "004", "005"}
    ]
    prompt = "Select recording numbers separated by commas"
    if defaults:
        prompt += f" [reference sub-001–sub-005: {', '.join(defaults)}]"
    answer = input(prompt + ": ").strip()
    selected = (
        [int(value.strip()) for value in answer.split(",") if value.strip()]
        if answer
        else [int(value) for value in defaults]
    )
    if not selected:
        raise CohortCliError("Select at least one recording.")
    if len(set(selected)) != len(selected) or any(
        index < 1 or index > len(ready) for index in selected
    ):
        raise CohortCliError("Recording selections must be unique valid numbers from the list.")
    return [str(ready[index - 1]["path"]) for index in selected]


def _run_review(api: LocalApi, project_path: str, run: dict[str, Any], workflow: Workflow) -> None:
    record = run.get("run")
    if not isinstance(record, dict) or not isinstance(record.get("node_runs"), list):
        raise CohortCliError("The paused child workflow has no node-run records.")
    nodes_by_id = {node.id: node for node in workflow.nodes}
    pending = [item for item in record["node_runs"] if item.get("state") == "waiting_for_review"]
    if not pending:
        raise CohortCliError("The child run is waiting, but no QC node identifies the pause.")
    for node_run in pending:
        node_id = str(node_run.get("node_id", "unknown"))
        definition = nodes_by_id.get(node_id)
        label = definition.label if definition else node_id
        print(f"\nQC pause: {label} ({node_run.get('node_type')})")
        print(
            f"Child run ID: {run.get('run_id')}. Open this run in the BrainLearn UI "
            "for visual inspection."
        )
        _print_upstream_diagnostics(api, project_path, str(run.get("run_id")), record, node_run)
        decision = input("Decision [a]pprove / [r]eject: ").strip().lower()
        if decision not in {"a", "approve", "approved", "r", "reject", "rejected"}:
            raise CohortCliError("Enter approve or reject; no QC decision was recorded.")
        approved = decision in {"a", "approve", "approved"}
        review_payload: dict[str, Any] = {
            "path": project_path,
            "run_id": run["run_id"],
            "node_run_id": node_run["id"],
            "decision": "approved" if approved else "rejected",
            "reviewer": input("Reviewer name/identifier: ").strip(),
            "note": input("Review note (required context; enter details or type none): ").strip(),
            "excluded_channel_names": [],
            "excluded_ica_components": [],
        }
        if approved and node_run.get("node_type") == "eeg.bad_channel_review":
            raw_names = input(
                "Bad channel names to exclude (comma-separated; empty explicitly approves none): "
            ).strip()
            review_payload["excluded_channel_names"] = [
                item.strip() for item in raw_names.split(",") if item.strip()
            ]
        if approved and node_run.get("node_type") == "eeg.ica_review":
            raw_components = input(
                "ICA component indices to exclude (comma-separated; "
                "empty explicitly excludes none): "
            ).strip()
            try:
                review_payload["excluded_ica_components"] = [
                    int(item.strip()) for item in raw_components.split(",") if item.strip()
                ]
            except ValueError as exc:
                raise CohortCliError(
                    "ICA component exclusions must be non-negative integers."
                ) from exc
            if any(item < 0 for item in review_payload["excluded_ica_components"]):
                raise CohortCliError("ICA component indices must be non-negative integers.")
        if not review_payload["reviewer"]:
            raise CohortCliError("A researcher identifier is required to record a QC decision.")
        if not review_payload["note"]:
            raise CohortCliError("A review note is required; enter 'none' if no additional note.")
        api.request("/api/runs/review", review_payload)


def _print_upstream_diagnostics(
    api: LocalApi,
    project_path: str,
    run_id: str,
    run_record: dict[str, Any],
    paused_node: dict[str, Any],
) -> None:
    upstream_ids = set(paused_node.get("dependencies", []))
    for node in run_record["node_runs"]:
        if node.get("id") not in upstream_ids:
            continue
        for artifact in node.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            media_type = str(artifact.get("media_type", "")).lower()
            if "json" not in media_type:
                continue
            try:
                value = json.loads(api.artifact(project_path, run_id, str(artifact["artifact_id"])))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            print(f"Persisted diagnostic {artifact.get('port_id')} from {node.get('node_type')}:")
            print(json.dumps(value, ensure_ascii=False, indent=2)[:4000])


def run_cohort_cli() -> int:
    parser = argparse.ArgumentParser(
        prog="brainlearn-cohort",
        description="Run one local BIDS EEG cohort with explicit per-recording QC decisions.",
    )
    parser.add_argument("--project", required=True, help="Existing BrainLearn project directory")
    parser.add_argument("--workflow", required=True, type=Path, help="Saved workflow JSON file")
    parser.add_argument("--dataset-root", required=True, help="Project-relative BIDS dataset path")
    parser.add_argument(
        "--recording",
        action="append",
        default=[],
        help="BIDS recording path; repeat to select multiple",
    )
    parser.add_argument(
        "--dataset-id", default=None, help="Pinned dataset identifier, e.g. ds005284"
    )
    parser.add_argument(
        "--dataset-version", default=None, help="Immutable dataset version, e.g. 1.0.0"
    )
    parser.add_argument("--seed", type=int, default=0, help="Non-negative base random seed")
    parser.add_argument(
        "--service-url", default="http://127.0.0.1:8000", help="Loopback service URL"
    )
    args = parser.parse_args()
    try:
        token = os.environ.get("BRAINLEARN_SESSION_TOKEN", "").strip() or getpass.getpass(
            "BrainLearn session token: "
        )
        api = LocalApi(args.service_url, token)
        project_path = str(Path(args.project).expanduser().resolve())
        workflow = _read_workflow(args.workflow, args.dataset_root)
        api.request("/api/projects/open", {"path": project_path})
        discovery = api.request(
            "/api/datasets/bids-eeg/discover",
            {"path": project_path, "relative_dir": args.dataset_root},
        )
        recording_paths = args.recording or _choose_recordings(discovery, args.dataset_root)
        dataset_id = args.dataset_id
        dataset_version = args.dataset_version
        if dataset_id is None and "ds005284" in args.dataset_root:
            dataset_id, dataset_version = "ds005284", dataset_version or "1.0.0"
        if dataset_id is not None and dataset_version is None:
            raise CohortCliError("Set --dataset-version whenever --dataset-id is supplied.")
        cohort = api.request(
            "/api/cohorts/start",
            {
                "path": project_path,
                "workflow": workflow.model_dump(mode="json"),
                "recording_paths": recording_paths,
                "seed": args.seed,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
            },
        )
        print(
            f"Started cohort {cohort['id']} for {cohort['selected_count']} selected recording(s)."
        )
        seen_review_ids: set[str] = set()
        while cohort["state"] in {"running", "waiting_for_review"}:
            cohort = api.request(
                "/api/cohorts/open", {"path": project_path, "cohort_id": cohort["id"]}
            )
            active = next(
                (
                    item
                    for item in cohort["recordings"]
                    if item["state"] in {"running", "waiting_for_review"}
                    and item.get("child_run_id")
                ),
                None,
            )
            if active and active["state"] == CohortRecordingState.WAITING_FOR_REVIEW.value:
                run = api.request(
                    "/api/runs/open", {"path": project_path, "run_id": active["child_run_id"]}
                )
                pending = [
                    item
                    for item in run.get("run", {}).get("node_runs", [])
                    if item.get("state") == "waiting_for_review"
                    and item.get("id") not in seen_review_ids
                ]
                if pending:
                    _run_review(api, project_path, run, workflow)
                    seen_review_ids.update(str(item["id"]) for item in pending)
                else:
                    print(
                        f"{active['recording_path']} is paused in child run "
                        f"{active['child_run_id']} (already presented for review)."
                    )
                    time.sleep(POLL_SECONDS)
            else:
                states = ", ".join(
                    f"{item['recording_path']}={item['state']}" for item in cohort["recordings"]
                )
                print(
                    f"Cohort {cohort['state']}: processed {cohort['processed_count']}/"
                    f"{cohort['selected_count']} · excluded {cohort['excluded_count']} · "
                    f"failed {cohort['failed_count']} · cancelled {cohort['cancelled_count']} · "
                    f"{states}"
                )
                time.sleep(POLL_SECONDS)
        if cohort["state"] == "succeeded":
            print("Cohort completed. Saved local artifacts:")
            print(f"  {project_path}/{cohort['report_path']}")
            print(f"  {project_path}/{cohort['summary_path']}")
            return 0
        print(
            f"Cohort ended as {cohort['state']}: "
            f"processed={cohort['processed_count']}, excluded={cohort['excluded_count']}, "
            f"failed={cohort['failed_count']}, cancelled={cohort['cancelled_count']}"
        )
        if cohort.get("failure_reason"):
            print(cohort["failure_reason"], file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped polling. Use the BrainLearn UI or API to cancel the active cohort.")
        return 130
    except (CohortCliError, ValueError, OSError) as exc:
        print(f"brainlearn-cohort: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cohort_cli())
