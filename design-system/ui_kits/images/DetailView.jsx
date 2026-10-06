function DetailView({ i, setI, size }) {
  const { Button, LoraChip, FloatingToolbar, OverlayChip, RoundButton, MetadataBlock, Thumbnail, Label, EmptyState } = window.DTFlowDesignSystem_cee425;
  const [panel, setPanel] = React.useState(null), [fit, setFit] = React.useState("Cover");
  const cur = IMG_FIX[i], step = (d) => setI((i + d + IMG_FIX.length) % IMG_FIX.length);
  const rows = [["ban", "Negative prompt", "Not recorded"], ["dices", "Actual resolved seed", "Not recorded"], ["file-image", "Filename", "portrait.jpg (illustrative fixture)"], ["hard-drive", "Source", "None — offline fixture"], ["weight", "File size", "403 KB (fixture file)"], ["timer", "Recorded duration", "Not recorded"]];
  return <div style={{ flex: 1, minWidth: 0, display: "flex", gap: 8 }}>
    <div className="center">
      <div className="gprev"><EmptyState title="Illustrative fixture">No generation or saved output</EmptyState>
        <div style={{ position: "absolute", top: 10, left: 10, display: "flex" }}><OverlayChip>{i + 1} / {IMG_FIX.length}</OverlayChip><OverlayChip>{cur.dim}</OverlayChip><OverlayChip muted>Sample preview</OverlayChip></div>
        <RoundButton direction="prev" onClick={() => step(-1)} /><RoundButton direction="next" onClick={() => step(1)} />
        <FloatingToolbar position="bottom-center" pill><select className="ghost" value={fit} onChange={(e) => setFit(e.target.value)} aria-label="View mode"><option>Cover</option><option>Fit</option></select><button className="ghost">−</button><button className="ghost">100%</button><button className="ghost">+</button><button className="ghost" aria-label="Fullscreen">{Ic("fullscreen")}</button></FloatingToolbar></div>
      <div className="meta">
        <MetadataBlock minWidth={190} icon={Ic("box", 12)} label="Model"><div className="row" style={{ gap: 8 }}><span className="mmark">{Ic("box")}</span><div><b>Illustrative model</b><div><Label textRole="supporting">fixture-model.ckpt</Label></div></div></div></MetadataBlock>
        <MetadataBlock minWidth={240} icon={Ic("layers", 12)} label="LoRAs"><LoraChip name="portrait_enhancer_v2" weight="0.8" /><LoraChip name="film_photography_xl" weight="0.4" /></MetadataBlock>
        <MetadataBlock icon={Ic("repeat", 12)} label="Steps">30</MetadataBlock><MetadataBlock icon={Ic("compass", 12)} label="CFG">7.0</MetadataBlock><MetadataBlock icon={Ic("waypoints", 12)} label="Sampler">Not recorded</MetadataBlock><MetadataBlock icon={Ic("calendar", 12)} label="Date">Not recorded</MetadataBlock>
      </div>
      <div className="pp"><div style={{ flex: 1, fontSize: 14, fontWeight: 500, lineHeight: 1.35 }} aria-label="Positive prompt">{IMG_PROMPT}</div>
        <div className="row" style={{ alignItems: "flex-start" }}>{[["add", "Additional details", "document"], ["raw", "Raw metadata JSON", "json"]].map(([k, l, n]) => <Button key={k} variant="disclosure" checked={panel === k} onClick={() => setPanel(panel === k ? null : k)}>{Ic(n, 12)}{l}</Button>)}</div></div>
      {panel === "add" ? <div className="pp" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 24px", fontSize: "var(--font-size-small)" }}>{rows.map(([n, l, v]) => <div key={l} style={{ display: "flex", gap: 10, borderBottom: "1px solid var(--color-section-border)", paddingBottom: 3 }}><Label textRole="caption" style={{ display: "flex", gap: 6, width: 150, alignItems: "center" }}>{Ic(n, 14)}{l}</Label><span>{v}</span></div>)}<div className="row" style={{ gridColumn: "1/-1", gap: 8 }}><Button style={{ flex: 1 }} disabled title="Offline layout preview: no saved output exists yet, so file actions stay disabled.">Copy path</Button><Button style={{ flex: 1 }} disabled title="Offline layout preview: no saved output exists yet, so file actions stay disabled.">Open folder</Button><Button style={{ flex: 1 }} disabled title="Offline layout preview: no saved output exists yet, so file actions stay disabled.">Open in external viewer</Button></div></div> : panel === "raw" ? <div className="pp" style={{ background: "var(--color-canvas)" }}><Label textRole="identifier">{'{ "model": "fixture-model.ckpt", "steps": 30, "cfg": 7.0 }'}</Label></div> : null}
    </div>
    <div className="film" style={{ width: Math.round(size * 0.7) }}>{IMG_FIX.map((f, n) => <Thumbnail key={f.id} label={n + 1} selected={n === i} onClick={() => setI(n)} />)}</div>
  </div>;
}
