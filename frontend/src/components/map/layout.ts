// Layout of the concept map: courses in a left and a right column, shared concepts in the
// middle, ordered by the barycentre of their courses so that edges cross as little as possible.
// Pure geometry (no DOM): the component measures the container and passes its width.

export interface MapCourse {
  id: number;
  name: string;
  slug: string;
  status: "ok" | "working" | "error";
  published: boolean;
  concept_count: number;
}

export interface Occurrence {
  course_id: number;
  course_name: string;
  chapter_id: number;
  chapter_title: string;
  chapter_number: number;
  level?: string;
  section: string;
  section_number: string | null;
  path: string;
  line: number;
}

export interface MapConcept {
  id: string;
  label: string;
  score: number;
  course_ids: number[];
  occurrences: Occurrence[];
}

export interface MapData {
  mode: "semantic" | "lexical";
  courses: MapCourse[];
  concepts: MapConcept[];
}

export interface CourseBox {
  x: number; // left edge
  y: number; // vertical centre
  w: number;
  side: "left" | "right";
}
export interface ConceptBox {
  x: number;
  y: number;
  w: number;
}
export interface Edge {
  course: number;
  concept: string;
  d: string; // SVG path
}
export interface Layout {
  height: number;
  courses: Map<number, CourseBox>;
  concepts: Map<string, ConceptBox>;
  edges: Edge[];
}

const PAD_X = 24;
const PAD_TOP = 40;
const LEGEND_H = 76; // reserved at the bottom for the legend
const COURSE_H = 92; // generous estimate of a course node (name on two lines)
const COURSE_GAP = 44;
const PILL_H = 38;
const STEP_MIN = PILL_H + 14;
const STEP_MAX = 84;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

/** Spread `n` centres evenly over [a, b]. */
function spread(n: number, a: number, b: number): number[] {
  return Array.from({ length: n }, (_, i) => a + ((i + 0.5) * (b - a)) / n);
}

/** 1-D placement: keep each item near its wanted position, at least `step` apart, inside [lo, hi]. */
function place(wanted: number[], step: number, lo: number, hi: number): number[] {
  type Block = { first: number; n: number; start: number; sum: number };
  const blocks: Block[] = [];
  wanted.forEach((w, i) => {
    let b: Block = { first: i, n: 1, start: w, sum: w };
    // Merge with the previous block while they overlap; a merged block centres on its members' wishes.
    while (blocks.length) {
      const prev = blocks[blocks.length - 1];
      if (prev.start + prev.n * step <= b.start) break;
      blocks.pop();
      const n = prev.n + b.n;
      // Σ (wanted_k − k·step) over the merged items, k = index inside the block.
      const sum = prev.sum + b.sum - b.n * prev.n * step;
      b = { first: prev.first, n, sum, start: sum / n };
    }
    blocks.push(b);
  });
  const out: number[] = [];
  for (const b of blocks) for (let k = 0; k < b.n; k++) out.push(b.start + k * step);
  // Keep inside the band: push down from the top, then up from the bottom.
  for (let i = 0; i < out.length; i++) out[i] = Math.max(out[i], i ? out[i - 1] + step : lo);
  for (let i = out.length - 1; i >= 0; i--) out[i] = Math.min(out[i], i < out.length - 1 ? out[i + 1] - step : hi);
  return out;
}

export function computeLayout(data: MapData, width: number): Layout {
  const W = Math.max(width, 320);
  const cw = clamp(W * 0.26, 140, 215);
  const pw = clamp(W * 0.28, 140, 225);
  const concepts = data.concepts;
  const byCourse = new Map<number, MapConcept[]>();
  for (const k of concepts) for (const c of k.course_ids) byCourse.set(c, [...(byCourse.get(c) ?? []), k]);

  // Columns: biggest courses first, each into the lighter column (by concepts), sizes within one of each other.
  const sorted = [...data.courses].sort((a, b) => b.concept_count - a.concept_count || a.name.localeCompare(b.name));
  const cols: { left: MapCourse[]; right: MapCourse[] } = { left: [], right: [] };
  const load = { left: 0, right: 0 };
  const half = Math.ceil(sorted.length / 2);
  for (const c of sorted) {
    let side: "left" | "right" = load.left <= load.right ? "left" : "right";
    if (cols[side].length >= half) side = side === "left" ? "right" : "left";
    cols[side].push(c);
    load[side] += Math.max(1, c.concept_count);
  }

  const rows = Math.max(cols.left.length, cols.right.length);
  const height = Math.max(560, PAD_TOP + rows * (COURSE_H + COURSE_GAP) + LEGEND_H, PAD_TOP + concepts.length * STEP_MIN + PILL_H + LEGEND_H);
  const top = PAD_TOP;
  const bottom = height - LEGEND_H;

  // Barycentre sweeps: concepts follow their courses, courses follow their concepts.
  const courseY = new Map<number, number>();
  const setCourseY = () => {
    for (const col of [cols.left, cols.right]) {
      const ys = spread(col.length, top + COURSE_H / 2 - 20, bottom - COURSE_H / 2 + 20);
      col.forEach((c, i) => courseY.set(c.id, ys[i]));
    }
  };
  const wantY = (k: MapConcept) => mean(k.course_ids.map((c) => courseY.get(c) ?? (top + bottom) / 2));
  setCourseY();
  let order = [...concepts];
  for (let pass = 0; pass < 4; pass++) {
    order = [...concepts].sort((a, b) => wantY(a) - wantY(b) || b.course_ids.length - a.course_ids.length);
    const rank = new Map(order.map((k, i) => [k.id, i]));
    const bary = (c: MapCourse) => {
      const ks = byCourse.get(c.id) ?? [];
      return ks.length ? mean(ks.map((k) => rank.get(k.id)!)) : Number.POSITIVE_INFINITY;
    };
    for (const col of [cols.left, cols.right]) col.sort((a, b) => bary(a) - bary(b) || b.concept_count - a.concept_count);
    setCourseY();
  }

  const step = clamp((bottom - top) / Math.max(1, concepts.length), STEP_MIN, STEP_MAX);
  const ys = place(order.map(wantY), step, top + PILL_H / 2, bottom - PILL_H / 2);

  const courseBoxes = new Map<number, CourseBox>();
  cols.left.forEach((c) => courseBoxes.set(c.id, { x: PAD_X, y: courseY.get(c.id)!, w: cw, side: "left" }));
  cols.right.forEach((c) => courseBoxes.set(c.id, { x: W - PAD_X - cw, y: courseY.get(c.id)!, w: cw, side: "right" }));
  const conceptBoxes = new Map<string, ConceptBox>();
  const px = (W - pw) / 2;
  order.forEach((k, i) => conceptBoxes.set(k.id, { x: px, y: ys[i], w: pw }));

  const edges: Edge[] = [];
  for (const k of order) {
    const p = conceptBoxes.get(k.id)!;
    for (const cid of k.course_ids) {
      const c = courseBoxes.get(cid);
      if (!c) continue;
      const [x1, x2] = c.side === "left" ? [c.x + c.w, p.x] : [c.x, p.x + p.w];
      const dx = (x2 - x1) * 0.5;
      edges.push({ course: cid, concept: k.id, d: `M${x1},${c.y} C${x1 + dx},${c.y} ${x2 - dx},${p.y} ${x2},${p.y}` });
    }
  }
  return { height, courses: courseBoxes, concepts: conceptBoxes, edges };
}
