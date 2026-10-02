// Italian wording for jobs: kind names, the backend's (English) progress texts and
// the "Lettura · 2 di 3" stage labels of the activity lists.
// Dependency-free on purpose: imported by the Home, the jobs list and the job page.

export interface JobLike {
  kind: string;
  status: string;
  progress: number;
  progress_text: string;
}

export interface JobStage {
  /** Short Italian name of the current stage ("Lettura pagine"). */
  label: string;
  /** 1-based visible step (≤ steps). */
  step: number;
  steps: number;
}

const KIND_LABEL: Record<string, string> = {
  ingest: "Importazione",
  publish: "Pubblicazione",
  "inbox.assign": "Smistamento",
  "index.rebuild": "Indicizzazione",
  "embeddings.benchmark": "Benchmark embeddings",
  "demo.sleep": "Job di prova",
};

export function jobKindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.replace(/[._]/g, " ");
}

type Rule = [RegExp, (m: RegExpMatchArray) => string];

// Every text passed to ctx.progress(...) in backend/app (pipeline/*, worker/*).
const RULES: Rule[] = [
  [/^extracting$/i, () => "Estrazione"],
  [/^unpacking (.+)$/i, (m) => `Estrazione di ${m[1]}`],
  [/^extracting (.+)$/i, (m) => `Estrazione di ${m[1]}`],
  [/^reading \((\d+)\/(\d+)\)$/i, (m) => `Lettura (${m[1]}/${m[2]})`],
  [/^composing \((\d+)\/(\d+)\)$/i, (m) => `Stesura del testo (${m[1]}/${m[2]})`],
  [/^placing (“.*”?)$/i, (m) => `Collocazione di ${m[1]}`],
  [/^checking (“.*”?)$/i, (m) => `Verifica di compilazione di ${m[1]}`],
  [/^writing (“.*”?)$/i, (m) => `Inserimento di ${m[1]}`],
  [/^clean build \(review markers stripped\)$/i, () => "Compilazione pulita (segni di revisione rimossi)"],
  [/^splitting chapters$/i, () => "Divisione dei capitoli"],
  [/^(\d+) chunks embedded, (\d+) chapters left$/i, (m) => `${m[1]} frammenti indicizzati, ${m[2]} capitoli rimanenti`],
  [/^step (\d+)\/(\d+)$/i, (m) => `Passo ${m[1]} di ${m[2]}`],
];

/** Translate a backend progress text; unknown texts are returned unchanged. */
export function translateProgress(text: string): string {
  const t = (text ?? "").trim();
  if (!t) return "";
  for (const [re, fn] of RULES) {
    const m = t.match(re);
    if (m) return fn(m);
  }
  return t;
}

/** Stage name + visible step for the import pipeline (4 visible steps). */
function ingestStage(text: string): [string, number] {
  const t = text.toLowerCase();
  if (t.startsWith("extracting") || t.startsWith("unpacking")) return ["Estrazione", 1];
  if (t.startsWith("reading")) return ["Lettura", 2];
  if (t.startsWith("composing")) return ["Stesura", 3];
  if (t.startsWith("placing") || t.startsWith("checking") || t.startsWith("writing")) return ["Inserimento", 4];
  return ["", 0];
}

function publishStage(text: string): [string, number] {
  const t = text.toLowerCase();
  if (t.startsWith("clean build")) return ["Compilazione PDF", 2];
  if (t.startsWith("splitting")) return ["Divisione capitoli", 3];
  return ["", 0];
}

/**
 * The stage a job is in, as shown above its progress bar:
 * "In coda · 1 di 3", "Lettura · 2 di 3", "Compilazione PDF · 3 di 3", "Completato".
 */
export function jobStage(job: JobLike): JobStage {
  const text = job.progress_text ?? "";
  const multi = job.kind === "ingest" || job.kind === "inbox.assign" || job.kind === "publish";
  const steps = job.kind === "ingest" || job.kind === "inbox.assign" ? 4 : multi ? 3 : 1;
  let [label, step] = job.kind === "publish" ? publishStage(text) : multi ? ingestStage(text) : ["", 0];
  if (job.kind === "inbox.assign" && !label) [label, step] = ["Inserimento", 4];
  if (!step) {
    // Unknown text: estimate the step from the progress fraction.
    step = Math.min(steps, Math.max(1, Math.ceil((job.progress || 0) * steps)));
  }
  switch (job.status) {
    case "queued":
      return { label: "In coda", step: 1, steps };
    case "succeeded":
      return { label: job.kind === "publish" ? "Pubblicato" : "Completato", step: steps, steps };
    case "cancelled":
      return { label: "Annullato", step, steps };
    case "failed":
      // The red pill/bar already says it failed; the label says where.
      return { label: label || "Non riuscito", step, steps };
    default:
      return { label: label || translateProgress(text) || jobKindLabel(job.kind), step, steps };
  }
}

/** Names of the visible steps of a job kind (what `jobStage().step` counts), e.g. for a stepper. */
export function jobSteps(kind: string): string[] {
  if (kind === "ingest" || kind === "inbox.assign") return ["Estrazione", "Lettura", "Stesura", "Inserimento"];
  if (kind === "publish") return ["Preparazione", "Compilazione PDF", "Divisione capitoli"];
  return [jobKindLabel(kind)];
}

/** Italian version of the English job titles the backend generates ("Publish “X”", "Upload: a.pdf"). */
export function translateJobTitle(title: string): string {
  const rules: Rule[] = [
    [/^Publish (“.*”)$/, (m) => `Pubblicazione di ${m[1]}`],
    [/^Upload: (.*)$/, (m) => `Caricamento: ${m[1]}`],
    [/^Place (“.*”) in (.*)$/, (m) => `Collocazione di ${m[1]} in ${m[2]}`],
    [/^Embedding benchmark$/, () => "Benchmark embeddings"],
    [/^Rebuild the retrieval index$/, () => "Ricostruzione dell'indice di ricerca"],
  ];
  for (const [re, fn] of rules) {
    const m = title.match(re);
    if (m) return fn(m);
  }
  return title;
}
