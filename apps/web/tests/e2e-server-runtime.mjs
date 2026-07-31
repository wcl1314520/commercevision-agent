import { spawn } from "node:child_process";
import { cp, mkdir, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const E2E_SERVER_URL = "http://127.0.0.1:3100";

export function resolveE2eWebRoot(moduleUrl) {
  return path.resolve(path.dirname(fileURLToPath(moduleUrl)), "..");
}

function e2ePaths(webRoot) {
  const productionDist = path.resolve(webRoot, ".next");
  const testResultsRoot = path.resolve(webRoot, "test-results");
  const e2eDist = path.resolve(testResultsRoot, "next");
  if (path.dirname(e2eDist) !== testResultsRoot) {
    throw new Error(`Refusing to prepare unexpected E2E dist path: ${e2eDist}`);
  }
  return { e2eDist, productionDist, testResultsRoot };
}

export async function prepareE2eDistribution(webRoot) {
  const { e2eDist, productionDist, testResultsRoot } = e2ePaths(webRoot);
  const buildId = (
    await readFile(path.join(productionDist, "BUILD_ID"), "utf8").catch(
      () => "",
    )
  ).trim();
  if (!buildId) {
    throw new Error("Run `pnpm build` before Playwright E2E tests.");
  }

  await rm(e2eDist, { recursive: true, force: true });
  await mkdir(testResultsRoot, { recursive: true });
  await cp(productionDist, e2eDist, { recursive: true });
  await rm(path.join(e2eDist, "types"), { recursive: true, force: true });
}

export async function prepareAndSpawnE2eServer(
  webRoot,
  { spawnImpl = spawn } = {},
) {
  await prepareE2eDistribution(webRoot);
  const nextCli = path.join(
    webRoot,
    "node_modules",
    "next",
    "dist",
    "bin",
    "next",
  );
  return spawnImpl(
    process.execPath,
    [nextCli, "start", "--hostname", "127.0.0.1", "--port", "3100"],
    {
      cwd: webRoot,
      env: {
        ...process.env,
        COMMERCEVISION_WEB_E2E_DIST_DIR: "test-results/next",
      },
      stdio: "inherit",
      windowsHide: true,
    },
  );
}

function delay(durationMs) {
  return new Promise((resolve) => setTimeout(resolve, durationMs));
}

export async function waitForHttpReady(
  serverUrl,
  {
    fetchImpl = fetch,
    overallTimeoutMs = 120_000,
    pollIntervalMs = 250,
    requestTimeoutMs = 2_000,
  } = {},
) {
  const deadline = Date.now() + overallTimeoutMs;
  while (Date.now() < deadline) {
    const remainingMs = Math.max(1, deadline - Date.now());
    const controller = new AbortController();
    const requestTimer = setTimeout(
      () =>
        controller.abort(
          new Error(
            `E2E readiness request exceeded ${requestTimeoutMs}ms`,
          ),
        ),
      Math.min(requestTimeoutMs, remainingMs),
    );
    requestTimer.unref?.();
    try {
      const response = await fetchImpl(serverUrl, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (response.ok) {
        await response.body?.cancel();
        return;
      }
      await response.body?.cancel();
    } catch {
      // The production server is still starting or this probe timed out.
    } finally {
      clearTimeout(requestTimer);
    }
    const pollDelayMs = Math.min(
      pollIntervalMs,
      Math.max(0, deadline - Date.now()),
    );
    if (pollDelayMs > 0) await delay(pollDelayMs);
  }
  throw new Error(`E2E server did not become ready at ${serverUrl}`);
}

export async function stopSupervisedE2eServer(supervisor) {
  supervisor.stop();
  return supervisor.completion;
}
