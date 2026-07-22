import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const standaloneBuild = process.env.NEXT_OUTPUT === "standalone";

/** @type {import("next").NextConfig} */
const nextConfig = {
  outputFileTracingRoot: repositoryRoot,
  poweredByHeader: false,
  output: standaloneBuild ? "standalone" : undefined,
};

export default nextConfig;
