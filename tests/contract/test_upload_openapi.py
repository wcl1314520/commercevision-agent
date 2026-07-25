import json
from pathlib import Path

from commercevision_api.main import app


def _parameters_by_name(operation: dict[str, object]) -> dict[str, dict[str, object]]:
    parameters = operation.get("parameters", [])
    assert isinstance(parameters, list)
    return {
        str(parameter["name"]): parameter for parameter in parameters if isinstance(parameter, dict)
    }


def test_upload_session_openapi_is_versioned_and_committed() -> None:
    committed = json.loads(
        (Path(__file__).parents[2] / "docs" / "api" / "openapi.json").read_text(encoding="utf-8")
    )
    generated = app.openapi()

    assert committed == generated
    assert "/api/v1/upload-sessions" in generated["paths"]
    assert "/api/v1/upload-sessions/{upload_session_id}" in generated["paths"]
    assert "/api/v1/upload-sessions/{upload_session_id}:abort" in generated["paths"]
    assert "/api/v1/upload-sessions/{upload_session_id}:finalize" in generated["paths"]
    assert "/api/v1/assets/{asset_id}" in generated["paths"]

    create = generated["paths"]["/api/v1/upload-sessions"]["post"]
    assert create["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/UploadSessionCreateResponseV1"
    )
    assert create["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorResponse"
    )
    create_actor = _parameters_by_name(create)["X-Actor-Id"]
    assert create_actor["in"] == "header"
    assert create_actor["required"] is True
    assert create_actor["schema"]["minLength"] == 1
    assert create_actor["schema"]["maxLength"] == 128

    abort = generated["paths"]["/api/v1/upload-sessions/{upload_session_id}:abort"]["post"]
    finalize = generated["paths"]["/api/v1/upload-sessions/{upload_session_id}:finalize"]["post"]
    for mutation in (abort, finalize):
        actor = _parameters_by_name(mutation)["X-Actor-Id"]
        assert actor["required"] is True
        assert actor["schema"]["minLength"] == 1
        assert actor["schema"]["maxLength"] == 128
    assert finalize["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/UploadFinalizeResponseV1"
    )
    assert finalize["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorResponse"
    )

    reads = (
        generated["paths"]["/api/v1/upload-sessions/{upload_session_id}"]["get"],
        generated["paths"]["/api/v1/assets/{asset_id}"]["get"],
    )
    assert all("X-Actor-Id" not in _parameters_by_name(read) for read in reads)
