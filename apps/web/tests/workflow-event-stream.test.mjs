import { describe, expect, it, vi } from "vitest";

import {
  consumeWorkflowEventStream,
  WorkflowEventStreamProtocolError,
} from "../lib/workflow-event-stream";

const WORKFLOW_ID = "019f8a00-0000-7000-8000-000000000122";

function event(aggregateVersion = 7) {
  return {
    event_id: "019f8a00-0000-7000-8000-000000000130",
    event_type: "workflow.human_input_received",
    schema_version: 1,
    aggregate_type: "workflow",
    aggregate_id: WORKFLOW_ID,
    aggregate_version: aggregateVersion,
    occurred_at: "2026-08-05T08:00:00Z",
    trace_id: "trace-creative-plan",
    payload: { approval_type: "CREATIVE_PLAN" },
  };
}

function fragmentedResponse(chunks) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { headers: { "content-type": "text/event-stream; charset=utf-8" } },
  );
}

describe("Workflow event stream", () => {
  it("decodes fragmented persisted events and commits only their opaque cursors", async () => {
    const body = `retry: 750\nid: cursor-safe-1\nevent: workflow.event\ndata: ${JSON.stringify(event())}\n\n`;
    const fetchMock = vi.fn(async () =>
      fragmentedResponse([body.slice(0, 31), body.slice(31, 94), body.slice(94)]),
    );
    const events = [];
    const cursors = [];

    const result = await consumeWorkflowEventStream({
      fetcher: fetchMock,
      workspaceId: "catalog-demo",
      workflowId: WORKFLOW_ID,
      cursor: "cursor-safe-0",
      signal: new AbortController().signal,
      onEvent: (value) => events.push(value),
      onCursor: (value) => cursors.push(value),
    });

    expect(events).toEqual([event()]);
    expect(cursors).toEqual(["cursor-safe-1"]);
    expect(result).toEqual({ cursor: "cursor-safe-1", retryMilliseconds: 750 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`/api/v1/workflows/${WORKFLOW_ID}/events`);
    expect(Object.fromEntries(new Headers(init.headers).entries())).toEqual({
      accept: "text/event-stream",
      "last-event-id": "cursor-safe-0",
      "x-workspace-id": "catalog-demo",
    });
    expect(init.cache).toBe("no-store");
  });

  it("fails closed before advancing a cursor for a cross-Workflow event", async () => {
    const invalid = event();
    invalid.aggregate_id = "019f8a00-0000-7000-8000-000000000199";
    const body = `id: cursor-must-not-commit\nevent: workflow.event\ndata: ${JSON.stringify(invalid)}\n\n`;
    const cursors = [];

    await expect(
      consumeWorkflowEventStream({
        fetcher: async () => fragmentedResponse([body]),
        workspaceId: "catalog-demo",
        workflowId: WORKFLOW_ID,
        cursor: null,
        signal: new AbortController().signal,
        onEvent: vi.fn(),
        onCursor: (value) => cursors.push(value),
      }),
    ).rejects.toBeInstanceOf(WorkflowEventStreamProtocolError);
    expect(cursors).toEqual([]);
  });

  it("rejects non-stream responses and injected or oversized resume cursors", async () => {
    await expect(
      consumeWorkflowEventStream({
        fetcher: async () => Response.json({}),
        workspaceId: "catalog-demo",
        workflowId: WORKFLOW_ID,
        cursor: null,
        signal: new AbortController().signal,
        onEvent: vi.fn(),
        onCursor: vi.fn(),
      }),
    ).rejects.toBeInstanceOf(WorkflowEventStreamProtocolError);

    for (const cursor of ["line-one\nline-two", "x".repeat(257)]) {
      await expect(
        consumeWorkflowEventStream({
          fetcher: vi.fn(),
          workspaceId: "catalog-demo",
          workflowId: WORKFLOW_ID,
          cursor,
          signal: new AbortController().signal,
          onEvent: vi.fn(),
          onCursor: vi.fn(),
        }),
      ).rejects.toBeInstanceOf(WorkflowEventStreamProtocolError);
    }
  });
});
