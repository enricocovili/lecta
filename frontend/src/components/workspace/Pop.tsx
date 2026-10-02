import { useEffect, useRef, useState, type ReactNode } from "react";

/** A button with a small popover menu; closes on outside click, Escape and after picking an entry. */
export default function Pop({
  summary,
  label,
  className = "",
  menuClass = "",
  children,
}: {
  summary: ReactNode;
  label: string;
  className?: string;
  menuClass?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open]);
  return (
    <div className="pop" ref={ref}>
      <button type="button" className={`btn ${className}`} aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label} onClick={() => setOpen(!open)}>
        {summary}
      </button>
      {open && (
        <div className={`menu-pop ${menuClass}`} role="menu" onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  );
}
