"use client";

import { useCallback, useState } from "react";

import {
  decodePersistedUpload,
  encodePersistedUpload,
  PersistedUpload,
  reduceUploadWorkflow,
  uploadStorageKey,
  UploadWorkflowEvent,
} from "./upload-workflow";

export function useUploadWorkflow(productId: string) {
  const [persisted, setPersisted] = useState<PersistedUpload | null>(null);

  const load = useCallback((): PersistedUpload | null => {
    const key = uploadStorageKey(productId);
    const recovered = decodePersistedUpload(localStorage.getItem(key));
    if (recovered === null) {
      localStorage.removeItem(key);
    }
    setPersisted(recovered);
    return recovered;
  }, [productId]);

  const save = useCallback(
    (next: PersistedUpload): PersistedUpload => {
      localStorage.setItem(
        uploadStorageKey(productId),
        encodePersistedUpload(next),
      );
      setPersisted(next);
      return next;
    },
    [productId],
  );

  const transition = useCallback(
    (
      current: PersistedUpload | null,
      event: UploadWorkflowEvent,
    ): PersistedUpload => save(reduceUploadWorkflow(current, event)),
    [save],
  );

  const clear = useCallback(() => {
    localStorage.removeItem(uploadStorageKey(productId));
    setPersisted(null);
  }, [productId]);

  return {
    clear,
    load,
    persisted,
    save,
    transition,
  };
}
