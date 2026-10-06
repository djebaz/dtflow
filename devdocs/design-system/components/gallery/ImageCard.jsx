import React from "react";
export function ImageCard({ src, ratio = "1 / 1", badge, title, subtitle, onClick }) {
  return <div className="dt-icard" onClick={onClick}><div className="dt-tile" style={{ aspectRatio: ratio }}>{src ? <img src={src} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : badge}</div><span className="dt-label value" style={{ padding: "0 8px", fontSize: "var(--font-size-small)" }}>{title}</span><span className="dt-label supporting" style={{ padding: "0 8px 8px" }}>{subtitle}</span></div>;
}
