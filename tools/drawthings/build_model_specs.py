"""Generate ``community_model_specs.json``: Draw Things model descriptions for FLUX and Krea checkpoints.

Source: the public ``drawthingsai/community-models`` repository (``models.txt`` and each model's ``metadata.json``).
Each converted checkpoint file gets the model's description, which DT+ cloud needs in ``override.models`` for models
missing from its built-in list. Usage: ``uv run python tools/drawthings/build_model_specs.py [out.json]``.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/drawthingsai/community-models/main"
OUT = Path(__file__).parents[2] / "src" / "dtflow" / "resources" / "catalogue" / "community_model_specs.json"
WANTED = re.compile(r"flux|krea", re.IGNORECASE)
DROP = {"converted", "note", "copyright", "hugging_face_link"}
SUPPORT = re.compile(r"(vae|encoder|clip|mistral|qwen_3|t5_)", re.IGNORECASE)


def fetch(path: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=30) as response:  # noqa: S310
        return response.read().decode()


def main(out: Path) -> None:
    specs: dict[str, dict] = {}
    for name in fetch("models.txt").split():
        if not WANTED.search(name):
            continue
        meta = json.loads(fetch(f"models/{name}/metadata.json"))
        spec = {key: value for key, value in meta.items() if key not in DROP}
        for file in meta.get("converted", {}):
            if SUPPORT.search(file) and not file.startswith(("flux_", "krea_")):
                continue
            if file.endswith("_vae_f16.ckpt"):
                continue
            specs[file] = {**spec, "file": file}
    out.write_text(json.dumps(dict(sorted(specs.items())), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(specs)} specs -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
