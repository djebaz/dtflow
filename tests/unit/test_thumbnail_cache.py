"""Thumbnail deletion stays inside ordinary cache shards, including parent checks."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support.network_guard import child_command

from dtflow.persistence import thumbnail_cache as cache


def redirect_directory(link: Path, target: Path, tmp_path: Path) -> None:
    """Use native Windows junctions or POSIX symlinks, only inside test fixtures."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return
    pwsh = shutil.which("pwsh.exe")
    if pwsh is None:
        pytest.skip("Native Windows junction coverage requires PowerShell 7.")
    script = tmp_path / "create-fixture-junction.ps1"
    script.write_text(
        "param([string]$Link, [string]$Target)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n",
        encoding="utf-8",
        newline="\n",
    )
    command = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script), "-Link", str(link), "-Target", str(target)]
    with child_command(command):
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    assert result.returncode == 0, "Could not create isolated native Windows junction fixture."
    assert link.is_junction()


def entry_for(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_enabled", True)
    monkeypatch.setattr(cache, "_format", "jpg")
    source = tmp_path / "original-fixture.bin"
    source.write_bytes(b"original remains unchanged")
    state, identity = tmp_path / "state", "fixture-output:1"
    entry = cache.entry_path(state, identity, source.stat(), 600)
    assert entry is not None
    return state, identity, entry, source


def remove(operation, state, identity, entry):
    if operation == "forget":
        return cache.forget(state, [identity])
    if operation == "prune":
        return cache.prune(state, limit=0, keep=0)
    cache.discard(state, entry)
    return None


@pytest.mark.parametrize("operation", ["forget", "discard", "prune"])
@pytest.mark.parametrize("redirect", ["shard", "category", "cache"])
def test_removal_refuses_redirected_parent_and_preserves_external_files(tmp_path, monkeypatch, operation, redirect):
    state, identity, entry, source = entry_for(tmp_path, monkeypatch)
    link = {"shard": entry.parent, "category": entry.parent.parent, "cache": state / "cache"}[redirect]
    outside = tmp_path / "external-sentinels"
    external_entry = outside / entry.relative_to(link)
    external_entry.parent.mkdir(parents=True)
    sentinels = {
        external_entry: b"external JPEG sentinel",
        external_entry.with_suffix(".webp"): b"external WebP sentinel",
        outside / "unrelated.txt": b"unrelated sentinel",
    }
    for path, content in sentinels.items():
        path.write_bytes(content)
    redirect_directory(link, outside, tmp_path)
    assert remove(operation, state, identity, entry) in (None, 0)
    assert {path: path.read_bytes() for path in sentinels} == sentinels
    assert source.read_bytes() == b"original remains unchanged"


@pytest.mark.parametrize("operation", ["forget", "discard", "prune"])
def test_ordinary_shard_removal_preserves_unrelated_files(tmp_path, monkeypatch, operation):
    state, identity, entry, source = entry_for(tmp_path, monkeypatch)
    entry.parent.mkdir(parents=True)
    webp = entry.with_suffix(".webp")
    unrelated = entry.parent / "unrelated.txt"
    for path in (entry, webp, unrelated):
        path.write_bytes(b"fixture")
    result = remove(operation, state, identity, entry)
    assert not entry.exists()
    if operation == "discard":
        assert result is None and webp.read_bytes() == b"fixture"
    else:
        assert result == 2 and not webp.exists()
    assert unrelated.read_bytes() == b"fixture"
    assert source.read_bytes() == b"original remains unchanged"


def test_discard_refuses_a_leaf_outside_the_cache_category(tmp_path, monkeypatch):
    state, _identity, _entry, source = entry_for(tmp_path, monkeypatch)
    external = tmp_path / "external.jpg"
    external.write_bytes(b"external sentinel")
    cache.discard(state, external)
    assert external.read_bytes() == b"external sentinel"
    assert source.read_bytes() == b"original remains unchanged"
