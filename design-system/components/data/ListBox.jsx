import React from "react";
export function ListBox({ children, minHeight = 90, label, style }) {
  return <div role="listbox" aria-label={label} className="dt-listbox" style={{ minHeight, ...style }}>{children}</div>;
}
