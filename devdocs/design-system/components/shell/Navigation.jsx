import React from "react";
import { Button } from "../controls/Button.jsx";
export function Navigation({ current, onChange, items = ["Generate", "Queues"] }) {
  return <div className="dt-nav">{items.map((t) => <Button key={t} variant="navigation" checked={current === t} onClick={() => onChange && onChange(t)}>{t}</Button>)}</div>;
}
