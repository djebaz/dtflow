const Ic = (n, s = 16) => { const { Icon } = window.DTFlowDesignSystem_cee425; return Icon ? <Icon name={n} size={s} /> : null; };
function Chrome({ tab, onTab }) {
  const { Brand, Navigation, Button } = window.DTFlowDesignSystem_cee425;
  const [on, setOn] = React.useState(true);
  return <div className="chrome"><Brand logo="../../assets/dtflow-app.png"><Button variant="neutral" checked={on} aria-label="Toggle right details panel" title="Show or hide the right details panel" style={{ width: 32, height: 32, padding: 0, marginLeft: 8 }} onClick={() => { setOn(!on); window.dispatchEvent(new Event("dt-inspector")); }}><svg width="16" height="16" viewBox="0 0 32 32" fill="none" stroke="var(--color-secondary)" strokeWidth="2" aria-hidden="true"><rect x="5" y="6" width="22" height="20" rx="4" /><line x1="20" y1="6" x2="20" y2="26" /></svg></Button></Brand><Navigation current={tab} onChange={onTab} /></div>;
}
function useInspector() { const [o, s] = React.useState(true); React.useEffect(() => { const f = () => s((x) => !x); window.addEventListener("dt-inspector", f); return () => window.removeEventListener("dt-inspector", f); }, []); return o; }
// Connection lives at the extreme right of the bottom bar; Images is navigation above ordinary inspectors (absent in Images/Logs).
function StatusBar({ tab, conn = "unknown", onTab }) { const { StatusBar: SB, ConnectionButton } = window.DTFlowDesignSystem_cee425; return <SB trailing={<ConnectionButton state={conn} checked={tab === "Connection"} onClick={() => onTab && onTab("Connection")} />}>DTFlow 0.3.0 | revision 2026-10-06 03:42 | source: dev</SB>; }
function GalleryLink({ onTab }) { const { Button } = window.DTFlowDesignSystem_cee425; return <Button variant="neutral" className="gallery-link" title="Browse committed saved outputs" aria-label="Show saved Images" onClick={() => onTab && onTab("Images")}>{Ic("image", 14)}Images</Button>; }
function ImagesAction(props) { return <GalleryLink {...props} />; }
Object.assign(window, { Chrome, useInspector, Ic, StatusBar, ImagesAction });
