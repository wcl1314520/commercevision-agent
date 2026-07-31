import type {
  BrandProfileCreateRequestV1,
  BrandProfilePublishedMemberV1,
  BrandProfileVersionResponseV1,
} from "../lib/generated/catalog-api";

const createRequest = {
  brand: "Northstar Labs",
  profile_key: "primary",
  draft: {
    rules: [
      {
        code: "logo.clear-space",
        scope: "VISUAL",
        instruction: "Keep one mark-width of clear space.",
      },
    ],
    approved_colors: [{ name: "Primary", value: "#1457FF" }],
    required_marks: ["Northstar wordmark"],
    prohibited_elements: ["Competitor marks"],
    tone_constraints: ["Calm"],
    copy_constraints: ["No unsupported claims"],
    purpose: "BRAND_PROFILE",
    provider: "qwen-vl",
    requires_derivative: true,
    selected_assets: [
      {
        asset_version_id: "019f8a00-0000-7000-8000-000000000045",
        role: "LOGO",
      },
    ],
  },
} satisfies BrandProfileCreateRequestV1;

const historicalMember = {
  ordinal: 0,
  asset_id: "019f8a00-0000-7000-8000-000000000044",
  asset_version_id: createRequest.draft.selected_assets[0].asset_version_id,
  role: "LOGO",
  published_rights_record_id:
    "019f8a00-0000-7000-8000-000000000046",
  published_rights_record_version: 1,
  currently_usable: false,
  current_reason_code: "RIGHTS_REVOKED",
  current_rights_record_id:
    "019f8a00-0000-7000-8000-000000000046",
  current_rights_record_version: 2,
  decided_at: "2026-07-30T09:00:00Z",
} satisfies BrandProfilePublishedMemberV1;

const version = {
  id: "019f8a00-0000-7000-8000-000000000043",
  workspace_id: "brand-workspace",
  profile_id: "019f8a00-0000-7000-8000-000000000041",
  version_number: 1,
  draft: createRequest.draft,
  content_sha256: "a".repeat(64),
  published_by: "brand-admin",
  published_at: "2026-07-30T08:00:00Z",
  members: [historicalMember],
} satisfies BrandProfileVersionResponseV1;

void version;
