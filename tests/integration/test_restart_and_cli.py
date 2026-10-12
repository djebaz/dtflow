"""Fresh-process crash recovery and headless CLI acceptance, all guarded."""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from dtflow.persistence.store import PersistenceError, SQLiteAttempts, patched_sqlite


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,expected",
    [
        ("prepared", "interrupted_before_submission"),
        ("possibly_submitted", "unknown"),
        ("saving", "save_failed"),
    ],
)
async def test_process_exit_at_durable_boundary_never_replays(tmp_path, guarded_python, boundary, expected):
    state = tmp_path / "state"
    child = f"""
assert "dtflow.backends.fake" not in sys.modules  # ordinary guarded children preserve import-time setup
import asyncio, os
from pathlib import Path
from dtflow.persistence.store import SQLiteAttempts
from dtflow.core.models import ResolvedGenerationRequest, SubmissionStage, RemoteOutcome
async def work():
    store = SQLiteAttempts(Path({str(state)!r}))
    await store.open()
    await store.create("crashed", "fake", ResolvedGenerationRequest("crash", "fixture", "fake-fixture", 0))
    if {boundary!r} != "prepared":
        await store.transition("crashed", ("prepared",), "possibly_submitted",
            SubmissionStage.POSSIBLY_SUBMITTED, RemoteOutcome.UNKNOWN)
    if {boundary!r} == "saving":
        await store.transition("crashed", ("possibly_submitted",), "saving",
            SubmissionStage.TERMINAL_OBSERVED, RemoteOutcome.SUCCEEDED)
    os._exit(7)
asyncio.run(work())
"""
    result = guarded_python(child)
    assert result.returncode == 7, result.stderr
    store = SQLiteAttempts(state)
    try:
        await store.open()
        rows = await store.inspect()
        assert len(rows) == 1 and rows[0]["state"] == expected
        assert rows[0]["request"]["seed"] == 0 and rows[0]["artifacts"] == []
    finally:
        await store.aclose()


def test_headless_cli_success_unknown_and_inspection(tmp_path, guarded_python):
    state = tmp_path / "state"
    environment = dict(os.environ, DRAWTHINGS_API_KEY="private-sentinel")

    def command(*args):
        argv = ["-q", str(tmp_path / "logs"), *args]
        return guarded_python(
            f"from dtflow.cli import main; raise SystemExit(main({argv!r}))", env=environment, fake_image=True
        )

    help_result = command("--help")
    assert help_result.returncode == 0 and "generate" in help_result.stdout
    version = command("--version")
    assert version.returncode == 0 and "DTFlow" in version.stdout
    generated = command("generate", "-b", "fake", "-p", "fixture", "-d", str(state), "-s", "0")
    assert generated.returncode == 0, generated.stderr
    paths = json.loads(generated.stdout)
    metadata = Path(paths["metadata"]).read_text()
    assert "private-sentinel" not in metadata + generated.stdout + generated.stderr
    assert json.loads(metadata)["request"]["seed"] == 0
    lost = command("generate", "-b", "fake", "-p", "fixture", "-d", str(state), "-f", "lost")
    assert lost.returncode == 4 and json.loads(lost.stderr.splitlines()[-1])["remote_outcome"] == "unknown"
    records = command("attempts", "-d", str(state))
    assert records.returncode == 0
    assert sorted(x["state"] for x in json.loads(records.stdout)) == ["succeeded", "unknown"]
    invalid = command("generate", "-b", "fake", "-p", "fixture", "-w", "300", "-d", str(state))
    assert invalid.returncode == 2
    assert len(json.loads(command("attempts", "-d", str(state)).stdout)) == 2
    health = command("check", "-b", "fake", "-d", str(state))
    models = command("models", "-b", "fake", "-r", "-nc", "-d", str(state))
    assert json.loads(health.stdout)["authentication"] == "not_applicable"
    assert json.loads(models.stdout)["refreshed"] is True


@pytest.mark.asyncio
async def test_store_ownership_schema_backup_and_durability(tmp_path):
    store, other = SQLiteAttempts(tmp_path / "state"), SQLiteAttempts(tmp_path / "state")
    try:
        await store.open()
        with pytest.raises(PersistenceError, match="Another process"):
            await other.open()
        flags = await store._run(
            lambda: (
                store._db().execute("PRAGMA journal_mode").fetchone()[0],
                store._db().execute("PRAGMA synchronous").fetchone()[0],
                store._db().execute("PRAGMA foreign_keys").fetchone()[0],
            )
        )
        assert flags == ("wal", 2, 1)
        await store.backup(tmp_path / "backup.sqlite3")
        assert (tmp_path / "backup.sqlite3").exists()
    finally:
        await other.aclose()
        await store.aclose()
    db = sqlite3.connect(tmp_path / "state" / "attempts.sqlite3")
    db.execute("PRAGMA user_version=999")
    db.close()
    reopened = SQLiteAttempts(tmp_path / "state")
    try:
        with pytest.raises(PersistenceError, match="Unsupported"):
            await reopened.open()
    finally:
        await reopened.aclose()
    with sqlite3.connect(tmp_path / "state" / "attempts.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999


@pytest.mark.parametrize(
    "version,expected",
    [
        ("3.51.2", False),
        ("3.51.3", True),
        ("3.53.1", True),
        ("3.50.6", False),
        ("3.50.7", True),
        ("3.44.6", True),
        ("3.45.0", False),
    ],
)
def test_sqlite_runtime_gate_only_accepts_fixed_releases(version, expected):
    assert patched_sqlite(version) is expected
