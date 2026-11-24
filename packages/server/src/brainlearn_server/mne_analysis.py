"""Allowlisted MNE epoch, descriptive analysis, and run-record report adapters."""

from __future__ import annotations

import html
import json
import math
import shutil
from pathlib import Path
from typing import Any

from brainlearn_server.demo_nodes import (
    ControlledFailure,
    NodeContext,
    StagedOutput,
    _require_cancel,
)
from brainlearn_server.mne_ica import _ica_work_dir, _verified_copy

EPOCH_DIAGNOSTICS_MEDIA_TYPE = "application/vnd.brainlearn.eeg-epoch-diagnostics+json"
ERP_SUMMARY_MEDIA_TYPE = "application/vnd.brainlearn.eeg-erp-summary+json"
PSD_SUMMARY_MEDIA_TYPE = "application/vnd.brainlearn.eeg-psd-summary+json"


def _number(ctx: NodeContext, name: str, *, integer: bool = False) -> float | int:
    value = ctx.parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControlledFailure(f"Set the required numeric parameter {name!r} explicitly.")
    result = int(value) if integer else float(value)
    if not math.isfinite(float(result)):
        raise ControlledFailure(f"Parameter {name!r} must be finite.")
    if integer and result != value:
        raise ControlledFailure(f"Parameter {name!r} must be an integer.")
    return result


def _boolean(ctx: NodeContext, name: str) -> bool:
    value = ctx.parameters.get(name)
    if not isinstance(value, bool):
        raise ControlledFailure(f"Set the required boolean parameter {name!r} explicitly.")
    return value


