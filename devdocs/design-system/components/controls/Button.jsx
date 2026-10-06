import React from "react";
export function Button({ variant = "neutral", checked = false, disabled, className = "", children, ...rest }) {
  return <button type="button" disabled={disabled} aria-pressed={checked || undefined} className={["dt-btn", variant, checked ? "checked" : "", className].filter(Boolean).join(" ")} {...rest}>{children}</button>;
}
