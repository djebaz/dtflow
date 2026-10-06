function GridView({ size, onOpen }) {
  const { ImageCard } = window.DTFlowDesignSystem_cee425;
  return <div className="ggrid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(" + size + "px,1fr))" }}>
    {IMG_FIX.map((f, n) => <ImageCard key={f.id} badge={f.dim} title={"sample" + (n + 1) + ".png · 403 KB"} subtitle="SDXL 1.0 (sample)" onClick={() => onOpen(n)} />)}
  </div>;
}
