// The small toolbar that floats next to the blocks picked in the draft.
import { useLayoutEffect, useRef, useState } from "react";
import type { DraftSelection } from "./selection";

/** remove: take it out at once · explain / fix: open the chat on this text and wait for the user's question. */
export type SelectionAction = "remove" | "explain" | "fix";

const ACTIONS: { key: SelectionAction; label: string; title: string }[] = [
  { key: "remove", label: "Rimuovi", title: "Toglie questo passaggio dal testo, sistemando la formattazione intorno" },
  { key: "explain", label: "Spiega", title: "Apre la chat su questo passaggio: poi fai la tua domanda" },
  { key: "fix", label: "Correggi", title: "Apre la chat su questo passaggio: poi dici cosa correggere" },
];

export default function SelectionToolbar({
  sel,
  onAction,
  onPress,
}: {
  sel: DraftSelection;
  onAction: (a: SelectionAction) => void;
  /** The pointer went down on the toolbar. */
  onPress?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; below: boolean } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const vw = window.innerWidth;
    const touch = window.matchMedia("(pointer: coarse)").matches;
    const gap = 10;
    // On touch screens the system's own menu sits above the selection: go below it.
    let below = touch;
    let top = below ? sel.rect.bottom + gap + 18 : sel.rect.top - h - gap;
    if (!below && top < 8) {
      below = true;
      top = sel.rect.bottom + gap;
    }
    if (below && top + h > window.innerHeight - 8) top = Math.max(8, sel.rect.top - h - gap);
    const center = (sel.rect.left + sel.rect.right) / 2;
    const left = Math.min(Math.max(8, center - w / 2), Math.max(8, vw - w - 8));
    setPos({ top, left, below });
  }, [sel.rect.top, sel.rect.bottom, sel.rect.left, sel.rect.right]);

  return (
    <div
      ref={ref}
      className={`sel-toolbar ${pos?.below ? "below" : ""}`}
      data-testid="selection-toolbar"
      role="toolbar"
      aria-label="Chiedi all'AI su questo testo"
      style={{ top: pos?.top ?? -1000, left: pos?.left ?? 0, visibility: pos ? "visible" : "hidden" }}
      onPointerDown={(e) => {
        onPress?.();
        e.preventDefault(); // keep the focus where it is
      }}
      onMouseDown={(e) => e.preventDefault()}
    >
      {ACTIONS.map((a) => (
        <button key={a.key} type="button" className="sel-btn" title={a.title} onClick={() => onAction(a.key)}>
          {a.label}
        </button>
      ))}
    </div>
  );
}
