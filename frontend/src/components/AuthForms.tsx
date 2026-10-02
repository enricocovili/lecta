import { useEffect, useState } from "react";
import { post } from "../lib/api";
import { passkeyError, passkeysSupported, signInWithPasskey } from "../lib/passkey";
import { Icon } from "./icons";

function FormError({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="alert danger" role="alert">
      <Icon name="alert-circle" />
      <span>{error}</span>
    </div>
  );
}

export function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Known only in the browser (the page is rendered on the server first): set after the first render.
  const [canPasskey, setCanPasskey] = useState(false);
  useEffect(() => setCanPasskey(passkeysSupported()), []);

  const enter = () => {
    const next = new URLSearchParams(location.search).get("next");
    location.href = next && next.startsWith("/admin") ? next : "/admin";
  };

  const withPasskey = async () => {
    setBusy(true);
    setError(null);
    try {
      await signInWithPasskey();
      enter();
    } catch (err) {
      setError(passkeyError(err));
      setBusy(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ ok: boolean; totp_required?: boolean }>("/api/auth/login", {
        username,
        password,
        totp: needTotp ? totp : undefined,
      });
      if (r.totp_required) {
        setNeedTotp(true);
      } else if (r.ok) {
        enter();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card auth-card" onSubmit={submit}>
      <h1 className="auth-title">{needTotp ? "Verifica in due passaggi" : "Accedi"}</h1>
      <p className="auth-sub">{needTotp ? "Inserisci il codice a 6 cifre della tua app di autenticazione." : "Entra per gestire i tuoi appunti."}</p>
      <FormError error={error} />
      {!needTotp ? (
        <>
          <label className="field">
            Nome utente
            <input type="text" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
          </label>
          <label className="field">
            Password
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
        </>
      ) : (
        <label className="field">
          Codice 2FA
          <input
            type="text"
            className="auth-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9 ]*"
            placeholder="123 456"
            value={totp}
            onChange={(e) => setTotp(e.target.value)}
            required
            autoFocus
          />
        </label>
      )}
      <button className="btn primary lg auth-submit" disabled={busy} type="submit">
        {busy ? "Accesso in corso…" : needTotp ? "Verifica" : "Accedi"}
      </button>
      {!needTotp && canPasskey && (
        <button className="btn lg auth-submit" type="button" disabled={busy} onClick={withPasskey} data-testid="passkey-login">
          <Icon name="key" />
          Accedi con una passkey
        </button>
      )}
      {needTotp && (
        <button
          type="button"
          className="btn ghost sm auth-back"
          onClick={() => {
            setNeedTotp(false);
            setTotp("");
            setError(null);
          }}
        >
          <Icon name="arrow-left" />
          Cambia utente
        </button>
      )}
    </form>
  );
}

const STEPS = ["Verifica del server", "Account amministratore"];

export function SetupWizard() {
  const [step, setStep] = useState(1);
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [siteTitle, setSiteTitle] = useState("Lecta");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const finish = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== password2) {
      setError("Le password non coincidono");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post("/api/setup", { setup_code: code, username, password, site_title: siteTitle });
      location.href = "/admin";
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card auth-card">
      <ol className="auth-steps" aria-label={`Passo ${step} di ${STEPS.length}`}>
        {STEPS.map((label, i) => {
          const n = i + 1;
          const state = n < step ? "done" : n === step ? "current" : "";
          return (
            <li key={label} className={state} aria-current={n === step ? "step" : undefined}>
              <span className="auth-step-n">{n < step ? <Icon name="check" /> : n}</span>
              <span className="auth-step-label">{label}</span>
            </li>
          );
        })}
      </ol>
      <h1 className="auth-title">Benvenuto in Lecta</h1>
      <FormError error={error} />
      {step === 1 ? (
        <form
          className="auth-form"
          onSubmit={(e) => {
            e.preventDefault();
            setStep(2);
          }}
        >
          <p className="auth-sub">
            Per dimostrare che il server è tuo, inserisci il codice di configurazione monouso. Lo trovi nel log del backend e nel volume dei dati:
          </p>
          <pre className="auth-pre">
            docker compose logs backend | grep "setup code"{"\n"}docker compose exec backend cat /data/secrets/setup-code
          </pre>
          <label className="field">
            Codice di configurazione
            <input type="text" className="mono" autoCapitalize="none" spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} placeholder="xxxxxx-xxxxxx-xxxxxx" required autoFocus />
          </label>
          <button className="btn primary lg auth-submit" type="submit">
            Continua
            <Icon name="arrow-right" />
          </button>
        </form>
      ) : (
        <form className="auth-form" onSubmit={finish}>
          <p className="auth-sub">Scegli come si chiama il sito e crea l'account con cui accederai.</p>
          <label className="field">
            Titolo del sito
            <input type="text" value={siteTitle} onChange={(e) => setSiteTitle(e.target.value)} />
          </label>
          <label className="field">
            Nome utente amministratore
            <input
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              pattern="[A-Za-z0-9._@\-]+"
            />
          </label>
          <label className="field">
            Password <span className="hint">almeno 12 caratteri</span>
            <input type="password" autoComplete="new-password" minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <label className="field">
            Ripeti la password
            <input type="password" autoComplete="new-password" minLength={12} value={password2} onChange={(e) => setPassword2(e.target.value)} required />
          </label>
          <button className="btn primary lg auth-submit" disabled={busy} type="submit">
            {busy ? "Creazione in corso…" : "Crea account amministratore"}
          </button>
          <button className="btn ghost sm auth-back" type="button" onClick={() => setStep(1)}>
            <Icon name="arrow-left" />
            Indietro
          </button>
          <p className="auth-foot">Potrai attivare la verifica in due passaggi più tardi da Impostazioni → Account.</p>
        </form>
      )}
    </div>
  );
}
