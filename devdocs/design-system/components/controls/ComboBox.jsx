import React from "react";
export function ComboBox({ options = [], value, defaultValue, onChange, disabled, invalid, className = "", style, title, ...rest }) {
  const [open, setOpen] = React.useState(false);
  const [inner, setInner] = React.useState(defaultValue !== undefined ? defaultValue : options[0]);
  const cur = value !== undefined ? value : inner;
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const f = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", f);
    return () => document.removeEventListener("mousedown", f);
  }, [open]);
  const pick = (o) => { setInner(o); setOpen(false); onChange && onChange({ target: { value: o }, currentTarget: { value: o } }); };
  const key = (e) => {
    const i = options.indexOf(cur);
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") { e.preventDefault(); if (open) pick(options[Math.min(options.length - 1, i + 1)]); else setOpen(true); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (open) pick(options[Math.max(0, i - 1)]); }
  };
  return (
    <div ref={ref} className="dt-combo" style={style} title={title}>
      <button type="button" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)} onKeyDown={key} className={["dt-field", "dt-combo-btn", invalid ? "invalid" : "", className].filter(Boolean).join(" ")} {...rest}>{cur}</button>
      {open ? <div role="listbox" className="dt-combo-pop">{options.map((o) => <div key={o} role="option" aria-selected={o === cur} className={"dt-combo-opt" + (o === cur ? " selected" : "")} onMouseDown={(e) => { e.preventDefault(); pick(o); }}>{o}</div>)}</div> : null}
    </div>
  );
}
