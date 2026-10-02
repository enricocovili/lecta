// Shapes of the AI-workspace API (docs/AI-WORKSPACE.md).

export interface TocEntry {
  id: string;
  title: string;
  level: number;
  line: number;
}

export interface PreviewChapter {
  chapter: { id: number; title: string; path: string; position: number };
  html: string;
  toc: TocEntry[];
  warnings: string[];
  blob?: string;
  took_ms?: number;
}

export interface Hunk {
  from_line: number;
  to_line: number;
}

export interface ChangedFile {
  path: string;
  chapter_id: number | null;
  op: "modify" | "create" | "delete" | "rename";
  added: number;
  removed: number;
  hunks: Hunk[];
}

export interface Step {
  id: string;
  name: string;
  label: string;
  status: "ok" | "error" | "running";
  summary?: string | null;
}

export interface ChangeInfo {
  status: "applied" | "undone";
  files: ChangedFile[];
  chapters?: { id: number; title: string; op: "created" | "renamed" | "deleted" | "moved" }[];
}

export interface ReviewIssue {
  chapter?: string | null;
  chapter_id?: number | null;
  text: string;
  fix?: string | null;
}

export interface Review {
  verdict: string;
  score: number;
  strengths: string[];
  issues: ReviewIssue[];
}

export type Mode = "ask" | "edit" | "explain" | "review";

export interface SelectionScope {
  from_line: number;
  to_line?: number;
  text: string;
}

export interface Scope {
  chapter_id?: number | null;
  selection?: SelectionScope;
  mode?: Mode;
  attachments?: { source_file_id: number; page?: number }[];
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  status: "streaming" | "done" | "error" | "cancelled" | string;
  content: string;
  scope: Scope;
  steps: Step[];
  change: ChangeInfo | null;
  suggestions: string[];
  review: Review | null;
  error: string | null;
  cost_usd?: number;
  created_at?: string;
}

export interface SessionInfo {
  id: number;
  title: string;
  created_at?: string;
  updated_at?: string;
}

/** A line range of one chapter that the AI just touched (highlighted in the draft). */
export interface FlashTarget {
  chapterId: number;
  from: number;
  to: number;
}

export type CourseChapter = { id: number; title: string; path: string; position: number; updated_at?: string };
