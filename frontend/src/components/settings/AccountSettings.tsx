import { useEffect, useState } from "react";
import { del, fmtDate, get, patch, post } from "../../lib/api";
import { Icon } from "../icons";
import { passkeyError, passkeysSupported, registerPasskey } from "../../lib/passkey";
import { Confirm, fmtWhen, StatusPill, toast, toastError, useApi } from "../ui";
import { SetCard } from "./common";

interface Me {
  authenticated: boolean;
  username: string;
  totp_enabled: boolean;
}

interface PasskeyRow {
  id: number;
  name: string;
  synced: boolean;
  created_at: string;
  last_used_at: string | null;
}

interface SessionRow {
  id: string;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  ip: string | null;
  user_agent: string | null;
}

/** "Firefox · Linux" from a user-agent string (best effort; the full string is in the tooltip). */
function describeUA(ua: string | null): string {
  if (!ua) return "Browser sconosciuto";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : /curl|python|node|playwright/i.test(ua)
            ? "Script"
            : "Browser";
  const os = /Android/.test(ua)
    ? "Android"
    : /iPhone|iPad/.test(ua)
      ? "iOS"
      : /Mac OS X/.test(ua)
        ? "macOS"
        : /Windows/.test(ua)
          ? "Windows"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return os ? `${browser} · ${os}` : browser;
}

