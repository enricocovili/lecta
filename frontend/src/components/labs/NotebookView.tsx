// A Jupyter notebook as its cells: code with its colours and its saved outputs (text, pictures, errors), Markdown rendered.
// Nothing runs: outputs are what the notebook already holds, and HTML outputs are not shown (only their plain text).
import { useMemo, type ReactNode } from "react";
import { Icon } from "../icons";
import { Markdown } from "../workspace/Markdown";
import StaticCode from "./StaticCode";

type Text = string | string[];

interface Output {
  output_type: "stream" | "execute_result" | "display_data" | "error" | string;
  name?: string;
  text?: Text;
  data?: Record<string, Text>;
  ename?: string;
  evalue?: string;
  traceback?: string[];
}

interface Cell {
  cell_type: "code" | "markdown" | "raw" | string;
  source?: Text;
  execution_count?: number | null;
  outputs?: Output[];
}

const MAX_OUTPUT = 20_000;
const join = (t: Text | undefined) => (Array.isArray(t) ? t.join("") : (t ?? ""));
const clip = (s: string) => (s.length > MAX_OUTPUT ? `${s.slice(0, MAX_OUTPUT)}\n… (output tagliato)` : s);
// Tracebacks carry terminal colours.
// eslint-disable-next-line no-control-regex
const plain = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "");
const BASE64 = /^[A-Za-z0-9+/=\s]+$/;

function OutputView({ o }: { o: Output }) {
  if (o.output_type === "stream") return <pre className={`lab-out ${o.name === "stderr" ? "err" : ""}`}>{clip(join(o.text))}</pre>;
  if (o.output_type === "error") return <pre className="lab-out err">{clip(plain([`${o.ename ?? "Error"}: ${o.evalue ?? ""}`, ...(o.traceback ?? [])].join("\n")))}</pre>;
  const data = o.data ?? {};
  for (const type of ["image/png", "image/jpeg", "image/gif"]) {
    const b64 = join(data[type]);
    if (b64 && BASE64.test(b64)) return <img className="lab-out-img" src={`data:${type};base64,${b64.replace(/\s/g, "")}`} alt="Output" />;
  }
  // An SVG drawn as a picture can't run anything.
  if (data["image/svg+xml"]) return <img className="lab-out-img" src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(join(data["image/svg+xml"]))}`} alt="Output" />;
  if (data["text/markdown"]) return <div className="lab-out md"><Markdown text={clip(join(data["text/markdown"]))} /></div>;
  if (data["text/latex"] && !data["text/plain"]) return <div className="lab-out md"><Markdown text={clip(join(data["text/latex"]))} /></div>;
  if (data["text/plain"]) return <pre className="lab-out">{clip(join(data["text/plain"]))}</pre>;
  if (data["text/html"]) return <p className="lab-out muted small">Output HTML non mostrato.</p>;
  return null;
}

export default function NotebookView({
  content,
  language,
  counts,
  activeCell,
  canComment,
  onComment,
}: {
  content: string;
  language: string | null;
  counts: Map<number, number>;
  activeCell: number | null;
  canComment: boolean;
  onComment: (cell: number) => void;
}) {
  const cells = useMemo<Cell[] | null>(() => {
    try {
      const nb = JSON.parse(content) as { cells?: Cell[] };
      return Array.isArray(nb.cells) ? nb.cells : null;
    } catch {
      return null;
    }
  }, [content]);
  if (!cells) return <div className="alert danger">Il notebook non si legge.</div>;

  return (
    <div className="lab-notebook" data-testid="lab-notebook">
      {cells.map((c, i) => {
        const n = i + 1;
        const src = join(c.source);
        let body: ReactNode;
        if (c.cell_type === "markdown") body = <div className="lab-cell-md"><Markdown text={src} /></div>;
        else if (c.cell_type === "code") body = <StaticCode code={src} language={language} />;
        else body = <pre className="lab-static">{src}</pre>;
        return (
          <section key={i} id={`lab-cell-${n}`} className={`lab-cell ${c.cell_type} ${activeCell === n ? "on" : ""}`} data-testid="lab-cell">
            <div className="lab-cell-side mono" title={`Cella ${n}`}>
              {c.cell_type === "code" ? `[${c.execution_count ?? " "}]` : c.cell_type === "markdown" ? "Md" : ""}
            </div>
            <div className="lab-cell-main">
              {body}
              {c.cell_type === "code" && (c.outputs ?? []).map((o, k) => <OutputView key={k} o={o} />)}
            </div>
            <div className="lab-cell-actions">
              {counts.get(n) ? <span className="lab-count" title={`${counts.get(n)} commenti`}>{counts.get(n)}</span> : null}
              {canComment && (
                <button type="button" className="btn ghost icon sm" onClick={() => onComment(n)} aria-label={`Commenta la cella ${n}`} title="Commenta questa cella">
                  <Icon name="message" />
                </button>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
