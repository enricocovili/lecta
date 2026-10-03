// A lesson's state: «In corso» while it is being worked on, «Completata» once its text is merged into the subject's notes
// (an «Integra appunti» that wrote into a chapter sets it). The owner switches it by hand with a «Completata» switch;
// the read-only view shows it as a pill.
import { useState } from "react";
import { patch } from "../../lib/api";
import { Icon } from "../icons";
import { Switch, toastError } from "../ui";

export type LessonStatus = "working" | "completed";

const LABEL: Record<LessonStatus, string> = { working: "In corso", completed: "Completata" };

export default function LessonStatusPill({ id, status, editable, onChange }: { id: number; status: LessonStatus; editable: boolean; onChange?: (s: LessonStatus) => void }) {
  const [busy, setBusy] = useState(false);
  if (!editable)
    return (
      <span className={`pill sm les-status ${status === "completed" ? "ok" : "accent"}`} data-testid="lesson-status">
        <Icon name={status === "completed" ? "check-circle" : "pencil"} />
        {LABEL[status]}
      </span>
    );
  return (
    <span
      className="les-status-switch"
      data-testid="lesson-status"
      title={status === "completed" ? "Integrata negli appunti. Spegni per rimetterla in corso" : "In corso: non ancora integrata negli appunti. Accendi per segnarla completata"}
    >
      <Switch
        checked={status === "completed"}
        disabled={busy}
        label="Completata"
        onChange={async (on) => {
          const next: LessonStatus = on ? "completed" : "working";
          setBusy(true);
          try {
            await patch(`/api/lessons/${id}`, { status: next });
            onChange?.(next);
          } catch (e) {
            toastError(e);
          } finally {
            setBusy(false);
          }
        }}
      />
    </span>
  );
}
