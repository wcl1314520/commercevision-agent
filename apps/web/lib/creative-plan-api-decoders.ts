import type {
  ApprovalDecision,
  ApprovalResponse,
  ApprovalType,
  AttemptStatus,
  CreativePlanCitationSelectionV1,
  CreativePlanCurrentResponseV1,
  CreativePlanDirectionV1,
  CreativePlanPayloadV1,
  CreativePlanProvenanceV1,
  CreativePlanSource,
  CreativePlanToolIntentV1,
  CreativePlanVersionListResponseV1,
  CreativePlanVersionResponseV1,
  ErrorResponse,
  EventResponse,
  ImageRole,
  RetentionStatus,
  StepStatus,
  StepType,
  WorkflowAttemptResponse,
  WorkflowResponse,
  WorkflowStatus,
  WorkflowStepResponse,
} from "./generated/catalog-api";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TOKEN_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const UTC_TIMESTAMP_PATTERN = /(?:Z|\+00:00)$/;
const CONTROL_PATTERN = /[\u0000-\u001f\u007f]/;

const PLAN_SOURCES = new Set<CreativePlanSource>(["AGENT", "USER"]);
const IMAGE_ROLES = new Set<ImageRole>([
  "MAIN",
  "HERO",
  "SCENE",
  "DETAIL",
  "SELLING_POINT",
]);
const WORKFLOW_STATUSES = new Set<WorkflowStatus>([
  "DRAFT",
  "INGESTING",
  "UNDERSTANDING",
  "AWAITING_PRODUCT_CONFIRMATION",
  "RETRIEVING",
  "PLANNING",
  "AWAITING_PLAN_APPROVAL",
  "GENERATING",
  "EVALUATING",
  "REPAIRING",
  "AWAITING_RESULT_APPROVAL",
  "EXPORTING",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);
const RETENTION_STATUSES = new Set<RetentionStatus>([
  "ACTIVE",
  "EXPIRING",
  "DELETING",
  "EXPIRED",
]);
const APPROVAL_TYPES = new Set<ApprovalType>([
  "PRODUCT_BRIEF",
  "CREATIVE_PLAN",
  "RESULTS",
]);
const APPROVAL_DECISIONS = new Set<ApprovalDecision>([
  "APPROVE",
  "REJECT",
  "REGENERATE",
]);
const STEP_TYPES = new Set<StepType>([
  "VALIDATE_INPUT",
  "UNDERSTAND_PRODUCT",
  "RETRIEVE_REFERENCES",
  "CREATE_PLAN",
  "APPROVE_PLAN",
  "EXECUTE_TOOL",
  "EVALUATE_RESULTS",
  "APPROVE_RESULTS",
  "EXPORT",
]);
const STEP_STATUSES = new Set<StepStatus>([
  "PENDING",
  "QUEUED",
  "CLAIMED",
  "RUNNING",
  "WAITING_HUMAN",
  "SUCCEEDED",
  "RETRYABLE_FAILED",
  "FAILED",
  "CANCELLED",
]);
const ATTEMPT_STATUSES = new Set<AttemptStatus>([
  "CREATED",
  "SUBMITTING",
  "SUBMITTED",
  "POLLING",
  "SUCCEEDED",
  "UNKNOWN",
  "RETRYABLE_FAILED",
  "PERMANENT_FAILED",
  "CANCELLED",
]);

type JsonObject = Record<string, unknown>;
type JsonBudget = { remaining: number };

export type CreativePlanIdentity = {
  workspaceId: string;
  workflowId: string;
  creativePlanId: string;
};

export type WorkflowIdentity = {
  workspaceId: string;
  workflowId: string;
};

export class CreativePlanProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CreativePlanProtocolError";
  }
}

function reject(field: string): never {
  throw new CreativePlanProtocolError(
    `Creative Plan response field is invalid: ${field}`,
  );
}

function object(value: unknown, field: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    reject(field);
  }
  return value as JsonObject;
}

