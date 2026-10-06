function GalleryHeader({ mode, setMode, size, setSize }) {
  const { Brand, Navigation, Button, Slider, TextField, ComboBox, ToolbarRow, Label } = window.DTFlowDesignSystem_cee425;
  const M = [["grid", "Grid", "grid"], ["list", "List", "list"], ["detail", "Detail", "image"]];
  return <div className="chrome" style={{ gap: 8 }}>
    <div className="grow"><div className="gslot"><Brand logo="../../assets/dtflow-app.png" /></div>
      <ToolbarRow>{M.map(([k, l, ic]) => <Button key={k} variant="mode" checked={mode === k} onClick={() => setMode(k)}>{Ic(ic, 14)}{l}</Button>)}
        <Label textRole="caption" style={{ marginLeft: 8 }}>{mode === "list" ? "Row density" : mode === "detail" ? "Filmstrip size" : "Thumbnail size"}</Label>
        <Slider min={96} max={240} value={size} onChange={(e) => setSize(+e.target.value)} style={{ flex: 1, minWidth: 80 }} /><Label textRole="supporting">{size} px</Label>
        <Button disabled title="Preview only: this application action is not wired." style={{ background: "var(--color-surface)", borderColor: "var(--color-disabled-border)" }}>• Auto-refresh: Off</Button>
        <Button aria-label="Refresh" style={{ width: 44, height: 34, background: "var(--color-surface)", borderColor: "var(--color-border)" }}>{Ic("refresh", 20)}</Button></ToolbarRow></div>
    <div className="grow"><div className="gslot"><Navigation current="Images" onChange={go} /></div>
      <ToolbarRow><TextField placeholder="Search positive prompts…" aria-label="Search positive prompts" style={{ flex: 2 }} />{["All models", "All LoRAs", "All time", "Any ratio"].map((t) => <ComboBox key={t} options={[t]} aria-label={"Filter: " + t} style={{ flex: 1 }} />)}</ToolbarRow></div>
  </div>;
}
