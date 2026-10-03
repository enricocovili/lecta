import { useCallback, useEffect, useState } from "react";
import { get } from "../lib/api";
import { Icon } from "./icons";
import { COURSE_LABEL, COURSE_TONE } from "./pagekit";
import type { TreeCourse } from "./types";
import { Dot, usePoll } from "./ui";

export interface Counts {
  jobs_active: number;
}

function NavItem({ href, icon, label, active, count, hot }: { href: string; icon: string; label: string; active: boolean; count?: number; hot?: boolean }) {
  return (
    <a href={href} className={`nav-item ${active ? "active" : ""}`} aria-current={active ? "page" : undefined}>
      <Icon name={icon} />
      <span className="grow">{label}</span>
      {count !== undefined && <span className={`count ${hot && count > 0 ? "hot" : ""}`}>{count}</span>}
    </a>
  );
}

export default function Sidebar({ currentPath }: { currentPath: string }) {
  const [tree, setTree] = useState<TreeCourse[] | null>(null);
  const [counts, setCounts] = useState<Counts | null>(null);

  const loadTree = useCallback(() => {
    get<TreeCourse[]>("/api/tree")
      .then(setTree)
      .catch(() => setTree((t) => t ?? []));
  }, []);
  const loadCounts = useCallback(() => {
    get<Counts>("/api/dashboard/counts")
      .then((c) => {
        setCounts(c);
        window.dispatchEvent(new CustomEvent("lecta:counts", { detail: c }));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    loadTree();
    loadCounts();
    const onTree = () => loadTree();
    const onCounts = () => loadCounts();
    window.addEventListener("lecta:tree-changed", onTree);
    window.addEventListener("lecta:counts-changed", onCounts);
    return () => {
      window.removeEventListener("lecta:tree-changed", onTree);
      window.removeEventListener("lecta:counts-changed", onCounts);
    };
  }, [loadTree, loadCounts]);
  usePoll(loadCounts, 15000);
  usePoll(loadTree, 30000);

  const is = (prefix: string) => currentPath === prefix || currentPath.startsWith(prefix + "/");

  return (
    <nav className="sidebar-nav" aria-label="Navigazione" style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div className="nav">
        <NavItem href="/admin/courses" icon="folder" label="Materie" active={is("/admin/courses")} />
        <NavItem href="/admin/lessons" icon="notebook" label="Lezioni" active={is("/admin/lessons")} />
        <NavItem href="/admin/map" icon="map" label="Mappa" active={is("/admin/map")} />
      </div>

      <div className="nav-group-title">
        <span>Le tue materie</span>
        <a href="/admin/courses?new=1" title="Nuova materia" aria-label="Nuova materia">
          <Icon name="plus" />
        </a>
      </div>
      <div className="tree">
        {tree === null ? (
          <div className="stack tight" style={{ padding: "0 .75rem" }}>
            <div className="skeleton" style={{ height: 18, width: "80%" }} />
            <div className="skeleton" style={{ height: 18, width: "65%" }} />
          </div>
        ) : tree.length === 0 ? (
          <div className="small muted" style={{ padding: "0 .75rem" }}>
            Nessuna materia. <a href="/admin/courses?new=1">Creane una</a>.
          </div>
        ) : (
          tree.map((c) => (
            <div key={c.id} className="tree-course">
              <a href={`/admin/courses/${c.id}`} title={c.name}>
                <span className="name">{c.name}</span>
                <Dot tone={COURSE_TONE[c.status ?? "ok"] ?? "ok"} title={COURSE_LABEL[c.status ?? "ok"]} />
              </a>
            </div>
          ))
        )}
      </div>
    </nav>
  );
}