function text(
  value: unknown,
  field: string,
  { min = 1, max, pattern }: { min?: number; max: number; pattern?: RegExp },
): string {
  if (typeof value !== "string") reject(field);
  const length = Array.from(value).length;
  if (
    length < min ||
    length > max ||
    CONTROL_PATTERN.test(value) ||
    (pattern !== undefined && !pattern.test(value))
  ) {
    reject(field);
  }
  return value;
}

function nullableText(value: unknown, field: string, max: number): string | null {
  return value === null ? null : text(value, field, { max });
}

function integer(value: unknown, field: string, min: number, max = 1_000_000): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < min ||
    value > max
  ) {
    reject(field);
  }
  return value;
}

function nullableInteger(
  value: unknown,
  field: string,
  min: number,
): number | null {
  return value === null ? null : integer(value, field, min);
}

function enumeration<T extends string>(
  value: unknown,
  field: string,
  allowed: ReadonlySet<T>,
): T {
  if (typeof value !== "string" || !allowed.has(value as T)) reject(field);
  return value as T;
}

function array(value: unknown, field: string, min: number, max: number): unknown[] {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    reject(field);
  }
  return value;
}

function uuid(value: unknown, field: string): string {
  return text(value, field, { min: 36, max: 36, pattern: UUID_PATTERN });
}

function nullableUuid(value: unknown, field: string): string | null {
  return value === null ? null : uuid(value, field);
}

function token(value: unknown, field: string): string {
  return text(value, field, { max: 128, pattern: TOKEN_PATTERN });
}

function sha256(value: unknown, field: string): string {
  return text(value, field, { min: 64, max: 64, pattern: SHA256_PATTERN });
}

function nullableSha256(value: unknown, field: string): string | null {
  return value === null ? null : sha256(value, field);
}

function timestamp(value: unknown, field: string): string {
  const result = text(value, field, { max: 64 });
  if (!UTC_TIMESTAMP_PATTERN.test(result) || !Number.isFinite(Date.parse(result))) {
    reject(field);
  }
  return result;
}

function nullableTimestamp(value: unknown, field: string): string | null {
  return value === null ? null : timestamp(value, field);
}

function jsonValue(
  value: unknown,
  field: string,
  budget: JsonBudget,
  depth = 0,
): unknown {
  budget.remaining -= 1;
  if (budget.remaining < 0 || depth > 16) reject(field);
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) reject(field);
    return value;
  }
  if (typeof value === "string") return text(value, field, { min: 0, max: 8192 });
  if (Array.isArray(value)) {
    return array(value, field, 0, 256).map((item, index) =>
      jsonValue(item, `${field}[${index}]`, budget, depth + 1),
    );
  }
  const source = object(value, field);
  const entries = Object.entries(source);
  if (entries.length > 256) reject(field);
  return Object.fromEntries(
    entries.map(([key, item]) => [
      text(key, `${field}.key`, { max: 128 }),
      jsonValue(item, `${field}.${key}`, budget, depth + 1),
    ]),
  );
}

function jsonObject(value: unknown, field: string): JsonObject {
  return jsonValue(value, field, { remaining: 4096 }) as JsonObject;
}

function stringList(
  value: unknown,
  field: string,
  min: number,
  max: number,
  itemMax = 1024,
): string[] {
  return array(value, field, min, max).map((item, index) =>
    text(item, `${field}[${index}]`, { max: itemMax }),
  );
}

function tokenList(value: unknown, field: string, max: number): string[] {
  const result = array(value, field, 0, max).map((item, index) =>
    token(item, `${field}[${index}]`),
  );
  if (new Set(result).size !== result.length) reject(field);
  return result;
}

function decodeCitation(value: unknown, field: string): CreativePlanCitationSelectionV1 {
  const citation = object(value, field);
  return {
    citation_id: token(citation.citation_id, `${field}.citation_id`),
    reason: text(citation.reason, `${field}.reason`, { max: 512 }),
  };
}