def _label(ctx: NodeContext, name: str) -> str:
    value = ctx.parameters.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ControlledFailure(f"Set the required text parameter {name!r} explicitly.")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json_artifact(ctx: NodeContext, port: str, target: Path) -> dict[str, Any]:
    artifact = ctx.input_artifacts.get(port)
    if artifact is None or not artifact.media_type.endswith("+json"):
        raise ControlledFailure(f"The {port!r} input must be a persisted BrainLearn JSON artifact.")
    _verified_copy(ctx, port, target)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledFailure(f"The {port!r} input artifact is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ControlledFailure(f"The {port!r} input artifact has an unsupported structure.")
    return payload


def epoch_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Create MNE epochs with mandatory event, timing, baseline, and rejection settings."""

    _require_cancel(ctx)
    artifact = ctx.input_artifacts.get("reviewed")
    if (
        artifact is None
        or artifact.media_type != "application/octet-stream"
        or not artifact.path.endswith(".fif")
    ):
        raise ControlledFailure("Epoching requires the current reviewed EEG FIF artifact.")
    work_dir = _ica_work_dir(ctx, "epoch-work")
    raw = None
    epochs = None
    try:
        source = work_dir / "reviewed-eeg_raw.fif"
        _verified_copy(ctx, "reviewed", source)
        mne = __import__("mne")
        try:
            raw = mne.io.read_raw_fif(source, preload=False, verbose="ERROR")
        except Exception as exc:
            raise ControlledFailure(f"MNE could not open the reviewed EEG FIF: {exc}") from exc
        from brainlearn_mne.analysis import MneAnalysisError, create_epochs

        try:
            result = create_epochs(
                raw,
                event_label=_label(ctx, "event"),
                tmin_seconds=float(_number(ctx, "tmin")),
                tmax_seconds=float(_number(ctx, "tmax")),
                baseline_enabled=_boolean(ctx, "baseline_enabled"),
                baseline_start_seconds=float(_number(ctx, "baseline_start")),
                baseline_end_seconds=float(_number(ctx, "baseline_end")),
                reject_eeg_peak_to_peak_uv=float(_number(ctx, "reject_eeg_peak_to_peak_uv")),
                reject_by_annotation=_boolean(ctx, "reject_by_annotation"),
                cancel_check=lambda: _require_cancel(ctx),
            )
        except MneAnalysisError as exc:
            raise ControlledFailure(str(exc)) from exc
        epochs = result.epochs
        epoch_path = ctx.staging_dir / "reviewed-epochs-epo.fif"
        diagnostics_path = ctx.staging_dir / "epoch-diagnostics.json"
        if epoch_path.exists() or diagnostics_path.exists():
            raise ControlledFailure("Epoch output paths already exist; refusing to overwrite.")
        try:
            epochs.save(epoch_path, overwrite=False, verbose="ERROR")
        except Exception as exc:
            raise ControlledFailure(f"MNE could not persist epochs: {exc}") from exc
        _write_json(diagnostics_path, result.diagnostics)
        _require_cancel(ctx)
        return [
            StagedOutput("epochs", epoch_path.name, "application/octet-stream"),
            StagedOutput("diagnostics", diagnostics_path.name, EPOCH_DIAGNOSTICS_MEDIA_TYPE),
        ]
    finally:
        if epochs is not None:
            del epochs
        if raw is not None:
            raw.close()
        shutil.rmtree(work_dir, ignore_errors=True)


def erp_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Summarize the channel/time mean and within-recording epoch SD."""

    _require_cancel(ctx)
    artifact = ctx.input_artifacts.get("epochs")
    if (
        artifact is None
        or artifact.media_type != "application/octet-stream"
        or not artifact.path.endswith("-epo.fif")
    ):
        raise ControlledFailure("ERP averaging requires a current MNE epochs FIF artifact.")
    work_dir = _ica_work_dir(ctx, "erp-work")
    epochs = None
    try:
        source = work_dir / "epochs-epo.fif"
        _verified_copy(ctx, "epochs", source)
        mne = __import__("mne")
        try:
            epochs = mne.read_epochs(source, preload=True, verbose="ERROR")
        except Exception as exc:
            raise ControlledFailure(f"MNE could not read the epochs artifact: {exc}") from exc
        from brainlearn_mne.analysis import MneAnalysisError, erp_summary

        try:
            summary = erp_summary(epochs, cancel_check=lambda: _require_cancel(ctx))
        except MneAnalysisError as exc:
            raise ControlledFailure(str(exc)) from exc
        summary_path = ctx.staging_dir / "erp-summary.json"
        _write_json(summary_path, summary)
        return [StagedOutput("evoked", summary_path.name, ERP_SUMMARY_MEDIA_TYPE)]
    finally:
        if epochs is not None:
            del epochs
        shutil.rmtree(work_dir, ignore_errors=True)


def psd_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Estimate Welch PSD from the exact retained epoch artifact used for ERP."""

    _require_cancel(ctx)
    artifact = ctx.input_artifacts.get("epochs")
    if (
        artifact is None
        or artifact.media_type != "application/octet-stream"
        or not artifact.path.endswith("-epo.fif")
    ):
        raise ControlledFailure("PSD requires the same current MNE epochs FIF artifact as ERP.")
    work_dir = _ica_work_dir(ctx, "psd-work")
    epochs = None
    try:
        source = work_dir / "epochs-epo.fif"
        _verified_copy(ctx, "epochs", source)
        mne = __import__("mne")
        try:
            epochs = mne.read_epochs(source, preload=True, verbose="ERROR")
        except Exception as exc:
            raise ControlledFailure(f"MNE could not read the epochs artifact: {exc}") from exc
        from brainlearn_mne.analysis import MneAnalysisError, psd_summary

        try:
            summary = psd_summary(
                epochs,
                fmin_hz=float(_number(ctx, "fmin_hz")),
                fmax_hz=float(_number(ctx, "fmax_hz")),
                n_fft=int(_number(ctx, "n_fft", integer=True)),
                n_per_seg=int(_number(ctx, "n_per_seg", integer=True)),
                n_overlap=int(_number(ctx, "n_overlap", integer=True)),
                window=_label(ctx, "window"),
                cancel_check=lambda: _require_cancel(ctx),
            )
        except MneAnalysisError as exc:
            raise ControlledFailure(str(exc)) from exc
        summary_path = ctx.staging_dir / "psd-summary.json"
        _write_json(summary_path, summary)
        return [StagedOutput("spectrum", summary_path.name, PSD_SUMMARY_MEDIA_TYPE)]
    finally:
        if epochs is not None:
            del epochs
        shutil.rmtree(work_dir, ignore_errors=True)


def report_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Write a self-contained descriptive HTML report from durable run records."""

    _require_cancel(ctx)
    work_dir = _ica_work_dir(ctx, "report-work")
    try:
        erp = _read_json_artifact(ctx, "evoked", work_dir / "evoked.json")
        psd = _read_json_artifact(ctx, "spectrum", work_dir / "spectrum.json")
        diagnostics = _read_json_artifact(ctx, "diagnostics", work_dir / "epoch-diagnostics.json")
        if ctx.project_path is None or ctx.run_id is None or ctx.run_store is None:
            raise ControlledFailure("Reports require a persisted project run.")
        record = ctx.run_store.get_run(ctx.project_path, ctx.run_id)
        executed = [
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "node_version": node.node_version,
                "state": node.state.value,
                "parameters": node.parameters,
                "seed": node.seed,
                "artifacts": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "sha256": artifact.sha256,
                        "byte_size": artifact.byte_size,
                        "port_id": artifact.port_id,
                    }
                    for artifact in node.artifacts
                ],
            }
            for node in record.node_runs
        ]
        reviews = [
            review.model_dump(mode="json")
            for review in ctx.run_store.list_qc_reviews(ctx.project_path, ctx.run_id)
        ]
        from brainlearn_server.registry import NODE_REGISTRY_BY_ID

        citations: dict[str, dict[str, str | None]] = {}
        for node in record.node_runs:
            manifest = NODE_REGISTRY_BY_ID.get(node.node_type)
            if manifest is None:
                continue
            candidates = [*manifest.citations]
            for dependency in manifest.software_dependencies:
                candidates.extend(dependency.citations)
            for citation in candidates:
                identity = citation.doi or citation.url or citation.title
                citations[identity] = {
                    "title": citation.title,
                    "doi": citation.doi,
                    "url": citation.url,
                }

        subject = "unknown"
        inspect_node = next(
            (node for node in record.node_runs if node.node_type == "eeg.inspect"), None
        )
        if inspect_node is not None:
            path = str(inspect_node.parameters.get("recording_path", ""))
            subject = next((part for part in Path(path).parts if part.startswith("sub-")), subject)
        safe = html.escape
        citation_html = "".join(
            "<li>"
            + safe(item["title"] or "")
            + (f" — DOI: {safe(item['doi'])}" if item["doi"] else "")
            + (f' — <a href="{safe(item["url"])}">source</a>' if item["url"] else "")
            + "</li>"
            for item in sorted(citations.values(), key=lambda item: item["title"] or "")
        )
        review_html = (
            "".join(
                "<li>"
                + safe(str(item.get("node_id", "QC")))
                + ": "
                + safe(str(item.get("decision", "unknown")))
                + "; reviewer: "
                + safe(str(item.get("reviewer", "not recorded")))
                + "; note: "
                + safe(str(item.get("note", "")))
                + "</li>"
                for item in reviews
            )
            or "<li>No completed QC decisions were recorded.</li>"
        )
        methods = html.escape(
            json.dumps(
                {
                    "workflow_id": record.workflow_id,
                    "workflow_identity": record.workflow_identity,
                    "environment": record.environment.model_dump(mode="json"),
                    "run_seed": record.seed,
                    "nodes": executed,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        event_label = safe(str(diagnostics.get("selected_event_label", "not recorded")))
        event_count = safe(str(diagnostics.get("selected_event_count", "not recorded")))
        retained_count = safe(str(diagnostics.get("retained_epoch_count", "not recorded")))
        dropped_count = safe(str(diagnostics.get("dropped_epoch_count", "not recorded")))
        diagnostics_json = safe(
            json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, indent=2)
        )
        erp_count = safe(str(erp.get("epoch_count", "not recorded")))
        psd_count = safe(str(psd.get("epoch_count", "not recorded")))
        report = "\n".join(
            [
                "<!doctype html>",
                '<html lang="en"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                f"<title>BrainLearn descriptive EEG report — {safe(subject)}</title>",
                "<style>body{font:16px/1.55 system-ui,sans-serif;max-width:1100px;",
                "margin:2rem auto;padding:0 1rem;color:#18232b}pre{overflow:auto;",
                "background:#f4f7f8;padding:1rem;border-radius:.5rem}.notice{",
                "background:#fff4d8;padding:1rem;border-left:4px solid #bd8400}</style>",
                "</head><body>",
                f"<h1>Descriptive EEG report — {safe(subject)}</h1>",
                '<p class="notice">Research software output. This report has not received '
                "independent EEG methods approval and is not a clinical or inferential result. "
                "Review event interpretation, preprocessing, exclusions, and methods "
                "before use.</p>",
                "<h2>Event and epoch diagnostics</h2>",
                f"<p>Exact event label: {event_label}. Matching events: {event_count}. "
                f"Retained epochs: {retained_count}. Dropped epochs: {dropped_count}.</p>",
                f"<pre>{diagnostics_json}</pre>",
                "<h2>Analysis summary</h2>",
                f"<p>ERP epochs: {erp_count}. PSD epochs: {psd_count}.</p>",
                "<h2>Human review placeholders</h2><ul>",
                "<li>Reviewer: add name and date after review.</li>",
                "<li>Confirm event-label meaning and provisional settings for the "
                "research question.</li>",
                "<li>Document any additional exclusions and interpretation limits.</li></ul>",
                f"<h2>Quality-control decisions</h2><ul>{review_html}</ul>",
                f"<h2>Executed methods and provenance</h2><pre>{methods}</pre>",
                f"<h2>Citations for executed methods</h2><ul>{citation_html}</ul>",
                "</body></html>",
            ]
        )
        target = ctx.staging_dir / "report.html"
        target.write_text(report, encoding="utf-8")
        _require_cancel(ctx)
        return [StagedOutput("report", target.name, "text/html")]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
