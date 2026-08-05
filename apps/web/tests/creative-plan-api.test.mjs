import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CreativePlanApi,
  newCreativePlanIdempotencyKey,
} from "../lib/creative-plan-api";
import {
  CreativePlanProtocolError,
  decodeCreativePlanCurrentResponse,
  decodeWorkflowResponse,
} from "../lib/creative-plan-api-decoders";

const PLAN_ID = "019f8a00-0000-7000-8000-000000000121";
const PLAN_VERSION_ID = "019f8a00-0000-7000-8000-000000000123";
const WORKFLOW_ID = "019f8a00-0000-7000-8000-000000000122";
const APPROVAL_ID = "019f8a00-0000-7000-8000-000000000124";

function planResponse(overrides = {}) {
  const version = {
    id: PLAN_VERSION_ID,
    workspace_id: "catalog-demo",
    workflow_id: WORKFLOW_ID,
    creative_plan_id: PLAN_ID,
    version_number: 2,
    supersedes_version_id: "019f8a00-0000-7000-8000-000000000120",
    source: "AGENT",
    payload: {
      schema_version: "creative-plan.v1",
      directions: [
        {
          key: "hero-main",
          image_role: "HERO",
          scene: "Clean studio surface",
          composition: "Centered product with negative space",
          camera: "85mm eye-level",
          lighting: "Soft key with controlled rim",
          color_direction: "Brand blue with neutral support",
          product_constraints: ["Preserve packaging geometry"],
          required_elements: ["Product label"],
          prohibited_elements: ["Unsupported claims"],
          citation_selections: [
            {
              citation_id: "019f8a00-0000-7000-8000-000000000125",
              reason: "Approved lighting reference",
            },
          ],
          candidate_count: 2,
          quality_targets: ["Legible label"],
          repair_scope: ["background"],
          tool_intents: [
            {
              intent_key: "generate-hero",
              tool_name: "fixture.generate_image",
              schema_version: "1",
              purpose: "Generate the approved hero direction",
              arguments: { count: 2 },
              estimated_cost_units: 2,
            },
          ],
        },
      ],
    },
    provenance: {
      product_brief_id: "019f8a00-0000-7000-8000-000000000126",
      product_brief_version: 3,
      product_brief_sha256: "a".repeat(64),
      brand_profile_id: "019f8a00-0000-7000-8000-000000000127",
      brand_profile_version: 4,
      brand_profile_sha256: "b".repeat(64),
      retrieval_run_id: "019f8a00-0000-7000-8000-000000000128",
      retrieval_citation_ids: [
        "019f8a00-0000-7000-8000-000000000125",
      ],
      context_policy_version: "planning-context-v1",
      context_sha256: "c".repeat(64),
      prompt_id: "creative-planner",
      prompt_revision: "fixture-v1",
      prompt_sha256: "d".repeat(64),
    },
    payload_sha256: "e".repeat(64),
    actor_id: "fixture-planner",
    revision_reason: null,
    created_at: "2026-08-05T08:00:00Z",
  };
  return {
    head: {
      workspace_id: "catalog-demo",
      workflow_id: WORKFLOW_ID,
      creative_plan_id: PLAN_ID,
      current_version_id: PLAN_VERSION_ID,
      current_version_number: 2,
      version: 2,
      retain_until: "2026-08-08T08:00:00Z",
      created_at: "2026-08-05T07:59:00Z",
      updated_at: "2026-08-05T08:00:00Z",
    },
    version,
    ...overrides,
  };
}

