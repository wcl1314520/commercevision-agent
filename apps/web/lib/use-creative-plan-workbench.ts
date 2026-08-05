"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ApprovalDecision,
  CreativePlanCurrentResponseV1,
  CreativePlanPayloadV1,
  CreativePlanVersionResponseV1,
  EventResponse,
  WorkflowResponse,
} from "./generated/catalog-api";
import {
  CreativePlanApi,
  CreativePlanApiCancelledError,
  CreativePlanApiError,
  newCreativePlanIdempotencyKey,
} from "./creative-plan-api";
import { decodeCreativePlanPayload } from "./creative-plan-api-decoders";
import {
  readCreativePlanReviewSession,
  writeCreativePlanReviewSession,
  type CreativePlanRecoverableDraft,
  type CreativePlanReviewSession,
} from "./creative-plan-review-session";
import {
  classifyCreativePlanCommandFailure,
  type CreativePlanCommandFailure,
} from "./creative-plan-workbench-state";
import {
  consumeWorkflowEventStream,
  WorkflowEventStreamHttpError,
  WorkflowEventStreamProtocolError,
} from "./workflow-event-stream";

const DEFAULT_WORKSPACE_ID = "catalog-demo";

export type CreativePlanReviewData = {
  current: CreativePlanCurrentResponseV1;
  workflow: WorkflowResponse;
  versions: CreativePlanVersionResponseV1[];
  nextCursor: string | null;
  visibleVersionNumber: number;
};

export type CreativePlanReadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "policy-denied"; message: string }
  | { kind: "retention-expired"; message: string }
  | { kind: "ready"; data: CreativePlanReviewData };

export type CreativePlanCommandState =
  | { kind: "idle" }
  | { kind: "submitting"; action: "revise" | ApprovalDecision }
  | {
      kind: "failure";
      action: "revise" | ApprovalDecision;
      failure: CreativePlanCommandFailure;
      message: string;
      retryable: boolean;
    }
  | { kind: "success"; message: string };

export type CreativePlanStreamState =
  | "offline"
  | "connecting"
  | "live"
  | "reconnecting"
  | "degraded"
  | "retention-expired"
  | "policy-denied";

type PendingCommand =
  | {
      kind: "revise";
      idempotencyKey: string;
      draft: CreativePlanRecoverableDraft;
      payload: CreativePlanPayloadV1;
      expectedWorkflowVersion: number;
      expectedHeadVersion: number;
    }
  | {
      kind: "decision";
      decision: "APPROVE" | "REJECT";
      idempotencyKey: string;
      reasonCode: string | null;
      commentRef: string | null;
      subjectVersion: number;
      expectedWorkflowVersion: number;
    };

function readMessage(error: unknown): string {
  if (error instanceof CreativePlanApiError) {
    if (error.status === 404) {
      return "未找到该工作区中的创意方案或 Workflow，请核对两个标识。";
    }
    if (error.status === 403 || error.status === 401) {
      return "当前身份无权读取此工作区的方案审查事实。";
    }
    if (error.status === 410) return "方案或 Workflow 的保留期已结束。";
    if (error.status === 502) {
      return "服务返回了不一致的审查事实；页面已拒绝显示。";
    }
    if (error.status === 504) return "读取审查事实超时，请重试。";
    return error.envelope?.message ?? "无法读取方案审查事实，请稍后重试。";
  }
  return "无法读取方案审查事实，请稍后重试。";
}

function commandMessage(
  failure: CreativePlanCommandFailure,
  error: unknown,
): string {
  if (failure.kind === "conflict") {
    return "权威版本已变化。页面已重新读取当前事实；输入内容仍保留，命令未被自动重放。";
  }
  if (failure.kind === "policy-denied") return "策略或身份不允许执行此命令。";
  if (failure.kind === "retention-expired") return "保留期已结束，不能再修改或审批。";
  if (failure.kind === "retryable") {
    return "命令结果尚未确认；可使用同一幂等键安全重试。";
  }
  return error instanceof CreativePlanApiError
    ? (error.envelope?.message ?? "服务器拒绝了该命令。")
    : "服务器拒绝了该命令。";
}

