// Italian formatting helpers shared by the public pages (SSR) and the reader island.

export interface PubChapter {
  slug: string;
  title: string;
  position: number;
  page_start: number | null;
  page_end: number | null;
  pdf_url: string | null;
  pdf_size: number | null;
}

export interface PubCourse {
  slug: string;
  name: string;
  description: string | null;
  academic_year: string | null;
  updated_at: string;
  pdf_url: string;
  pdf_size: number;
  /** The LaTeX project as a .zip (null until the course is rebuilt after this was introduced). */
  source_url?: string | null;
  source_size?: number | null;
  chapter_count: number;
  page_count: number | null;
  tags: string[];
  chapters?: PubChapter[];
}

/** "2,4 MB", "410 KB". */
export function sizeIt(bytes: number | null | undefined): string {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const n = new Intl.NumberFormat("it-IT", { maximumFractionDigits: v < 10 && i > 0 ? 1 : 0 }).format(v);
  return `${n} ${units[i]}`;
}

/** "24 set 2026". */
export function dateIt(value: string | null | undefined): string {
  if (!value) return "";
  return new Date(value).toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" });
}

/** "pp. 7–14", "p. 5", or "" when the split didn't find the chapter. */
export function pagesIt(ch: Pick<PubChapter, "page_start" | "page_end">): string {
  if (!ch.page_start) return "";
  if (!ch.page_end || ch.page_end === ch.page_start) return `p. ${ch.page_start}`;
  return `pp. ${ch.page_start}–${ch.page_end}`;
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}