function decodeToolIntent(value: unknown, field: string): CreativePlanToolIntentV1 {
  const intent = object(value, field);
  return {
    intent_key: token(intent.intent_key, `${field}.intent_key`),
    tool_name: token(intent.tool_name, `${field}.tool_name`),
    schema_version: token(intent.schema_version, `${field}.schema_version`),
    purpose: text(intent.purpose, `${field}.purpose`, { max: 512 }),
    arguments: jsonObject(intent.arguments, `${field}.arguments`),
    estimated_cost_units: integer(
      intent.estimated_cost_units,
      `${field}.estimated_cost_units`,
      1,
    ),
  };
}

function decodeDirection(value: unknown, index: number): CreativePlanDirectionV1 {
  const field = `version.payload.directions[${index}]`;
  const direction = object(value, field);
  return {
    key: token(direction.key, `${field}.key`),
    image_role: enumeration(direction.image_role, `${field}.image_role`, IMAGE_ROLES),
    scene: text(direction.scene, `${field}.scene`, { max: 1024 }),
    composition: text(direction.composition, `${field}.composition`, { max: 1024 }),
    camera: text(direction.camera, `${field}.camera`, { max: 1024 }),
    lighting: text(direction.lighting, `${field}.lighting`, { max: 1024 }),
    color_direction: text(direction.color_direction, `${field}.color_direction`, {
      max: 1024,
    }),
    product_constraints: stringList(
      direction.product_constraints,
      `${field}.product_constraints`,
      1,
      32,
    ) as CreativePlanDirectionV1["product_constraints"],
    required_elements: stringList(
      direction.required_elements,
      `${field}.required_elements`,
      1,
      32,
    ) as CreativePlanDirectionV1["required_elements"],
    prohibited_elements: stringList(
      direction.prohibited_elements,
      `${field}.prohibited_elements`,
      0,
      32,
    ),
    citation_selections: array(
      direction.citation_selections,
      `${field}.citation_selections`,
      0,
      32,
    ).map((item, citationIndex) =>
      decodeCitation(item, `${field}.citation_selections[${citationIndex}]`),
    ),
    candidate_count: integer(direction.candidate_count, `${field}.candidate_count`, 1, 16),
    quality_targets: stringList(
      direction.quality_targets,
      `${field}.quality_targets`,
      1,
      32,
    ) as CreativePlanDirectionV1["quality_targets"],
    repair_scope: stringList(direction.repair_scope, `${field}.repair_scope`, 0, 32),
    tool_intents: array(direction.tool_intents, `${field}.tool_intents`, 0, 16).map(
      (item, intentIndex) => decodeToolIntent(item, `${field}.tool_intents[${intentIndex}]`),
    ),
  };
}

export function decodeCreativePlanPayload(value: unknown): CreativePlanPayloadV1 {
  const payload = object(value, "version.payload");
  const directions = array(payload.directions, "version.payload.directions", 1, 12).map(
    decodeDirection,
  ) as CreativePlanPayloadV1["directions"];
  if (new Set(directions.map((direction) => direction.key)).size !== directions.length) {
    reject("version.payload.directions");
  }
  return {
    schema_version: token(payload.schema_version, "version.payload.schema_version"),
    directions,
  };
}

