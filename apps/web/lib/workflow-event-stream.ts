import type { EventResponse } from "./generated/catalog-api";
import {
  CreativePlanProtocolError,
  decodeWorkflowEventResponse,
} from "./creative-plan-api-decoders";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const WORKSPACE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAXIMUM_CURSOR_CHARACTERS = 256;
const MAXIMUM_EVENT_DATA_CHARACTERS = 256 * 1024;
const MAXIMUM_PENDING_STREAM_CHARACTERS = MAXIMUM_EVENT_DATA_CHARACTERS + 1024;

export class WorkflowEventStreamProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowEventStreamProtocolError";
  }
}

export class WorkflowEventStreamHttpError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Workflow event stream failed with ${status}`);
    this.name = "WorkflowEventStreamHttpError";
    this.status = status;
  }
}

export type WorkflowEventStreamResult = {
  cursor: string | null;
  retryMilliseconds: number;
};

type ConsumeOptions = {
  baseUrl?: string;
  fetcher?: typeof fetch;
  workspaceId: string;
  workflowId: string;
  cursor: string | null;
  signal: AbortSignal;
  onEvent: (event: EventResponse) => void | Promise<void>;
  onCursor: (cursor: string) => void;
};

function validateCursor(value: string | null): void {
  if (
    value !== null &&
    (value.length < 1 ||
      value.length > MAXIMUM_CURSOR_CHARACTERS ||
      /[\u0000-\u001f\u007f]/.test(value))
  ) {
    throw new WorkflowEventStreamProtocolError("Workflow event cursor is invalid");
  }
}

function validateIdentity(workspaceId: string, workflowId: string): void {
  if (!WORKSPACE_PATTERN.test(workspaceId) || !UUID_PATTERN.test(workflowId)) {
    throw new WorkflowEventStreamProtocolError("Workflow stream identity is invalid");
  }
}

export async function consumeWorkflowEventStream({
  baseUrl = "",
  fetcher = fetch,
  workspaceId,
  workflowId,
  cursor: initialCursor,
  signal,
  onEvent,
  onCursor,
}: ConsumeOptions): Promise<WorkflowEventStreamResult> {
  validateIdentity(workspaceId, workflowId);
  validateCursor(initialCursor);
  const headers = new Headers({
    Accept: "text/event-stream",
    "X-Workspace-Id": workspaceId,
  });
  if (initialCursor !== null) headers.set("Last-Event-ID", initialCursor);

  const response = await fetcher(
    `${baseUrl}/api/v1/workflows/${encodeURIComponent(workflowId)}/events`,
    { cache: "no-store", headers, signal },
  );
  if (!response.ok) {
    void response.body?.cancel().catch(() => undefined);
    throw new WorkflowEventStreamHttpError(response.status);
  }
  if (
    !response.headers
      .get("content-type")
      ?.toLowerCase()
      .startsWith("text/event-stream") ||
    response.body === null
  ) {
    void response.body?.cancel().catch(() => undefined);
    throw new WorkflowEventStreamProtocolError(
      "Workflow event response is not an SSE stream",
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let pending = "";
  let eventName = "";
  let eventId: string | null = null;
  let dataLines: string[] = [];
  let cursor = initialCursor;
  let retryMilliseconds = 1_000;

  const dispatch = async (): Promise<void> => {
    if (dataLines.length === 0) {
      eventName = "";
      eventId = null;
      return;
    }
    const data = dataLines.join("\n");
    const name = eventName || "message";
    const id = eventId;
    eventName = "";
    eventId = null;
    dataLines = [];
    if (name !== "workflow.event") return;
    validateCursor(id);
    if (id === null) {
      throw new WorkflowEventStreamProtocolError(
        "Persisted Workflow event has no resume cursor",
      );
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
      const event = decodeWorkflowEventResponse(parsed, {
        workspaceId,
        workflowId,
      });
      await onEvent(event);
    } catch (error) {
      if (error instanceof WorkflowEventStreamProtocolError) throw error;
      if (error instanceof CreativePlanProtocolError || error instanceof SyntaxError) {
        throw new WorkflowEventStreamProtocolError(
          "Persisted Workflow event failed its public contract",
        );
      }
      throw error;
    }
    cursor = id;
    onCursor(id);
  };

  const acceptLine = async (line: string): Promise<void> => {
    if (line === "") {
      await dispatch();
      return;
    }
    if (line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    if (field === "id") {
      validateCursor(value);
      eventId = value;
    }
    if (field === "data") {
      const size = dataLines.reduce((total, item) => total + item.length, 0);
      if (size + value.length > MAXIMUM_EVENT_DATA_CHARACTERS) {
        throw new WorkflowEventStreamProtocolError(
          "Workflow event data exceeds the browser limit",
        );
      }
      dataLines.push(value);
    }
    if (field === "retry" && /^[0-9]+$/.test(value)) {
      const candidate = Number(value);
      if (Number.isSafeInteger(candidate) && candidate >= 100 && candidate <= 30_000) {
        retryMilliseconds = candidate;
      }
    }
  };

  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      try {
        pending += decoder.decode(value, { stream: true });
      } catch {
        throw new WorkflowEventStreamProtocolError(
          "Workflow event stream contains invalid UTF-8",
        );
      }
      if (pending.length > MAXIMUM_PENDING_STREAM_CHARACTERS) {
        throw new WorkflowEventStreamProtocolError(
          "Workflow event stream frame exceeds the browser limit",
        );
      }
      let newline = pending.indexOf("\n");
      while (newline >= 0) {
        let line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        await acceptLine(line);
        newline = pending.indexOf("\n");
      }
    }
    try {
      decoder.decode();
    } catch {
      throw new WorkflowEventStreamProtocolError(
        "Workflow event stream ended with invalid UTF-8",
      );
    }
    return { cursor, retryMilliseconds };
  } catch (error) {
    void reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
}
