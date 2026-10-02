import { useEffect, useRef, useState } from "react";
import { fmtDate, get, post } from "../lib/api";
import { Icon } from "./icons";
import { FigureCard, ItemCard, ItemModal, type ItemView } from "./IngestManifest";
import { JobPill } from "./pagekit";
import type { TreeCourse } from "./types";
import { Confirm, Empty, ErrorBox, fmtWhen, Loading, Modal, toast, toastError, useApi, type Tone } from "./ui";

interface Guess {
  type: string;
  course_id: number | null;
  chapter_id: number | null;
  confidence: number;
  rationale: string;
  course_name: string | null;
  chapter_title: string | null;
}

interface Row {
  id: number;
  status: string;
  title: string;
  language: string | null;
  job_id: number | null;
  created_at: string;
  sections: number;
  figures: number;
  guesses: Guess[];
}

interface InboxFigure {
  key: string;
  origin: string;
  crop_url: string | null;
}

interface Detail extends Omit<Row, "figures"> {
  body: string;
  outline: string[];
  figures: InboxFigure[];
  source_items: ItemView[];
  assigned_job?: { id: number; status: string } | null;
}

const ITEM_STATUS: Record<string, { label: string; tone: Tone }> = {
  open: { label: "Da smistare", tone: "warn" },
  assigning: { label: "In smistamento", tone: "warn" },
  assigned: { label: "Assegnato", tone: "ok" },
  discarded: { label: "Scartato", tone: "" },
};

const FILTERS: { key: string; label: string }[] = [
  { key: "open", label: "Da smistare" },
  { key: "assigned", label: "Assegnati" },
  { key: "discarded", label: "Scartati" },
  { key: "all", label: "Tutti" },
];

function ItemStatus({ status }: { status: string }) {
  const s = ITEM_STATUS[status] ?? { label: status, tone: "" as Tone };
  return <span className={`badge ${s.tone}`}>{s.label}</span>;
}

