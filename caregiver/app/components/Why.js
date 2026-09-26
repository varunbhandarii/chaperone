import { btn, card } from "./styles";

export default function Why({ explanation, onClose }) {
  if (!explanation) return null;
  return (
    <section style={{ ...card, position: "relative" }}>
      <h2>{explanation.headline}</h2>
      <p style={{ fontSize: "1.15rem" }}>{explanation.what_happened}</p>
      <p style={{ fontSize: "1.15rem" }}><strong>The rule.</strong> {explanation.rule_in_plain_words}</p>
      <p style={{ fontSize: "1.15rem" }}><strong>What Ruth heard.</strong> {explanation.what_ruth_heard}</p>
      <p style={{ fontSize: "1.15rem" }}><strong>What you can do.</strong> {explanation.what_you_can_do}</p>
      <button style={btn} onClick={onClose}>Close</button>
    </section>
  );
}
