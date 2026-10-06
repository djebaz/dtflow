import React from "react";
export function FloatingToolbar({ position = "top-left", pill = false, children }) {
  return <div className={"dt-ftb " + position + (pill ? " pill" : "")}>{children}</div>;
}
