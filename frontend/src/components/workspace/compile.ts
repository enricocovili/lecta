import { get, post } from "../../lib/api";

export interface Diagnostic {
  level: "error" | "warning" | "badbox" | "info";
  file: string | null;
  line: number | null;
  message: string;
}

export interface CompileResult {
  status: string;
  diagnostics: Diagnostic[];
  summary?: Record<string, number>;
  pdf_url: string | null;
}

/** Compile the whole working version now (slow: seconds). */
export const compileFull = (courseId: number) => post<CompileResult>(`/api/courses/${courseId}/compile`, { full: true });

/** The PDF of the latest compile, else the published one: whoever may only read a course doesn't compile it. */
export async function latestPdf(course: { id: number; slug: string; published: boolean }): Promise<string | null> {
  const r = await get<{ build: { pdf_url: string | null } | null }>(`/api/courses/${course.id}/builds/latest`).catch(() => null);
  return r?.build?.pdf_url ?? (course.published ? `/api/public/courses/${course.slug}.pdf` : null);
}

/** The build's errors as text the assistant can act on. */
export function errorsText(r: CompileResult, max = 6): string {
  return r.diagnostics
    .filter((d) => d.level === "error")
    .slice(0, max)
    .map((d) => `- ${d.file ?? "?"}${d.line ? `:${d.line}` : ""}: ${d.message}`)
    .join("\n");
}
