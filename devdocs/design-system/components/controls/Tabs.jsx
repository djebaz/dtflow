import React from "react";
export function Tabs({ tabs = [], value, onChange, children }) {
  return <div><div className="dt-tabbar" role="tablist">{tabs.map((t) => <button key={t} type="button" role="tab" aria-selected={value === t} className={"dt-tab" + (value === t ? " on" : "")} onClick={() => onChange && onChange(t)}>{t}</button>)}</div><div className="dt-tabpane">{children}</div></div>;
}
