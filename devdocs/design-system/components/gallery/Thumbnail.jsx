import React from "react";
export function Thumbnail({ src, label, selected, onClick }) {
  return <button type="button" className={"dt-thumb" + (selected ? " on" : "")} onClick={onClick}><div className="dt-tile" style={{ aspectRatio: "1 / 1", borderRadius: 6 }}>{src ? <img src={src} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : label}</div></button>;
}
