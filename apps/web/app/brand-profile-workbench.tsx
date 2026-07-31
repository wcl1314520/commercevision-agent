"use client";

import { useEffect } from "react";

import type { BrandProfileIdentityChangeGuard } from "../lib/brand-profile-editor-state";
import { BrandProfileWorkbenchView } from "./brand-profile-workbench-view";
import { useBrandProfileWorkbench } from "./use-brand-profile-workbench";

export function BrandProfileWorkbench({
  brand,
  onIdentityChangeGuardChange,
  workspaceId = "catalog-demo",
}: {
  brand: string;
  onIdentityChangeGuardChange?: (
    guard: BrandProfileIdentityChangeGuard,
  ) => void;
  workspaceId?: string;
}) {
  const view = useBrandProfileWorkbench({ brand, workspaceId });
  useEffect(() => {
    onIdentityChangeGuardChange?.(view.identityChangeGuard);
    return () => onIdentityChangeGuardChange?.("clear");
  }, [onIdentityChangeGuardChange, view.identityChangeGuard]);
  return <BrandProfileWorkbenchView {...view} />;
}
