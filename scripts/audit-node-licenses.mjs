#!/usr/bin/env node

import { pathToFileURL } from "node:url";

const BLOCKED_LICENSE =
  /GNU\s+(?:AFFERO\s+)?GENERAL\s+PUBLIC\s+LICENSE|(?:^|[^A-Z])(?:A?GPL|SSPL|BUSL|EUPL)(?=[^A-Z]|V?\d|$)/i;
const MAX_REPORT_BYTES = 16 * 1024 * 1024;

export function auditNodeLicenses(report) {
  if (report === null || typeof report !== "object" || Array.isArray(report)) {
    throw new TypeError("Node license report must be an object");
  }
  const findings = [];
  for (const packages of Object.values(report)) {
    if (!Array.isArray(packages)) {
      throw new TypeError("Node license groups must be arrays");
    }
    for (const dependency of packages) {
      if (dependency === null || typeof dependency !== "object" || Array.isArray(dependency)) {
        throw new TypeError("Node license entries must be objects");
      }
      const name = typeof dependency.name === "string" ? dependency.name : "unknown-dependency";
      const license = typeof dependency.license === "string" ? dependency.license.trim() : "";
      if (!license || license.toUpperCase() === "UNKNOWN") {
        findings.push(`${name}: missing license metadata`);
      } else if (BLOCKED_LICENSE.test(license)) {
        findings.push(`${name}: blocked license ${license}`);
      }
    }
  }
  return findings.sort();
}

async function main() {
  process.stdin.setEncoding("utf8");
  let payload = "";
  for await (const chunk of process.stdin) {
    payload += chunk;
    if (Buffer.byteLength(payload, "utf8") > MAX_REPORT_BYTES) {
      throw new Error("Node license report exceeds the size limit");
    }
  }
  const findings = auditNodeLicenses(JSON.parse(payload));
  if (findings.length > 0) {
    console.error("Node license policy: FAIL");
    for (const finding of findings) console.error(`- ${finding}`);
    return 1;
  }
  console.log("Node license policy: PASS");
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
