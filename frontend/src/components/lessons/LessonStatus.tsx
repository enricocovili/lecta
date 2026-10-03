// A lesson's state: «In corso» while it is being worked on, «Completata» once its text is merged into the subject's notes
// (an «Integra appunti» that wrote into a chapter sets it). The owner can switch it by hand with a click.
import { useState } from "react";
import { patch } from "../../lib/api";
import { Icon } from "../icons";
import { toastError } from "../ui";

export type LessonStatus = "working" | "completed";

const LABEL: Record<LessonStatus, string> = { working: "In corso", completed: "Completata" };

export default function LessonStatusPill({ id, status, editable, onChange }: { id: number; status: LessonStatus; editable: boolean; onChange?: (s: LessonStatus) => void }) {
  const [busy, setBusy] = useState(false);
  const cls = `pill sm les-status ${status === "completed" ? "ok" : "accent"}`;
  const body = (
    <>
      <Icon name={status === "completed" ? "check-circle" : "pencil"} />
      {LABEL[status]}
    </>
  );
  if (!editable) return <span className={cls} data-testid="lesson-status">{body}</span>;
  const next: LessonStatus = status === "completed" ? "working" : "completed";
  return (
    <button
      type="button"
      className={cls}
      data-testid="lesson-status"
      disabled={busy}
      title={status === "completed" ? "Integrata negli appunti. Clic per rimetterla in corso" : "Non ancora integrata negli appunti. Clic per segnarla completata"}
      onClick={async () => {
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
    >
      {body}
    </button>
  );
}
