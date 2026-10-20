// Where things open in the workspace.

/** The text of a course (draft, outline, assistant): at a chapter, with the assistant open, with a question ready. */
export function courseText(courseId: number, opts: { chapter?: number | null; chat?: boolean; ask?: string } = {}): string {
  const q = new URLSearchParams();
  if (opts.chapter) q.set("chapter", String(opts.chapter));
  if (opts.chat || opts.ask) q.set("chat", "1");
  if (opts.ask) q.set("ask", opts.ask);
  const s = q.toString();
  return `/admin/courses/${courseId}/testo${s ? `?${s}` : ""}`;
}

/** The public landing page. A signed-in user is sent from `/` to their dashboard, so for them it takes `?public`. */
export function publicHome(signedIn: boolean | undefined): string {
  return signedIn ? "/?public" : "/";
}
