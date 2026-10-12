// The text box of the draft, where a block is written in place and a note on a block is written: it grows with its
// text, Ctrl+Enter sends, Esc cancels; the bar under it says so, or what went wrong.
import { useEffect, useLayoutEffect, useRef } from "react";
import { Icon } from "../icons";

export function EditBox({
  value,
  onChange,
  label,
  disabled,
  autoFocus = false,
  prose = false,
  placeholder,
  onSubmit,
  onCancel,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  disabled?: boolean;
  /** Focused when shown, the caret at the end. */
  autoFocus?: boolean;
  /** Words rather than LaTeX: the text's own font. */
  prose?: boolean;
  placeholder?: string;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const area = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value]);
  useEffect(() => {
    const el = area.current;
    if (!autoFocus || !el) return;
    el.focus({ preventScroll: true });
    el.setSelectionRange(el.value.length, el.value.length);
  }, [autoFocus]);

  return (
    <textarea
      ref={area}
      className={prose ? "prose" : undefined}
      value={value}
      spellCheck
      aria-label={label}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onCancel();
        } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          onSubmit();
        }
      }}
    />
  );
}

export function EditBar({
  hint,
  error,
  busy,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  hint: string;
  error: string | null;
  busy: boolean;
  submitLabel: string;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="doc-edit-bar">
      {error ? (
        <span className="doc-edit-error small" role="alert">
          <Icon name="alert-triangle" /> {error}
        </span>
      ) : (
        <span className="muted small">{hint}</span>
      )}
      <button type="button" className="btn ghost sm" disabled={busy} onClick={onCancel}>
        Annulla
      </button>
      <button type="button" className="btn primary sm" disabled={busy} onClick={onSubmit}>
        {busy ? <Icon name="loader" className="spin" /> : null}
        {submitLabel}
      </button>
    </div>
  );
}
