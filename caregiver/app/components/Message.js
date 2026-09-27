import Icon from "./Icon";

const ICONS = { ok: "check", info: "info", err: "alert", wait: "clock" };

// One message at a time under the app bar: green when something went through, red only when it broke.
export default function Message({ message, onDismiss }) {
  if (!message) return null;
  const tone = message.tone || "info";
  return (
    <div className={`ch-banner ch-banner--${tone} cg-message`} role={tone === "err" ? "alert" : "status"}>
      <Icon name={ICONS[tone] || "info"} size={22} stroke={tone === "ok" ? 2.6 : 2.2} />
      <span className="cg-message__text">{message.text}</span>
      <button type="button" className="cg-message__close" aria-label="Dismiss message" onClick={onDismiss}>
        <Icon name="close" size={18} stroke={2.4} />
      </button>
    </div>
  );
}
