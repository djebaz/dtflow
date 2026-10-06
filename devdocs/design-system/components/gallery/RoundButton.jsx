import React from "react";
export function RoundButton({ direction = "next", ...rest }) {
  return <button type="button" aria-label={direction === "next" ? "Next" : "Previous"} className={"dt-round " + direction} {...rest}>{direction === "next" ? "\u203A" : "\u2039"}</button>;
}
