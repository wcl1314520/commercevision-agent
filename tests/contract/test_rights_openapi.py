import json
from pathlib import Path

from commercevision_api.main import app


def _parameters_by_name(operation: dict[str, object]) -> dict[str, dict[str, object]]:
    parameters = operation.get("parameters", [])
    assert isinstance(parameters, list)
    return {
        str(parameter["name"]): parameter for parameter in parameters if isinstance(parameter, dict)
    }


def test_rights_openapi_is_versioned_bounded_and_committed() -> None:
    committed = json.loads(
        (Path(__file__).parents[2] / "docs" / "api" / "openapi.json").read_text(encoding="utf-8")
    )
    generated = app.openapi()

    assert committed == generated
    paths = generated["paths"]
    rights_path = "/api/v1/assets/{asset_id}/rights"
    mutations = (
        paths[rights_path]["post"],
        paths["/api/v1/assets/{asset_id}/rights:replace"]["post"],
        paths["/api/v1/assets/{asset_id}/rights:revoke"]["post"],
        paths["/api/v1/assets/{asset_id}:block"]["post"],
    )
    for mutation in mutations:
        parameters = _parameters_by_name(mutation)
        assert parameters["Idempotency-Key"]["required"] is True
        assert parameters["X-Actor-Id"]["required"] is True
        assert mutation["responses"]["409"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/ErrorResponse")
        assert {"400", "401", "403", "404", "409", "422"}.issubset(mutation["responses"])
        assert {"410", "503"}.isdisjoint(mutation["responses"])

    register = paths[rights_path]["post"]
    assert register["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RightsMutationResponseV1"
    )

    history = paths[rights_path]["get"]
    history_parameters = _parameters_by_name(history)
    assert history_parameters["limit"]["schema"]["maximum"] == 100
    assert history["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RightsHistoryResponseV1"
    )
    assert {"400", "401", "403", "404", "422"}.issubset(history["responses"])
    assert {"409", "410", "503"}.isdisjoint(history["responses"])

    usability = paths["/api/v1/assets/{asset_id}/usability:check"]["post"]
    assert usability["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RightsUsabilityResponseV1"
    )
    assert {"400", "401", "403", "404", "422"}.issubset(usability["responses"])
    assert {"409", "410", "503"}.isdisjoint(usability["responses"])
    rights_operations = (*mutations, history, usability)
    assert all(
        "upload" not in response.get("description", "").lower()
        and "object storage" not in response.get("description", "").lower()
        for operation in rights_operations
        for response in operation["responses"].values()
    )
    decision_schema = generated["components"]["schemas"]["RightsUsabilityResponseV1"]
    assert {
        "authorized",
        "reason_code",
        "rights_record_id",
        "rights_record_version",
        "decided_at",
    }.issubset(decision_schema["required"])
