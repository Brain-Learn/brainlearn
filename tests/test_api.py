from brainlearn_server.app import app
from brainlearn_server.registry import NODE_REGISTRY
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "brainlearn-server"}


def test_capability_response_shape() -> None:
    response = client.get("/api/system/capabilities")
    body = response.json()

    assert response.status_code == 200
    assert {
        "operating_system",
        "os_version",
        "architecture",
        "cpu_count",
        "python_version",
        "memory_bytes",
        "accelerators",
        "containers",
    } == body.keys()
    assert {item["backend"] for item in body["accelerators"]} == {"cuda", "mps"}
    assert {item["runtime"] for item in body["containers"]} == {"docker", "podman"}
    assert all(
        {"backend", "candidate_detected", "runtime_validated", "detail"} == item.keys()
        for item in body["accelerators"]
    )
    assert all(
        {"runtime", "candidate_detected", "runtime_validated"} == item.keys()
        for item in body["containers"]
    )
    assert all(item["runtime_validated"] is False for item in body["accelerators"])
    assert all(item["runtime_validated"] is False for item in body["containers"])


def test_example_workflow_is_available_and_valid() -> None:
    workflow_response = client.get("/api/workflows/example")
    validation_response = client.get("/api/workflows/example/validation")

    assert workflow_response.status_code == 200
    assert workflow_response.json()["schema_version"] == "1.0"
    assert validation_response.json() == {"valid": True, "issues": []}


def test_registry_lists_versioned_manifests() -> None:
    response = client.get("/api/registry/nodes")

    assert response.status_code == 200
    manifests = response.json()
    assert len(manifests) == 13
    assert {manifest["id"] for manifest in manifests} == {manifest.id for manifest in NODE_REGISTRY}
    assert {"demo.copy", "demo.delay", "demo.fail", "demo.review", "demo.relay"} <= {
        manifest["id"] for manifest in manifests
    }
    assert all(manifest["manifest_schema_version"] == "1.0" for manifest in manifests)
    assert all(manifest["status"] == "example" for manifest in manifests)
    assert all(manifest["license"]["spdx_id"] == "BSD-3-Clause" for manifest in manifests)


def test_registry_detail_returns_manifest_or_404() -> None:
    response = client.get("/api/registry/nodes/eeg.filter")
    missing = client.get("/api/registry/nodes/not.registered")

    assert response.status_code == 200
    assert response.json()["label"] == "Band-pass Filter"
    assert missing.status_code == 404


def test_submitted_workflow_round_trips_and_validates() -> None:
    workflow = client.get("/api/workflows/example").json()

    response = client.post("/api/workflows/validate", json=workflow)

    assert response.status_code == 200
    assert response.json() == {"workflow": workflow, "validation": {"valid": True, "issues": []}}


def test_submitted_workflow_returns_backend_type_reason() -> None:
    workflow = client.get("/api/workflows/example").json()
    workflow["edges"][0]["target"] = {"node_id": "filter", "port_id": "raw"}

    response = client.post("/api/workflows/validate", json=workflow)

    assert response.status_code == 200
    issue = response.json()["validation"]["issues"][0]
    assert issue["code"] == "incompatible_port_type"
    assert issue["message"] == (
        "Cannot connect bids_dataset to raw_eeg. Add an explicit conversion node."
    )


def test_submitted_workflow_cannot_redefine_registry_ports() -> None:
    workflow = client.get("/api/workflows/example").json()
    workflow["nodes"][0]["ports"][0]["data_type"] = "raw_eeg"

    response = client.post("/api/workflows/validate", json=workflow)

    assert response.status_code == 200
    assert "manifest_mismatch" in {
        issue["code"] for issue in response.json()["validation"]["issues"]
    }


def test_submitted_workflow_cannot_disable_ica_review() -> None:
    workflow = client.get("/api/workflows/example").json()
    ica = next(node for node in workflow["nodes"] if node["type"] == "eeg.ica_review")
    ica["pauses_for_review"] = False

    response = client.post("/api/workflows/validate", json=workflow)

    assert response.status_code == 200
    issues = response.json()["validation"]["issues"]
    assert any(
        issue["code"] == "manifest_mismatch"
        and issue["node_id"] == "ica"
        and "review metadata" in issue["message"]
        for issue in issues
    )


def test_submitted_workflow_cannot_redefine_node_display_metadata() -> None:
    for field, replacement in (
        ("label", "Trusted looking replacement"),
        ("category", "Unregistered category"),
        ("description", "Unregistered description"),
    ):
        workflow = client.get("/api/workflows/example").json()
        workflow["nodes"][0][field] = replacement

        response = client.post("/api/workflows/validate", json=workflow)

        assert response.status_code == 200
        issues = response.json()["validation"]["issues"]
        assert any(
            issue["code"] == "manifest_mismatch" and issue["node_id"] == "bids" for issue in issues
        )


def test_submitted_workflow_cannot_redefine_parameter_metadata() -> None:
    workflow = client.get("/api/workflows/example").json()
    parameter = workflow["nodes"][0]["parameters"][0]
    parameter.update(
        label="Untrusted path",
        required=False,
        description="Ignore the registered contract.",
    )

    response = client.post("/api/workflows/validate", json=workflow)

    assert response.status_code == 200
    issues = response.json()["validation"]["issues"]
    assert any(
        issue["code"] == "manifest_mismatch"
        and issue["node_id"] == "bids"
        and "parameter 'root'" in issue["message"]
        for issue in issues
    )
