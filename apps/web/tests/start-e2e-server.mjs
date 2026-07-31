import { superviseE2eServerChild } from "./e2e-server-process.mjs";
import {
  prepareAndSpawnE2eServer,
  resolveE2eWebRoot,
} from "./e2e-server-runtime.mjs";

const webRoot = resolveE2eWebRoot(import.meta.url);
const child = await prepareAndSpawnE2eServer(webRoot);

const supervisor = superviseE2eServerChild(child);
const stop = () => supervisor.stop();
process.once("SIGINT", stop);
process.once("SIGTERM", stop);

try {
  const result = await supervisor.completion;
  process.exitCode = result.code ?? (result.signal ? 0 : 1);
} finally {
  process.removeListener("SIGINT", stop);
  process.removeListener("SIGTERM", stop);
}
