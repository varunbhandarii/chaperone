import { useState } from "react";
import Sheet from "./Sheet";

// Asks before an action that loosens protection or cannot be undone. When it loosens protection, the safe
// choice is the primary button and the action itself is the secondary one.
export default function Confirm({ ask, onClose }) {
  const [note, setNote] = useState("");
  if (!ask) return null;
  const loosens = ask.kind === "loosen";
  const run = () => {
    onClose();
    ask.run(note.trim());
  };
  const confirmButton = (
    <button key="go" type="button" className={`ch-btn cg-btn ${loosens ? "" : "ch-btn--danger"}`} onClick={run}>
      {ask.confirmLabel}
    </button>
  );
  const safeButton = (
    <button key="safe" type="button" className={`ch-btn cg-btn ${loosens ? "ch-btn--primary" : ""}`} onClick={onClose} data-autofocus>
      {ask.safeLabel}
    </button>
  );
  return (
    <Sheet title={ask.title} onClose={onClose} actions={loosens ? [safeButton, confirmButton] : [confirmButton, safeButton]}>
      {ask.body ? <p className="cg-sheet__text">{ask.body}</p> : null}
      {ask.note ? (
        <div className="cg-field">
          <label className="cg-field__label" htmlFor="confirm-note">{ask.note.label}</label>
          <textarea id="confirm-note" className="cg-input" maxLength={140} value={note} onChange={(event) => setNote(event.target.value)} />
          {ask.note.help ? <p className="cg-field__help">{ask.note.help}</p> : null}
        </div>
      ) : null}
    </Sheet>
  );
}
