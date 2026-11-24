from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
_SPEC = spec_from_file_location(
    "check_action_security", REPOSITORY_ROOT / "scripts" / "check_action_security.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_ACTION_SECURITY = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ACTION_SECURITY)

_action_ref_is_pinned = _ACTION_SECURITY._action_ref_is_pinned
audit_workflow_document = _ACTION_SECURITY.audit_workflow_document
audit_workflows = _ACTION_SECURITY.audit_workflows


def test_repository_workflows_use_pinned_actions_and_minimal_permissions() -> None:
    assert audit_workflows(REPOSITORY_ROOT / ".github" / "workflows") == []


def test_action_references_require_a_full_commit_sha_or_local_path() -> None:
    assert _action_ref_is_pinned("actions/checkout@" + "a" * 40)
    assert _action_ref_is_pinned("owner/repo/actions/setup@" + "b" * 40)
    assert _action_ref_is_pinned("./.github/actions/local")
    assert not _action_ref_is_pinned("actions/checkout@v7")
    assert not _action_ref_is_pinned("actions/checkout@" + "a" * 39)
    assert not _action_ref_is_pinned("docker://example/action:latest")
    assert not _action_ref_is_pinned("./.github/actions/../outside")


def test_unpinned_action_is_reported() -> None:
    workflow = {
        "permissions": {"contents": "read"},
        "jobs": {"check": {"steps": [{"uses": "actions/checkout@v7"}]}},
    }

    issues = audit_workflow_document(Path("ci.yml"), workflow)

    assert any("actions/checkout@v7" in issue for issue in issues)


def test_workflow_and_job_write_permissions_are_rejected() -> None:
    workflow = {
        "permissions": "write-all",
        "jobs": {"pull-request": {"permissions": {"contents": "write"}}},
    }

    issues = audit_workflow_document(Path("ci.yml"), workflow)

    assert any("workflow permissions must be exactly" in issue for issue in issues)
    assert any("broadens permissions" in issue for issue in issues)


def test_docs_write_permissions_require_the_main_or_dispatch_guard() -> None:
    workflow = {
        "permissions": {"contents": "read"},
        "jobs": {
            "deploy": {
                "permissions": {"pages": "write", "id-token": "write"},
                "if": "github.event_name == 'pull_request'",
            }
        },
    }

    issues = audit_workflow_document(Path("docs.yml"), workflow)

    assert any("broadens permissions" in issue for issue in issues)


def test_docs_write_permissions_allow_only_main_push_or_dispatch() -> None:
    workflow = {
        "permissions": {"contents": "read"},
        "jobs": {
            "deploy": {
                "permissions": {"pages": "write", "id-token": "write"},
                "if": (
                    "(github.event_name == 'push' || github.event_name == 'workflow_dispatch') "
                    "&& github.ref == 'refs/heads/main'"
                ),
            }
        },
    }

    assert audit_workflow_document(Path("docs.yml"), workflow) == []
