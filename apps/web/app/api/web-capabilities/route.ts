import {
  TrustedPrincipalConfigurationError,
  WorkspaceBoundaryError,
  workspaceGatewayCapabilities,
} from "../../../lib/trusted-principal.ts";

const NO_STORE_HEADERS = {
  "Cache-Control": "no-store",
};

export function GET(request: Request): Response {
  try {
    return Response.json(
      workspaceGatewayCapabilities(request.headers.get("x-workspace-id")),
      { headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    if (error instanceof WorkspaceBoundaryError) {
      return Response.json(
        {
          code: "NOT_FOUND",
          message: "Workspace capability was not found.",
          retryable: false,
        },
        { status: 404, headers: NO_STORE_HEADERS },
      );
    }
    if (error instanceof TrustedPrincipalConfigurationError) {
      return Response.json(
        {
          code: "GATEWAY_CONFIGURATION_UNAVAILABLE",
          message: "Workspace capability is temporarily unavailable.",
          retryable: true,
        },
        { status: 503, headers: NO_STORE_HEADERS },
      );
    }
    throw error;
  }
}