function workflowResponse(overrides = {}) {
  return {
    id: WORKFLOW_ID,
    workspace_id: "catalog-demo",
    created_by: "catalog-workbench",
    workflow_type: "commerce-vision",
    status: "AWAITING_PLAN_APPROVAL",
    retention_status: "ACTIVE",
    current_node: "approve_plan",
    version: 7,
    input_data: {},
    result_data: null,
    expires_at: "2026-08-08T08:00:00Z",
    cancellation_requested_at: null,
    created_at: "2026-08-05T07:00:00Z",
    updated_at: "2026-08-05T08:00:00Z",
    steps: [],
    attempts: [],
    approvals: [
      {
        id: APPROVAL_ID,
        approval_type: "CREATIVE_PLAN",
        subject_id: PLAN_ID,
        subject_version: 1,
        decision: "REJECT",
        approved_by: "reviewer-a",
        expected_workflow_version: 5,
        created_at: "2026-08-05T07:45:00Z",
      },
    ],
    ...overrides,
  };
}

describe("CreativePlanApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("loads exact plan and Workflow review authority without caches", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json(planResponse()))
      .mockResolvedValueOnce(Response.json(workflowResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const api = new CreativePlanApi({
      baseUrl: "https://web.example",
      workspaceId: "catalog-demo",
      actorId: "creative-plan-workbench",
    });

    await expect(api.getCurrent(PLAN_ID, WORKFLOW_ID)).resolves.toEqual(
      planResponse(),
    );
    await expect(api.getWorkflow(WORKFLOW_ID)).resolves.toEqual(
      workflowResponse(),
    );

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `https://web.example/api/v1/creative-plans/${PLAN_ID}?workflow_id=${WORKFLOW_ID}`,
      `https://web.example/api/v1/workflows/${WORKFLOW_ID}`,
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init.cache).toBe("no-store");
      expect(Object.fromEntries(new Headers(init.headers).entries())).toEqual({
        accept: "application/json",
        "x-workspace-id": "catalog-demo",
      });
    }
  });

  it("fails closed when the current head and immutable version disagree", () => {
    const response = planResponse();
    response.head.current_version_number = 3;

    expect(() =>
      decodeCreativePlanCurrentResponse(response, {
        workspaceId: "catalog-demo",
        workflowId: WORKFLOW_ID,
        creativePlanId: PLAN_ID,
      }),
    ).toThrow(CreativePlanProtocolError);
  });

  it.each([
    ["workspace", { workspace_id: "other-workspace" }],
    ["Workflow", { id: "019f8a00-0000-7000-8000-000000000199" }],
    ["status", { status: "NOT_A_WORKFLOW_STATUS" }],
  ])("rejects a Workflow response with invalid %s authority", (_label, patch) => {
    expect(() =>
      decodeWorkflowResponse(workflowResponse(patch), {
        workspaceId: "catalog-demo",
        workflowId: WORKFLOW_ID,
      }),
    ).toThrow(CreativePlanProtocolError);
  });

  it("rejects malformed approval history before publishing the Workflow", () => {
    const response = workflowResponse();
    response.approvals[0].subject_version = 0;

    expect(() =>
      decodeWorkflowResponse(response, {
        workspaceId: "catalog-demo",
        workflowId: WORKFLOW_ID,
      }),
    ).toThrow(CreativePlanProtocolError);
  });

  it("reads a bounded immutable version page through the exact cursor seam", async () => {
    const responseBody = {
      items: [planResponse().version],
      next_cursor: "v1.current.cGF5bG9hZA.c2lnbmF0dXJl",
    };
    const fetchMock = vi.fn(async () => Response.json(responseBody));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new CreativePlanApi().listVersions(PLAN_ID, WORKFLOW_ID, {
        limit: 20,
        cursor: "v1.current.cHJldmlvdXM.c2lnbmF0dXJl",
      }),
    ).resolves.toEqual(responseBody);

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/v1/creative-plans/${PLAN_ID}/versions?workflow_id=${WORKFLOW_ID}&limit=20&cursor=v1.current.cHJldmlvdXM.c2lnbmF0dXJl`,
    );
  });

  it("reads one exact immutable version for restored review position", async () => {
    const version = planResponse().version;
    const fetchMock = vi.fn(async () => Response.json(version));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new CreativePlanApi().getVersion(PLAN_ID, WORKFLOW_ID, 2),
    ).resolves.toEqual(version);

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/v1/creative-plans/${PLAN_ID}/versions/2?workflow_id=${WORKFLOW_ID}`,
    );
  });

  it("submits immutable revision and exact approval decisions with idempotency", async () => {
    const revised = planResponse();
    revised.head.current_version_number = 3;
    revised.head.current_version_id =
      "019f8a00-0000-7000-8000-000000000129";
    revised.head.version = 4;
    revised.version = {
      ...revised.version,
      id: revised.head.current_version_id,
      version_number: 3,
      source: "USER",
      revision_reason: "Adjust the hero composition",
    };
    const approvedWorkflow = workflowResponse({
      status: "GENERATING",
      version: 8,
    });
    const rejectedWorkflow = workflowResponse({ version: 8 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json(revised, { status: 201 }))
      .mockResolvedValueOnce(Response.json(approvedWorkflow))
      .mockResolvedValueOnce(Response.json(rejectedWorkflow));
    vi.stubGlobal("fetch", fetchMock);
    const api = new CreativePlanApi({ actorId: "reviewer-a" });
    const revision = {
      workflow_id: WORKFLOW_ID,
      payload: revised.version.payload,
      revision_reason: "Adjust the hero composition",
      expected_workflow_version: 7,
      expected_head_version: 3,
    };
    const approval = {
      expected_workflow_version: 7,
      subject_id: PLAN_ID,
      subject_version: 2,
      decision: "APPROVE",
      reason_code: "HUMAN_VERIFIED",
      comment_ref: null,
    };

    await api.revise(PLAN_ID, revision, "web-plan-revise-fixed-0001");
    await api.approve(
      WORKFLOW_ID,
      approval,
      "web-plan-approve-fixed-0001",
    );
    await api.reject(
      WORKFLOW_ID,
      { ...approval, decision: "REJECT", reason_code: "NEEDS_REVISION" },
      "web-plan-reject-fixed-0001",
    );

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `/api/v1/creative-plans/${PLAN_ID}:revise`,
      `/api/v1/workflows/${WORKFLOW_ID}/creative-plan:approve`,
      `/api/v1/workflows/${WORKFLOW_ID}/creative-plan:reject`,
    ]);
    expect(fetchMock.mock.calls.map(([, init]) => init.method)).toEqual([
      "POST",
      "POST",
      "POST",
    ]);
    expect(fetchMock.mock.calls.map(([, init]) => JSON.parse(init.body))).toEqual([
      revision,
      approval,
      { ...approval, decision: "REJECT", reason_code: "NEEDS_REVISION" },
    ]);
    expect(
      fetchMock.mock.calls.map(([, init]) =>
        Object.fromEntries(new Headers(init.headers).entries()),
      ),
    ).toEqual([
      {
        accept: "application/json",
        "content-type": "application/json",
        "idempotency-key": "web-plan-revise-fixed-0001",
        "x-actor-id": "reviewer-a",
        "x-workspace-id": "catalog-demo",
      },
      {
        accept: "application/json",
        "content-type": "application/json",
        "idempotency-key": "web-plan-approve-fixed-0001",
        "x-actor-id": "reviewer-a",
        "x-workspace-id": "catalog-demo",
      },
      {
        accept: "application/json",
        "content-type": "application/json",
        "idempotency-key": "web-plan-reject-fixed-0001",
        "x-actor-id": "reviewer-a",
        "x-workspace-id": "catalog-demo",
      },
    ]);
  });

  it("creates action-scoped non-repeating idempotency keys", () => {
    const first = newCreativePlanIdempotencyKey("approve");
    const second = newCreativePlanIdempotencyKey("approve");

    expect(first).toMatch(/^web-creative-plan-approve-/);
    expect(second).toMatch(/^web-creative-plan-approve-/);
    expect(first).not.toBe(second);
  });
});
