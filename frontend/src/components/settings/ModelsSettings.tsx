import { useEffect, useState } from "react";
import { get, put } from "../../lib/api";
import { Icon } from "../icons";
import { Loading, toast, toastError } from "../ui";
import { ROLE_IT, SetCard } from "./common";
import type { ProviderRow } from "./ProvidersSettings";

type Target = { provider_id: number | null; model: string | null };
type Assignment = { primary: Target; fallback: Target };
type Roles = Record<string, Assignment>;

function TargetPicker({ value, providers, onChange, label }: { value: Target; providers: ProviderRow[]; onChange: (t: Target) => void; label: string }) {
  const usable = providers.filter((p) => p.usable);
  const prov = providers.find((p) => p.id === value.provider_id);
  return (
    <div className="set-target">
      <select
        aria-label={`${label}: provider`}
        value={value.provider_id ?? ""}
        onChange={(e) => {
          const id = e.target.value ? Number(e.target.value) : null;
          const p = providers.find((x) => x.id === id);
          onChange({ provider_id: id, model: p?.models[0]?.id ?? null });
        }}
      >
        <option value="">— nessuno —</option>
        {usable.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <input
        type="text"
        className="mono"
        aria-label={`${label}: modello`}
        list={`models-${value.provider_id}`}
        value={value.model ?? ""}
        placeholder="ID modello"
        disabled={!value.provider_id}
        onChange={(e) => onChange({ ...value, model: e.target.value || null })}
      />
      {prov && (
        <datalist id={`models-${prov.id}`}>
          {prov.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label ?? m.id}
            </option>
          ))}
        </datalist>
      )}
    </div>
  );
}

export default function ModelsSettings() {
  const [roles, setRoles] = useState<Roles | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([get<Record<string, unknown>>("/api/settings"), get<ProviderRow[]>("/api/providers")])
      .then(([s, p]) => {
        setRoles(s.roles as Roles);
        const meta = s.roles_meta as { roles: string[]; labels: Record<string, string> };
        setLabels(meta.labels);
        setOrder(meta.roles);
        setProviders(p);
      })
      .catch(toastError);
  }, []);

  if (!roles) return <Loading />;
  const set = (role: string, slot: "primary" | "fallback", t: Target) => setRoles({ ...roles, [role]: { ...roles[role], [slot]: t } });
  const save = async () => {
    setSaving(true);
    try {
      setRoles(await put<Roles>("/api/settings/roles", roles));
      toast("Assegnazioni salvate");
    } catch (e) {
      toastError(e);
    } finally {
      setSaving(false);
    }
  };
  const useFake = () => {
    const fake = providers.find((p) => p.type === "fake" && p.usable);
    if (!fake) return toast("Aggiungi prima un provider Fake (sezione Provider)", "error");
    const next: Roles = { ...roles };
    for (const r of order) next[r] = { primary: { provider_id: fake.id, model: fake.models[0]?.id ?? "fake-large" }, fallback: { provider_id: null, model: null } };
    setRoles(next);
  };
  return (
    <div className="set-stack">
      <SetCard
        title="Ruoli"
        flush
        aside={
          <button type="button" className="btn sm ghost" onClick={useFake}>
            <Icon name="sparkles" />
            Usa il provider Fake ovunque
          </button>
        }
        actions={
          <button type="button" className="btn primary" onClick={save} disabled={saving}>
            Salva assegnazioni
          </button>
        }
      >
        <p className="set-help set-pad">
          Puoi scegliere solo provider attivi. La riserva si usa quando il principale non risponde o dà errore.
        </p>
        <p className="set-help set-pad">
          L’assistente AI usa degli strumenti (legge e modifica i file del corso): serve un modello che li supporti, per esempio Claude, GPT-4 o superiore, Gemini.
        </p>
        <div className="set-roles">
          <div className="set-roles-head" aria-hidden="true">
            <span>Ruolo</span>
            <span>Principale</span>
            <span>Riserva (facoltativa)</span>
          </div>
          {order.map((role) => {
            const name = ROLE_IT[role] ?? labels[role] ?? role;
            return (
              <div key={role} className="set-roles-row">
                <div className="set-roles-name">
                  <strong>{name}</strong>
                  <span className="tiny faint mono">{role}</span>
                </div>
                <div>
                  <div className="set-roles-slot">Principale</div>
                  <TargetPicker label={`${name}, principale`} value={roles[role].primary} providers={providers} onChange={(t) => set(role, "primary", t)} />
                </div>
                <div>
                  <div className="set-roles-slot">Riserva (facoltativa)</div>
                  <TargetPicker label={`${name}, riserva`} value={roles[role].fallback} providers={providers} onChange={(t) => set(role, "fallback", t)} />
                </div>
              </div>
            );
          })}
        </div>
      </SetCard>
    </div>
  );
}
