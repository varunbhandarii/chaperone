import Icon from "./Icon";
import Sheet from "./Sheet";

function Part({ label, children }) {
  if (!children) return null;
  return (
    <div className="cg-sheet__section">
      <h3 className="cg-sheet__label">{label}</h3>
      {children}
    </div>
  );
}

// Why Chaperone stepped in, from the decision's explanation, as a bottom sheet.
export default function Why({ explanation, ruthPhone, onClose }) {
  if (!explanation) return null;
  const heard = String(explanation.what_ruth_heard || "").trim();
  const quoted = heard && !/^["“]/.test(heard) ? `“${heard}”` : heard;
  return (
    <Sheet
      title={explanation.headline || "Why Chaperone stepped in"}
      onClose={onClose}
      actions={[
        ruthPhone ? (
          <a key="call" className="ch-btn ch-btn--primary cg-btn" href={`tel:${ruthPhone}`}>
            <Icon name="phone" size={18} stroke={2.2} />Call Ruth
          </a>
        ) : null,
        <button key="done" type="button" className={`ch-btn cg-btn ${ruthPhone ? "" : "ch-btn--primary"}`} onClick={onClose}>Done</button>,
      ]}
    >
      <Part label="What happened">{explanation.what_happened ? <p className="cg-sheet__text">{explanation.what_happened}</p> : null}</Part>
      <Part label="The rule">{explanation.rule_in_plain_words ? <p className="cg-sheet__text">{explanation.rule_in_plain_words}</p> : null}</Part>
      <Part label="What Ruth heard">{quoted ? <blockquote className="cg-quote cg-quote--blue">{quoted}</blockquote> : null}</Part>
      <Part label="What you can do">{explanation.what_you_can_do ? <p className="cg-sheet__text">{explanation.what_you_can_do}</p> : null}</Part>
    </Sheet>
  );
}
