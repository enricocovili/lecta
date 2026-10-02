// Server-side (SSR) helpers: talk to the backend directly over the internal network.
import type { AstroGlobal, APIContext } from "astro";

const BACKEND = process.env.LECTA_BACKEND_URL || "http://backend:8000";

type Ctx = Pick<APIContext, "request"> | AstroGlobal;

function forwardHeaders(ctx: Ctx): Record<string, string> {
  const h = ctx.request.headers;
  const out: Record<string, string> = { accept: "application/json" };
  for (const name of ["cookie", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "user-agent"]) {
    const v = h.get(name);
    if (v) out[name] = v;
  }
  if (!out["x-forwarded-host"] && h.get("host")) out["x-forwarded-host"] = h.get("host")!;
  return out;
}

export async function backendGet<T>(ctx: Ctx, path: string): Promise<{ status: number; data: T | null }> {
  try {
    const res = await fetch(BACKEND + path, { headers: forwardHeaders(ctx) });
    if (!res.ok) return { status: res.status, data: null };
    return { status: res.status, data: (await res.json()) as T };
  } catch {
    return { status: 502, data: null };
  }
}

export interface Site {
  title: string;
  description: string;
  /** Small kicker above the landing title (e.g. "Enrico Covili · Unimore"). */
  byline?: string;
  allow_indexing: boolean;
}

export async function getSite(ctx: Ctx): Promise<Site> {
  const r = await backendGet<Site>(ctx, "/api/public/site");
  return r.data ?? { title: "Lecta", description: "", allow_indexing: false };
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  return d.toLocaleDateString("it-IT", { year: "numeric", month: "short", day: "numeric" });
}

export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}
