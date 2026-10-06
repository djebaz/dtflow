import React from "react";
export function Brand({ title = "DTFlow", subtitle, logo = "../../assets/dtflow-app.png", children }) {
  return <div className="dt-brand"><img src={logo} alt="DTFlow" /><div style={{ flex: 1, minWidth: 0 }}><b>{title}</b>{subtitle ? <small>{subtitle}</small> : null}</div>{children}</div>;
}
