// Shapes of the AI-workspace API (docs/AI-WORKSPACE.md).

export interface TocEntry {
  id: string;
  title: string;
  level: number;
  line: number;
}

/** One picture of a block typeset by LaTeX; sizes in bp, `x` from the text's left edge. */
export interface DraftPage {
  url: string;
  w: number;
  h: number;
  x: number;
  /** holds a photo: not inverted in the dark theme */
  raster?: boolean;
}

/** A paragraph, heading or environment of the chapter source (lines start…end), typeset as one or more pictures. */
export interface DraftBlock {
  start: number;
  end: number;
  pages: DraftPage[];
  error: string | null;
  heading?: string;
  id?: string;
  src?: string;
}

export interface DraftChapter {
  chapter: { id: number; title: string; path: string; position: number };
  /** text width in bp */
  width: number;
  /** null while the chapter is being typeset */
  blocks: DraftBlock[] | null;
  toc: TocEntry[];
  warnings: string[];
  typeset?: number;
  took_ms?: number;
  error?: string;
}

export interface Hunk {
  from_line: number;
  to_line: number;
}

export interface ChangedFile {
  path: string;
  chapter_id: number | null;
  op: "modify" | "create" | "delete" | "rename" | "comment";
  added: number;
  removed: number;
  hunks: Hunk[];
  /** a lab's file (the lab assistant), and the comments it added there */
  file_id?: number;
  comments?: number;
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
  /** the lab file being looked at (a lab's conversation) */
  file_id?: number | null;
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
