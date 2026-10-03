import { useEffect, useRef, useState } from "react";
import { get } from "../lib/api";
import { Icon } from "./icons";
import { courseText } from "../lib/links";

interface Result {
  files: { course_id: number; course_name: string; path: string; chapter_id: number | null; chapter_title: string | null; snippet: string; line: number | null }[];
  titles: { type: string; id: number; course_id?: number; title: string; course_name?: string }[];
  sources: { id: number; name: string; kind: string }[];
}

interface Hit {
  href: string;
  title: string;
  meta: string;
}

function hits(r: Result): Hit[] {
  const out: Hit[] = [];
  for (const t of r.titles.slice(0, 5)) {
    out.push({
      href: t.type === "course" ? `/admin/courses/${t.id}` : courseText(t.course_id!, { chapter: t.id }),
      title: t.title,
      meta: t.type === "course" ? "Materia" : `Capitolo · ${t.course_name ?? ""}`,
    });
  }
  for (const f of r.files.slice(0, 6)) {
    out.push({
      href: courseText(f.course_id, { chapter: f.chapter_id }),
      title: f.chapter_title ?? f.path,
      meta: `${f.course_name} · ${f.path}${f.line ? `:${f.line}` : ""}`,
    });
  }
  for (const s of r.sources.slice(0, 3)) out.push({ href: `/admin/sources/${s.id}`, title: s.name, meta: "Sorgente" });
  return out;
}

/** The search box of the top bar: live results as you type, "/" to focus, Enter for the full results page. */
export default function TopSearch() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Hit[] | null>(null);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, []);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setItems(null);
      return;
    }
    const n = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await get<Result>(`/api/search?q=${encodeURIComponent(query)}`);
        if (n === seq.current) {
          setItems(hits(r));
          setSel(0);
        }
      } catch {
        if (n === seq.current) setItems([]);
      }
    }, 220);
    return () => clearTimeout(t);
  }, [q]);

  const go = () => {
    const h = items?.[sel];
    location.href = h && open ? h.href : `/admin/search?q=${encodeURIComponent(q.trim())}`;
  };

  return (
    <div className="searchbox" ref={box}>
      <Icon name="search" />
      <input
        ref={input}
        type="search"
        placeholder="Cerca negli appunti, nelle materie e nelle sorgenti"
        aria-label="Cerca"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setOpen(false);
            input.current?.blur();
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            setSel((s) => Math.min((items?.length ?? 1) - 1, s + 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setSel((s) => Math.max(0, s - 1));
          } else if (e.key === "Enter" && q.trim()) {
            e.preventDefault();
            go();
          }
        }}
      />
      {!q && <kbd>/</kbd>}
      {open && items !== null && (
        <div className="search-results" role="listbox">
          {items.length === 0 ? (
            <div className="small muted" style={{ padding: ".6rem .7rem" }}>
              Nessun risultato per «{q.trim()}».
            </div>
          ) : (
            items.map((h, i) => (
              <a key={h.href + i} href={h.href} className={i === sel ? "active" : ""} onMouseEnter={() => setSel(i)} role="option" aria-selected={i === sel}>
                <span className="ellipsis">{h.title}</span>
                <span className="meta ellipsis">{h.meta}</span>
              </a>
            ))
          )}
          <a href={`/admin/search?q=${encodeURIComponent(q.trim())}`} className="small" style={{ color: "var(--accent)" }}>
            Tutti i risultati per «{q.trim()}» →
          </a>
        </div>
      )}
    </div>
  );
}
