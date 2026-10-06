import React from "react";
export function StatusCard({ state = "neutral", heading, children, glyph = "\u25CF", minHeight }) {
  const cls = state === "neutral" ? "unknown" : state;
  return (
    <div className={"dt-status " + (state === "neutral" || state === "unknown" ? "" : state)} style={minHeight ? { minHeight, alignItems: "flex-start" } : undefined}>
      <span className={"dt-glyph dt-st-" + (state === "neutral" ? "unknown" : cls)} style={state === "neutral" ? { color: "#8a8a8a" } : undefined}>{glyph}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <div className={"dt-st-" + cls} style={{ fontSize: "12pt", fontWeight: 500, color: state === "neutral" ? "#d7d7d7" : undefined }}>{heading}</div>
        <div style={{ fontSize: "var(--font-size-small)", color: "var(--color-secondary)" }}>{children}</div>
      </div>
    </div>
  );
}
