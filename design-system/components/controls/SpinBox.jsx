import React from "react";
export function SpinBox({ className = "", ...rest }) {
  return <input type="number" className={["dt-field", className].filter(Boolean).join(" ")} {...rest} />;
}
