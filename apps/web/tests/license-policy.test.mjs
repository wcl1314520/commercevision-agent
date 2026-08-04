import { describe, expect, it } from "vitest";

import { auditNodeLicenses } from "../../../scripts/audit-node-licenses.mjs";

describe("Node license policy", () => {
  it("accepts the production dependency license families used by the Web app", () => {
    expect(
      auditNodeLicenses({
        MIT: [{ name: "react", license: "MIT" }],
        "Apache-2.0 AND LGPL-3.0-or-later": [
          { name: "sharp-binary", license: "Apache-2.0 AND LGPL-3.0-or-later" },
        ],
      }),
    ).toEqual([]);
  });

  it("rejects missing and blocked dependency licenses", () => {
    expect(
      auditNodeLicenses({
        UNKNOWN: [{ name: "mystery" }],
        "GPLv3+": [{ name: "network-service", license: "GPLv3+" }],
        "GNU General Public License v2": [
          { name: "legacy-service", license: "GNU General Public License v2" },
        ],
      }),
    ).toEqual([
      "legacy-service: blocked license GNU General Public License v2",
      "mystery: missing license metadata",
      "network-service: blocked license GPLv3+",
    ]);
  });
});
