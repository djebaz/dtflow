import React from "react";

export function SectionCard({ title, icon, checkable = false, checked = true, onCheckedChange, disabled = false, children, style }) {
  const open = !checkable || checked;
  const edge = disabled ? "var(--color-disabled-border)" : "var(--color-section-border)";
  const heading = { color: disabled ? "var(--color-disabled)" : "var(--color-text)", fontSize: "var(--font-size-small)", fontWeight: "var(--font-weight-emphasis)" };
  return (
    <section aria-disabled={disabled} style={{ background: "var(--color-surface)", border: `1px solid ${edge}`, borderRadius: 8, flex: "none", ...style }}>
      <header style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", background: "var(--color-section-header)", borderBottom: open ? `1px solid ${edge}` : "0", borderTopLeftRadius: 7, borderTopRightRadius: 7 }}>
        {icon ? <span style={{ width: 16, height: 16, display: "inline-flex", alignItems: "center", justifyContent: "center", flex: "none" }}>{icon}</span> : null}
        {checkable ? (
          <label style={{ display: "flex", alignItems: "center", gap: 7, cursor: disabled ? "default" : "pointer", ...heading }}>
            <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onCheckedChange && onCheckedChange(e.target.checked)} style={{ width: 14, height: 14, accentColor: "var(--color-accent)" }} />
            {title}
          </label>
        ) : <span style={heading}>{title}</span>}
      </header>
      {open ? <div style={{ padding: "var(--layout-panel-padding)", opacity: disabled ? 0.6 : 1, pointerEvents: disabled ? "none" : "auto" }}>{children}</div> : null}
    </section>
  );
}
