// A lesson's lab opened through the lesson's share link (/s/<token>/lab): the same page as the owner's, without uploading,
// renaming or deleting files and without the assistant; a read link only looks, a write link comments, writes notes and edits.
import { useEffect, useState } from "react";
import { ApiError, get } from "../../lib/api";
import { Icon } from "../icons";
import { LabEditor } from "./LabEditor";
import type { LabData } from "./files";

export default function SharedLab({ token }: { token: string }) {
  const [data, setData] = useState<(LabData & { mode: "read" | "write" }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<LabData & { mode: "read" | "write" }>(`/api/public/lesson/${encodeURIComponent(token)}/lab`)
      .then((d) => {
        setData(d);
        document.title = `Laboratorio · ${d.lesson.title} · Lecta`;
      })
      .catch((e) => setError(e instanceof ApiError && e.status === 404 ? "Questo link non è valido, è stato revocato, o la lezione non ha un laboratorio." : e instanceof Error ? e.message : String(e)));
  }, [token]);
  if (error) {
    return (
      <div className="lab lab-loading">
        <div className="alert danger">{error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="lab lab-loading muted">
        <Icon name="loader" className="spin" /> Caricamento…
      </div>
    );
  }
  const t = encodeURIComponent(token);
  return <LabEditor lab={data} access={data.mode} base={`/api/public/lesson/${t}`} lessonHref={`/s/${t}`} storeKey={`share:${token}`} />;
}
