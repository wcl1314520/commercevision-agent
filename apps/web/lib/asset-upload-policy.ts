import type { AssetKind } from "./generated/catalog-api";

export type AssetUploadPolicy = {
  accept: string;
  label: string;
  maximumBytes: number;
};

export const ASSET_UPLOAD_POLICIES: Record<AssetKind, AssetUploadPolicy> = {
  IMAGE: {
    accept: ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp",
    label: "商品图片",
    maximumBytes: 10 * 1024 * 1024,
  },
  LORA: {
    accept:
      ".safetensors,application/octet-stream,application/x-safetensors",
    label: "LoRA SafeTensors",
    maximumBytes: 100 * 1024 * 1024,
  },
  PROMPT_TEMPLATE: {
    accept: ".prompt.json,application/json",
    label: "提示词模板",
    maximumBytes: 256 * 1024,
  },
  MODEL_CONFIGURATION: {
    accept: ".model.json,application/json",
    label: "模型配置",
    maximumBytes: 64 * 1024,
  },
};

type FileFacts = {
  name: string;
  size: number;
  type: string;
};

export function declaredMimeForAsset(
  assetKind: AssetKind,
  file: FileFacts,
): string {
  const policy = ASSET_UPLOAD_POLICIES[assetKind];
  if (!Number.isSafeInteger(file.size) || file.size < 1) {
    throw new Error("文件不能为空。");
  }
  if (file.size > policy.maximumBytes) {
    throw new Error(`${policy.label}超过允许的大小。`);
  }
  const filename = file.name.toLowerCase();
  const mime = file.type.toLowerCase();
  if (assetKind === "IMAGE") {
    const extensionsByMime: Record<string, string[]> = {
      "image/jpeg": [".jpg", ".jpeg"],
      "image/png": [".png"],
      "image/webp": [".webp"],
    };
    const extensions = extensionsByMime[mime];
    if (!extensions || !extensions.some((extension) => filename.endsWith(extension))) {
      throw new Error("图片扩展名与 JPEG、PNG 或 WebP 类型不匹配。");
    }
    return mime;
  }
  if (assetKind === "LORA") {
    if (!filename.endsWith(".safetensors")) {
      throw new Error("LoRA 文件必须使用 .safetensors 扩展名。");
    }
    if (
      mime &&
      mime !== "application/octet-stream" &&
      mime !== "application/x-safetensors"
    ) {
      throw new Error("LoRA 文件 MIME 类型无效。");
    }
    return mime || "application/octet-stream";
  }
  const suffix =
    assetKind === "PROMPT_TEMPLATE" ? ".prompt.json" : ".model.json";
  if (!filename.endsWith(suffix)) {
    throw new Error(
      assetKind === "PROMPT_TEMPLATE"
        ? "提示词模板必须使用 .prompt.json 扩展名。"
        : "模型配置必须使用 .model.json 扩展名。",
    );
  }
  if (mime && mime !== "application/json") {
    throw new Error("JSON 资产 MIME 类型必须为 application/json。");
  }
  return "application/json";
}
