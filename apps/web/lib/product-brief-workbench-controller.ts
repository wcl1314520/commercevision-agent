import type {
  ProductBriefOperationStatusResponseV1,
  ProductBriefResponseV1,
  ProductBriefVersionListResponseV1,
  ProductBriefVersionSummaryResponseV1,
} from "./generated/catalog-api";
import {
  isProductBriefOperationPollTerminal,
  operationPollDelayMs,
  shouldContinueOperationPolling,
} from "./operation-polling";
import type { PendingProductBriefAnalysis } from "./product-brief-persistence";
import {
  pendingProductBriefCommandFor,
  productBriefCommandsMatch,
} from "./product-brief-workbench-state";
import type { PendingProductBriefCommand } from "./product-brief-workbench-state";

export type ProductBriefControllerIdentity = {
  workspaceId: string;
  productId: string;
};

type ReadToken = {
  identityGeneration: number;
  readGeneration: number;
};

type HistoryReadToken = ReadToken & {
  briefId: string;
  mode: "initial" | "more";
};

type OperationReadToken = ReadToken & {
  operationId: string;
};

export const PRODUCT_BRIEF_HISTORY_PAGE_SIZE = 20;

export type ProductBriefControllerSnapshot = {
  identity: ProductBriefControllerIdentity;
  identityGeneration: number;
  brief: ProductBriefResponseV1 | null;
  versions: ProductBriefVersionSummaryResponseV1[];
  versionsNextCursor: number | null;
  historyLoading: "initial" | "more" | null;
  operation: ProductBriefOperationStatusResponseV1 | null;
  pendingAnalysis: PendingProductBriefAnalysis | null;
  pendingCommand: PendingProductBriefCommand | null;
  commandStatus: "pending" | "version-conflict" | null;
  polling: {
    operationId: string | null;
    completedRequests: number;
    nextDelayMs: number | null;
    requestToken: number;
    paused: boolean;
  };
  formRevision: number;
  retentionExpired: boolean;
};

function initialSnapshot(
  identity: ProductBriefControllerIdentity,
  identityGeneration: number,
): ProductBriefControllerSnapshot {
  return {
    identity,
    identityGeneration,
    brief: null,
    versions: [],
    versionsNextCursor: null,
    historyLoading: null,
    operation: null,
    pendingAnalysis: null,
    pendingCommand: null,
    commandStatus: null,
    polling: {
      operationId: null,
      completedRequests: 0,
      nextDelayMs: null,
      requestToken: 0,
      paused: false,
    },
    formRevision: identityGeneration,
    retentionExpired: false,
  };
}

export class ProductBriefWorkbenchController {
  private snapshot: ProductBriefControllerSnapshot;
  private briefReadGeneration = 0;
  private historyReadGeneration = 0;
  private operationReadGeneration = 0;

  constructor(identity: ProductBriefControllerIdentity) {
    this.snapshot = initialSnapshot(identity, 1);
  }

  getSnapshot = (): ProductBriefControllerSnapshot => this.snapshot;

  restartIdentity(identity: ProductBriefControllerIdentity): void {
    this.briefReadGeneration += 1;
    this.historyReadGeneration += 1;
    this.operationReadGeneration += 1;
    this.snapshot = initialSnapshot(
      identity,
      this.snapshot.identityGeneration + 1,
    );
  }

  changeIdentity(identity: ProductBriefControllerIdentity): boolean {
    if (
      identity.workspaceId === this.snapshot.identity.workspaceId &&
      identity.productId === this.snapshot.identity.productId
    ) {
      return false;
    }
    this.restartIdentity(identity);
    return true;
  }

  expireRetention(): void {
    const next = initialSnapshot(
      this.snapshot.identity,
      this.snapshot.identityGeneration + 1,
    );
    this.briefReadGeneration += 1;
    this.historyReadGeneration += 1;
    this.operationReadGeneration += 1;
    this.snapshot = { ...next, retentionExpired: true };
  }

