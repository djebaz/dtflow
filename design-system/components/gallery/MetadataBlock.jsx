import React from "react";
export function MetadataBlock({ icon, label, children, minWidth }) {
  return <div className="dt-mblock" style={minWidth ? { minWidth } : undefined}><span className="dt-label caption" style={{ display: "flex", alignItems: "center", gap: 5 }}>{icon}{label}</span><div className="dt-mvalue">{children}</div></div>;
}
