import React from "react";
export function ProgressBar({ value = 0, max = 100, label, style, ...rest }) {
  const pct = Math.min(100, (value / max) * 100) + "%";
  return <div className="dt-bar" role="progressbar" aria-valuenow={value} aria-valuemax={max} style={label ? { height: 24, position: "relative", display: "flex", alignItems: "center", justifyContent: "center", ...style } : style} {...rest}><i style={label ? { position: "absolute", left: 0, top: 0, bottom: 0, width: pct } : { width: pct }}></i>{label ? <span style={{ position: "relative", fontSize: "var(--font-size-body)" }}>{label}</span> : null}</div>;
}
