import React from "react";
export function DataTable({ columns = [], rows = [], selectedIndex, onSelect, variant = "default", ...rest }) {
  return (
    <table className={"dt-table" + (variant === "gallery" ? " gallery" : "")} {...rest}>
      <thead><tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i} className={selectedIndex === i ? "selected" : ""} onClick={() => onSelect && onSelect(i)}>{columns.map((c) => <td key={c.key} style={c.mono ? { fontFamily: "var(--font-family-mono)" } : undefined}>{r[c.key]}</td>)}</tr>)}</tbody>
    </table>
  );
}
