import React from "react";
import { Button } from "../controls/Button.jsx";
import { ConnectionButton } from "../controls/ConnectionButton.jsx";
export function Navigation({ current, onChange, connectionState = "unknown", items = ["Generate", "Queues", "Images"] }) {
  return <div className="dt-nav"><ConnectionButton state={connectionState} checked={current === "Connection"} onClick={() => onChange && onChange("Connection")} />{items.map((t) => <Button key={t} variant="navigation" checked={current === t} onClick={() => onChange && onChange(t)}>{t}</Button>)}</div>;
}
