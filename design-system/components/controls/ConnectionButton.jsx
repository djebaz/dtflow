import React from "react";
const DOT = { ok: "var(--color-connection-accent)", warning: "var(--color-warning)", error: "var(--color-error)", unknown: "var(--color-secondary)" };
export function ConnectionButton({ state = "unknown", checked = false, disabled, ...rest }) {
  return (
    <button type="button" disabled={disabled} aria-label={"Connection: " + state} title={"Show connection workspace \u2014 " + state} className={["dt-conn", state === "unknown" ? "" : state, checked ? "checked" : ""].filter(Boolean).join(" ")} {...rest}>
      <svg width="40" height="24" viewBox="0 0 40 24" fill="none" aria-hidden="true">
        <g stroke="var(--color-connection-glyph)" strokeWidth="1.6" strokeLinecap="round"><path d="M13.76 7.76A6 6 0 1 0 22.24 7.76" /><line x1="18" y1="3" x2="18" y2="11" /></g>
        <circle cx="37" cy="4" r="3" fill={DOT[state] || DOT.unknown} />
      </svg>
    </button>
  );
}
