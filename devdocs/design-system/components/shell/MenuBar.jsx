import React from "react";
export function MenuBar({ menus = [] }) {
  const [open, setOpen] = React.useState(null);
  return <div className="dt-menubar" onMouseLeave={() => setOpen(null)}>{menus.map((m) => <div key={m.label} style={{ position: "relative" }}><button type="button" className={"dt-menuitem" + (open === m.label ? " on" : "")} onClick={() => setOpen(open === m.label ? null : m.label)}>{m.label}</button>{open === m.label ? <div className="dt-menu">{m.items.map((i) => <button key={i} type="button" className="dt-menuitem" onClick={() => setOpen(null)}>{i}</button>)}</div> : null}</div>)}</div>;
}