function decodeProvenance(value: unknown): CreativePlanProvenanceV1 {
  const provenance = object(value, "version.provenance");
  const brandProfileId = nullableUuid(
    provenance.brand_profile_id,
    "version.provenance.brand_profile_id",
  );
  const brandProfileVersion = nullableInteger(
    provenance.brand_profile_version,
    "version.provenance.brand_profile_version",
    1,
  );
  const brandProfileSha256 = nullableSha256(
    provenance.brand_profile_sha256,
    "version.provenance.brand_profile_sha256",
  );
  if (
    (brandProfileId === null) !== (brandProfileVersion === null) ||
    (brandProfileId === null) !== (brandProfileSha256 === null)
  ) {
    reject("version.provenance.brand_profile");
  }
  return {
    product_brief_id: uuid(
      provenance.product_brief_id,
      "version.provenance.product_brief_id",
    ),
    product_brief_version: integer(
      provenance.product_brief_version,
      "version.provenance.product_brief_version",
      1,
    ),
    product_brief_sha256: sha256(
      provenance.product_brief_sha256,
      "version.provenance.product_brief_sha256",
    ),
    brand_profile_id: brandProfileId,
    brand_profile_version: brandProfileVersion,
    brand_profile_sha256: brandProfileSha256,
    retrieval_run_id: uuid(
      provenance.retrieval_run_id,
      "version.provenance.retrieval_run_id",
    ),
    retrieval_citation_ids: tokenList(
      provenance.retrieval_citation_ids,
      "version.provenance.retrieval_citation_ids",
      32,
    ),
    context_policy_version: token(
      provenance.context_policy_version,
      "version.provenance.context_policy_version",
    ),
    context_sha256: sha256(
      provenance.context_sha256,
      "version.provenance.context_sha256",
    ),
    prompt_id: token(provenance.prompt_id, "version.provenance.prompt_id"),
    prompt_revision: token(
      provenance.prompt_revision,
      "version.provenance.prompt_revision",
    ),
    prompt_sha256: sha256(
      provenance.prompt_sha256,
      "version.provenance.prompt_sha256",
    ),
  };
}

function decodePlanVersion(
  value: unknown,
  identity: CreativePlanIdentity,
): CreativePlanVersionResponseV1 {
  const version = object(value, "version");
  const result: CreativePlanVersionResponseV1 = {
    id: uuid(version.id, "version.id"),
    workspace_id: token(version.workspace_id, "version.workspace_id"),
    workflow_id: uuid(version.workflow_id, "version.workflow_id"),
    creative_plan_id: uuid(version.creative_plan_id, "version.creative_plan_id"),
    version_number: integer(version.version_number, "version.version_number", 1),
    supersedes_version_id: nullableUuid(
      version.supersedes_version_id,
      "version.supersedes_version_id",
    ),
    source: enumeration(version.source, "version.source", PLAN_SOURCES),
    payload: decodeCreativePlanPayload(version.payload),
    provenance: decodeProvenance(version.provenance),
    payload_sha256: sha256(version.payload_sha256, "version.payload_sha256"),
    actor_id: token(version.actor_id, "version.actor_id"),
    revision_reason: nullableText(version.revision_reason, "version.revision_reason", 512),
    created_at: timestamp(version.created_at, "version.created_at"),
  };
  if (
    result.workspace_id !== identity.workspaceId ||
    result.workflow_id !== identity.workflowId ||
    result.creative_plan_id !== identity.creativePlanId
  ) {
    reject("version.identity");
  }
  return result;
}

export function decodeCreativePlanVersionResponse(
  value: unknown,
  identity: CreativePlanIdentity,
): CreativePlanVersionResponseV1 {
  return decodePlanVersion(value, identity);
}

export function decodeCreativePlanCurrentResponse(
  value: unknown,
  identity: CreativePlanIdentity,
): CreativePlanCurrentResponseV1 {
  const current = object(value, "current");
  const head = object(current.head, "head");
  const result: CreativePlanCurrentResponseV1 = {
    head: {
      workspace_id: token(head.workspace_id, "head.workspace_id"),
      workflow_id: uuid(head.workflow_id, "head.workflow_id"),
      creative_plan_id: uuid(head.creative_plan_id, "head.creative_plan_id"),
      current_version_id: uuid(head.current_version_id, "head.current_version_id"),
      current_version_number: integer(
        head.current_version_number,
        "head.current_version_number",
        1,
      ),
      version: integer(head.version, "head.version", 1),
      retain_until: timestamp(head.retain_until, "head.retain_until"),
      created_at: timestamp(head.created_at, "head.created_at"),
      updated_at: timestamp(head.updated_at, "head.updated_at"),
    },
    version: decodePlanVersion(current.version, identity),
  };
  if (
    result.head.workspace_id !== identity.workspaceId ||
    result.head.workflow_id !== identity.workflowId ||
    result.head.creative_plan_id !== identity.creativePlanId ||
    result.head.current_version_id !== result.version.id ||
    result.head.current_version_number !== result.version.version_number
  ) {
    reject("current.identity");
  }
  return result;
}

