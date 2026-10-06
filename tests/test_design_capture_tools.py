"""Offline contracts for the development-only design-system capture tooling."""

import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "design_capture", Path(__file__).parents[1] / "tools/ui/design_capture.py"
)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def export(tmp_path, integrity, url="https://unpkg.com/react@18.3.1/umd/react.development.js"):
    folder = tmp_path / "ui_kits/example"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text(f'<script src="{url}" integrity="{integrity}"></script>', encoding="utf-8")
    return url


def test_verified_cache_is_offline_and_rejects_tampering(tmp_path, monkeypatch):
    data = b"fixture pinned JavaScript"
    integrity = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
    url = export(tmp_path, integrity)
    cache = tmp_path / "cache"
    cache.mkdir()
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".js")
    path.write_bytes(data)

    def network_forbidden(*args, **kwargs):
        raise AssertionError("Ordinary captures must not fetch vendors")

    monkeypatch.setattr(capture, "urlopen", network_forbidden)
    assert capture.verified_vendor(tmp_path, cache) == {url: data}
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        capture.verified_vendor(tmp_path, cache)
    path.unlink()
    with pytest.raises(ValueError, match="cache missing"):
        capture.verified_vendor(tmp_path, cache)


@pytest.mark.parametrize(
    "url,integrity",
    [("https://example.com/script.js", "sha384-x"), ("https://unpkg.com/react@18.3.1/umd/react.development.js", "")],
)
def test_export_rejects_unapproved_or_unpinned_vendors(tmp_path, url, integrity):
    export(tmp_path, integrity, url)
    with pytest.raises(ValueError, match="Unapproved or unpinned"):
        capture.vendor_sources(tmp_path)
