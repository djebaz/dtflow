function QueuesScreen() {
  const { SectionCard, ListItem, SpinBox, Button, Badge, ComboBox, TextField, TextArea, Checkbox, DataTable, ProgressBar, Label, ListBox, EmptyState, Panel, FloatingToolbar } = window.DTFlowDesignSystem_cee425;
  const insp = useInspector();
  const [qs, setQs] = React.useState([{ id: "22762d11", be: "cloud", st: "finished", done: 1, total: 1 }, { id: "9d0020ea", be: "fake", st: "finished", done: 5, total: 5 }, { id: "2d58df38", be: "fake", st: "paused", done: 0, total: 1 }]);
  const [sel, setSel] = React.useState("2d58df38"), [count, setCount] = React.useState(1), [plan, setPlan] = React.useState(false);
  const q = qs.find((x) => x.id === sel);
  const set = (st) => setQs(qs.map((x) => x.id === sel ? { ...x, st } : x));
  const create = () => { const id = Math.random().toString(16).slice(2, 10); setQs([...qs, { id, be: "fake", st: "paused", done: 0, total: plan ? 1 : count }]); setSel(id); };
  const tone = !q ? "unknown" : q.st === "running" ? "ok" : q.st === "paused" ? "warning" : "unknown";
  const msg = !q ? "Create a queue from the Generate form, then Start it here." : q.st === "paused" ? "Paused. Nothing dispatches until Start." : q.st === "running" ? "Running. Dispatching items." : "Finished.";
  const rows = q ? Array.from({ length: q.total }, (_, i) => ({ n: <span style={{ display: "block", textAlign: "right" }}>{i + 1}</span>, i: "d8293ddf", sd: 3567690377 + i, s: i < q.done ? "succeeded" : "pending", a: "—", o: "—" })) : [];
  const cols = [{ key: "n", label: "#" }, { key: "i", label: "Item" }, { key: "sd", label: "Seed" }, { key: "s", label: "State" }, { key: "a", label: "Attempt" }, { key: "o", label: "Outputs" }];
  return <><div className="shell">
    <div className="rail"><Chrome tab="Queues" conn="unknown" onTab={go} />
      <Panel eyebrow="DURABLE QUEUES" className="railscroll" style={{ flex: 1 }}>
        <ListBox label="Durable queues" minHeight={200} style={{ flex: 1 }}>{qs.map((x) => <ListItem key={x.id} selected={sel === x.id} onClick={() => setSel(x.id)}>{x.id} · {x.be} · {x.st} · {x.done}/{x.total}</ListItem>)}</ListBox>
        <div className="row" style={{ justifyContent: "space-between" }}><Label textRole="caption">Items</Label><SpinBox min={1} max={1000} value={plan ? 1 : count} disabled={plan} onChange={(e) => setCount(+e.target.value)} style={{ width: 106 }} title="Item N uses the form's seed + N - 1." /></div>
        <Button variant="primary" style={{ minHeight: 36 }} onClick={create} title="Creates a paused queue from the Generate form. Contacts no backend and uploads nothing.">{plan ? "Create queue from plan" : "Create queue"}</Button>
        <Button style={{ minHeight: 36 }}>Refresh</Button>
        <SectionCard title="Dispatch policy" icon={Ic("send")}><div className="form"><Label textRole="caption">Cloud max</Label><SpinBox min={1} max={4} defaultValue={2} /><Label textRole="supporting" style={{ gridColumn: "1/-1" }}>Local Draw Things remains one effective lane. This is a client policy, not entitlement, and the memory budget currently admits about two.</Label></div></SectionCard>
      </Panel></div>
    <div className="center" style={{ padding: "0 4px" }}>
      <div><Badge>NOT DISPATCHING</Badge></div>
      <div className="row"><Button disabled={!q || q.st === "running" || q.st === "finished"} onClick={() => set("running")}>Start</Button><Button disabled={!q || q.st !== "running"} onClick={() => set("paused")}>Pause</Button><Button disabled={!q || q.st !== "paused"} onClick={() => set("running")}>Resume</Button><Button variant="danger" disabled={!q || q.st === "finished"} onClick={() => set("cancelled")}>Cancel</Button><span className={"val dt-st-" + tone} style={{ marginLeft: "auto" }}>{q ? msg : ""}</span></div>
      <Label textRole="supporting">Create a queue from the Generate form, then Start it here.</Label>
      <SectionCard title="Plan batch (pair or cross)" icon={Ic("layers")} checkable checked={plan} onCheckedChange={setPlan}><div className="stack"><div className="form">
        <Label textRole="caption">Mode</Label><ComboBox options={["Cross", "Pair"]} />
        <Label textRole="caption">Prompts</Label><TextArea rows={2} placeholder="One prompt per line" />
        <Label textRole="caption">Models</Label><TextField placeholder="e.g. model-a.ckpt; model-b.ckpt" />
        <Label textRole="caption">Seeds</Label><TextField placeholder="e.g. 1; 2; 10..20" />
        <Label textRole="caption">Steps</Label><TextField placeholder="e.g. 20; 30" />
        <Label textRole="caption">Guidance</Label><TextField placeholder="e.g. 4.5; 7" />
        <Label textRole="caption">Samplers</Label><TextField placeholder="e.g. euler_a; dpmpp_2m_karras" />
        <Label textRole="caption">Sizes</Label><TextField placeholder="e.g. 512x512; 768x512" />
        <Label textRole="caption">LoRA weights</Label><TextArea rows={2} placeholder="IDENTIFIER: 0.6; 1.0 (one LoRA per line)" /></div>
        <Checkbox label="Increment the seed per item (otherwise the base seed is kept)" />
        <div className="row"><Button style={{ minWidth: 160 }}>Dry run</Button><Label textRole="supporting" style={{ color: "var(--color-text)" }}>A cross plan needs at least one dimension.</Label></div>
        <div className="host" style={{ minHeight: 120 }}></div></div></SectionCard>
      {q ? <div className="host" style={{ flex: 1, background: "var(--color-input)", border: "1px solid var(--color-border)" }}><DataTable columns={cols} rows={rows} selectedIndex={0} /></div>
        : <div className="host" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 8 }}><Label textRole="section">No queue selected</Label><Label textRole="supporting">Select a queue from the left, or create one from the Generate form.</Label></div>}
      {q ? <ProgressBar value={q.done} max={q.total} label={q.done + " of " + q.total + " settled · " + q.done + " succeeded"} aria-label="Queue progress" /> : null}
    </div>
    {insp ? <div className="rail"><Panel eyebrow="ITEM EVIDENCE" style={{ flex: 1 }}>
      <SectionCard title="Selected item" icon={Ic("list-checks")}><div className="form"><Label textRole="caption">Item</Label><Label textRole="identifier">{q ? "d8293ddf" : "—"}</Label><Label textRole="caption">Attempt</Label><span>—</span><Label textRole="caption">Backend</Label><Label textRole="value">{q ? q.be : "—"}</Label><Label textRole="caption">Outputs</Label><div className="row" style={{ justifyContent: "space-between" }}><span>—</span><Button disabled style={{ minWidth: 110 }}>Open folder</Button></div></div></SectionCard>
      <div className="host" style={{ padding: 10, flex: 1, minHeight: 120, background: "var(--color-input)", border: "1px solid var(--color-border)" }}><Label textRole="identifier">03:37:13 &nbsp;created, items 1, cap 2</Label></div>
    </Panel></div> : null}
  </div><StatusBar /></>;
}
