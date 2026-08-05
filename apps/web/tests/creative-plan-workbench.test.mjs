import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CreativePlanReview } from "../app/creative-plan-workbench";

globalThis.React = React;

const PLAN_ID = "019f8a00-0000-7000-8000-000000000121";
const WORKFLOW_ID = "019f8a00-0000-7000-8000-000000000122";

function reviewFixture({ approvals = true } = {}) {
  return {
    current: {
      head: {
        workspace_id: "catalog-demo",
        workflow_id: WORKFLOW_ID,
        creative_plan_id: PLAN_ID,
        current_version_id: "019f8a00-0000-7000-8000-000000000123",
        current_version_number: 2,
        version: 3,
        retain_until: "2026-08-08T08:00:00Z",
        created_at: "2026-08-05T07:59:00Z",
        updated_at: "2026-08-05T08:00:00Z",
      },
      version: {
        id: "019f8a00-0000-7000-8000-000000000123",
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
                  citation_id: "citation.hero-lighting",
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
                  purpose: "<script>alert(1)</script>",
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
          retrieval_citation_ids: ["citation.hero-lighting"],
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
      },
    },
    workflow: {
      id: WORKFLOW_ID,
      workspace_id: "catalog-demo",
      status: "AWAITING_PLAN_APPROVAL",
      retention_status: "ACTIVE",
      current_node: "approve_plan",
      version: 7,
      expires_at: "2026-08-08T08:00:00Z",
      approvals: approvals
        ? [
            {
              id: "019f8a00-0000-7000-8000-000000000124",
              approval_type: "CREATIVE_PLAN",
              subject_id: PLAN_ID,
              subject_version: 1,
              decision: "REJECT",
              approved_by: "reviewer-a",
              expected_workflow_version: 5,
              created_at: "2026-08-05T07:45:00Z",
            },
          ]
        : [],
    },
  };
}

describe("CreativePlanReview", () => {
  it("renders exact authority, provenance, citations, Tool Intents, and approval history", () => {
    const markup = renderToStaticMarkup(
      React.createElement(CreativePlanReview, reviewFixture()),
    );

    expect(markup).toContain('aria-labelledby="creative-plan-review-heading"');
    expect(markup).toContain("方案版本 2");
    expect(markup).toContain("Workflow 版本 7");
    expect(markup).toContain("AWAITING_PLAN_APPROVAL");
    expect(markup).toContain("商品简报 · 版本 3");
    expect(markup).toContain("品牌档案 · 版本 4");
    expect(markup).toContain("citation.hero-lighting");
    expect(markup).toContain("fixture.generate_image");
    expect(markup).toContain("REJECT");
    expect(markup).toContain("reviewer-a");
    expect(markup).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(markup).not.toContain("<script>alert(1)</script>");
  });

  it("renders an actionable empty approval history without inventing authorization", () => {
    const markup = renderToStaticMarkup(
      React.createElement(
        CreativePlanReview,
        reviewFixture({ approvals: false }),
      ),
    );

    expect(markup).toContain("尚无此方案的审批记录");
    expect(markup).toContain("当前页面不代表已授权执行");
  });
});