export function decodeCreativePlanVersionListResponse(
  value: unknown,
  identity: CreativePlanIdentity,
): CreativePlanVersionListResponseV1 {
  const page = object(value, "versions");
  const items = array(page.items, "versions.items", 0, 100).map((item) =>
    decodePlanVersion(item, identity),
  );
  for (let index = 1; index < items.length; index += 1) {
    if (items[index - 1].version_number <= items[index].version_number) {
      reject("versions.order");
    }
  }
  const nextCursor =
    page.next_cursor === null
      ? null
      : text(page.next_cursor, "versions.next_cursor", { max: 256 });
  return { items, next_cursor: nextCursor };
}

function decodeApproval(value: unknown, index: number): ApprovalResponse {
  const field = `workflow.approvals[${index}]`;
  const approval = object(value, field);
  return {
    id: uuid(approval.id, `${field}.id`),
    approval_type: enumeration(approval.approval_type, `${field}.approval_type`, APPROVAL_TYPES),
    subject_id: text(approval.subject_id, `${field}.subject_id`, { max: 128 }),
    subject_version: integer(approval.subject_version, `${field}.subject_version`, 1),
    decision: enumeration(approval.decision, `${field}.decision`, APPROVAL_DECISIONS),
    approved_by: text(approval.approved_by, `${field}.approved_by`, { max: 128 }),
    expected_workflow_version: integer(
      approval.expected_workflow_version,
      `${field}.expected_workflow_version`,
      1,
    ),
    created_at: timestamp(approval.created_at, `${field}.created_at`),
  };
}

function decodeStep(value: unknown, index: number): WorkflowStepResponse {
  const field = `workflow.steps[${index}]`;
  const step = object(value, field);
  return {
    id: uuid(step.id, `${field}.id`),
    step_key: token(step.step_key, `${field}.step_key`),
    step_type: enumeration(step.step_type, `${field}.step_type`, STEP_TYPES),
    status: enumeration(step.status, `${field}.status`, STEP_STATUSES),
    sequence: integer(step.sequence, `${field}.sequence`, 0),
    attempt_count: integer(step.attempt_count, `${field}.attempt_count`, 0),
    max_attempts: integer(step.max_attempts, `${field}.max_attempts`, 1),
    lease_expires_at: nullableTimestamp(step.lease_expires_at, `${field}.lease_expires_at`),
    output_ref: nullableText(step.output_ref, `${field}.output_ref`, 2048),
    output_data: step.output_data === null ? null : jsonObject(step.output_data, `${field}.output_data`),
    error_class: nullableText(step.error_class, `${field}.error_class`, 256),
    error_message: nullableText(step.error_message, `${field}.error_message`, 4096),
    started_at: nullableTimestamp(step.started_at, `${field}.started_at`),
    completed_at: nullableTimestamp(step.completed_at, `${field}.completed_at`),
  };
}

function decodeAttempt(value: unknown, index: number): WorkflowAttemptResponse {
  const field = `workflow.attempts[${index}]`;
  const attempt = object(value, field);
  return {
    id: uuid(attempt.id, `${field}.id`),
    step_id: uuid(attempt.step_id, `${field}.step_id`),
    attempt_number: integer(attempt.attempt_number, `${field}.attempt_number`, 1),
    idempotency_key: text(attempt.idempotency_key, `${field}.idempotency_key`, { max: 255 }),
    status: enumeration(attempt.status, `${field}.status`, ATTEMPT_STATUSES),
    provider_request_id: nullableText(
      attempt.provider_request_id,
      `${field}.provider_request_id`,
      512,
    ),
    result_ref: nullableText(attempt.result_ref, `${field}.result_ref`, 2048),
    result_data:
      attempt.result_data === null
        ? null
        : jsonObject(attempt.result_data, `${field}.result_data`),
    error_class: nullableText(attempt.error_class, `${field}.error_class`, 256),
    error_message: nullableText(attempt.error_message, `${field}.error_message`, 4096),
    started_at: nullableTimestamp(attempt.started_at, `${field}.started_at`),
    completed_at: nullableTimestamp(attempt.completed_at, `${field}.completed_at`),
  };
}

