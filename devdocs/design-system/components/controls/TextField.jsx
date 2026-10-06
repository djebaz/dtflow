import React from "react";
export function TextField({ invalid, className = "", ...rest }) {
  return <input className={["dt-field", invalid ? "invalid" : "", className].filter(Boolean).join(" ")} {...rest} />;
}
