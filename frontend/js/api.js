// Thin client for the Schemata API (same origin, under /api).
// State-changing requests carry X-Schemata: 1, which the server requires
// (a simple guard against cross-site requests from other tabs).

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function errorMessage(body, fallback) {
  if (!body) return fallback;
  const d = body.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length) return d.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return fallback;
}

export async function api(path, { method = "GET", body, form } = {}) {
  const headers = {};
  let payload;
  if (method !== "GET") headers["X-Schemata"] = "1";
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "Can't reach the Schemata server. Is it still running?");
  }
  if (!res.ok) {
    let parsed = null;
    try { parsed = await res.json(); } catch { /* not JSON */ }
    throw new ApiError(res.status, errorMessage(parsed, `${res.status} ${res.statusText}`));
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res.text();
}

// Uploads use XHR so we can show progress for large files.
export function upload(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.setRequestHeader("X-Schemata", "1");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let parsed = null;
      try { parsed = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(parsed);
      else reject(new ApiError(xhr.status, errorMessage(parsed, `Upload failed (${xhr.status})`)));
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed: can't reach the server"));
    xhr.send(formData);
  });
}

export const P = (pid, rest = "") => `/projects/${encodeURIComponent(pid)}${rest}`;
export const enc = encodeURIComponent;
