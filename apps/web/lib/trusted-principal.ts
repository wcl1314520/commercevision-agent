import { createHmac } from "node:crypto";

const WORKSPACE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const KEY_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const MINIMUM_HMAC_SECRET_CHARACTERS = 32;

export class TrustedPrincipalConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TrustedPrincipalConfigurationError";
  }
}

export class WorkspaceBoundaryError extends Error {
  constructor() {
    super("workspace is outside the configured Web gateway boundary");
    this.name = "WorkspaceBoundaryError";
  }
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new TrustedPrincipalConfigurationError(`${name} is required`);
  }
  return value;
}

function parseWorkspaceSet(raw: string, name: string): Set<string> {
  const workspaces = raw.split(",");
  if (
    workspaces.some((value) => !WORKSPACE_ID_PATTERN.test(value)) ||
    new Set(workspaces).size !== workspaces.length
  ) {
    throw new TrustedPrincipalConfigurationError(
      `${name} must contain unique canonical workspace IDs`,
    );
  }
  return new Set(workspaces);
}

function configuredWorkspaces(): Set<string> {
  return parseWorkspaceSet(
    requiredEnvironment("CV_WEB_ALLOWED_WORKSPACE_IDS"),
    "CV_WEB_ALLOWED_WORKSPACE_IDS",
  );
}

function configuredAdminWorkspaces(allowedWorkspaces: Set<string>): Set<string> {
  const raw = process.env.CV_WEB_ADMIN_WORKSPACE_IDS;
  if (!raw) return new Set();
  const adminWorkspaces = parseWorkspaceSet(
    raw,
    "CV_WEB_ADMIN_WORKSPACE_IDS",
  );
  if ([...adminWorkspaces].some((workspace) => !allowedWorkspaces.has(workspace))) {
    throw new TrustedPrincipalConfigurationError(
      "CV_WEB_ADMIN_WORKSPACE_IDS must be a subset of CV_WEB_ALLOWED_WORKSPACE_IDS",
    );
  }
  return adminWorkspaces;
}

function configuredActorId(): string {
  const actorId = requiredEnvironment("CV_WEB_PRINCIPAL_ACTOR_ID");
  if (
    actorId.trim() !== actorId ||
    actorId.length === 0 ||
    Array.from(actorId).length > 128
  ) {
    throw new TrustedPrincipalConfigurationError(
      "CV_WEB_PRINCIPAL_ACTOR_ID must be a canonical actor ID",
    );
  }
  return actorId;
}

export function workspaceGatewayCapabilities(workspaceId: string | null): {
  administrator: boolean;
} {
  const allowedWorkspaces = configuredWorkspaces();
  if (
    workspaceId === null ||
    !WORKSPACE_ID_PATTERN.test(workspaceId) ||
    !allowedWorkspaces.has(workspaceId)
  ) {
    throw new WorkspaceBoundaryError();
  }
  return {
    administrator: configuredAdminWorkspaces(allowedWorkspaces).has(workspaceId),
  };
}

export function issueWorkspacePrincipal(
  workspaceId: string | null,
  issuedAt = Math.floor(Date.now() / 1000),
): { actorId: string; token: string } {
  const capabilities = workspaceGatewayCapabilities(workspaceId);
  const keyId = requiredEnvironment("CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID");
  const secret = requiredEnvironment(
    "CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET",
  );
  if (!KEY_ID_PATTERN.test(keyId)) {
    throw new TrustedPrincipalConfigurationError(
      "CV_TRUSTED_PRINCIPAL_CURRENT_KEY_ID is invalid",
    );
  }
  if (secret.length < MINIMUM_HMAC_SECRET_CHARACTERS) {
    throw new TrustedPrincipalConfigurationError(
      "CV_TRUSTED_PRINCIPAL_CURRENT_HMAC_SECRET must contain at least 32 characters",
    );
  }
  if (!Number.isSafeInteger(issuedAt) || issuedAt < 0) {
    throw new TrustedPrincipalConfigurationError(
      "trusted principal issue time is invalid",
    );
  }
  const actorId = configuredActorId();
  const claims = {
    actor_id: actorId,
    workspace_ids: [workspaceId],
    admin_workspace_ids: capabilities.administrator ? [workspaceId] : [],
    system_admin: false,
    issued_at: issuedAt,
  };
  const encoded = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const signature = createHmac("sha256", secret)
    .update(`${keyId}.${encoded}`)
    .digest("hex");
  return {
    actorId,
    token: `${keyId}.${encoded}.${signature}`,
  };
}
