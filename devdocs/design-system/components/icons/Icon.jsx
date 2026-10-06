import React from "react";
const LOCAL = ["grid","list","detail","refresh","document","json","calendar","fullscreen","chevron"];
const DOT = { ok: "var(--color-connection-accent)", warning: "var(--color-warning)", error: "var(--color-error)", unknown: "var(--color-secondary)" };
export function Icon({ name, size = 16, base = "../../assets/icons/", state = "unknown", style, ...rest }) {
  const s = { display: "block", flex: "none", ...style };
  if (name === "panel") return <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="var(--color-secondary)" strokeWidth="2" aria-hidden="true" style={s} {...rest}><rect x="5" y="6" width="22" height="20" rx="4" /><line x1="20" y1="6" x2="20" y2="26" /></svg>;
  if (name === "connection") return <svg width={size * 40 / 24} height={size} viewBox="0 0 40 24" fill="none" aria-hidden="true" style={s} {...rest}><g stroke="var(--color-connection-glyph)" strokeWidth="1.6" strokeLinecap="round"><path d="M13.76 7.76A6 6 0 1 0 22.24 7.76" /><line x1="18" y1="3" x2="18" y2="11" /></g><circle cx="37" cy="4" r="3" fill={DOT[state] || DOT.unknown} /></svg>;
  if (LOCAL.includes(name)) return <img src={base + name + ".svg"} width={size} height={size} alt="" style={s} {...rest} />;
  return <img src={"https://unpkg.com/lucide-static@latest/icons/" + name + ".svg"} width={size} height={size} alt="" style={{ ...s, filter: "invert(1) brightness(.96)" }} {...rest} />;
}
