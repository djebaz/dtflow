import React from "react";
export function OverlayChip({ children, muted }) {
  return <span className={"dt-ov" + (muted ? " muted" : "")}>{children}</span>;
}
