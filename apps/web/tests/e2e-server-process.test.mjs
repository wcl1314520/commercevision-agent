import { EventEmitter } from "node:events";

import { afterEach, describe, expect, it, vi } from "vitest";

import { superviseE2eServerChild } from "./e2e-server-process.mjs";
import {
  stopSupervisedE2eServer,
  waitForHttpReady,
} from "./e2e-server-runtime.mjs";

class FakeChild extends EventEmitter {
  killed = false;
  signals = [];

  kill(signal) {
    this.killed = true;
    this.signals.push(signal);
    return true;
  }
}

afterEach(() => {
  vi.useRealTimers();
});

describe("E2E server process supervision", () => {
  it("terminates the exact server child when its launcher disappears", async () => {
    vi.useFakeTimers();
    const child = new FakeChild();
    const supervisor = superviseE2eServerChild(child, {
      launcherPid: 1234,
      isProcessAlive: () => false,
      pollIntervalMs: 100,
      forceAfterMs: 500,
    });

    await vi.advanceTimersByTimeAsync(100);
    expect(child.signals).toEqual(["SIGTERM"]);

    child.emit("exit", 0, null);
    await expect(supervisor.completion).resolves.toEqual({
      code: 0,
      signal: null,
    });
  });

  it("escalates only the recorded child after the graceful deadline", async () => {
    vi.useFakeTimers();
    const child = new FakeChild();
    const supervisor = superviseE2eServerChild(child, {
      launcherPid: 1234,
      isProcessAlive: () => true,
      pollIntervalMs: 100,
      forceAfterMs: 500,
    });

    supervisor.stop();
    expect(child.signals).toEqual(["SIGTERM"]);
    await vi.advanceTimersByTimeAsync(500);
    expect(child.signals).toEqual(["SIGTERM", "SIGKILL"]);

    child.emit("exit", null, "SIGKILL");
    await supervisor.completion;
  });

  it("rejects when the exact child never reports exit after forced termination", async () => {
    vi.useFakeTimers();
    const child = new FakeChild();
    const supervisor = superviseE2eServerChild(child, {
      launcherPid: 1234,
      isProcessAlive: () => true,
      pollIntervalMs: 100,
      forceAfterMs: 500,
      settleAfterForceMs: 250,
    });
    const completion = expect(supervisor.completion).rejects.toThrow(
      "did not exit after SIGKILL",
    );

    supervisor.stop();
    await vi.advanceTimersByTimeAsync(750);

    expect(child.signals).toEqual(["SIGTERM", "SIGKILL"]);
    await completion;
  });
});

describe("E2E server readiness", () => {
  it("aborts a hung fetch before the overall readiness deadline", async () => {
    vi.useFakeTimers();
    let attempts = 0;
    const readiness = waitForHttpReady("http://127.0.0.1:3100", {
      fetchImpl: (_url, init) => {
        attempts += 1;
        return new Promise((_resolve, reject) => {
          init.signal.addEventListener(
            "abort",
            () => reject(init.signal.reason),
            { once: true },
          );
        });
      },
      overallTimeoutMs: 1_100,
      pollIntervalMs: 100,
      requestTimeoutMs: 400,
    });
    const rejected = expect(readiness).rejects.toThrow(
      "did not become ready",
    );

    await vi.advanceTimersByTimeAsync(1_500);

    expect(attempts).toBeGreaterThan(1);
    await rejected;
  });
});

describe("E2E runner cleanup", () => {
  it("propagates an exact-child cleanup failure to the gate", async () => {
    const cleanupError = new Error("server child remained alive");
    const supervisor = {
      stop: vi.fn(),
      completion: Promise.reject(cleanupError),
    };

    await expect(
      stopSupervisedE2eServer(supervisor),
    ).rejects.toBe(cleanupError);
    expect(supervisor.stop).toHaveBeenCalledOnce();
  });
});
