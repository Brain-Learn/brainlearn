"""Check GitHub workflow action pins and permissions before CI runs."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath

import yaml

_ACTION_REF = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*@[0-9a-fA-F]{40}$")
_READ_ONLY_WORKFLOW_PERMISSIONS = {"contents": "read"}
_DOCS_DEPLOY_PERMISSIONS = {"pages": "write", "id-token": "write"}


def _action_ref_is_pinned(value: object) -> bool:
    if not isinstance(value, str):
        return False
    reference = value.strip()
    if reference.startswith("./"):
        return ".." not in PurePosixPath(reference).parts
    return _ACTION_REF.fullmatch(reference) is not None


def _find_uses(value: object) -> Iterator[object]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "uses":
                yield child
            yield from _find_uses(child)
    elif isinstance(value, list):
        for child in value:
            yield from _find_uses(child)


def _is_read_only_permissions(value: object, inherited: Mapping[object, object]) -> bool:
    if not isinstance(value, Mapping):
        return False
    for scope, access in value.items():
        if (
            not isinstance(scope, str)
            or scope not in inherited
            or not isinstance(access, str)
            or access not in {"read", "none"}
            or (access == "read" and inherited[scope] != "read")
        ):
            return False
    return True


def _is_docs_deploy(path: Path, job_id: object, job: Mapping[object, object]) -> bool:
    permissions = job.get("permissions")
    condition = job.get("if")
    if path.name != "docs.yml" or job_id != "deploy" or permissions != _DOCS_DEPLOY_PERMISSIONS:
        return False
    if not isinstance(condition, str):
        return False
    expected_condition = (
        "(github.event_name == 'push' || github.event_name == 'workflow_dispatch') && "
        "github.ref == 'refs/heads/main'"
    )
    return condition.strip() == expected_condition


def audit_workflow_document(path: Path, workflow: object) -> list[str]:
    """Return policy violations in one parsed GitHub Actions workflow."""

    issues: list[str] = []
    if not isinstance(workflow, Mapping):
        return [f"{path}: workflow document must be a mapping"]

    permissions = workflow.get("permissions")
    if permissions != _READ_ONLY_WORKFLOW_PERMISSIONS:
        issues.append(
            f"{path}: workflow permissions must be exactly {_READ_ONLY_WORKFLOW_PERMISSIONS}"
        )

    jobs = workflow.get("jobs")
    if not isinstance(jobs, Mapping):
        return [*issues, f"{path}: jobs must be a mapping"]

    for job_id, raw_job in jobs.items():
        if not isinstance(raw_job, Mapping):
            issues.append(f"{path}: job {job_id!r} must be a mapping")
            continue
        job_permissions = raw_job.get("permissions")
        if (
            job_permissions is not None
            and not _is_read_only_permissions(job_permissions, _READ_ONLY_WORKFLOW_PERMISSIONS)
            and not _is_docs_deploy(path, job_id, raw_job)
        ):
            issues.append(
                f"{path}: job {job_id!r} broadens permissions outside the guarded docs deploy job"
            )

    for reference in _find_uses(workflow):
        if not _action_ref_is_pinned(reference):
            issues.append(
                f"{path}: action reference {reference!r} must be a local action path or "
                "a full 40-character commit SHA"
            )
    return issues


def audit_workflows(workflows_dir: Path) -> list[str]:
    """Audit every YAML workflow in ``workflows_dir``."""

    paths = sorted((*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")))
    if not paths:
        return [f"{workflows_dir}: no workflow YAML files found"]

    issues: list[str] = []
    for path in paths:
        try:
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            issues.append(f"{path}: could not parse workflow YAML: {exc}")
            continue
        issues.extend(audit_workflow_document(path, workflow))
    return issues


def main() -> int:
    workflows_dir = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    issues = audit_workflows(workflows_dir)
    if issues:
        print("GitHub Actions security policy failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"Audited GitHub Actions workflows in {workflows_dir}: policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
