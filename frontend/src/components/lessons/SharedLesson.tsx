// A lesson opened through a share link (/s/<token>): the same editor as the owner's, without what is the owner's alone;
// a read link only looks, a write link writes too.
import { useEffect, useState } from "react";
import { ApiError, get } from "../../lib/api";
import { Icon } from "../icons";
import { Editor, type Access, type LessonData } from "./LessonEditor";
import type { PageState } from "./useLesson";

interface Shared {
  mode: "read" | "write";
  title: string;
  course_name: string;
  has_pdf: boolean;
  pdf_pages: number;
  pages: PageState[];
}

export default function SharedLesson({ token }: { token: string }) {
  const [data, setData] = useState<Shared | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<Shared>(`/api/public/lesson/${encodeURIComponent(token)}`)
      .then((d) => {
        setData(d);
        document.title = `${d.title} · Lecta`;
      })
      .catch((e) => setError(e instanceof ApiError && e.status === 404 ? "Questo link non è valido o è stato revocato." : e instanceof Error ? e.message : String(e)));
  }, [token]);
  if (error) {
    return (
      <div className="les les-loading">
        <div className="alert danger">{error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="les les-loading muted">
        <Icon name="loader" className="spin" /> Caricamento…
      </div>
    );
  }
  const lesson: LessonData = {
    id: 0, number: 0, course_id: 0, course_name: data.course_name, course_guidelines: "", title: data.title, status: "working", has_pdf: data.has_pdf, pdf_pages: data.pdf_pages,
    generated_at: null, chapter_id: null, last_page_id: null, last_result: null, pages: data.pages,
  };
  const access: Access = data.mode;
  return <Editor lesson={lesson} access={access} source={{ base: `/api/public/lesson/${encodeURIComponent(token)}`, key: `share:${token}` }} />;
}