export default function AccountSettings() {
  const me = useApi(() => get<Me>("/api/auth/me"));
  const sessions = useApi(() => get<SessionRow[]>("/api/account/sessions"));
  const passkeys = useApi(() => get<PasskeyRow[]>("/api/account/passkeys"));
  const [passkeyName, setPasskeyName] = useState("");
  const [canPasskey, setCanPasskey] = useState(true); // known only in the browser (the page is rendered on the server first)
  useEffect(() => setCanPasskey(passkeysSupported()), []);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<PasskeyRow | null>(null);
  const [renaming, setRenaming] = useState<{ id: number; name: string } | null>(null);
  const passkeyList = passkeys.data ?? [];

  const addPasskey = async () => {
    setAdding(true);
    try {
      await registerPasskey(passkeyName.trim());
      setPasskeyName("");
      toast("Passkey aggiunta: ora puoi accedere senza password");
      passkeys.reload();
    } catch (e) {
      toast(passkeyError(e), "error");
    } finally {
      setAdding(false);
    }
  };
  const rename = async () => {
    if (!renaming || !renaming.name.trim()) return setRenaming(null);
    try {
      await patch(`/api/account/passkeys/${renaming.id}`, { name: renaming.name.trim() });
      setRenaming(null);
      passkeys.reload();
    } catch (e) {
      toastError(e);
    }
  };
  const [pw, setPw] = useState({ current_password: "", new_password: "", repeat: "" });
  const [totpSetup, setTotpSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const [disable, setDisable] = useState({ password: "", code: "" });
  const [allSessions, setAllSessions] = useState(false);
  const sessionList = sessions.data ?? [];
  const SHOWN = 6;

  const changePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (pw.new_password !== pw.repeat) return toast("Le password non coincidono", "error");
    try {
      await post("/api/account/password", { current_password: pw.current_password, new_password: pw.new_password });
      setPw({ current_password: "", new_password: "", repeat: "" });
      toast("Password cambiata; le altre sessioni sono state disconnesse");
      sessions.reload();
    } catch (err) {
      toastError(err);
    }
  };

  const initials = (me.data?.username ?? "")
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");

  return (
    <div className="set-stack">
      <div className="card set-me">
        <span className="avatar set-me-avatar" aria-hidden="true">
          {initials || "A"}
        </span>
        <div className="grow">
          <div className="set-me-name">{me.data?.username ?? "…"}</div>
          <div className="small muted">Amministratore · accesso con password{me.data?.totp_enabled ? " e codice 2FA" : ""}</div>
        </div>
        {me.data && (me.data.totp_enabled ? <StatusPill tone="ok" label="2FA attiva" size="sm" /> : <StatusPill tone="warn" label="2FA non attiva" size="sm" />)}
      </div>

      <form onSubmit={changePassword}>
        <SetCard
          title="Password"
          actions={
            <button className="btn primary" type="submit" disabled={!pw.current_password || pw.new_password.length < 12}>
              Cambia password
            </button>
          }
        >
          <div className="form-grid">
            <label className="field">
              Password attuale
              <input
                type="password"
                autoComplete="current-password"
                value={pw.current_password}
                onChange={(e) => setPw({ ...pw, current_password: e.target.value })}
              />
            </label>
            <label className="field">
              Nuova password <span className="hint">almeno 12 caratteri</span>
              <input type="password" autoComplete="new-password" minLength={12} value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
            </label>
            <label className="field">
              Ripeti la nuova password
              <input type="password" autoComplete="new-password" minLength={12} value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })} />
            </label>
          </div>
          <p className="set-help">Cambiando la password vengono chiuse tutte le altre sessioni.</p>
        </SetCard>
      </form>

      <SetCard title={`Passkey · accesso senza password${passkeyList.length ? ` · ${passkeyList.length}` : ""}`}>
        <div className="set-stack" style={{ gap: ".9rem" }} data-testid="passkeys">
          <p className="set-help" style={{ margin: 0 }}>
            Una passkey ti fa entrare con l'impronta, il PIN o il tuo gestore di password (per esempio Bitwarden), senza digitare password né codice 2FA. La password resta come alternativa.
          </p>
          {passkeyList.length > 0 && (
            <div className="rows">
              {passkeyList.map((p) => (
                <div key={p.id} className="set-session" data-testid="passkey-row">
                  <span className="set-session-ico">
                    <Icon name="key" />
                  </span>
                  <div className="grow" style={{ minWidth: 0 }}>
                    {renaming?.id === p.id ? (
                      <input
                        type="text"
                        value={renaming.name}
                        maxLength={100}
                        autoFocus
                        aria-label="Nome della passkey"
                        onChange={(e) => setRenaming({ id: p.id, name: e.target.value })}
                        onBlur={rename}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void rename();
                          if (e.key === "Escape") setRenaming(null);
                        }}
                      />
                    ) : (
                      <div className="set-session-title">
                        <strong>{p.name}</strong>
                        {p.synced && <span className="badge accent">sincronizzata</span>}
                      </div>
                    )}
                    <div className="small muted">
                      aggiunta il {fmtDate(p.created_at, true)} · {p.last_used_at ? `usata il ${fmtDate(p.last_used_at, true)}` : "mai usata"}
                    </div>
                  </div>
                  <button className="btn ghost icon sm" onClick={() => setRenaming({ id: p.id, name: p.name })} aria-label={`Rinomina ${p.name}`} title="Rinomina">
                    <Icon name="pencil" />
                  </button>
                  <button className="btn ghost icon sm" onClick={() => setRemoving(p)} aria-label={`Elimina ${p.name}`} title="Elimina" data-testid="passkey-remove">
                    <Icon name="trash" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {canPasskey ? (
            <form
              className="set-totp-confirm"
              onSubmit={(e) => {
                e.preventDefault();
                void addPasskey();
              }}
            >
              <label className="field">
                Nome <span className="hint">per riconoscerla (facoltativo)</span>
                <input type="text" value={passkeyName} maxLength={100} placeholder="Bitwarden" onChange={(e) => setPasskeyName(e.target.value)} data-testid="passkey-name" />
              </label>
              <button className="btn primary" type="submit" disabled={adding} data-testid="passkey-add">
                <Icon name={adding ? "loader" : "key"} className={adding ? "spin" : ""} />
                Aggiungi una passkey
              </button>
            </form>
          ) : (
            <div className="alert warn small">
              <Icon name="alert-triangle" />
              <span>Questo browser non supporta le passkey, oppure la pagina non è su HTTPS.</span>
            </div>
          )}
        </div>
      </SetCard>
      {removing && (
        <Confirm
          title="Elimina la passkey"
          danger
          confirmLabel="Elimina"
          message={<>«{removing.name}» non potrà più essere usata per accedere. Se è l'unico modo che usi, ti resta la password.</>}
          onConfirm={async () => {
            await del(`/api/account/passkeys/${removing.id}`);
            passkeys.reload();
          }}
          onClose={() => setRemoving(null)}
        />
      )}

      <SetCard title="Sicurezza · verifica in due passaggi (2FA)">
        {me.data?.totp_enabled ? (
          <div className="set-stack" style={{ gap: ".9rem" }}>
            <div className="alert ok">
              <Icon name="shield" />
              <span>La verifica in due passaggi è attiva: all'accesso ti chiediamo anche il codice dell'app di autenticazione.</span>
            </div>
            <div className="form-grid">
              <label className="field">
                Password
                <input type="password" autoComplete="current-password" value={disable.password} onChange={(e) => setDisable({ ...disable, password: e.target.value })} />
              </label>
              <label className="field">
                Codice attuale
                <input type="text" inputMode="numeric" autoComplete="one-time-code" value={disable.code} onChange={(e) => setDisable({ ...disable, code: e.target.value })} />
              </label>
            </div>
            <div className="set-actions inline">
              <button
                className="btn danger"
                disabled={!disable.password || !disable.code}
                onClick={async () => {
                  try {
                    await post("/api/account/totp/disable", disable);
                    setDisable({ password: "", code: "" });
                    toast("2FA disattivata");
                    me.reload();
                  } catch (e) {
                    toastError(e);
                  }
                }}
              >
                Disattiva 2FA
              </button>
            </div>
          </div>
        ) : totpSetup ? (
          <div className="set-totp">
            <div className="set-totp-qr" dangerouslySetInnerHTML={{ __html: totpSetup.qr_svg }} />
            <div className="set-stack" style={{ gap: ".75rem" }}>
              <ol className="set-steps-list">
                <li>Inquadra il codice QR con la tua app di autenticazione.</li>
                <li>
                  Oppure inserisci il segreto a mano: <code className="set-secret">{totpSetup.secret}</code>
                </li>
                <li>Scrivi qui il codice a 6 cifre per confermare.</li>
              </ol>
              <form
                className="set-totp-confirm"
                onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    await post("/api/account/totp/enable", { code });
                    setTotpSetup(null);
                    setCode("");
                    toast("2FA attivata");
                    me.reload();
                  } catch (err) {
                    toastError(err);
                  }
                }}
              >
                <label className="field">
                  Codice 2FA
                  <input
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    className="mono"
                    placeholder="123456"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                </label>
                <button className="btn primary" type="submit" disabled={!code.trim()}>
                  Attiva
                </button>
                <button className="btn ghost" type="button" onClick={() => setTotpSetup(null)}>
                  Annulla
                </button>
              </form>
            </div>
          </div>
        ) : (
          <div className="set-stack" style={{ gap: ".9rem" }}>
            <p className="set-help" style={{ margin: 0 }}>
              Aggiungi un codice temporaneo (TOTP) all'accesso, generato da un'app come Aegis, 2FAS o Google Authenticator.
            </p>
            <div className="set-actions inline">
              <button
                className="btn primary"
                onClick={async () => {
                  try {
                    setTotpSetup(await post("/api/account/totp/setup"));
                  } catch (e) {
                    toastError(e);
                  }
                }}
              >
                <Icon name="lock" />
                Configura la 2FA
              </button>
            </div>
          </div>
        )}
      </SetCard>

      <SetCard
        title={`Sessioni attive · ${sessionList.length}`}
        flush
        actions={
          sessionList.length > SHOWN ? (
            <button type="button" className="btn sm ghost" onClick={() => setAllSessions(!allSessions)}>
              <Icon name={allSessions ? "chevron-up" : "chevron-down"} />
              {allSessions ? "Mostra meno" : `Mostra tutte (${sessionList.length})`}
            </button>
          ) : undefined
        }
      >
        <div className="rows">
          {(allSessions ? sessionList : [...sessionList].sort((a, b) => Number(b.current) - Number(a.current)).slice(0, SHOWN)).map((s) => (
            <div key={s.id} className="set-session">
              <span className="set-session-ico">
                <Icon name={/Android|iPhone|iPad|Mobile/.test(s.user_agent ?? "") ? "smartphone" : "monitor"} />
              </span>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="set-session-title">
                  <strong title={s.user_agent ?? ""}>{describeUA(s.user_agent)}</strong>
                  {s.current && <span className="badge accent">questa sessione</span>}
                </div>
                <div className="small muted ellipsis">
                  <span className="mono">{s.ip ?? "—"}</span> · aperta il {fmtDate(s.created_at, true)} · <span className="mono">{s.id}</span>
                </div>
              </div>
              <span className="mono small muted nowrap" title={fmtDate(s.last_seen_at, true)}>
                {fmtWhen(s.last_seen_at)}
              </span>
            </div>
          ))}
        </div>
      </SetCard>
    </div>
  );
}
