// Course settings (name, address, language, LaTeX engine, preamble, delete), opened from the workspace menu.
import { useState } from "react";
import { get, patch, post } from "../../lib/api";
import { LANGUAGES } from "../CoursesBrowser";
import { Icon } from "../icons";
import type { Course } from "../types";
import { Confirm, Switch, toast, toastError } from "../ui";
import GuidelinesField from "../lessons/GuidelinesField";

export default function CourseSettings({ course, onSaved }: { course: Course; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: course.name,
    slug: course.slug,
    academic_year: course.academic_year ?? "",
    language: course.language,
    tags: course.tags.join(", "),
    description: course.description ?? "",
    engine: course.engine ?? "default",
  });
  const [guidelines, setGuidelines] = useState(course.guidelines ?? "");
  const [override, setOverride] = useState<string>(course.preamble_override ?? "");
  const [useOverride, setUseOverride] = useState<boolean>(!!course.preamble_override);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmSlug, setConfirmSlug] = useState("");
  const [busy, setBusy] = useState(false);

  const loadGlobal = async () => {
    const t = await get<{ preamble: string }>("/api/settings/template/effective").catch(() => null);
    if (t) setOverride(t.preamble);
  };

  const save = async () => {
    setBusy(true);
    try {
      await patch(`/api/courses/${course.id}`, {
        ...form,
        academic_year: form.academic_year || null,
        description: form.description || null,
        guidelines: guidelines.trim(),
        tags: form.tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        ...(useOverride ? { preamble_override: override } : { clear_preamble_override: true }),
      });
      toast("Impostazioni salvate");
      window.dispatchEvent(new Event("lecta:tree-changed"));
      onSaved();
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pg-settings">
      <div className="section-label">Generali</div>
      <div className="card pg-pad stack">
        <div className="form-grid">
          <label className="field">
            Nome
            <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">
            Slug <span className="hint">usato negli indirizzi pubblici</span>
            <input type="text" className="mono" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} />
          </label>
          <label className="field">
            Anno accademico
            <input type="text" value={form.academic_year} onChange={(e) => setForm({ ...form, academic_year: e.target.value })} />
          </label>
          <label className="field">
            Lingua <span className="hint">per gli appunti generati e la ricerca</span>
            <select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Motore LaTeX
            <select value={form.engine} onChange={(e) => setForm({ ...form, engine: e.target.value })}>
              <option value="default">Predefinito (globale)</option>
              <option value="pdflatex">pdflatex</option>
              <option value="xelatex">xelatex</option>
              <option value="lualatex">lualatex</option>
            </select>
          </label>
          <label className="field">
            Tag <span className="hint">separati da virgola</span>
            <input type="text" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} />
          </label>
        </div>
        <label className="field">
          Descrizione <span className="hint">mostrata sul sito pubblico</span>
          <textarea className="prose" rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </label>
      </div>

      <div className="section-label">Linee guida di scrittura</div>
      <div className="card pg-pad stack">
        <div className="pg-row-sub">
          Come vuoi che Lecta scriva il testo di questa materia. Ti vengono richieste ogni volta che generi un testo (da una lezione o da un caricamento), e
          l’assistente le tiene presenti quando scrive.
        </div>
        <GuidelinesField value={guidelines} onChange={setGuidelines} subject={course.name} rows={5} />
      </div>

      <div className="section-label">Preambolo</div>
      <div className="card pg-pad stack">
        <div className="row between">
          <div className="grow">
            <div className="pg-row-title">Preambolo personalizzato</div>
            <div className="pg-row-sub">Sostituisce il preambolo globale solo per questa materia.</div>
          </div>
          <Switch
            checked={useOverride}
            label={<span className="sr-only">Sostituisci il preambolo globale per questa materia</span>}
            onChange={(v) => {
              setUseOverride(v);
              if (v && !override) loadGlobal();
            }}
          />
        </div>
        {useOverride && <textarea rows={16} value={override} onChange={(e) => setOverride(e.target.value)} spellCheck={false} aria-label="Preambolo" />}
      </div>
      <div className="row" style={{ marginTop: "1.25rem" }}>
        <button className="btn primary" onClick={save} disabled={busy}>
          <Icon name="check" />
          Salva impostazioni
        </button>
      </div>

      <div className="section-label">Zona pericolosa</div>
      <div className="card pg-pad pg-danger-zone">
        <div className="grow">
          <div className="pg-row-title">Elimina la materia</div>
          <div className="pg-row-sub">Capitoli e PDF pubblico vengono cancellati. Le sorgenti caricate restano.</div>
        </div>
        <button className="btn danger" onClick={() => setConfirmDelete(true)}>
          <Icon name="trash" />
          Elimina materia…
        </button>
      </div>
      {confirmDelete && (
        <Confirm
          title="Elimina materia"
          danger
          confirmLabel="Elimina definitivamente"
          message={
            <div className="stack">
              <div>
                Vengono eliminati la materia, i capitoli e il PDF pubblico. Le sorgenti caricate restano. Scrivi <code>{course.slug}</code> per
                confermare.
              </div>
              <input type="text" value={confirmSlug} onChange={(e) => setConfirmSlug(e.target.value)} aria-label="Slug di conferma" />
            </div>
          }
          onConfirm={async () => {
            await post(`/api/courses/${course.id}/delete`, { confirm_slug: confirmSlug });
            location.href = "/admin/courses";
          }}
          onClose={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}
