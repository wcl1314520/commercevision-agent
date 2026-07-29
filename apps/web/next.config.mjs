import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const standaloneBuild = process.env.NEXT_OUTPUT === "standalone";
const e2eDistDir = process.env.COMMERCEVISION_WEB_E2E_DIST_DIR;

/** @type {import("next").NextConfig} */
const nextConfig = {
  distDir: e2eDistDir || undefined,
  outputFileTracingRoot: repositoryRoot,
  poweredByHeader: false,
  output: standaloneBuild ? "standalone" : undefined,
};

export default nextConfig;
