const INITIAL_POLL_DELAY_MS = 1_000;
const MAXIMUM_POLL_DELAY_MS = 10_000;
const MAXIMUM_AUTOMATIC_POLL_REQUESTS = 24;

export function operationPollDelayMs(
  completedRequests: number,
  random: () => number = Math.random,
): number {
  if (!Number.isSafeInteger(completedRequests) || completedRequests < 1) {
    throw new TypeError("completedRequests must be a positive safe integer");
  }
  const randomValue = random();
  if (!Number.isFinite(randomValue) || randomValue < 0 || randomValue > 1) {
    throw new TypeError("random must return a finite value between 0 and 1");
  }
  const maximumDelay = Math.min(
    INITIAL_POLL_DELAY_MS * 2 ** (completedRequests - 1),
    MAXIMUM_POLL_DELAY_MS,
  );
  return Math.round(maximumDelay * (0.5 + randomValue * 0.5));
}

export function shouldContinueOperationPolling(
  completedRequests: number,
): boolean {
  if (!Number.isSafeInteger(completedRequests) || completedRequests < 1) {
    throw new TypeError("completedRequests must be a positive safe integer");
  }
  return completedRequests < MAXIMUM_AUTOMATIC_POLL_REQUESTS;
}
