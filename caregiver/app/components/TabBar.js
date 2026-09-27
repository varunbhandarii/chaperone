import Icon from "./Icon";

export const TABS = [
  { id: "home", label: "Home", icon: "home" },
  { id: "safety", label: "Safety", icon: "shield" },
  { id: "approvals", label: "Approvals", icon: "bell" },
  { id: "activity", label: "Activity", icon: "receipt" },
  { id: "rules", label: "Rules", icon: "sliders" },
];

export default function TabBar({ tab, counts, onTab }) {
  return (
    <nav className="ch-tabs cg-tabs" aria-label="Main">
      <div className="cg-tabs__inner">
        {TABS.map(({ id, label, icon }) => {
          const count = counts[id] || 0;
          return (
            <button key={id} type="button" aria-current={tab === id ? "page" : undefined} onClick={() => onTab(id)}>
              <Icon name={icon} />
              {count ? <span className="cg-tabs__count" aria-hidden="true">{count > 9 ? "9+" : count}</span> : null}
              {label}
              {count ? <span className="cg-sr">, {count} new</span> : null}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
