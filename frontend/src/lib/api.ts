// Browser-side API client. All calls go to same-origin /api (proxied to the backend).

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function readCookie(name: string): string {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/[.$?*|{}()[\]\\/+^]/g, "\\$&") + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : "";
}

export function csrfToken(): string {
  return readCookie("lecta_csrf");
}

async function ensureCsrf(refresh = false): Promise<string> {
  let t = csrfToken();
  if (!t || refresh) {
    // Signed in, this returns (and re-sets) the session's token, fixing a missing or stale cookie.
    const res = await fetch("/api/auth/csrf", { credentials: "same-origin" });
    const data = await res.json().catch(() => null);
    t = (data && typeof data.csrf === "string" && data.csrf) || csrfToken();
  }
  return t;
}

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && "message" in d && typeof (d as { message: unknown }).message === "string") return (d as { message: string }).message;
    if (Array.isArray(d)) {
      return d
        .map((e) => (e && typeof e === "object" && "msg" in e ? `${(e as { loc?: unknown[] }).loc?.slice(-1)[0] ?? ""}: ${(e as { msg: string }).msg}` : String(e)))
        .join("; ");
    }
  }
  return `Request failed (${status})`;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const method = (init.method || (init.json !== undefined ? "POST" : "GET")).toUpperCase();
  const headers = new Headers(init.headers || {});
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(init.json);
  }
  const unsafe = !["GET", "HEAD"].includes(method);
  const send = async (refresh: boolean) => {
    if (unsafe) headers.set("x-csrf-token", await ensureCsrf(refresh));
    return fetch(path.startsWith("/api") ? path : "/api" + path, {
      ...init,
      method,
      headers,
      body,
      credentials: "same-origin",
    });
  };
  let res = await send(false);
  let ct = res.headers.get("content-type") || "";
  let data = ct.includes("application/json") ? await res.json().catch(() => null) : await res.text();
  if (unsafe && res.status === 403 && errorMessage(data, res.status) === "CSRF check failed" && typeof body !== "object") {
    // Stale token (e.g. the cookie outlived or lost its session): re-sync once and retry.
    res = await send(true);
    ct = res.headers.get("content-type") || "";
    data = ct.includes("application/json") ? await res.json().catch(() => null) : await res.text();
  }
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, res.status), data);
  return data as T;
}

export const get = <T = unknown>(path: string) => api<T>(path);
export const post = <T = unknown>(path: string, json: unknown = {}) => api<T>(path, { method: "POST", json });
export const patch = <T = unknown>(path: string, json: unknown = {}) => api<T>(path, { method: "PATCH", json });
export const put = <T = unknown>(path: string, json: unknown = {}) => api<T>(path, { method: "PUT", json });
export const del = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

/** Upload a File/Blob as a raw streamed body with progress (XMLHttpRequest). */
export function uploadRaw<T = unknown>(
  path: string,
  file: Blob,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise(async (resolve, reject) => {
    const token = await ensureCsrf();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path.startsWith("/api") ? path : "/api" + path);
    xhr.setRequestHeader("x-csrf-token", token);
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = xhr.responseText;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(xhr.status, errorMessage(data, xhr.status), data));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error"));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled"));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(file);
  });
}

export function sse(path: string, handlers: Record<string, (data: any) => void>, onEnd?: () => void): () => void {
  const es = new EventSource(path.startsWith("/api") ? path : "/api" + path, { withCredentials: true });
  for (const [event, fn] of Object.entries(handlers)) {
    es.addEventListener(event, (e) => {
      try {
        fn(JSON.parse((e as MessageEvent).data));
      } catch {
        fn((e as MessageEvent).data);
      }
    });
  }
  es.addEventListener("end", () => {
    es.close();
    onEnd?.();
  });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) onEnd?.();
  };
  return () => es.close();
}

export function fmtDate(value: string | null | undefined, withTime = false): string {
  if (!value) return "";
  const d = new Date(value);
  return withTime
    ? d.toLocaleString("it-IT", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("it-IT", { year: "numeric", month: "short", day: "numeric" });
}

export function fmtSize(bytes: number | null | undefined): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function fmtMoney(usd: number | null | undefined): string {
  const v = usd || 0;
  return v < 0.01 && v > 0 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

/**
 * Consume a server-sent-events response with fetch (EventSource cannot tell a named `error` event from a
 * broken connection, and cannot be paused or resumed with `?after=`). Resolves when the stream ends;
 * `onEvent` gets the event name, the parsed data and the event `id` (the sequence number, when sent).
 */
export async function streamSSE(
  path: string,
  onEvent: (event: string, data: any, id: string | null) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(path.startsWith("/api") ? path : "/api" + path, {
    headers: { accept: "text/event-stream" },
    credentials: "same-origin",
    signal,
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null);
    throw new ApiError(res.status, errorMessage(data, res.status), data);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "message";
  let id: string | null = null;
  let data: string[] = [];
  const flush = () => {
    if (data.length) {
      const raw = data.join("\n");
      let parsed: unknown = raw;
      try {
        parsed = JSON.parse(raw);
      } catch {
        /* plain text */
      }
      onEvent(event, parsed, id);
    }
    event = "message";
    data = [];
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).replace(/\r$/, "");
      buf = buf.slice(idx + 1);
      if (line === "") flush();
      else if (line.startsWith(":")) continue;
      else if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
  }
  flush();
}
