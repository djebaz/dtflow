import React from "react";
export function StatusBar({ children, trailing }) {
  return <div className="dt-statusbar"><span className="dt-statusbar-text">{children}</span>{trailing ? <span className="dt-statusbar-trailing">{trailing}</span> : null}</div>;
}
