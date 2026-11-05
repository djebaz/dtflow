function GenerateScreen() {
  const { SectionCard, ComboBox, TextField, TextArea, SpinBox, Button, Checkbox, ProgressBar, ListItem, Label, ListBox, EmptyState, Panel, FloatingToolbar } = window.DTFlowDesignSystem_cee425;
  const insp = useInspector();
  const [loras, setLoras] = React.useState(["fake-soft @ 0.8"]), [loraSel, setLoraSel] = React.useState(null);
  const [big, setBig] = React.useState(false), [det, setDet] = React.useState(false), [done, setDone] = React.useState(false), [busy, setBusy] = React.useState(false);
  const F = (l, c) => <><Label textRole="caption">{l}</Label>{c}</>;
  const gen = () => { setBusy(true); setDone(false); setTimeout(() => { setBusy(false); setDone(true); }, 600); };
  return <><div className="shell">
    <div className="rail"><Chrome tab="Generate" onTab={go} />
      <div className="railscroll">
        <SectionCard title="Backend" icon={Ic("server")}><div className="form">{F("Provider", <ComboBox options={["fake"]} disabled />)}{F("Model", <ComboBox options={["fake-fixture (default)"]} />)}<Button style={{ gridColumn: "1/-1" }}>Refresh inventory</Button><Label textRole="supporting" style={{ gridColumn: "1/-1" }}>Inventory has not been refreshed.</Label></div></SectionCard>
        <SectionCard title="Parameters" icon={Ic("detail")}><div className="form">{F("Seed", <TextField defaultValue="0" />)}{F("Steps", <SpinBox min={1} max={150} defaultValue={4} />)}{F("Width", <SpinBox min={64} max={4096} defaultValue={320} />)}{F("Height", <SpinBox min={64} max={4096} defaultValue={192} />)}{F("Guidance", <SpinBox step="0.5" defaultValue={1} />)}{F("Sampler", <ComboBox options={["euler_a", "dpmpp_2m_karras"]} />)}</div></SectionCard>
        <SectionCard title="Simulation (fake provider)" icon={Ic("flask-conical")}><div className="form">{F("Outcome", <ComboBox options={["Succeeds", "Fails"]} />)}{F("Duration", <SpinBox step="0.5" defaultValue={0} />)}<span style={{ gridColumn: "1/-1" }}><Checkbox label="Show previews while waiting" /></span></div></SectionCard>
        <SectionCard title="Configuration" icon={Ic("settings")}><div className="stack"><ComboBox options={["Current values"]} /><div className="row"><Button style={{ flex: 1 }}>Load</Button><Button style={{ flex: 1 }}>Save</Button></div><Label textRole="supporting">Credential-free versioned configuration only.</Label></div></SectionCard>
      </div></div>
    <div className="center" style={{ overflow: "hidden" }}>
      <div className="row" style={{ justifyContent: "space-between" }}><Label textRole="section" style={{ fontSize: "11pt" }}>Prompt</Label><Button checked={big} onClick={() => setBig(!big)}>Expand prompt</Button></div>
      <TextArea rows={3} placeholder="Describe the image to generate…" defaultValue="an apple" style={{ height: big ? 180 : 76, resize: "none" }} />
      <TextField placeholder="Negative prompt (optional)" />
      <div style={{ flex: 1, minHeight: 200, background: "var(--color-canvas)", border: "1px solid var(--color-button-hover)", borderRadius: 8, position: "relative", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 10 }}>
        <FloatingToolbar position="top-left"><Button disabled={!done}>Fit</Button><Button disabled={!done}>100%</Button><Button disabled={!done}>−</Button><input type="range" className="dt-slider" disabled={!done} style={{ width: 70 }} /><Button disabled={!done}>+</Button></FloatingToolbar>
        <Button style={{ position: "absolute", top: 10, right: 10 }}>Expand</Button>
        {done ? <div style={{ width: "86%", aspectRatio: "320 / 192", background: "var(--color-surface)", border: "1px dashed var(--color-border-strong)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-secondary)", fontSize: "var(--font-size-small)" }}>Fixture output — sample image placeholder</div> : <EmptyState logo="../../assets/dtflow-app.png">Your next image starts here<br />Set a prompt and press Generate.</EmptyState>}
        {done ? <span style={{ position: "absolute", left: 12, bottom: 12, background: "var(--color-toolbar)", borderRadius: 4, padding: "4px 7px", fontSize: "var(--font-size-small)" }}>320 × 192 · Fixture · no inference</span> : null}
        <FloatingToolbar position="bottom-right"><Button disabled={!done}>Open file</Button><Button disabled={!done}>Open folder</Button><Button disabled={!done}>Metadata</Button><Button disabled={!done}>Copy path</Button></FloatingToolbar>
      </div>
      <div className="row"><Button variant="primary" style={{ flex: 1, minHeight: 36 }} onClick={gen} disabled={busy}>Generate image</Button><Button style={{ flex: 1, minHeight: 36 }} disabled={!busy} title="Stops DTFlow waiting locally. It does not confirm cancellation on the server.">Stop waiting</Button></div>
      <ProgressBar value={done ? 1 : 0} max={1} label={done ? "Saved" : busy ? "Generating…" : "Idle"} />
      <span className={done ? "dt-st-ok" : "dt-st-unknown"} style={{ fontSize: "var(--font-size-small)", fontWeight: 600 }}>{done ? "Saved. 1 image with reproducibility metadata. Fixture output; no model inference performed." : "No generation yet."}</span>
    </div>
    {insp ? <div className="rail"><ImagesAction onTab={go} /><Panel className="railscroll" style={{ flex: 1 }}>
      <ListBox label="LoRAs" minHeight={110} style={{ flex: "none" }}>{loras.map((l) => <ListItem key={l} selected={loraSel === l} onClick={() => setLoraSel(loraSel === l ? null : l)}>{l}</ListItem>)}</ListBox>
      <div className="row"><Button style={{ flex: 1 }} onClick={() => setLoras([...loras, "fake-lora-" + (loras.length + 1) + " @ 0.8"])}>Add LoRA</Button><Button style={{ flex: 1 }} disabled={!loraSel} onClick={() => { setLoras(loras.filter((x) => x !== loraSel)); setLoraSel(null); }}>Remove</Button><Button style={{ flex: 1 }}>Check</Button></div>
      <Checkbox label="Use LoRA trigger prefixes" />
      <Label textRole="value" className="dt-st-unknown" style={{ fontSize: "var(--font-size-small)" }}>Compatibility not checked.</Label>
      <SectionCard title="Cloud assets" icon={Ic("cloud")}><div className="stack"><div className="host" style={{ minHeight: 90, display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center", background: "var(--color-input)" }}><Label textRole="supporting">No local receipts<br />Prepare a cloud asset to begin.</Label></div><div className="row"><Button style={{ flex: 1 }} disabled>Prepare…</Button><Button style={{ flex: 1 }} disabled>Reconcile</Button><Button style={{ flex: 1 }} disabled>Use</Button></div><Label textRole="supporting" style={{ color: "var(--color-text)" }}>Cloud only. Asset actions need the cloud backend (choose it on the Connection page).</Label><Label textRole="supporting">Local registry of receipts, not DT+ account inventory. Only Prepare uploads.</Label></div></SectionCard>
      <Button checked={det} onClick={() => setDet(!det)}>Attempt details</Button>
      {det ? <div className="stack"><div className="host" style={{ minHeight: 50 }}></div><Label textRole="supporting">No durable attempt yet.</Label><div className="host" style={{ padding: 8, color: "var(--color-secondary)" }}><Label textRole="identifier">Effective request and resolution evidence</Label></div></div> : null}
    </Panel></div> : null}
  </div><StatusBar tab="Generate" onTab={go} /></>;
}
