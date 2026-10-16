"""Generate both notebook profiles from recovered source cells and Python modules."""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
from dataclasses import fields
import sys
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def bundle():
    paths = [ROOT / "requirements.lock"] + sorted(
        p for p in (ROOT / "src/project_app").rglob("*.py")
        if "__pycache__" not in p.parts
    )
    data = {str(p.relative_to(ROOT)): p.read_bytes() for p in paths}
    digest = hashlib.sha256()
    for name in sorted(data):
        digest.update(name.encode() + b"\0" + data[name] + b"\0")
    return {k: base64.b64encode(v).decode() for k, v in sorted(data.items())}, digest.hexdigest()


def build(profile):
    embedded, revision = bundle()
    sys.path.insert(0, str(ROOT / "src"))
    from project_app.config import JobConfig
    defaults = {field.name: field.default for field in fields(JobConfig)}
    cells = []
    for path in sorted((ROOT / "src/cells").iterdir()):
        if path.suffix not in {".py", ".md"}:
            continue
        text = path.read_text()
        if path.name == "01_form.py":
            for name, value in defaults.items():
                text = re.sub(
                    r"^" + name + r" = [^\n]*",
                    lambda match, name=name, value=value: name + " = " + repr(value) + (" #@param " + match[0].split("#@param ", 1)[1] if "#@param " in match[0] else ""),
                    text, flags=re.M,
                )
        replacements = {
            "_EMBEDDED_PROJECT_FILES": json.dumps(embedded, sort_keys=True),
            "_EMBEDDED_PROJECT_REVISION": repr(revision),
            "_FORM_EMBEDDED_PROJECT_REVISION": repr(revision),
        }
        for name, value in replacements.items():
            text = re.sub(r"^" + name + r" = .*?$", lambda _: name + " = " + value + "  # generated", text, flags=re.M)
        cell = {"id": path.stem, "metadata": {"id": path.stem}, "source": text.splitlines(keepends=True)}
        if path.suffix == ".py":
            ast.parse(text, filename=str(path))
            cell.update(cell_type="code", execution_count=None, outputs=[])
            cell["metadata"]["cellView"] = "form"
        else:
            cell["cell_type"] = "markdown"
        cells.append(cell)
    metadata = json.loads((ROOT / "notebook_metadata.json").read_text())
    metadata["production_colab"]["profile"] = profile
    notebook = dict(nbformat=4, nbformat_minor=5, metadata=metadata, cells=cells)
    import nbformat
    nbformat.validate(nbformat.from_dict(notebook))
    output = ROOT / "notebooks" / f"production_colab_{profile}.ipynb"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n")
    print(f"Built {output}: {revision}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["full", "minimal", "both"], default="both")
    args = parser.parse_args()
    for profile in (["full", "minimal"] if args.profile == "both" else [args.profile]):
        build(profile)


if __name__ == "__main__":
    main()
