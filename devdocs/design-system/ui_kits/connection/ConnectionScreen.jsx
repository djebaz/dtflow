function ConnectionScreen() {
  const { SectionCard, ComboBox, TextField, Button, StatusCard, Label, ListBox, EmptyState, Panel, FloatingToolbar } = window.DTFlowDesignSystem_cee425;
  const insp = useInspector();
  const [backend, setBackend] = React.useState("fake");
  const [key, setKey] = React.useState(""), [saved, setSaved] = React.useState(false);
  const [st, setSt] = React.useState({ s: "neutral", h: "Not checked. Check the fake connection.", t: "A connection check is bounded, non-generative, and does not consume generation credits." });
  const cloud = backend === "cloud";
  const pick = (b) => { setBackend(b); setSt({ s: "neutral", h: "Not checked. Check the " + b + " connection.", t: st.t }); };
  const check = () => { setSt({ s: "warning", h: "Checking…", t: "Contacting the " + backend + " backend." }); setTimeout(() => setSt({ s: "ok", h: "Connected. The " + backend + " backend answered.", t: "An explicit, non-generative check. It does not poll." }), 700); };
  return <><div className="shell">
    <div className="rail"><Chrome tab="Connection" conn={st.s === "neutral" ? "unknown" : st.s} onTab={go} />
      <Panel eyebrow="CONNECTION CONTEXT" className="railscroll">
        <SectionCard title="Backend" icon={Ic("server")}><div className="form">
          <Label textRole="caption">Provider</Label><ComboBox options={["fake", "local", "cloud"]} value={backend} onChange={(e) => pick(e.target.value)} aria-label="Generation backend" />
          <Label textRole="caption">Endpoint</Label><TextField placeholder="HOST:PORT" disabled={backend !== "local"} aria-label="Local Draw Things endpoint" />
          <Button variant="primary" onClick={check} style={{ gridColumn: "1/-1" }}>Check connection</Button>
        </div></SectionCard>
        <SectionCard title="DT+ credentials" icon={Ic("key-round")} disabled={!cloud}><div className="stack">
          <Label textRole="supporting">Saved securely in Windows Credential Manager. An environment key keeps precedence.</Label>
          <TextField type="password" placeholder="DT+ API key" value={key} onChange={(e) => setKey(e.target.value)} aria-label="DT plus API key" />
          <div className="row"><Button style={{ flex: 1 }} disabled={!key} onClick={() => setSaved(true)}>Save key</Button><Button style={{ flex: 1 }} variant="danger" disabled={!saved} onClick={() => { setSaved(false); setKey(""); }}>Remove</Button></div>
          <Label textRole="value" style={{ fontSize: "var(--font-size-small)", fontWeight: 400 }}>{saved ? "Key saved in Windows Credential Manager." : "No API key available."}</Label>
        </div></SectionCard>
      </Panel></div>
    <Panel eyebrow="CONNECTION" className="work"><Label textRole="title">Connection readiness</Label>
      <Label textRole="supporting">Verify the selected backend without submitting generation. DTFlow never falls back to another provider.</Label>
      <StatusCard state={st.s} heading={st.h} minHeight={160}>{st.t}</StatusCard></Panel>
    {insp ? <div className="rail"><Panel eyebrow="SAFETY & EVIDENCE" style={{ flex: 1 }}>
      <SectionCard title="Connection check" icon={Ic("shield-check")}><Label textRole="supporting" style={{ display: "block", whiteSpace: "pre-line", color: "var(--color-text)" }}>{"• No generation request\n• No automatic backend fallback\n• Credentials are never shown or logged\n• Failures retain explicit provider evidence"}</Label></SectionCard></Panel></div> : null}
  </div><StatusBar /></>;
}
