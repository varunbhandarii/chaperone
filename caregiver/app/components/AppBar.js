import Icon from "./Icon";

// The white app bar: the Chaperone lockup and whether alerts are armed. Alerts arm on the first tap anywhere
// (sound and the wake lock need one), so the off state is a button that is itself that tap.
export default function AppBar({ alertsOn }) {
  return (
    <header className="cg-appbar">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img className="cg-appbar__logo" src="/design/logo.svg" alt="Chaperone" height={28} />
      <span className="cg-appbar__spacer" />
      {alertsOn ? (
        <span className="cg-chip cg-chip--ok"><Icon name="bell" size={14} stroke={2.4} />Alerts on</span>
      ) : (
        <button type="button" className="cg-chip"><Icon name="bell" size={14} stroke={2.4} />Turn on alerts</button>
      )}
    </header>
  );
}
