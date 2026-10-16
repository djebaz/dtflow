"""Validate the focused Swift URL repair and deterministic notebook generation offline."""
import ast
import base64
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from project_app.converter import swift_distribution

for ubuntu in ("22.04", "24.04"):
    url, folder = swift_distribution(ubuntu)
    expected = f"swift-6.0.3-RELEASE-ubuntu{ubuntu}"
    assert folder == expected
    assert url == f"https://download.swift.org/swift-6.0.3-release/ubuntu{ubuntu.replace('.', '')}/swift-6.0.3-RELEASE/{expected}.tar.gz"
try:
    swift_distribution("20.04")
except ValueError:
    pass
else:
    raise AssertionError("Unsupported Ubuntu must be rejected")
for path in (ROOT / "src").rglob("*.py"):
    ast.parse(path.read_text(), filename=str(path))

spec = importlib.util.spec_from_file_location("builder", ROOT / "tools/build_notebook.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
notebooks = []
for profile in ("full", "minimal"):
    path = builder.build(profile)
    first = path.read_bytes()
    builder.build(profile)
    assert first == path.read_bytes(), "Generation must be deterministic"
    notebook = json.loads(first)
    notebooks.append(notebook)
    assert len({c['id'] for c in notebook['cells']}) == len(notebook['cells'])
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code':
            continue
        source = ''.join(cell['source'])
        assert source.startswith('#@title') and cell['metadata']['cellView'] == 'form'
        assert cell['outputs'] == [] and cell['execution_count'] is None
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_EMBEDDED_PROJECT_FILES' for t in node.targets):
                assets = ast.literal_eval(node.value)
                for name, encoded in assets.items():
                    assert base64.b64decode(encoded) == (ROOT / name).read_bytes()
    sections = notebook['metadata']['colab']['collapsed_sections']
    assert all(any(c['id'] == s and c['cell_type'] == 'markdown' for c in notebook['cells']) for s in sections)
assert [c['source'] for c in notebooks[0]['cells'] if c['cell_type'] == 'code'] == [c['source'] for c in notebooks[1]['cells'] if c['cell_type'] == 'code']
print('PASS: Swift URLs/folders, unsupported OS, Python syntax, notebook schema, embedded-source parity, folding, profile code parity and deterministic rebuilds.')
