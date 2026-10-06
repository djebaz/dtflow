import React from "react";
export function Label({ textRole = "body", children, className = "", ...rest }) {
  return <span className={["dt-label", textRole, className].filter(Boolean).join(" ")} {...rest}>{children}</span>;
}
