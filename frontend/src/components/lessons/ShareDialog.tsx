// «Condividi»: the links that open the lesson without signing in, one to look (notes, slides, strokes) and one to write too.
import { useEffect, useState } from "react";
import { del, fmtDay, get, post } from "../../lib/api";
import { Icon } from "../icons";
import { Modal, toast, toastError } from "../ui";

interface Share {
  mode: "read" | "write";
  token: string;
  created_at: string;
  last_used_at: string | null;
}

const MODES: { mode: Share["mode"]; icon: string; title: string; text: string }[] = [
  { mode: "read", icon: "eye", title: "Sola lettura", text: "Chi ha il link vede slide, appunti e scritte, e li segue mentre scrivi. Non può cambiare nulla." },
  { mode: "write", icon: "pencil", title: "Può modificare", text: "Chi ha il link scrive appunti e disegna come te, nella stessa lezione. Non può eliminarla né generare il testo." },
];

export const shareUrl = (token: string) => `${location.origin}/s/${token}`;

export default function ShareDialog({ lessonId, onChange, onClose }: { lessonId: number; onChange: (links: number) => void; onClose: () => void }) {
  const [shares, setShares] = useState<Share[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    get<Share[]>(`/api/lessons/${lessonId}/shares`).then(setShares).catch((e) => {
      toastError(e);
      setShares([]);
    });
  }, [lessonId]);

  const update = (next: Share[]) => {
    setShares(next);
    onChange(next.length);
  };
  const make = async (mode: Share["mode"], newLink = false) => {
    setBusy(mode);
    try {
      const sh = await post<Share>(`/api/lessons/${lessonId}/shares`, { mode, new_link: newLink });
      update([...(shares ?? []).filter((s) => s.mode !== mode), sh]);
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(null);
    }
  };
  const revoke = async (mode: Share["mode"]) => {
    setBusy(mode);
    try {
      await del(`/api/lessons/${lessonId}/shares/${mode}`);
      update((shares ?? []).filter((s) => s.mode !== mode));
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(null);
    }
  };
  const copy = async (sh: Share) => {
    try {
      await navigator.clipboard.writeText(shareUrl(sh.token));
      toast("Link copiato");
    } catch {
      toast("Seleziona il link e copialo a mano", "error");
    }
  };

  return (
    <Modal
      title="Condividi la lezione"
      onClose={onClose}
      actions={
        <button className="btn primary" onClick={onClose}>
          Fatto
        </button>
      }
    >
      <div className="stack">
        <div className="small muted">Chiunque abbia il link apre la lezione senza accedere. Condividilo solo con chi vuoi: puoi revocarlo quando vuoi.</div>
        {shares === null ? (
          <div className="muted small">
            <Icon name="loader" className="spin" /> Caricamento…
          </div>
        ) : (
          MODES.map((m) => {
            const sh = shares.find((s) => s.mode === m.mode);
            return (
              <div key={m.mode} className="les-share" data-testid={`share-${m.mode}`}>
                <div className="les-share-head">
                  <Icon name={m.icon} />
                  <strong>{m.title}</strong>
                  {sh ? (
                    <span className="muted small grow">{sh.last_used_at ? `ultimo uso ${fmtDay(sh.last_used_at)}` : "mai usato"}</span>
                  ) : (
                    <span className="grow" />
                  )}
                  {!sh && (
                    <button className="btn sm" onClick={() => make(m.mode)} disabled={busy !== null} data-testid={`share-make-${m.mode}`}>
                      <Icon name="link" />
                      Crea il link
                    </button>
                  )}
                </div>
                <div className="small muted">{m.text}</div>
                {sh && (
                  <div className="row nowrap les-share-link">
                    <input type="text" readOnly value={shareUrl(sh.token)} onFocus={(e) => e.currentTarget.select()} aria-label={`Link ${m.title}`} data-testid={`share-url-${m.mode}`} />
                    <button className="btn sm" onClick={() => copy(sh)}>
                      <Icon name="copy" />
                      Copia
                    </button>
                    <button className="btn sm" onClick={() => make(m.mode, true)} disabled={busy !== null} title="Il link attuale smette di funzionare">
                      <Icon name="refresh" />
                      <span className="hide-mobile">Nuovo</span>
                    </button>
                    <button className="btn sm danger" onClick={() => revoke(m.mode)} disabled={busy !== null} data-testid={`share-revoke-${m.mode}`}>
                      <Icon name="x" />
                      <span className="hide-mobile">Revoca</span>
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </Modal>
  );
}
