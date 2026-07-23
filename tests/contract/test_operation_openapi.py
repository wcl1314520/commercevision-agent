import json
from pathlib import Path

from commercevision_api.main import app


def test_operation_openapi_contract_is_committed_and_stable() -> None:
    root = Path(__file__).parents[2]
    committed = json.loads((root / "docs" / "api" / "openapi.json").read_text("utf-8"))

    assert committed == app.openapi()
    for path in (
        "/api/v1/operations",
        "/api/v1/operations/{operation_id}",
        "/api/v1/operator/dead-letters",
        "/api/v1/operator/dead-letters/{dead_letter_id}",
        "/api/v1/operator/dead-letters/{dead_letter_id}:replay",
        "/api/v1/operator/legacy-dead-letters",
        "/api/v1/operator/legacy-dead-letters/{dead_letter_id}",
    ):
        assert path in committed["paths"]

    replay = committed["paths"]["/api/v1/operator/dead-letters/{dead_letter_id}:replay"]["post"]
    assert replay["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DeadLetterReplayResponseV1"
    }
    assert {"400", "401", "403", "404", "409", "422"} <= set(replay["responses"])
    detail_schema = committed["components"]["schemas"]["DeadLetterDetailResponseV1"]
    assert {
        "child_dead_letters",
        "child_dead_letters_next_cursor",
        "replays_next_cursor",
    } <= set(detail_schema["required"])
    detail = committed["paths"]["/api/v1/operator/dead-letters/{dead_letter_id}"]["get"]
    assert {"child_limit", "child_cursor", "replay_limit", "replay_cursor"} <= {
        parameter["name"] for parameter in detail["parameters"]
    }
    operation_schema = committed["components"]["schemas"]["OperationResponseV1"]
    assert {
        "execution_deadline_at",
        "provider_request_id",
        "recovery_generation",
        "recovery_consumed_generation",
    } <= set(operation_schema["required"])
