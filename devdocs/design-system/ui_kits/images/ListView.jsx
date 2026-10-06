function ListView({ size, onOpen }) {
  const { DataTable } = window.DTFlowDesignSystem_cee425;
  const th = Math.round(size / 4);
  const thumb = (f) => <div className="gtile" style={{ width: Math.round(th * 1.4), height: th, borderRadius: 4, fontSize: 8 }}>{f.dim}</div>;
  const cols = [{ key: "i", label: "Image" }, { key: "f", label: "Filename" }, { key: "m", label: "Model" }, { key: "l", label: "LoRAs / weights / status" }, { key: "d", label: "Dimensions" }, { key: "z", label: "File size" }, { key: "t", label: "Date / time" }, { key: "p", label: "Positive prompt" }];
  const rows = IMG_FIX.map((f, n) => ({ i: thumb(f), f: "sample" + (n + 1) + ".png", m: "SDXL 1.0 (sample)", l: "portrait_enhancer_v2 · 0.8 · Unknown; film_photography_xl · 0.4 · Unknown", d: f.dim, z: "403 KB", t: "Not recorded", p: "Illustrative sample artwork; no recorded generation request." }));
  return <div className="host" style={{ flex: 1 }}><DataTable variant="gallery" columns={cols} rows={rows} onSelect={onOpen} /></div>;
}
