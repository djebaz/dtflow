import React from "react";
export function ListItem({ selected, children, ...rest }) {
  return <div role="option" aria-selected={!!selected} className={"dt-item" + (selected ? " selected" : "")} {...rest}>{children}</div>;
}
