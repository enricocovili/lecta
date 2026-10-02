export interface Chapter {
  id: number;
  course_id: number;
  position: number;
  slug: string;
  title: string;
  path: string;
  updated_at: string;
}

export interface Course {
  id: number;
  name: string;
  slug: string;
  academic_year: string | null;
  language: string;
  tags: string[];
  description: string | null;
  engine: string | null;
  has_preamble_override: boolean;
  guidelines?: string;
  preamble_override?: string | null;
  published: boolean;
  created_at: string;
  updated_at: string;
  chapter_count?: number;
  chapters?: Chapter[];
  publication?: { id: number; created_at: string; pdf_size: number };
}

export interface Job {
  id: number;
  kind: string;
  title: string;
  status: string;
  priority: number;
  progress: number;
  progress_text: string;
  error: string | null;
  result: Record<string, unknown> | null;
  attempts: number;
  course_id: number | null;
  parent_id: number | null;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  payload?: Record<string, unknown>;
  logs?: JobLogEntry[];
}

export interface JobLogEntry {
  id: number;
  ts: string;
  level: string;
  message: string;
  /** Structured details: stage, task, item (page/file), figure, attempt, kind, audit_id, provider, model, … */
  context?: Record<string, unknown> | null;
}

export interface TreeCourse {
  id: number;
  name: string;
  slug: string;
  published: boolean;
  chapters: { id: number; title: string; path: string; position: number }[];
  /** ok | working | error */
  status?: string;
  sources?: number;
}