function AssignModal({ item, onClose, guess }: { item: Detail; onClose: () => void; guess?: Guess }) {
  const [tree, setTree] = useState<TreeCourse[]>([]);
  const [courseId, setCourseId] = useState<string>(guess?.course_id ? String(guess.course_id) : "");
  const [chapterId, setChapterId] = useState<string>(guess?.chapter_id ? String(guess.chapter_id) : "");
  const [title, setTitle] = useState(item.title);
  useEffect(() => {
    get<TreeCourse[]>("/api/tree").then(setTree).catch(() => undefined);
  }, []);
  const course = tree.find((c) => String(c.id) === courseId);
  return (
    <Modal
      title="Assegna a una materia"
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Annulla
          </button>
          <button
            className="btn primary"
            disabled={!courseId}
            onClick={async () => {
              try {
                const r = await post<{ job_id: number }>(`/api/inbox/${item.id}/assign`, {
                  course_id: Number(courseId),
                  chapter_id: chapterId === "__new" || !chapterId ? null : Number(chapterId),
                  new_chapter_title: chapterId === "__new" ? title : null,
                });
                location.href = `/admin/jobs/${r.job_id}`;
              } catch (e) {
                toastError(e);
              }
            }}
          >
            <Icon name="folder" />
            Assegna
          </button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          Materia
          <select
            value={courseId}
            onChange={(e) => {
              setCourseId(e.target.value);
              setChapterId("");
            }}
          >
            <option value="">—</option>
            {tree.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Capitolo
          <select value={chapterId} onChange={(e) => setChapterId(e.target.value)} disabled={!course}>
            <option value="">Decide Lecta (capitolo esistente o nuovo)</option>
            <option value="__new">Nuovo capitolo…</option>
            {(course?.chapters ?? []).map((ch) => (
              <option key={ch.id} value={ch.id}>
                Unisci a {String(ch.position).padStart(2, "0")} {ch.title}
              </option>
            ))}
          </select>
        </label>
        {chapterId === "__new" && (
          <label className="field">
            Titolo del nuovo capitolo
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
        )}
        <p className="tiny muted" style={{ margin: 0 }}>
          Gli appunti vengono scritti subito nella materia: in un capitolo nuovo, oppure in fondo al capitolo scelto.
        </p>
      </div>
    </Modal>
  );
}

function NewCourseModal({ item, onClose }: { item: Detail; onClose: () => void }) {
  const [name, setName] = useState(item.title);
  const [year, setYear] = useState("");
  return (
    <Modal
      title="Nuova materia da questo materiale"
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Annulla
          </button>
          <button
            className="btn primary"
            disabled={!name.trim()}
            onClick={async () => {
              try {
                const r = await post<{ job_id: number; course_id: number; language: string }>(`/api/inbox/${item.id}/new-course`, {
                  name,
                  academic_year: year || null,
                });
                toast(`Materia creata (lingua: ${r.language})`);
                window.dispatchEvent(new Event("lecta:tree-changed"));
                location.href = `/admin/jobs/${r.job_id}`;
              } catch (e) {
                toastError(e);
              }
            }}
          >
            <Icon name="plus" />
            Crea materia
          </button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          Nome della materia
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          Anno accademico
          <input type="text" value={year} onChange={(e) => setYear(e.target.value)} placeholder="2025/26" />
        </label>
        <p className="tiny muted" style={{ margin: 0 }}>
          La lingua della materia viene rilevata dal materiale ({item.language ?? "sconosciuta"}); puoi cambiarla dopo nelle impostazioni.
        </p>
      </div>
    </Modal>
  );
}

function InboxDetail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const { data, error, loading, reload } = useApi(() => get<Detail>(`/api/inbox/${id}`), [id]);
  const [assign, setAssign] = useState<Guess | "manual" | null>(null);
  const [newCourse, setNewCourse] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [openItem, setOpenItem] = useState<ItemView | null>(null);
  if (loading && !data) return <Loading />;
  if (error || !data) return <ErrorBox error={error ?? "Elemento non trovato"} />;
  const open = data.status === "open";
  const reopen = async () => {
    try {
      await post(`/api/inbox/${id}/reopen`);
      window.dispatchEvent(new Event("lecta:counts-changed"));
      reload();
      onChanged();
    } catch (e) {
      toastError(e);
    }
  };
  return (
    <div className="pg-inbox-detail">
      <div className="card pg-pad stack">
        <div className="row between pg-nw" style={{ alignItems: "flex-start" }}>
          <div className="grow" style={{ minWidth: 0 }}>
            <h2 className="pg-card-title pg-inbox-title">{data.title}</h2>
            <div className="pg-row-sub">
              {fmtDate(data.created_at, true)} · lingua {data.language ?? "?"} · {data.sections} {data.sections === 1 ? "sezione" : "sezioni"} ·{" "}
              {data.figures.length} {data.figures.length === 1 ? "figura" : "figure"}
              {data.job_id && (
                <>
                  {" "}
                  · da <a href={`/admin/jobs/${data.job_id}`}>attività #{data.job_id}</a>
                </>
              )}
            </div>
          </div>
          <ItemStatus status={data.status} />
        </div>
        {open && (
          <div className="row">
            <button className="btn primary" onClick={() => setAssign("manual")}>
              <Icon name="folder" />
              Assegna a una materia…
            </button>
            <button className="btn" onClick={() => setNewCourse(true)}>
              <Icon name="plus" />
              Crea una nuova materia
            </button>
            <button className="btn danger" onClick={() => setDiscard(true)}>
              <Icon name="trash" />
              Scarta
            </button>
          </div>
        )}
        {(data.status === "discarded" || data.status === "assigning") && (
          <div className="row">
            <button className="btn" onClick={reopen}>
              <Icon name="refresh" />
              Rimetti da smistare
            </button>
          </div>
        )}
        {data.assigned_job && (
          <div className="alert small">
            <Icon name="activity" />
            <span className="row" style={{ gap: ".5rem" }}>
              Smistamento: <a href={`/admin/jobs/${data.assigned_job.id}`}>attività #{data.assigned_job.id}</a>
              <JobPill status={data.assigned_job.status} size="sm" />
            </span>
          </div>
        )}
      </div>

      <div className="section-label">Ipotesi migliori</div>
      {data.guesses.length === 0 ? (
        <Empty icon="folder">Nessuna materia candidata.</Empty>
      ) : (
        <div className="card flush">
          <div className="rows">
            {data.guesses.map((g, i) => (
              <div key={i} className="pg-row pg-guess">
                <div className="pg-guess-pct">
                  <span className="mono">{Math.round(g.confidence * 100)}%</span>
                  <div className="progress">
                    <div style={{ width: `${Math.round(g.confidence * 100)}%` }} />
                  </div>
                </div>
                <div className="grow">
                  <div className="pg-row-title">
                    <Icon name="folder" className="faint" /> {g.course_name ?? "?"}
                    <span className="muted"> / {g.chapter_title ?? "nuovo capitolo"}</span>
                  </div>
                  <div className="pg-row-sub">{g.rationale}</div>
                </div>
                {open && g.course_id && (
                  <button className="btn sm" onClick={() => setAssign(g)}>
                    Usa questa
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="pg-split">
        <div>
          <div className="section-label">
            Sorgenti <span className="pg-tab-n">{data.source_items.length}</span>
          </div>
          <div className="card pg-pad">
            <div className="thumbs">
              {data.source_items.map((it) => (
                <ItemCard key={it.id} it={it} onOpen={setOpenItem} />
              ))}
            </div>
          </div>
        </div>
        <div>
          <div className="section-label">Appunti generati</div>
          <pre className="small pg-inbox-body">{data.body}</pre>
        </div>
      </div>
      {data.figures.length > 0 && (
        <>
          <div className="section-label">
            Immagini <span className="pg-tab-n">{data.figures.length}</span>
          </div>
          <div className="card pg-pad">
            <div className="thumbs">
              {data.figures.map((f) => (
                <FigureCard key={f.key} f={{ id: 0, key: f.key, origin: f.origin, status: "pending", crop_url: f.crop_url }} />
              ))}
            </div>
          </div>
        </>
      )}
      {assign && <AssignModal item={data} guess={assign === "manual" ? undefined : assign} onClose={() => setAssign(null)} />}
      {newCourse && <NewCourseModal item={data} onClose={() => setNewCourse(false)} />}
      {discard && (
        <Confirm
          title="Scarta"
          danger
          message="Scartare questo elemento? Le sorgenti caricate restano."
          confirmLabel="Scarta"
          onConfirm={async () => {
            await post(`/api/inbox/${id}/discard`);
            window.dispatchEvent(new Event("lecta:counts-changed"));
            onChanged();
          }}
          onClose={() => setDiscard(false)}
        />
      )}
      {openItem && <ItemModal it={openItem} onClose={() => setOpenItem(null)} />}
    </div>
  );
}

export default function Inbox() {
  const [status, setStatus] = useState("open");
  const { data, error, loading, reload } = useApi(() => get<Row[]>(`/api/inbox?status=${status}`), [status]);
  const [selected, setSelected] = useState<number | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const id = new URLSearchParams(location.search).get("id");
    if (id) setSelected(Number(id));
  }, []);
  useEffect(() => {
    if (!selected && data && data.length) setSelected(data[0].id);
  }, [data, selected]);
  const pick = (id: number) => {
    setSelected(id);
    // On phones the detail sits below the list: bring it into view.
    if (window.matchMedia("(max-width: 1100px)").matches) setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };
  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Da smistare</h1>
          <div className="sub">Materiale che Lecta non ha saputo collocare con sicurezza. Assegnalo, trasformalo in una nuova materia o scartalo.</div>
        </div>
      </div>
      <div className="chips" style={{ marginBottom: "1.25rem" }} role="tablist" aria-label="Stato">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            role="tab"
            aria-selected={status === f.key}
            className={`chip ${status === f.key ? "active" : ""}`}
            onClick={() => {
              setStatus(f.key);
              setSelected(null);
            }}
          >
            {f.label}
            {status === f.key && data && <span className="n">{data.length}</span>}
          </button>
        ))}
      </div>
      <ErrorBox error={error} />
      {loading && !data ? (
        <Loading />
      ) : (data ?? []).length === 0 ? (
        <Empty icon="inbox">{status === "open" ? "Niente da smistare: tutto il materiale ha trovato posto." : "Nessun elemento."}</Empty>
      ) : (
        <div className="pg-inbox">
          <div className="card flush pg-inbox-list">
            <div className="rows">
              {(data ?? []).map((i) => (
                <button key={i.id} className={`pg-inbox-item ${selected === i.id ? "active" : ""}`} onClick={() => pick(i.id)}>
                  <div className="row between pg-nw" style={{ gap: ".5rem" }}>
                    <span className="pg-row-title ellipsis">{i.title}</span>
                    <span className="pg-time mono">{fmtWhen(i.created_at)}</span>
                  </div>
                  <div className="pg-row-sub">
                    {status === "all" && (
                      <>
                        <ItemStatus status={i.status} />{" "}
                      </>
                    )}
                    {i.guesses[0]?.course_name ? (
                      <>
                        <Icon name="folder" className="faint" /> {i.guesses[0].course_name} · {Math.round(i.guesses[0].confidence * 100)}%
                      </>
                    ) : (
                      "nessuna ipotesi"
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div ref={detailRef}>
            {selected ? (
              <InboxDetail
                key={selected}
                id={selected}
                onChanged={() => {
                  setSelected(null);
                  reload();
                }}
              />
            ) : (
              <Empty icon="inbox">Nessun elemento selezionato.</Empty>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
