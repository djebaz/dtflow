import React from "react";
export function TextArea({ invalid, rows = 3, className = "", ...rest }) {
  return <textarea rows={rows} className={["dt-field", invalid ? "invalid" : "", className].filter(Boolean).join(" ")} {...rest} />;
}
