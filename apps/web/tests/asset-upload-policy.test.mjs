import { describe, expect, it } from "vitest";

import { declaredMimeForAsset } from "../lib/asset-upload-policy";

describe("asset upload policy", () => {
  it("preserves the image MIME and extension contract", () => {
    expect(
      declaredMimeForAsset("IMAGE", {
        name: "product.PNG",
        size: 68,
        type: "image/png",
      }),
    ).toBe("image/png");
    expect(() =>
      declaredMimeForAsset("IMAGE", {
        name: "product.jpg",
        size: 68,
        type: "image/png",
      }),
    ).toThrow(/扩展名/);
  });

  it("registers only SafeTensors LoRA files without executable formats", () => {
    expect(
      declaredMimeForAsset("LORA", {
        name: "style.safetensors",
        size: 1024,
        type: "",
      }),
    ).toBe("application/octet-stream");
    expect(() =>
      declaredMimeForAsset("LORA", {
        name: "unsafe.ckpt",
        size: 1024,
        type: "application/octet-stream",
      }),
    ).toThrow(/safetensors/);
  });

  it("distinguishes prompt and model JSON registration suffixes", () => {
    expect(
      declaredMimeForAsset("PROMPT_TEMPLATE", {
        name: "studio.prompt.json",
        size: 256,
        type: "application/json",
      }),
    ).toBe("application/json");
    expect(
      declaredMimeForAsset("MODEL_CONFIGURATION", {
        name: "flux.model.json",
        size: 256,
        type: "",
      }),
    ).toBe("application/json");
    expect(() =>
      declaredMimeForAsset("PROMPT_TEMPLATE", {
        name: "flux.model.json",
        size: 256,
        type: "application/json",
      }),
    ).toThrow(/prompt\.json/);
  });
});