export function decodeWorkflowResponse(
  value: unknown,
  identity: WorkflowIdentity,
): WorkflowResponse {
  const workflow = object(value, "workflow");
  const result: WorkflowResponse = {
    id: uuid(workflow.id, "workflow.id"),
    workspace_id: token(workflow.workspace_id, "workflow.workspace_id"),
    created_by: text(workflow.created_by, "workflow.created_by", { max: 128 }),
    workflow_type: token(workflow.workflow_type, "workflow.workflow_type"),
    status: enumeration(workflow.status, "workflow.status", WORKFLOW_STATUSES),
    retention_status: enumeration(
      workflow.retention_status,
      "workflow.retention_status",
      RETENTION_STATUSES,
    ),
    current_node: nullableText(workflow.current_node, "workflow.current_node", 128),
    version: integer(workflow.version, "workflow.version", 1),
    input_data: jsonObject(workflow.input_data, "workflow.input_data"),
    result_data:
      workflow.result_data === null
        ? null
        : jsonObject(workflow.result_data, "workflow.result_data"),
    expires_at: timestamp(workflow.expires_at, "workflow.expires_at"),
    cancellation_requested_at: nullableTimestamp(
      workflow.cancellation_requested_at,
      "workflow.cancellation_requested_at",
    ),
    created_at: timestamp(workflow.created_at, "workflow.created_at"),
    updated_at: timestamp(workflow.updated_at, "workflow.updated_at"),
    steps: array(workflow.steps ?? [], "workflow.steps", 0, 512).map(decodeStep),
    attempts: array(workflow.attempts ?? [], "workflow.attempts", 0, 512).map(
      decodeAttempt,
    ),
    approvals: array(workflow.approvals ?? [], "workflow.approvals", 0, 512).map(
      decodeApproval,
    ),
  };
  if (result.id !== identity.workflowId || result.workspace_id !== identity.workspaceId) {
    reject("workflow.identity");
  }
  return result;
}

export function decodeWorkflowEventResponse(
  value: unknown,
  identity: WorkflowIdentity,
): EventResponse {
  const event = object(value, "workflow_event");
  const result: EventResponse = {
    event_id: uuid(event.event_id, "workflow_event.event_id"),
    event_type: token(event.event_type, "workflow_event.event_type"),
    schema_version: integer(
      event.schema_version,
      "workflow_event.schema_version",
      1,
    ),
    aggregate_type: token(
      event.aggregate_type,
      "workflow_event.aggregate_type",
    ),
    aggregate_id: uuid(event.aggregate_id, "workflow_event.aggregate_id"),
    aggregate_version: integer(
      event.aggregate_version,
      "workflow_event.aggregate_version",
      1,
    ),
    occurred_at: timestamp(event.occurred_at, "workflow_event.occurred_at"),
    trace_id: token(event.trace_id, "workflow_event.trace_id"),
    payload: jsonObject(event.payload, "workflow_event.payload"),
  };
  if (
    result.aggregate_type !== "workflow" ||
    result.aggregate_id !== identity.workflowId
  ) {
    reject("workflow_event.identity");
  }
  return result;
}

export function decodeErrorResponse(value: unknown): ErrorResponse {
  const error = object(value, "error");
  return {
    code: token(error.code, "error.code"),
    message: text(error.message, "error.message", { max: 1024 }),
    category: token(error.category, "error.category"),
    retryable:
      typeof error.retryable === "boolean" ? error.retryable : reject("error.retryable"),
    details: error.details === undefined ? undefined : jsonObject(error.details, "error.details"),
    request_id: text(error.request_id, "error.request_id", { max: 128 }),
    trace_id: text(error.trace_id, "error.trace_id", { max: 128 }),
  };
}
