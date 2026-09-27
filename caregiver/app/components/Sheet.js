import { useEffect, useId, useRef } from "react";
import Icon from "./Icon";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// A bottom sheet over a scrim: focus moves in and stays in, Escape or the scrim closes it, the page behind
// does not scroll, and focus goes back where it was on close.
export default function Sheet({ title, onClose, children, actions }) {
  const sheet = useRef(null);
  const titleId = useId();
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    const before = document.activeElement;
    document.body.classList.add("cg-locked");
    const node = sheet.current;
    const first = node && node.querySelector("[data-autofocus]");
    (first || node)?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close.current();
        return;
      }
      if (event.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll(FOCUSABLE)];
      if (!items.length) return;
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === firstItem || document.activeElement === node)) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("cg-locked");
      if (before && typeof before.focus === "function" && document.contains(before)) before.focus();
    };
  }, []);

  return (
    <div className="cg-sheet-layer">
      <button type="button" className="cg-sheet-scrim" tabIndex={-1} aria-hidden="true" onClick={() => onClose()} />
      <section ref={sheet} className="cg-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="cg-sheet__grab" aria-hidden="true" />
        <div className="cg-sheet__head">
          <h2 id={titleId} className="cg-sheet__title">{title}</h2>
          <button type="button" className="cg-sheet__close" aria-label="Close" onClick={() => onClose()}>
            <Icon name="close" size={18} stroke={2.4} />
          </button>
        </div>
        {children}
        {actions ? <div className="cg-sheet__actions">{actions}</div> : null}
      </section>
    </div>
  );
}
