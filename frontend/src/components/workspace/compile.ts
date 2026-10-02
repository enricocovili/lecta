import { post } from "../../lib/api";

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

/** The build's errors as text the assistant can act on. */
export function errorsText(r: CompileResult, max = 6): string {
  return r.diagnostics
    .filter((d) => d.level === "error")
    .slice(0, max)
    .map((d) => `- ${d.file ?? "?"}${d.line ? `:${d.line}` : ""}: ${d.message}`)
    .join("\n");
}
