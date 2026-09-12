from brainlearn_server.app import app
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
