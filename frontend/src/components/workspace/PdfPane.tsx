// The "PDF" view of the document: compiled on demand (the draft is what you normally work on).
import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { toastError } from "../ui";
import { compileFull, errorsText, type CompileResult } from "./compile";
import PdfViewer from "./PdfViewer";

export default function PdfPane({ courseId, revision, active, onFix }: { courseId: number; revision: number; active: boolean; onFix: (text: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [failed, setFailed] = useState<CompileResult | null>(null);
  const compiledRev = useRef<number | null>(null);

  const run = useCallback(async () => {
    setCompiling(true);
    compiledRev.current = revision;
    try {
      const r = await compileFull(courseId);
      if (r.status === "superseded") return;
      if (r.pdf_url) setUrl(`${r.pdf_url}&t=${Date.now()}`);
      setFailed(r.status === "ok" ? null : r);
    } catch (e) {
      toastError(e);
    } finally {
      setCompiling(false);
    }
  }, [courseId, revision]);

  // compile when the tab opens, and again if the document changed since
  useEffect(() => {
    if (active && compiledRev.current !== revision && !compiling) void run();
  }, [active, revision, compiling, run]);

  const errs = failed ? errorsText(failed) : "";
  return (
    <div className="pdfpane">
      {compiling && (
        <div className="pdfpane-busy" role="status">
          <Icon name="loader" className="spin" />
          Compilo il PDF… può richiedere qualche secondo
        </div>
      )}
      {failed && !compiling && (
        <div className="pdfpane-err alert danger" role="alert">
          <Icon name="alert-circle" />
          <div className="grow">
            <strong>La compilazione non è riuscita</strong>
            {errs ? <pre className="pdfpane-log">{errs}</pre> : <div className="small">Nessun dettaglio disponibile.</div>}
          </div>
          <div className="pdfpane-err-actions">
            <button type="button" className="btn sm primary" onClick={() => onFix(`La compilazione del PDF è fallita con questi errori:\n\n${errs || "(errore senza dettagli)"}\n\nCorreggili.`)}>
              <Icon name="sparkles" />
              Chiedi all’AI di correggere
            </button>
            <button type="button" className="btn sm" onClick={() => void run()}>
              Riprova
            </button>
          </div>
        </div>
      )}
      <div className="pdfpane-view">
        <PdfViewer url={url} emptyText={compiling ? "" : "Il PDF non è ancora disponibile."} />
      </div>
    </div>
  );
}