  beginBriefRead(): ReadToken {
    this.briefReadGeneration += 1;
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.briefReadGeneration,
    };
  }

  publishBrief(
    token: ReadToken,
    brief: ProductBriefResponseV1,
    resetForm = true,
  ): boolean {
    const published = this.snapshot.brief;
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.briefReadGeneration ||
      brief.product_id !== this.snapshot.identity.productId ||
      (published?.id === brief.id && brief.version < published.version)
    ) {
      return false;
    }
    const operationChanged =
      published?.operation_id !== brief.operation_id;
    const briefChanged = published?.id !== brief.id;
    this.snapshot = {
      ...this.snapshot,
      brief,
      ...(briefChanged
        ? {
            versions: [],
            versionsNextCursor: null,
            historyLoading: null,
          }
        : {}),
      ...(operationChanged
        ? {
            operation: null,
            polling: {
              operationId: brief.operation_id,
              completedRequests: 0,
              nextDelayMs: 0,
              requestToken: this.snapshot.polling.requestToken + 1,
              paused: false,
            },
          }
        : {}),
      formRevision: resetForm
        ? this.snapshot.formRevision + 1
        : this.snapshot.formRevision,
    };
    return true;
  }

  beginHistoryRead(
    mode: "initial" | "more",
    briefId: string,
  ): HistoryReadToken {
    this.historyReadGeneration += 1;
    this.snapshot = {
      ...this.snapshot,
      historyLoading: mode,
    };
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.historyReadGeneration,
      briefId,
      mode,
    };
  }

  publishHistory(
    token: HistoryReadToken,
    page: ProductBriefVersionListResponseV1,
  ): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.historyReadGeneration ||
      token.briefId !== this.snapshot.brief?.id ||
      page.items.length > PRODUCT_BRIEF_HISTORY_PAGE_SIZE ||
      page.items.some(
        (version) => version.product_brief_id !== token.briefId,
      )
    ) {
      return false;
    }
    const existing =
      token.mode === "more" ? this.snapshot.versions : [];
    const versions = [...existing];
    const versionIds = new Set(existing.map((version) => version.id));
    for (const version of page.items) {
      if (versionIds.has(version.id)) continue;
      versionIds.add(version.id);
      versions.push(version);
    }
    this.snapshot = {
      ...this.snapshot,
      versions,
      versionsNextCursor: page.next_cursor,
      historyLoading: null,
    };
    return true;
  }

  failHistory(token: HistoryReadToken, clearUnavailable: boolean): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.historyReadGeneration ||
      token.briefId !== this.snapshot.brief?.id
    ) {
      return false;
    }
    this.snapshot = {
      ...this.snapshot,
      ...(clearUnavailable && token.mode === "initial"
        ? { versions: [], versionsNextCursor: null }
        : {}),
      historyLoading: null,
    };
    return true;
  }

  beginOperationRead(operationId: string): OperationReadToken {
    this.operationReadGeneration += 1;
    return {
      identityGeneration: this.snapshot.identityGeneration,
      readGeneration: this.operationReadGeneration,
      operationId,
    };
  }

  publishOperation(
    token: OperationReadToken,
    operation: ProductBriefOperationStatusResponseV1,
  ): boolean {
    const published = this.snapshot.operation;
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.operationReadGeneration ||
      token.operationId !== this.snapshot.brief?.operation_id ||
      operation.id !== token.operationId ||
      (published?.id === operation.id &&
        operation.version < published.version)
    ) {
      return false;
    }
    const stopped = isProductBriefOperationPollTerminal(operation.state);
    this.snapshot = {
      ...this.snapshot,
      operation,
      polling: {
        ...this.snapshot.polling,
        operationId: operation.id,
        ...(stopped
          ? { nextDelayMs: null, paused: false }
          : {}),
      },
    };
    return true;
  }

  completeAuxiliaryOperationRead(
    token: OperationReadToken,
    operation: ProductBriefOperationStatusResponseV1,
    random: () => number = Math.random,
  ): boolean {
    if (!this.publishOperation(token, operation)) return false;
    if (!isProductBriefOperationPollTerminal(operation.state)) {
      this.rearmPollingAfterAuxiliaryRead(random);
    }
    return true;
  }

  failAuxiliaryOperationRead(
    token: OperationReadToken,
    random: () => number = Math.random,
  ): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.operationReadGeneration ||
      token.operationId !== this.snapshot.brief?.operation_id
    ) {
      return false;
    }
    this.rearmPollingAfterAuxiliaryRead(random);
    return true;
  }

  private rearmPollingAfterAuxiliaryRead(
    random: () => number,
  ): void {
    if (this.snapshot.polling.paused) return;
    const completedRequests = Math.max(
      1,
      this.snapshot.polling.completedRequests,
    );
    this.snapshot = {
      ...this.snapshot,
      polling: {
        ...this.snapshot.polling,
        nextDelayMs:
          this.snapshot.polling.nextDelayMs ??
          operationPollDelayMs(completedRequests, random),
        requestToken: this.snapshot.polling.requestToken + 1,
      },
    };
  }

  completeOperationPoll(
    token: OperationReadToken,
    operation: ProductBriefOperationStatusResponseV1,
    random: () => number = Math.random,
  ): boolean {
    if (!this.publishOperation(token, operation)) return false;
    const completedRequests =
      this.snapshot.polling.completedRequests + 1;
    const stopped = isProductBriefOperationPollTerminal(operation.state);
    const shouldContinue =
      !stopped && shouldContinueOperationPolling(completedRequests);
    this.snapshot = {
      ...this.snapshot,
      polling: {
        operationId: operation.id,
        completedRequests,
        nextDelayMs: shouldContinue
          ? operationPollDelayMs(completedRequests, random)
          : null,
        requestToken: shouldContinue
          ? this.snapshot.polling.requestToken + 1
          : this.snapshot.polling.requestToken,
        paused: !stopped && !shouldContinue,
      },
    };
    return true;
  }

  failOperationPoll(
    token: OperationReadToken,
    random: () => number = Math.random,
  ): boolean {
    if (
      token.identityGeneration !== this.snapshot.identityGeneration ||
      token.readGeneration !== this.operationReadGeneration ||
      token.operationId !== this.snapshot.brief?.operation_id
    ) {
      return false;
    }
    const completedRequests =
      this.snapshot.polling.completedRequests + 1;
    const shouldContinue =
      shouldContinueOperationPolling(completedRequests);
    this.snapshot = {
      ...this.snapshot,
      polling: {
        operationId: token.operationId,
        completedRequests,
        nextDelayMs: shouldContinue
          ? operationPollDelayMs(completedRequests, random)
          : null,
        requestToken: shouldContinue
          ? this.snapshot.polling.requestToken + 1
          : this.snapshot.polling.requestToken,
        paused: !shouldContinue,
      },
    };
    return true;
  }

  resumePolling(): boolean {
    const operationId = this.snapshot.brief?.operation_id;
    if (!operationId || !this.snapshot.polling.paused) return false;
    this.snapshot = {
      ...this.snapshot,
      polling: {
        operationId,
        completedRequests: 0,
        nextDelayMs: 0,
        requestToken: this.snapshot.polling.requestToken + 1,
        paused: false,
      },
    };
    return true;
  }

  recoverAnalysis(pending: PendingProductBriefAnalysis): void {
    this.snapshot = {
      ...this.snapshot,
      pendingAnalysis: pending,
    };
  }

  settleAnalysis(): void {
    this.snapshot = {
      ...this.snapshot,
      pendingAnalysis: null,
    };
  }

  recoverCommand(
    command: PendingProductBriefCommand,
    status: "pending" | "version-conflict",
  ): boolean {
    const canonical = pendingProductBriefCommandFor(
      this.snapshot.identity.productId,
      command.productBriefId,
      command,
    );
    if (
      canonical === null ||
      (status === "version-conflict" && canonical.kind !== "revise")
    ) {
      return false;
    }
    this.snapshot = {
      ...this.snapshot,
      pendingCommand: canonical,
      commandStatus: status,
    };
    return true;
  }

  markVersionConflict(command: PendingProductBriefCommand): boolean {
    if (
      command.kind !== "revise" ||
      !this.snapshot.pendingCommand ||
      !productBriefCommandsMatch(command, this.snapshot.pendingCommand)
    ) {
      return false;
    }
    this.snapshot = {
      ...this.snapshot,
      commandStatus: "version-conflict",
    };
    return true;
  }

  settleCommand(command: PendingProductBriefCommand): boolean {
    if (
      !this.snapshot.pendingCommand ||
      !productBriefCommandsMatch(command, this.snapshot.pendingCommand)
    ) {
      return false;
    }
    this.snapshot = {
      ...this.snapshot,
      pendingCommand: null,
      commandStatus: null,
    };
    return true;
  }
}

export function createProductBriefWorkbenchController(
  identity: ProductBriefControllerIdentity,
): ProductBriefWorkbenchController {
  return new ProductBriefWorkbenchController(identity);
}
