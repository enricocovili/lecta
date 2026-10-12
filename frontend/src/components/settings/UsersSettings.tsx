import { useState, type ReactNode } from "react";
import { del, fmtDate, get, patch, post } from "../../lib/api";
import { ROLE_HINT, ROLE_LABEL, type Role } from "../../lib/roles";
import { Icon } from "../icons";
import { Confirm, fmtWhen, toast, toastError, useApi } from "../ui";
import { SetCard } from "./common";

interface UserRow {
  id: number;
  username: string;
  role: Role;
  created_at: string;
  last_seen_at: string | null;
  passkeys: number;
  has_password: boolean;
  disabled_at: string | null;
  courses: number;
  you: boolean;
}

interface InviteRow {
  id: number;
  role: Role;
  note: string | null;
  user_id: number | null;
  recovery: boolean;
  username: string | null;
  created_at: string;
  expires_at: string;
}

const ROLES = Object.keys(ROLE_LABEL) as Role[];

/** A link shown this once (only its hash is kept), with a copy button. */
function OneTimeLink({ url, label, children, onDone, testid }: { url: string; label: string; children: ReactNode; onDone: () => void; testid: string }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copiato");
    } catch {
      toast("Seleziona il link e copialo a mano", "error");
    }
  };
  return (
    <div className="set-stack" style={{ gap: ".6rem" }} data-testid={`${testid}-link`}>
      <div className="alert warn small">
        <Icon name="alert-triangle" />
        <span>{children}</span>
      </div>
      <div className="row nowrap les-share-link">
        <input type="text" readOnly value={url} onFocus={(e) => e.currentTarget.select()} aria-label={label} data-testid={`${testid}-url`} />
        <button className="btn sm" type="button" onClick={copy}>
          <Icon name="copy" />
          Copia
        </button>
        <button className="btn sm ghost" type="button" onClick={onDone}>
          Fatto
        </button>
      </div>
    </div>
  );
}

