import React from "react";
export function Slider({ className = "", ...rest }) {
  return <input type="range" className={["dt-slider", className].filter(Boolean).join(" ")} {...rest} />;
}
