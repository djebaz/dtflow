import React from "react";
export function EmptyState({ title, children, logo, style }) {
  return <div className="dt-empty" style={style}>{logo ? <img src={logo} width="56" height="56" alt="" style={{ borderRadius: 8 }} /> : null}{title ? <span className="dt-label section">{title}</span> : null}<span className="dt-label supporting" style={{ textAlign: "center" }}>{children}</span></div>;
}