/** Settings → Utenti (admins): invite links, the users with their role, recovery links, disabling, signing out and deleting. */
export default function UsersSettings() {
  const users = useApi(() => get<UserRow[]>("/api/users"));
  const invites = useApi(() => get<InviteRow[]>("/api/invites"));
  const [form, setForm] = useState<{ role: Role; note: string }>({ role: "editor", note: "" });
  const [creating, setCreating] = useState(false);
  const [link, setLink] = useState<{ url: string; role: Role; note: string | null } | null>(null);
  const [recovery, setRecovery] = useState<{ userId: number; url: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<UserRow | null>(null);
  const [disabling, setDisabling] = useState<UserRow | null>(null);
  const userList = users.data ?? [];
  const inviteList = invites.data ?? [];

  const createInvite = async () => {
    setCreating(true);
    try {
      const inv = await post<InviteRow & { path: string }>("/api/invites", { role: form.role, note: form.note.trim() || null });
      setLink({ url: location.origin + inv.path, role: inv.role, note: inv.note });
      setForm({ ...form, note: "" });
      invites.reload();
    } catch (e) {
      toastError(e);
    } finally {
      setCreating(false);
    }
  };
  const revoke = async (inv: InviteRow) => {
    try {
      await del(`/api/invites/${inv.id}`);
      invites.reload();
    } catch (e) {
      toastError(e);
    }
  };
  /** One change to a user. Acting on oneself may end this page (signed out, or no longer an admin): then go to `leave`. */
  const act = async (u: UserRow, call: () => Promise<unknown>, done: string, leave?: string) => {
    setBusy(u.id);
    try {
      await call();
      if (leave && u.you) return location.assign(leave);
      toast(done);
      users.reload();
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(null);
    }
  };

  /** A recovery link for a user who lost their passkey, shown once under their row. */
  const createRecovery = (u: UserRow) =>
    act(u, async () => {
      const inv = await post<InviteRow & { path: string }>(`/api/users/${u.id}/recovery`);
      setRecovery({ userId: u.id, url: location.origin + inv.path });
      invites.reload();
    }, `Link di recupero per ${u.username} creato`);

  return (
    <div className="set-stack">
      <SetCard
        title="Nuovo invito"
        actions={
          <button className="btn primary" type="button" disabled={creating} onClick={createInvite} data-testid="invite-create">
            <Icon name={creating ? "loader" : "link"} className={creating ? "spin" : ""} />
            Crea il link
          </button>
        }
      >
        <div className="set-stack" style={{ gap: ".9rem" }}>
          <p className="set-help" style={{ margin: 0 }}>
            Non ci si iscrive da soli: chi apre il link sceglie un nome utente e crea una passkey, senza password. Il link vale sette giorni e una volta sola.
          </p>
          <div className="form-grid">
            <label className="field">
              Ruolo
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })} data-testid="new-invite-role">
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
              <span className="hint">{ROLE_HINT[form.role]}</span>
            </label>
            <label className="field">
              Nota <span className="hint">per ricordarti a chi l'hai dato (facoltativa)</span>
              <input type="text" value={form.note} maxLength={500} placeholder="Anna, analisi 2" onChange={(e) => setForm({ ...form, note: e.target.value })} data-testid="new-invite-note" />
            </label>
          </div>
          {link && (
            <OneTimeLink url={link.url} label="Link d'invito" onDone={() => setLink(null)} testid="invite">
              Copia il link ora e mandalo a chi deve entrare{link.note ? ` (${link.note})` : ""} come {ROLE_LABEL[link.role].toLowerCase()}: non lo mostreremo più.
            </OneTimeLink>
          )}
        </div>
      </SetCard>

      <SetCard title={`Inviti in attesa · ${inviteList.length}`} flush>
        {inviteList.length === 0 ? (
          <p className="set-help set-users-empty">Nessun invito in attesa.</p>
        ) : (
          <div className="rows" data-testid="invites">
            {inviteList.map((inv) => (
              <div key={inv.id} className="set-session" data-testid="invite-row">
                <span className="set-session-ico">
                  <Icon name={inv.recovery ? "key" : "link"} />
                </span>
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="set-session-title">
                    {inv.recovery ? (
                      <>
                        <strong>Recupero di {inv.username}</strong>
                        <span className="badge accent">link di recupero</span>
                      </>
                    ) : (
                      <>
                        <strong>{inv.note || "Invito senza nota"}</strong>
                        <span className="badge">{ROLE_LABEL[inv.role]}</span>
                      </>
                    )}
                  </div>
                  <div className="small muted">
                    creato il {fmtDate(inv.created_at, true)} · scade il {fmtDate(inv.expires_at, true)}
                  </div>
                </div>
                <button className="btn sm danger" type="button" onClick={() => revoke(inv)} data-testid="invite-revoke">
                  <Icon name="x" />
                  Revoca
                </button>
              </div>
            ))}
          </div>
        )}
      </SetCard>

      <SetCard title={`Utenti · ${userList.length}`} flush>
        <div className="rows" data-testid="users">
          {userList.map((u) => (
            <div key={u.id} className="set-session set-user" data-testid="user-row">
              <span className="set-session-ico">
                <Icon name="user" />
              </span>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="set-session-title">
                  <strong>{u.username}</strong>
                  {u.you && <span className="badge accent">tu</span>}
                  {u.disabled_at && <span className="badge danger">disattivato</span>}
                  {!u.has_password && <span className="badge">solo passkey</span>}
                </div>
                <div className="small muted">
                  dal {fmtDate(u.created_at)} · {u.last_seen_at ? <span title={fmtDate(u.last_seen_at, true)}>visto {fmtWhen(u.last_seen_at)}</span> : "mai entrato"} ·{" "}
                  {u.passkeys === 1 ? "1 passkey" : `${u.passkeys} passkey`} · {u.courses === 1 ? "1 materia" : `${u.courses} materie`}
                  {u.disabled_at && ` · disattivato il ${fmtDate(u.disabled_at)}`}
                </div>
              </div>
              <div className="set-user-actions">
                <select
                  aria-label={`Ruolo di ${u.username}`}
                  value={u.role}
                  disabled={busy === u.id}
                  onChange={(e) => {
                    const role = e.target.value as Role;
                    void act(u, () => patch(`/api/users/${u.id}`, { role }), `${u.username} ora è ${ROLE_LABEL[role].toLowerCase()}`, role === "admin" ? undefined : "/admin");
                  }}
                  data-testid="user-role"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </select>
                {u.disabled_at ? (
                  <button className="btn sm" type="button" disabled={busy === u.id} onClick={() => act(u, () => post(`/api/users/${u.id}/enable`), `${u.username} può di nuovo accedere`)} data-testid="user-enable">
                    Riattiva
                  </button>
                ) : (
                  <button className="btn sm" type="button" disabled={busy === u.id} onClick={() => setDisabling(u)} data-testid="user-disable">
                    Disattiva
                  </button>
                )}
                <button
                  className="btn sm ghost"
                  type="button"
                  disabled={busy === u.id || !!u.disabled_at}
                  title="Chiude tutte le sue sessioni"
                  onClick={() => act(u, () => post(`/api/users/${u.id}/logout`), `${u.username} è uscito da tutti i dispositivi`, "/login")}
                >
                  <Icon name="logout" />
                  <span className="hide-mobile">Esci ovunque</span>
                </button>
                <button
                  className="btn sm ghost"
                  type="button"
                  disabled={busy === u.id || !!u.disabled_at}
                  title="Un link per creare una nuova passkey, se ha perso la sua"
                  onClick={() => createRecovery(u)}
                  data-testid="user-recovery"
                >
                  <Icon name="key" />
                  <span className="hide-mobile">Link di recupero</span>
                </button>
                <button
                  className="btn sm ghost icon"
                  type="button"
                  disabled={busy === u.id || u.courses > 0 || u.you}
                  title={u.you ? "Non puoi eliminare il tuo account" : u.courses > 0 ? "Ha delle materie: puoi solo disattivarlo" : "Elimina"}
                  aria-label={`Elimina ${u.username}`}
                  onClick={() => setDeleting(u)}
                  data-testid="user-delete"
                >
                  <Icon name="trash" />
                </button>
              </div>
              {recovery?.userId === u.id && (
                <div className="set-user-link">
                  <OneTimeLink url={recovery.url} label={`Link di recupero di ${u.username}`} onDone={() => setRecovery(null)} testid="recovery">
                    Manda il link a {u.username}: aprendolo crea una nuova passkey per il suo account ed esce dagli altri dispositivi. Vale sette giorni e una volta
                    sola; non lo mostreremo più.
                  </OneTimeLink>
                </div>
              )}
            </div>
          ))}
        </div>
      </SetCard>

      {disabling && (
        <Confirm
          title={`Disattiva ${disabling.username}`}
          danger
          confirmLabel="Disattiva"
          message={
            disabling.you
              ? "Esci subito e non potrai più accedere finché un altro amministratore non ti riattiva. Le tue materie restano."
              : `${disabling.username} esce subito da tutti i dispositivi e non potrà più accedere finché non lo riattivi. Le sue materie restano.`
          }
          onConfirm={() => act(disabling, () => post(`/api/users/${disabling.id}/disable`), `${disabling.username} è disattivato`, "/login")}
          onClose={() => setDisabling(null)}
        />
      )}
      {deleting && (
        <Confirm
          title={`Elimina ${deleting.username}`}
          danger
          confirmLabel="Elimina"
          message={<>L'account, le sue passkey e le sue conversazioni con l'assistente vengono cancellati. Non si può annullare.</>}
          onConfirm={async () => {
            await del(`/api/users/${deleting.id}`);
            toast(`${deleting.username} è stato eliminato`);
            users.reload();
          }}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
