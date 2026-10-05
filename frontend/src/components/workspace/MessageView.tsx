// One message of the conversation: the user's request, or the assistant's turn (activity, reply,
// what it changed with the Annulla button, the review card, follow-up suggestions).
import { useState } from "react";
import { Icon } from "../icons";
import { Markdown } from "./Markdown";
import type { ChangedFile, CourseChapter, FlashTarget, Message, Mode, Review, Scope, Step } from "./types";

export interface MessageCtx {
  chapters: CourseChapter[];
  onJump: (chapterId: number | null, targets?: FlashTarget[]) => void;
  onAsk: (text: string, opts?: { mode?: Mode; chapterId?: number | null; scope?: Scope; noScope?: boolean }) => Promise<boolean> | boolean;
  onUndo: (replyId: number) => Promise<boolean>;
  /** a lab's conversation: open one of its files, and name the file a message was about */
  onFile?: (path: string) => void;
  fileName?: (fileId: number) => string | null;
}

const chapterName = (ctx: MessageCtx, id: number | null | undefined) => {
  if (id == null) return null;
  const i = ctx.chapters.findIndex((c) => c.id === id);
  return i < 0 ? null : { n: i + 1, title: ctx.chapters[i].title };
};

// ------------------------------------------------------------------ user

function UserMessage({ m, ctx }: { m: Message; ctx: MessageCtx }) {
  const ch = chapterName(ctx, m.scope.chapter_id);
  const file = m.scope.file_id != null ? (ctx.fileName?.(m.scope.file_id) ?? "File") : null;
  const sel = m.scope.selection;
  const review = m.scope.mode === "review";
  return (
    <div className="ai-msg user" data-testid="ai-message" data-role="user">
      {(review || ch || sel || file) && (
        <div className="ai-scope-chip" title={ch?.title ?? file ?? undefined}>
          <Icon name={review ? "sparkles" : sel ? "pencil" : file ? "code" : "file-text"} />
          <span>
            {review ? "Revisione del corso" : file ? file : ch ? `Capitolo ${ch.n}` : "Capitolo"}
            {sel && !review && <> · «{sel.text.replace(/\s+/g, " ").slice(0, 70)}{sel.text.length > 70 ? "…" : ""}»</>}
          </span>
        </div>
      )}
      <div className="ai-bubble">{m.content}</div>
    </div>
  );
}

// ------------------------------------------------------------------ activity

function StepIcon({ status }: { status: Step["status"] }) {
  if (status === "running") return <Icon name="loader" className="spin" />;
  if (status === "error") return <Icon name="alert-circle" className="text-danger" />;
  return <Icon name="check" className="text-ok" />;
}

