import React from "react";
export function Checkbox({ label, disabled, ...rest }) {
  return <label className={"dt-check" + (disabled ? " disabled" : "")}><input type="checkbox" disabled={disabled} {...rest} />{label}</label>;
}
