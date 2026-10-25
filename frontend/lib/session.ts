/**
 * The backend keeps sessions in memory; the frontend just needs to carry the
 * preview response (plan + profiles) from the upload page to the review page
 * for one browser tab. sessionStorage is enough -- nothing here is durable
 * state, it's a hand-off between two routes.
 */

import type { PreviewResponse } from "./api";

const KEY_PREFIX = "schemata:preview:";

export function savePreview(preview: PreviewResponse): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(KEY_PREFIX + preview.session_id, JSON.stringify(preview));
}

export function loadPreview(sessionId: string): PreviewResponse | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(KEY_PREFIX + sessionId);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as PreviewResponse;
  } catch {
    return null;
  }
}
