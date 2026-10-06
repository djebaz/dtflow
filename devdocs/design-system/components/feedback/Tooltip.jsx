import React from "react";
export function Tooltip({ text, children }) {
  return <span className="dt-tip-host">{children}<span role="tooltip" className="dt-tip">{text}</span></span>;
}
