"""Exercise the Phase 1 durable workflow through the public HTTP API."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from uuid import uuid4

API_BASE = f"http://127.0.0.1:{os.getenv('CV_API_HOST_PORT', '18000')}/api/v1"
WORKSPACE_ID = "phase1-verification"
ACTOR_ID = "phase1-verifier"


def request_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-Workspace-Id": WORKSPACE_ID,
        "X-Actor-Id": ACTOR_ID,
        "X-Trace-Id": f"phase1-{uuid4()}",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        method=method,
        headers=headers,
        data=(json.dumps(payload).encode() if payload is not None else None),
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {body}") from exc


def wait_for_status(
    workflow_id: str,
    expected: str,
    *,
    attempts: int = 60,
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for _ in range(attempts):
        latest = request_json("GET", f"/workflows/{workflow_id}")
        if latest["status"] == expected:
            return latest
        if latest["status"] in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"workflow entered terminal state: {latest}")
        time.sleep(1)
    raise RuntimeError(f"workflow {workflow_id} did not reach {expected}; latest state: {latest}")


def step_output(workflow: dict[str, Any], step_type: str, key: str) -> Any:
    matches = [step for step in workflow["steps"] if step["step_type"] == step_type]
    if not matches or not matches[-1]["output_data"]:
        raise RuntimeError(f"missing {step_type} output in workflow {workflow['id']}")
    return matches[-1]["output_data"][key]


def verify() -> None:
    run_id = uuid4().hex
    create_key = f"phase1-create-{run_id}"
    create_payload = {
        "workflow_type": "FIXTURE_IMAGE_GENERATION",
        "input_data": {"fixture_config": {"count": 3}},
        "retention_hours": 72,
    }
    created = request_json(
        "POST",
        "/workflows",
        payload=create_payload,
        idempotency_key=create_key,
    )
    duplicate = request_json(
        "POST",
        "/workflows",
        payload=create_payload,
        idempotency_key=create_key,
    )
    if created["id"] != duplicate["id"]:
        raise RuntimeError("workflow create idempotency returned different resources")

    workflow_id = created["id"]
    plan_wait = wait_for_status(workflow_id, "AWAITING_PLAN_APPROVAL")
    plan_ref = step_output(plan_wait, "CREATE_PLAN", "creative_plan_ref")
    request_json(
        "POST",
        f"/workflows/{workflow_id}/creative-plan:approve",
        payload={
            "expected_workflow_version": plan_wait["version"],
            "subject_id": plan_ref,
            "subject_version": 1,
            "decision": "APPROVE",
        },
        idempotency_key=f"phase1-plan-{run_id}",
    )

    result_wait = wait_for_status(workflow_id, "AWAITING_RESULT_APPROVAL")
    evaluation_ref = step_output(
        result_wait,
        "EVALUATE_RESULTS",
        "evaluation_report_ref",
    )
    request_json(
        "POST",
        f"/workflows/{workflow_id}/results:approve",
        payload={
            "expected_workflow_version": result_wait["version"],
            "subject_id": evaluation_ref,
            "subject_version": 1,
            "decision": "APPROVE",
        },
        idempotency_key=f"phase1-result-{run_id}",
    )

    completed = wait_for_status(workflow_id, "COMPLETED")
    if len(completed["attempts"]) != 1:
        raise RuntimeError(f"expected one effective attempt, got {completed['attempts']}")
    if len(completed["result_data"]["candidate_refs"]) != 3:
        raise RuntimeError("fixture candidate count does not match the accepted request")
    print(
        "Phase 1 verification passed: "
        f"workflow={workflow_id} version={completed['version']} "
        f"attempts={len(completed['attempts'])}"
    )


if __name__ == "__main__":
    verify()
