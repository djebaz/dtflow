"""Offline pairing and portable HTML serialization contracts."""

import base64
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "compare_gallery", Path(__file__).parents[1] / "tools/ui/compare_gallery.py"
)
gallery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gallery)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6S5cAAAAASUVORK5CYII=")


def test_pairing_preserves_unmatched_and_original_bytes(tmp_path):
    qt, design = tmp_path / "qt", tmp_path / "design"
    for root in [qt, design]:
        (root / "states").mkdir(parents=True)
        (root / "states/30-buttons.png").write_bytes(PNG)
    (qt / "states/31-connection-button.png").write_bytes(PNG)
    (design / "debug").mkdir()
    (design / "debug/30-buttons.png").write_bytes(PNG)
    (design / "states/manifest.json").write_text(
        json.dumps({"limits": ["</script>"], "unsupported": [{"state": "busy"}]})
    )
    data = gallery.build_data(qt, design)
    assert [pair["name"] for pair in data["pairs"]] == ["states/30-buttons.png", "states/31-connection-button.png"]
    assert data["pairs"][1]["design"] is None
    record = data["pairs"][0]["qt"]
    assert (record["width"], record["height"]) == (1, 1)
    assert base64.b64decode(record["src"].split(",")[1]) == PNG
    assert "</script>" not in json.dumps(data).replace("<", "\\u003c")
    assert data["evidence"][0]["unsupported"] == [{"state": "busy"}]


def test_empty_or_invalid_capture_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="No PNG captures"):
        gallery.build_data(tmp_path, tmp_path)
    (tmp_path / "invalid.png").write_bytes(b"not an image")
    with pytest.raises(ValueError, match="Invalid PNG header"):
        gallery.build_data(tmp_path, tmp_path)
