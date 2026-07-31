function processIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

export function superviseE2eServerChild(
  child,
  {
    launcherPid = process.ppid,
    isProcessAlive = processIsAlive,
    pollIntervalMs = 250,
    forceAfterMs = 5_000,
    settleAfterForceMs = 2_000,
  } = {},
) {
  let settled = false;
  let stopping = false;
  let forceTimer;
  let settleTimer;
  let parentMonitor;
  let resolveCompletion;
  let rejectCompletion;

  const completion = new Promise((resolve, reject) => {
    resolveCompletion = resolve;
    rejectCompletion = reject;
  });

  const cleanup = () => {
    clearInterval(parentMonitor);
    if (forceTimer) clearTimeout(forceTimer);
    if (settleTimer) clearTimeout(settleTimer);
  };

  const finish = (result) => {
    if (settled) return;
    settled = true;
    cleanup();
    resolveCompletion(result);
  };

  const fail = (error) => {
    if (settled) return;
    settled = true;
    cleanup();
    rejectCompletion(error);
  };

  const stop = () => {
    if (settled || stopping) return;
    stopping = true;
    try {
      child.kill("SIGTERM");
    } catch (error) {
      fail(error);
      return;
    }
    forceTimer = setTimeout(() => {
      if (settled) return;
      try {
        child.kill("SIGKILL");
      } catch (error) {
        fail(error);
        return;
      }
      settleTimer = setTimeout(() => {
        fail(
          new Error(
            `E2E server child did not exit after SIGKILL within ${settleAfterForceMs}ms`,
          ),
        );
      }, settleAfterForceMs);
      settleTimer.unref?.();
    }, forceAfterMs);
    forceTimer.unref?.();
  };

  child.once("error", fail);
  child.once("exit", (code, signal) => finish({ code, signal }));

  parentMonitor = setInterval(() => {
    if (!isProcessAlive(launcherPid)) stop();
  }, pollIntervalMs);
  parentMonitor.unref?.();

  return { completion, stop };
}