function mergeVersions(
  existing: CreativePlanVersionResponseV1[],
  incoming: CreativePlanVersionResponseV1[],
): CreativePlanVersionResponseV1[] {
  const versions = new Map<number, CreativePlanVersionResponseV1>();
  for (const version of [...existing, ...incoming]) {
    versions.set(version.version_number, version);
  }
  return [...versions.values()].sort(
    (left, right) => right.version_number - left.version_number,
  );
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timeout = setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timeout);
        resolve();
      },
      { once: true },
    );
  });
}

export function useCreativePlanWorkbench(
  workspaceId = DEFAULT_WORKSPACE_ID,
) {
  const [api] = useState(() => new CreativePlanApi({ workspaceId }));
  const [workflowId, setWorkflowId] = useState("");
  const [creativePlanId, setCreativePlanId] = useState("");
  const [readState, setReadState] = useState<CreativePlanReadState>({ kind: "idle" });
  const [commandState, setCommandState] = useState<CreativePlanCommandState>({
    kind: "idle",
  });
  const [streamState, setStreamState] = useState<CreativePlanStreamState>("offline");
  const [streamCursor, setStreamCursor] = useState<string | null>(null);
  const [draft, setDraft] = useState<CreativePlanRecoverableDraft | null>(null);
  const activeRead = useRef<AbortController | null>(null);
  const dataRef = useRef<CreativePlanReviewData | null>(null);
  const cursorRef = useRef<string | null>(null);
  const draftRef = useRef<CreativePlanRecoverableDraft | null>(null);
  const pendingCommand = useRef<PendingCommand | null>(null);
  const eventRefreshActive = useRef(false);
  const identityRef = useRef({ workflowId: "", creativePlanId: "" });

  const publishData = useCallback((data: CreativePlanReviewData) => {
    dataRef.current = data;
    setReadState({ kind: "ready", data });
  }, []);

  const loadAuthority = useCallback(
    async ({
      targetWorkflowId = identityRef.current.workflowId,
      targetCreativePlanId = identityRef.current.creativePlanId,
      restoredSession = null,
      foreground = true,
      selectCurrent = false,
    }: {
      targetWorkflowId?: string;
      targetCreativePlanId?: string;
      restoredSession?: CreativePlanReviewSession | null;
      foreground?: boolean;
      selectCurrent?: boolean;
    } = {}) => {
      activeRead.current?.abort();
      const request = new AbortController();
      activeRead.current = request;
      if (foreground) setReadState({ kind: "loading" });
      try {
        const [current, workflow, page] = await Promise.all([
          api.getCurrent(targetCreativePlanId, targetWorkflowId, request.signal),
          api.getWorkflow(targetWorkflowId, request.signal),
          api.listVersions(
            targetCreativePlanId,
            targetWorkflowId,
            { limit: 20 },
            request.signal,
          ),
        ]);
        if (request.signal.aborted || activeRead.current !== request) return;
        const prior = dataRef.current;
        const requestedVersion =
          selectCurrent
            ? current.head.current_version_number
            : (restoredSession?.selectedVersionNumber ??
              (prior?.current.head.creative_plan_id === targetCreativePlanId &&
              prior.current.head.workflow_id === targetWorkflowId
                ? prior.visibleVersionNumber
                : current.head.current_version_number));
        let versions = mergeVersions([current.version], page.items);
        if (!versions.some((item) => item.version_number === requestedVersion)) {
          const restoredVersion = await api.getVersion(
            targetCreativePlanId,
            targetWorkflowId,
            requestedVersion,
            request.signal,
          );
          if (request.signal.aborted || activeRead.current !== request) return;
          versions = mergeVersions(versions, [restoredVersion]);
        }
        setWorkflowId(targetWorkflowId);
        setCreativePlanId(targetCreativePlanId);
        identityRef.current = {
          workflowId: targetWorkflowId,
          creativePlanId: targetCreativePlanId,
        };
        publishData({
          current,
          workflow,
          versions,
          nextCursor: page.next_cursor ?? null,
          visibleVersionNumber: requestedVersion,
        });
        if (restoredSession) {
          setStreamCursor(restoredSession.streamCursor);
          cursorRef.current = restoredSession.streamCursor;
          setDraft(restoredSession.draft);
          draftRef.current = restoredSession.draft;
        }
      } catch (error) {
        if (
          request.signal.aborted ||
          activeRead.current !== request ||
          error instanceof CreativePlanApiCancelledError
        ) {
          return;
        }
        const message = readMessage(error);
        if (error instanceof CreativePlanApiError && [401, 403].includes(error.status)) {
          setReadState({ kind: "policy-denied", message });
        } else if (error instanceof CreativePlanApiError && error.status === 410) {
          setReadState({ kind: "retention-expired", message });
        } else if (foreground || dataRef.current === null) {
          setReadState({ kind: "error", message });
        } else {
          setCommandState({
            kind: "failure",
            action: "revise",
            failure: { kind: "retryable", status: 0 },
            message: "实时事件已到达，但权威事实刷新失败；请手动重试。",
            retryable: false,
          });
        }
      } finally {
        if (activeRead.current === request) activeRead.current = null;
      }
    },
    [api, publishData],
  );

  useEffect(() => {
    let session: CreativePlanReviewSession | null = null;
    try {
      session = readCreativePlanReviewSession(window.sessionStorage, workspaceId);
    } catch {
      session = null;
    }
    if (session) {
      setWorkflowId(session.workflowId);
      setCreativePlanId(session.creativePlanId);
      identityRef.current = {
        workflowId: session.workflowId,
        creativePlanId: session.creativePlanId,
      };
      void loadAuthority({
        targetWorkflowId: session.workflowId,
        targetCreativePlanId: session.creativePlanId,
        restoredSession: session,
      });
    }
    return () => activeRead.current?.abort();
  }, [loadAuthority, workspaceId]);

  useEffect(() => {
    if (readState.kind !== "ready") return;
    try {
      writeCreativePlanReviewSession(window.sessionStorage, {
        workspaceId,
        workflowId,
        creativePlanId,
        selectedVersionNumber: readState.data.visibleVersionNumber,
        streamCursor,
        draft,
      });
    } catch {
      // Review remains usable when browser storage is unavailable or full.
    }
  }, [creativePlanId, draft, readState, streamCursor, workflowId, workspaceId]);

  useEffect(() => {
    if (readState.kind !== "ready") {
      setStreamState("offline");
      return;
    }
    const controller = new AbortController();
    const run = async () => {
      setStreamState("connecting");
      let retryMilliseconds = 1_000;
      while (!controller.signal.aborted) {
        try {
          const result = await consumeWorkflowEventStream({
            workspaceId,
            workflowId,
            cursor: cursorRef.current,
            signal: controller.signal,
            onCursor: (nextCursor) => {
              cursorRef.current = nextCursor;
              setStreamCursor(nextCursor);
            },
            onEvent: async (event: EventResponse) => {
              setStreamState("live");
              if (
                event.aggregate_version <= (dataRef.current?.workflow.version ?? 0) ||
                eventRefreshActive.current
              ) {
                return;
              }
              eventRefreshActive.current = true;
              try {
                await loadAuthority({ foreground: false });
              } finally {
                eventRefreshActive.current = false;
              }
            },
          });
          retryMilliseconds = result.retryMilliseconds;
          if (controller.signal.aborted) return;
          setStreamState("reconnecting");
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof WorkflowEventStreamHttpError) {
            if (error.status === 401 || error.status === 403) {
              setStreamState("policy-denied");
              return;
            }
            if (error.status === 404 || error.status === 410) {
              setStreamState("retention-expired");
              return;
            }
          }
          setStreamState("degraded");
          if (error instanceof WorkflowEventStreamProtocolError) return;
        }
        await abortableDelay(retryMilliseconds, controller.signal);
        if (!controller.signal.aborted) setStreamState("reconnecting");
      }
    };
    void run();
    return () => controller.abort();
  }, [loadAuthority, readState.kind, workflowId, workspaceId]);

  const changeIdentity = useCallback((nextWorkflowId: string, nextPlanId: string) => {
    activeRead.current?.abort();
    dataRef.current = null;
    cursorRef.current = null;
    draftRef.current = null;
    pendingCommand.current = null;
    setWorkflowId(nextWorkflowId);
    setCreativePlanId(nextPlanId);
    identityRef.current = {
      workflowId: nextWorkflowId,
      creativePlanId: nextPlanId,
    };
    setStreamCursor(null);
    setDraft(null);
    setReadState({ kind: "idle" });
    setCommandState({ kind: "idle" });
  }, []);

  const selectVersion = useCallback((versionNumber: number) => {
    const data = dataRef.current;
    if (!data?.versions.some((version) => version.version_number === versionNumber)) {
      return;
    }
    publishData({ ...data, visibleVersionNumber: versionNumber });
  }, [publishData]);

  const loadOlderVersions = useCallback(async () => {
    const data = dataRef.current;
    if (!data?.nextCursor) return;
    const requestedPlanId = data.current.head.creative_plan_id;
    const requestedWorkflowId = data.current.head.workflow_id;
    try {
      const page = await api.listVersions(requestedPlanId, requestedWorkflowId, {
        limit: 20,
        cursor: data.nextCursor,
      });
      const latest = dataRef.current;
      if (
        !latest ||
        latest.current.head.creative_plan_id !== requestedPlanId ||
        latest.current.head.workflow_id !== requestedWorkflowId
      ) {
        return;
      }
      publishData({
        ...latest,
        versions: mergeVersions(latest.versions, page.items),
        nextCursor: page.next_cursor ?? null,
      });
    } catch (error) {
      setCommandState({
        kind: "failure",
        action: "revise",
        failure: classifyCreativePlanCommandFailure(error),
        message: readMessage(error),
        retryable: false,
      });
    }
  }, [api, publishData]);

  const beginRevision = useCallback(() => {
    const data = dataRef.current;
    if (!data) return;
    const version = data.versions.find(
      (item) => item.version_number === data.current.head.current_version_number,
    );
    if (!version) return;
    const nextDraft = {
      baseVersionId: version.id,
      baseVersionNumber: version.version_number,
      payloadText: JSON.stringify(version.payload, null, 2),
      revisionReason: "",
    };
    draftRef.current = nextDraft;
    setDraft(nextDraft);
    setCommandState({ kind: "idle" });
  }, []);

  const updateRevision = useCallback((payloadText: string, revisionReason: string) => {
    const currentDraft = draftRef.current;
    if (!currentDraft) return;
    const nextDraft = { ...currentDraft, payloadText, revisionReason };
    draftRef.current = nextDraft;
    setDraft(nextDraft);
    setCommandState({ kind: "idle" });
  }, []);

  const cancelRevision = useCallback(() => {
    draftRef.current = null;
    setDraft(null);
    pendingCommand.current = null;
    setCommandState({ kind: "idle" });
  }, []);

  const executePending = useCallback(async () => {
    const command = pendingCommand.current;
    const data = dataRef.current;
    if (!command || !data) return;
    const action = command.kind === "revise" ? "revise" : command.decision;
    setCommandState({ kind: "submitting", action });
    try {
      if (command.kind === "revise") {
        await api.revise(
          creativePlanId,
          {
            workflow_id: workflowId,
            payload: command.payload,
            revision_reason: command.draft.revisionReason,
            expected_workflow_version: command.expectedWorkflowVersion,
            expected_head_version: command.expectedHeadVersion,
          },
          command.idempotencyKey,
        );
      } else {
        const request = {
          expected_workflow_version: command.expectedWorkflowVersion,
          subject_id: creativePlanId,
          subject_version: command.subjectVersion,
          decision: command.decision,
          reason_code: command.reasonCode,
          comment_ref: command.commentRef,
        };
        await (command.decision === "APPROVE"
          ? api.approve(workflowId, request, command.idempotencyKey)
          : api.reject(workflowId, request, command.idempotencyKey));
      }
      pendingCommand.current = null;
      if (command.kind === "revise") {
        draftRef.current = null;
        setDraft(null);
      }
      await loadAuthority({
        foreground: false,
        selectCurrent: command.kind === "revise",
      });
      setCommandState({
        kind: "success",
        message:
          command.kind === "revise"
            ? "已创建新的不可变方案版本。"
            : `已提交方案 v${command.subjectVersion} 的${command.decision === "APPROVE" ? "批准" : "驳回"}决定。`,
      });
    } catch (error) {
      const failure = classifyCreativePlanCommandFailure(error);
      const retryable = failure.kind === "retryable";
      if (!retryable) pendingCommand.current = null;
      if (failure.kind === "conflict") {
        await loadAuthority({ foreground: false, selectCurrent: true });
      }
      setCommandState({
        kind: "failure",
        action,
        failure,
        message: commandMessage(failure, error),
        retryable,
      });
    }
  }, [api, creativePlanId, loadAuthority, workflowId]);

  const submitRevision = useCallback(() => {
    const data = dataRef.current;
    const currentDraft = draftRef.current;
    if (!data || !currentDraft) return;
    if (
      currentDraft.revisionReason.trim() !== currentDraft.revisionReason ||
      currentDraft.revisionReason.length < 1 ||
      currentDraft.revisionReason.length > 512
    ) {
      setCommandState({
        kind: "failure",
        action: "revise",
        failure: { kind: "rejected", status: 0 },
        message: "请填写 1–512 个字符且首尾无空格的修订原因。",
        retryable: false,
      });
      return;
    }
    let payload: CreativePlanPayloadV1;
    try {
      payload = decodeCreativePlanPayload(JSON.parse(currentDraft.payloadText));
    } catch {
      setCommandState({
        kind: "failure",
        action: "revise",
        failure: { kind: "rejected", status: 0 },
        message: "方案 JSON 不符合 Creative Plan v1 契约。",
        retryable: false,
      });
      return;
    }
    pendingCommand.current = {
      kind: "revise",
      idempotencyKey: newCreativePlanIdempotencyKey("revise"),
      draft: currentDraft,
      payload,
      expectedWorkflowVersion: data.workflow.version,
      expectedHeadVersion: data.current.head.version,
    };
    void executePending();
  }, [executePending]);

  const submitDecision = useCallback(
    (decision: "APPROVE" | "REJECT", reasonCode: string, commentRef: string) => {
      const data = dataRef.current;
      if (!data) return;
      const normalizedReason = reasonCode.trim();
      const normalizedComment = commentRef.trim();
      if (normalizedReason.length > 128 || normalizedComment.length > 512) {
        setCommandState({
          kind: "failure",
          action: decision,
          failure: { kind: "rejected", status: 0 },
          message: "原因代码最多 128 个字符，备注引用最多 512 个字符。",
          retryable: false,
        });
        return;
      }
      pendingCommand.current = {
        kind: "decision",
        decision,
        idempotencyKey: newCreativePlanIdempotencyKey(decision.toLowerCase()),
        reasonCode: normalizedReason || null,
        commentRef: normalizedComment || null,
        subjectVersion: data.visibleVersionNumber,
        expectedWorkflowVersion: data.workflow.version,
      };
      void executePending();
    },
    [executePending],
  );

  return {
    workflowId,
    creativePlanId,
    readState,
    commandState,
    streamState,
    draft,
    changeIdentity,
    loadAuthority,
    selectVersion,
    loadOlderVersions,
    beginRevision,
    updateRevision,
    cancelRevision,
    submitRevision,
    submitDecision,
    retryPendingCommand: executePending,
  };
}
