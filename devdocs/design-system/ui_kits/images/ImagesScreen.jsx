function ImagesScreen() {
  const [mode, setMode] = React.useState("detail"), [size, setSize] = React.useState(160), [i, setI] = React.useState(0);
  const open = (n) => { setI(n); setMode("detail"); };
  return <div style={{ height: "100vh", display: "flex", flexDirection: "column", gap: 8, padding: 8 }}>
    <GalleryHeader mode={mode} setMode={setMode} size={size} setSize={setSize} />
    <div style={{ flex: 1, minHeight: 0, display: "flex" }}>{mode === "grid" ? <GridView size={size} onOpen={open} /> : mode === "list" ? <ListView size={size} onOpen={open} /> : <DetailView i={i} setI={setI} size={size} />}</div>
  </div>;
}
