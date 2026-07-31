import { spawn } from "node:child_process";
import path from "node:path";

import { superviseE2eServerChild } from "./e2e-server-process.mjs";
import {
  E2E_SERVER_URL,
  prepareAndSpawnE2eServer,
  resolveE2eWebRoot,
  stopSupervisedE2eServer,
  waitForHttpReady,
} from "./e2e-server-runtime.mjs";

const webRoot = resolveE2eWebRoot(import.meta.url);
const server = await prepareAndSpawnE2eServer(webRoot);
const serverSupervisor = superviseE2eServerChild(server);

let playwright;
const stopChildren = () => {
  serverSupervisor.stop();
  if (playwright && !playwright.killed) playwright.kill("SIGTERM");
};
process.once("SIGINT", stopChildren);
process.once("SIGTERM", stopChildren);

try {
  await Promise.race([
    waitForHttpReady(E2E_SERVER_URL),
    serverSupervisor.completion.then((result) => {
      throw new Error(
        `E2E server exited before readiness: ${JSON.stringify(result)}`,
      );
    }),
  ]);

  const playwrightCli = path.join(
    webRoot,
    "node_modules",
    "@playwright",
    "test",
    "cli.js",
  );
  playwright = spawn(
    process.execPath,
    [playwrightCli, "test", ...process.argv.slice(2)],
    {
      cwd: webRoot,
      env: {
        ...process.env,
        COMMERCEVISION_E2E_EXTERNAL_SERVER: "1",
      },
      stdio: "inherit",
      windowsHide: true,
    },
  );
  const result = await new Promise((resolve, reject) => {
    playwright.once("error", reject);
    playwright.once("exit", (code, signal) => resolve({ code, signal }));
  });
  process.exitCode = result.code ?? (result.signal ? 1 : 0);
} finally {
  try {
    await stopSupervisedE2eServer(serverSupervisor);
  } finally {
    process.removeListener("SIGINT", stopChildren);
    process.removeListener("SIGTERM", stopChildren);
  }
}
