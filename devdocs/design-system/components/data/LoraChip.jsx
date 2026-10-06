import React from "react";
export function LoraChip({ name, weight, status = "Unknown" }) {
  return <div className="dt-chip"><span style={{ flex: 1, fontSize: "var(--font-size-small)", fontWeight: 500 }}>{name}</span><span style={{ fontFamily: "var(--font-family-mono)", fontSize: "var(--font-size-small)" }}>{weight}</span><span className="dt-unknown" title="Offline fixture: no compatibility evidence recorded.">{status}</span></div>;
}