function Steps({ steps, live }: { steps: Step[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;
  const failed = steps.filter((s) => s.status === "error").length;
  const expanded = live || open;
  return (
    <div className={`ai-steps ${live ? "live" : ""}`} data-testid="ai-steps">
      {live ? (
        <div className="ai-steps-head">
          <Icon name="loader" className="spin" />
          Sta lavorando · {steps.length} {steps.length === 1 ? "azione" : "azioni"}
        </div>
      ) : (
        <button type="button" className="ai-steps-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <Icon name={open ? "chevron-down" : "chevron-right"} />
          Ha fatto {steps.length} {steps.length === 1 ? "azione" : "azioni"}
          {failed > 0 && <span className="text-danger"> · {failed} non riuscit{failed === 1 ? "a" : "e"}</span>}
        </button>
      )}
      {expanded && (
        <ol className="ai-steps-list">
          {steps.map((s) => (
            <li key={s.id} className={`ai-step ${s.status}`} data-testid="ai-tool-step" data-status={s.status}>
              <StepIcon status={s.status} />
              <span className="ai-step-label">{s.label}</span>
              {s.summary && <span className="ai-step-sum">{s.summary}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ what changed

function fileLabel(f: ChangedFile, ctx: MessageCtx): string {
  const ch = chapterName(ctx, f.chapter_id);
  if (ch) return ch.title;
  const base = f.path.split("/").pop() ?? f.path;
  if (base === "preamble.tex") return "Preambolo";
  if (base === "main.tex") return "Struttura del documento (main.tex)";
  return f.path;
}

const OP_LABEL: Record<string, string> = { create: "nuovo", delete: "eliminato", rename: "rinominato", comment: "commentato" };
const CH_OP: Record<string, string> = { created: "Nuovo capitolo", renamed: "Capitolo rinominato", deleted: "Capitolo eliminato", moved: "Capitolo spostato" };

function ChangeCard({ m, live, canUndo, ctx }: { m: Message; live: boolean; canUndo: boolean; ctx: MessageCtx }) {
  const [busy, setBusy] = useState(false);
  const c = m.change;
  if (!c || (c.files.length === 0 && !(c.chapters?.length ?? 0))) return null;
  const undone = c.status === "undone";
  const n = c.files.length;
  const undo = async () => {
    setBusy(true);
    await ctx.onUndo(m.id);
    setBusy(false);
  };
  return (
    <div className={`ai-change ${undone ? "undone" : ""}`} data-testid="ai-change-card" data-status={c.status}>
      <div className="ai-change-head">
        <Icon name={live ? "loader" : undone ? "refresh" : "check-circle"} className={live ? "spin" : ""} />
        <strong>{live ? "Sto modificando…" : undone ? "Annullato" : n > 0 && c.files.every((f) => f.op === "comment") ? "Ho aggiunto dei commenti" : n > 0 ? `Ho modificato ${n} file` : "Ho cambiato la struttura"}</strong>
        {undone && <span className="badge">ripristinato</span>}
      </div>
      {n > 0 && (
        <ul className="ai-change-files">
          {c.files.map((f) => {
            const targets: FlashTarget[] = f.chapter_id != null ? f.hunks.map((h) => ({ chapterId: f.chapter_id!, from: h.from_line, to: h.to_line })) : [];
            return (
              <li key={f.path}>
                {ctx.onFile && f.file_id != null ? (
                  <button type="button" className="ai-change-link" disabled={undone} onClick={() => ctx.onFile!(f.path)} title={f.path}>
                    {f.path}
                  </button>
                ) : (
                  <button type="button" className="ai-change-link" disabled={f.chapter_id == null || undone} onClick={() => ctx.onJump(f.chapter_id, targets.slice(0, 1))} title={f.path}>
                    {fileLabel(f, ctx)}
                  </button>
                )}
                {OP_LABEL[f.op] && <span className="badge">{OP_LABEL[f.op]}</span>}
                {f.op !== "comment" && (
                  <span className="ai-diff mono">
                    <span className="add">+{f.added}</span> <span className="del">−{f.removed}</span>
                  </span>
                )}
                {f.comments ? <span className="badge">{f.comments === 1 ? "+1 commento" : `+${f.comments} commenti`}</span> : null}
              </li>
            );
          })}
        </ul>
      )}
      {(c.chapters?.length ?? 0) > 0 && (
        <ul className="ai-change-files">
          {c.chapters!.map((x) => (
            <li key={`${x.id}-${x.op}`}>
              <span className="ai-change-note">
                {CH_OP[x.op] ?? x.op}: <strong>{x.title}</strong>
              </span>
            </li>
          ))}
        </ul>
      )}
      {!live && !undone && canUndo && (
        <div className="ai-change-actions">
          <button type="button" className="btn ai-undo" data-testid="ai-undo" onClick={undo} disabled={busy}>
            <Icon name={busy ? "loader" : "history"} className={busy ? "spin" : ""} />
            Annulla
          </button>
          <span className="tiny muted">Ripristina il documento com’era prima di questa risposta.</span>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ review

function ReviewCard({ r, ctx }: { r: Review; ctx: MessageCtx }) {
  const tone = r.score >= 8 ? "ok" : r.score >= 6 ? "warn" : "danger";
  return (
    <div className="ai-review" data-testid="ai-review">
      <div className="ai-review-top">
        <div className={`ai-score ${tone}`} aria-label={`Voto ${r.score} su 10`}>
          <strong>{r.score}</strong>
          <span>/10</span>
        </div>
        <div className="grow">
          <div className="ai-review-title">Revisione del corso</div>
          <p className="ai-review-verdict">{r.verdict}</p>
        </div>
      </div>
      {r.strengths.length > 0 && (
        <div className="ai-review-sec">
          <div className="ai-review-h">Punti di forza</div>
          <ul className="ai-review-list">
            {r.strengths.map((s, i) => (
              <li key={i}>
                <Icon name="check" className="text-ok" />
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {r.issues.length > 0 && (
        <div className="ai-review-sec">
          <div className="ai-review-h">Da migliorare</div>
          <ul className="ai-review-issues">
            {r.issues.map((it, i) => {
              const ch = it.chapter_id != null ? chapterName(ctx, it.chapter_id) : null;
              return (
                <li key={i}>
                  <div className="grow">
                    {(it.chapter || ch) && (
                      <button type="button" className="ai-issue-ch" disabled={it.chapter_id == null} onClick={() => ctx.onJump(it.chapter_id ?? null)}>
                        {it.chapter ?? ch?.title}
                      </button>
                    )}
                    <span>{it.text}</span>
                  </div>
                  {it.fix && (
                    <button type="button" className="btn sm ai-fix" data-testid="ai-fix" onClick={() => ctx.onAsk(it.fix!, { mode: "edit", chapterId: it.chapter_id ?? null })}>
                      Correggi
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ assistant

function AssistantMessage({ m, prev, last, canUndo, ctx }: { m: Message; prev: Message | null; last: boolean; canUndo: boolean; ctx: MessageCtx }) {
  const live = m.status === "streaming";
  return (
    <div className={`ai-msg assistant ${m.status}`} data-testid="ai-message" data-role="assistant" data-status={m.status}>
      <div className="ai-avatar" aria-hidden="true">
        <Icon name="sparkles" />
      </div>
      <div className="ai-msg-body">
        <Steps steps={m.steps} live={live} />
        {m.content && <Markdown text={m.content} />}
        {live && !m.content && m.steps.length === 0 && (
          <div className="ai-typing" role="status" aria-label="L’assistente sta scrivendo">
            <span />
            <span />
            <span />
          </div>
        )}
        {m.review && <ReviewCard r={m.review} ctx={ctx} />}
        <ChangeCard m={m} live={live} canUndo={canUndo} ctx={ctx} />
        {m.status === "cancelled" && <div className="ai-note tiny muted">Interrotto.</div>}
        {m.status === "error" && (
          <div className="ai-error" role="alert">
            <Icon name="alert-circle" />
            <span className="grow">{m.error || "Qualcosa è andato storto."}</span>
            {prev && (
              <button type="button" className="btn sm" onClick={() => ctx.onAsk(prev.content, { scope: prev.scope })}>
                <Icon name="refresh" />
                Riprova
              </button>
            )}
          </div>
        )}
        {last && !live && m.suggestions.length > 0 && (
          <div className="ai-suggestions" aria-label="Suggerimenti">
            {m.suggestions.map((s) => (
              <button key={s} type="button" className="ai-chip" onClick={() => ctx.onAsk(s, { chapterId: m.scope.chapter_id ?? undefined })}>
                {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function MessageView({ m, prev, last, canUndo, ctx }: { m: Message; prev: Message | null; last: boolean; canUndo: boolean; ctx: MessageCtx }) {
  return m.role === "user" ? <UserMessage m={m} ctx={ctx} /> : <AssistantMessage m={m} prev={prev} last={last} canUndo={canUndo} ctx={ctx} />;
}
