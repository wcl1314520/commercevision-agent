import { spawn } from "node:child_process";
import { cp, mkdir, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const productionDist = path.join(webRoot, ".next");
const testResultsRoot = path.join(webRoot, "test-results");
const e2eDist = path.join(testResultsRoot, "next");

if (path.dirname(e2eDist) !== testResultsRoot) {
  throw new Error(`Refusing to prepare unexpected E2E dist path: ${e2eDist}`);
}

const buildId = (
  await readFile(path.join(productionDist, "BUILD_ID"), "utf8").catch(() => "")
).trim();
if (!buildId) {
  throw new Error("Run `pnpm build` before Playwright E2E tests.");
}

await rm(e2eDist, { recursive: true, force: true });
await mkdir(testResultsRoot, { recursive: true });
await cp(productionDist, e2eDist, { recursive: true });
await rm(path.join(e2eDist, "types"), { recursive: true, force: true });

const nextCli = path.join(
  webRoot,
  "node_modules",
  "next",
  "dist",
  "bin",
  "next",
);
const child = spawn(
  process.execPath,
  [nextCli, "start", "--hostname", "127.0.0.1", "--port", "3100"],
  {
    cwd: webRoot,
    env: {
      ...process.env,
      COMMERCEVISION_WEB_E2E_DIST_DIR: "test-results/next",
    },
    stdio: "inherit",
  },
);

const forwardSignal = (signal) => {
  if (!child.killed) child.kill(signal);
};
process.once("SIGINT", forwardSignal);
process.once("SIGTERM", forwardSignal);

const result = await new Promise((resolve, reject) => {
  child.once("error", reject);
  child.once("exit", (code, signal) => resolve({ code, signal }));
});

process.removeListener("SIGINT", forwardSignal);
process.removeListener("SIGTERM", forwardSignal);
process.exitCode = result.code ?? (result.signal ? 0 : 1);
