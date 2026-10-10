"""Generate ``community_model_specs.json``: Draw Things model descriptions for the community model list.

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
DROP = {"converted", "note", "copyright", "hugging_face_link"}
_SUFFIX = re.compile(r"(?:_(?:f16|f32|bf16|q\d+p?|\d+bit|i\d+x|svd))+$")


def model_key(file: str) -> str:
    return _SUFFIX.sub("", file.lower().removesuffix(".ckpt"))


def fetch(path: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=30) as response:  # noqa: S310
        return response.read().decode()


def main(out: Path) -> None:
    specs: dict[str, dict] = {}
    for name in fetch("models.txt").split():
        meta = json.loads(fetch(f"models/{name}/metadata.json"))
        if not isinstance(meta.get("file"), str) or not meta.get("version"):
            continue
        spec = {key: value for key, value in meta.items() if key not in DROP}
        for file in meta.get("converted", {}):
            if model_key(file) != model_key(meta["file"]):
                continue  # text encoders, autoencoders and other support files
            specs[file] = {**spec, "file": file}
    out.write_text(json.dumps(dict(sorted(specs.items())), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(specs)} specs -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
