import React from "react";
export function Panel({ eyebrow, children, style, className = "" }) {
  return <div className={"dt-panel " + className} style={style}>{eyebrow ? <span className="dt-label eyebrow">{eyebrow}</span> : null}{children}</div>;
}
