// Publishing is ON/OFF: while ON the public site always shows the latest version of the course,
// rebuilt automatically a couple of minutes after the last change.
import { useState } from "react";
import { fmtDate, get, post } from "../../lib/api";
import { Icon } from "../icons";
import { Confirm, Switch, toastError, useApi, usePoll } from "../ui";

interface PublishState {
  published: boolean;
  state: "off" | "preparing" | "updating" | "online" | "failed";
  updated_at: string | null;
  pdf_size: number | null;
  error: string | null;
  job_id: number | null;
}

export default function PublishControl({ courseId, courseName, onChanged }: { courseId: number; courseName: string; onChanged?: () => void }) {
  const st = useApi(() => get<PublishState>(`/api/courses/${courseId}/publish`), [courseId]);
  const [busy, setBusy] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const s = st.data;
  usePoll(st.reload, 3000, !!s && (s.state === "preparing" || s.state === "updating"));

  const turnOn = async () => {
    setBusy(true);
    try {
      await post(`/api/courses/${courseId}/publish`);
      st.reload();
      onChanged?.();
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };

  if (!s) return null;
  const since = s.updated_at ? fmtDate(s.updated_at, true) : "";
  const working = s.state === "preparing" || s.state === "updating";
  return (
    <div className="pub-ctl">
      <Switch checked={s.published} disabled={busy} onChange={(v) => (v ? turnOn() : setConfirmOff(true))} label="Pubblica" />
      <span className="pub-state tiny" data-testid="publish-state" data-state={s.state} title={s.state === "failed" && s.error ? s.error : since ? `Aggiornato il ${since}` : undefined}>
        {s.state === "off" && <span className="muted">OFF</span>}
        {working && (
          <span className="muted">
            <Icon name="loader" className="spin" />
            {s.state === "preparing" ? "Preparo…" : "Aggiorno…"}
          </span>
        )}
        {s.state === "online" && (
          <span className="text-ok">
            <Icon name="globe" />
            Online
          </span>
        )}
        {s.state === "failed" && (
          <button type="button" className="link-btn text-warn" onClick={turnOn} title={s.error ?? undefined}>
            <Icon name="alert-triangle" />
            Riprova
          </button>
        )}
      </span>
      {confirmOff && (
        <Confirm
          title="Rendere privata la materia?"
          message={
            <>
              <strong>{courseName}</strong> sparirà dal sito pubblico e il PDF non sarà più scaricabile. Puoi ripubblicarla quando vuoi.
            </>
          }
          confirmLabel="Rendi privata"
          danger
          onConfirm={async () => {
            await post(`/api/courses/${courseId}/unpublish`);
            st.reload();
            onChanged?.();
          }}
          onClose={() => setConfirmOff(false)}
        />
      )}
    </div>
  );
}
