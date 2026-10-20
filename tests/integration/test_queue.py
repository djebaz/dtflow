"""Phase 3B durable queues, rolling dispatch, controls, uncertainty and saved-state compatibility (3B-O1 to O5, O10).

A scripted backend wraps the fake backend so each test decides which item finishes when; everything else (generation
service, attempts, SQLite queue state, saved outputs) is the real implementation.
"""

import asyncio
import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from dtflow.backends.fake import FakeBackend
from dtflow.core.models import (
    BackendError,
    BackendFailure,
    ErrorCategory,
    RemoteOutcome,
    ResolvedGenerationRequest,
    ResourceLimits,
    SubmissionStage,
)
from dtflow.persistence.queues import QueueError, post_control, read_queues
from dtflow.persistence.store import SCHEMA_VERSION, PersistenceError, SQLiteAttempts, state_is_owned
from dtflow.services.generation import GenerationService
from dtflow.services.queue import QueueDispatcher

BACKEND = {"name": "fake", "endpoint": None, "scenario": "success"}


def item(seed: int) -> ResolvedGenerationRequest:
    return ResolvedGenerationRequest(f"request-{seed}", "fixture", "fake-fixture", seed)


class ScriptedBackend:
    """Fake backend whose items wait on a per-seed gate; ``lose`` seeds end with an unknown outcome."""

    def __init__(self, max_concurrency: int = 4, *, open_all: bool = False, lose: tuple[int, ...] = ()):
        self.inner = FakeBackend()
        # Small limits keep the shared buffer budget (default 256 MiB, about two real requests) out of the way.
        limits = ResourceLimits(
            artifact_bytes=2**20,
            final_bytes=2**22,
            transport_bytes=2**22,
            tensor_bytes=2**22,
            preview_bytes=2**18,
            buffer_budget=2**26,
        )
        self._capabilities = dataclasses.replace(
            self.inner.capabilities, max_concurrency=max_concurrency, limits=limits
        )
        self.gates: dict[int, asyncio.Event] = {}
        self.open_all, self.lose = open_all, set(lose)
        self.started: list[int] = []
        self.completed: list[int] = []
        self.active = self.peak = 0

    capabilities = property(lambda self: self._capabilities)

    def release(self, *seeds: int) -> None:
        for seed in seeds:
            self.gates.setdefault(seed, asyncio.Event()).set()

    async def open(self):
        await self.inner.open()

    async def get_inventory(self, refresh=False):
        return await self.inner.get_inventory(refresh)

    async def check_connection(self):
        return await self.inner.check_connection()

    async def generate(self, request, *, on_event):
        seed = request.seed
        self.started.append(seed)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if not self.open_all:
                await self.gates.setdefault(seed, asyncio.Event()).wait()
            if seed in self.lose:
                raise BackendError(
                    BackendFailure(
                        ErrorCategory.CONNECTIVITY,
                        "connection_lost",
                        "Scripted loss.",
                        request.request_id,
                        SubmissionStage.POSSIBLY_SUBMITTED,
                        RemoteOutcome.UNKNOWN,
                    )
                )
            result = await self.inner.generate(request, on_event=on_event)
            self.completed.append(seed)
            return result
        finally:
            self.active -= 1

    async def aclose(self):
        await self.inner.aclose()


# Waits are ceilings, not delays: a passing run returns as soon as the condition holds, and the generous limit only
# keeps slow or heavily shared CI runners from turning scheduling latency into a TimeoutError.
SETTLE = 30.0


async def until(predicate, timeout: float = SETTLE) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class Harness:
    """One owner process: store, backend, service and dispatcher on a state directory."""

    def __init__(self, state: Path, backend: ScriptedBackend):
        self.state, self.backend = state, backend
        self.store = SQLiteAttempts(state)

    async def __aenter__(self):
        await self.store.open()
        await self.backend.open()
        self.service = GenerationService(self.backend, self.store, self.state / "outputs")
        self.dispatcher = QueueDispatcher(self.store, self.service, poll=0.005)
        return self

    async def __aexit__(self, *exc):
        await self.backend.aclose()
        await self.store.aclose()

    def run(self, queue_id: str) -> asyncio.Task:
        return asyncio.create_task(self.dispatcher.run(queue_id))

    async def states(self, queue_id: str) -> list[str]:
        return [x["state"] for x in (await self.store.inspect_queue(queue_id))["items"]]


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


@pytest.mark.asyncio
async def test_ten_items_four_slots_roll_without_a_group_barrier(state):
    """3B-O1: items 1-4 start, out-of-order completion admits item 5 at once, bounded, identity stable."""
    backend = ScriptedBackend(4)
    async with Harness(state, backend) as h:
        queue_id = await h.store.create_queue(BACKEND, 4, [item(n) for n in range(10)])
        assert await h.states(queue_id) == ["pending"] * 10  # a new queue is paused: nothing is eligible yet
        run = h.run(queue_id)
        peak_reserved = 0

        async def watch(predicate):
            nonlocal peak_reserved

            async def poll():
                nonlocal peak_reserved
                while not predicate():
                    peak_reserved = max(peak_reserved, (await h.store.inspect_queue(queue_id))["summary"]["running"])
                    await asyncio.sleep(0.005)

            async with asyncio.timeout(SETTLE):
                await poll()

        await watch(lambda: backend.started == [0, 1, 2, 3])
        await asyncio.sleep(0.05)
        assert backend.started == [0, 1, 2, 3]  # the cap holds while all four are active
        backend.release(2)
        await watch(lambda: 4 in backend.started)
        assert backend.started == [0, 1, 2, 3, 4] and backend.active == 4  # refilled before the group completed
        backend.release(*range(10))
        final = await asyncio.wait_for(run, SETTLE)
    assert backend.peak <= 4 and peak_reserved <= 4
    assert backend.started == list(range(10)) and backend.completed[0] == 2  # dispatch in queue order
    assert [x["position"] for x in final["items"]] == list(range(1, 11))
    assert [x["seed"] for x in final["items"]] == list(range(10))  # presentation keeps queue order
    assert {x["state"] for x in final["items"]} == {"succeeded"} and final["status"] == "finished"
    assert all(Path(x["artifacts"][0]).is_file() for x in final["items"])


@pytest.mark.asyncio
async def test_pause_resume_cancel_controls_preserve_honest_evidence(state):
    """3B-O2: Pause stops refill but not active work, Resume continues, Cancel never claims remote cancellation."""
    backend = ScriptedBackend(2)
    async with Harness(state, backend) as h:
        queue_id = await h.store.create_queue(BACKEND, 2, [item(n) for n in range(6)])
        run = h.run(queue_id)
        await until(lambda: backend.started == [0, 1])
        post_control(state, queue_id, "pause")  # posted from a separate connection while this process owns dispatch
        await until(lambda: any(e["kind"] == "pause" for e in read_queues(state, queue_id)[0]["events"]))
        backend.release(0)
        await until(lambda: backend.completed == [0])
        await asyncio.sleep(0.05)
        assert backend.started == [0, 1]  # paused: the released slot was not refilled
        backend.release(1)
        await until(lambda: sorted(backend.completed) == [0, 1])
        await asyncio.sleep(0.05)
        assert backend.started == [0, 1] and not run.done()  # idle but alive, waiting for Resume
        post_control(state, queue_id, "resume")
        await until(lambda: backend.started == [0, 1, 2, 3])
        post_control(state, queue_id, "cancel")
        final = await asyncio.wait_for(run, SETTLE)
        assert backend.started == [0, 1, 2, 3]  # pending items 5 and 6 were never dispatched
    states = [x["state"] for x in final["items"]]
    assert states == ["succeeded", "succeeded", "unknown", "unknown", "cancelled", "cancelled"]
    assert final["status"] == "cancelled"
    interrupted = (await _failure(state, final, 2))["cancellation"]
    assert interrupted["remote_confirmed"] is False and interrupted["outcome_unknown"] is True
    assert all(Path(x["artifacts"][0]).is_file() for x in final["items"][:2])  # completed outputs survive


async def _failure(state, queue, index):
    store = SQLiteAttempts(state)
    await store.open()
    try:
        return (await store.inspect(queue["items"][index]["attempt_id"]))[0]["failure"]
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_unknown_outcome_is_aborted_never_blocks_and_never_replays(state):
    """3B-O3: an unknown item is settled as aborted, releases its reservation, never blocks or replays."""
    backend = ScriptedBackend(1, open_all=True, lose=(1,))
    async with Harness(state, backend) as h:
        queue_id = await h.store.create_queue(BACKEND, 1, [item(n) for n in range(4)])
        final = await asyncio.wait_for(h.run(queue_id), SETTLE)
    assert [x["state"] for x in final["items"]] == ["succeeded", "unknown", "succeeded", "succeeded"]
    assert final["status"] == "finished" and "blocked_by_uncertainty" not in final
    assert backend.started == [0, 1, 2, 3]  # the unknown item was dispatched once and never replayed
    assert final["items"][1]["attempt_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,attempt_state,item_state",
    [
        ("reserved", None, "pending"),  # crashed after the reservation, before any attempt existed
        ("possibly_submitted", "possibly_submitted", "unknown"),
        ("saving", "saving", "save_failed"),
        ("finished", "succeeded", "succeeded"),  # attempt committed, item settlement never ran
    ],
)
async def test_queue_crash_boundaries_reconcile_without_replay(state, boundary, attempt_state, item_state):
    """3B-O3: every new queue write boundary restores to an honest state; ambiguous items are never eligible."""
    from dtflow.core.models import RemoteOutcome as Outcome
    from dtflow.core.models import SubmissionStage as Stage

    store = SQLiteAttempts(state)
    await store.open()
    queue_id = await store.create_queue(BACKEND, 1, [item(0), item(1)])
    await store.set_running(queue_id)
    item_id, request = await store.claim_next(queue_id, 4)
    if attempt_state:
        await store.create("attempt-0", "fake", request)
        if attempt_state != "prepared":
            await store.transition(
                "attempt-0", ("prepared",), "possibly_submitted", Stage.POSSIBLY_SUBMITTED, Outcome.UNKNOWN
            )
        if attempt_state in ("saving", "succeeded"):
            await store.transition(
                "attempt-0", ("possibly_submitted",), "saving", Stage.TERMINAL_OBSERVED, Outcome.SUCCEEDED
            )
        if attempt_state == "succeeded":
            await store.transition("attempt-0", ("saving",), "succeeded", Stage.TERMINAL_OBSERVED, Outcome.SUCCEEDED)
    await store.aclose()  # simulated process exit with the item still reserved
    store = SQLiteAttempts(state)
    try:
        await store.open()
        queue = await store.inspect_queue(queue_id)
        assert [x["state"] for x in queue["items"]] == [item_state, "pending"]
        assert queue["state"] == "paused"
        assert await store.claim_next(queue_id, 4) is None  # restored paused: nothing is eligible until start
        await store.set_running(queue_id)
        claim = await store.claim_next(queue_id, 4)
        # The ambiguous item itself is never eligible again; an unknown outcome no longer blocks the next item.
        assert claim is not None and claim[1].seed == (0 if item_state == "pending" else 1)
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_cap_reduction_and_local_lane_policy(state):
    """3B-O4: a lowered cap withholds dispatch until reservations fall below it; one-lane backends stay at one."""
    backend = ScriptedBackend(4)
    async with Harness(state, backend) as h:
        queue_id = await h.store.create_queue(BACKEND, 4, [item(n) for n in range(7)])
        run = h.run(queue_id)
        await until(lambda: backend.started == [0, 1, 2, 3])
        post_control(state, queue_id, "cap", {"cap": 2})
        backend.release(0, 1)
        await until(lambda: sorted(backend.completed) == [0, 1])
        await asyncio.sleep(0.05)
        assert backend.started == [0, 1, 2, 3] and backend.active == 2  # active work is never cancelled
        backend.release(2)
        await until(lambda: 4 in backend.started)
        await asyncio.sleep(0.05)
        assert backend.started == [0, 1, 2, 3, 4] and backend.active == 2  # refill only below the new cap
        backend.release(*range(7))
        final = await asyncio.wait_for(run, SETTLE)
        assert [x["state"] for x in final["items"]] == ["succeeded"] * 7 and backend.peak == 4
    lane = ScriptedBackend(1, open_all=True)  # local gRPC policy: one lane whatever the queue maximum says
    async with Harness(state.parent / "lane", lane) as h:
        queue_id = await h.store.create_queue(BACKEND, 4, [item(n) for n in range(4)])
        await asyncio.wait_for(h.run(queue_id), SETTLE)
    assert lane.peak == 1 and lane.started == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_dispatch_ownership_and_non_owner_access(state):
    """3B-O5: one owner per state directory; non-owners only read and post controls; a closed owner cannot dispatch."""
    store = SQLiteAttempts(state)
    other = SQLiteAttempts(state)
    assert not state_is_owned(state) and not (state / "owner.lock").exists()
    await store.open()
    assert state_is_owned(state)
    queue_id = await store.create_queue(BACKEND, 1, [item(0)])
    try:
        with pytest.raises(PersistenceError, match="Another process"):
            await other.open()
        assert read_queues(state, queue_id)[0]["summary"]["pending"] == 1
        post_control(state, queue_id, "pause")
        with pytest.raises(QueueError):
            post_control(state, "missing", "pause")
        with pytest.raises(QueueError):
            post_control(state, queue_id, "cap", {"cap": 9})
        assert await store.apply_controls(queue_id) == ()  # Pause on a paused queue is recorded as ignored
    finally:
        await other.aclose()
        await store.aclose()
    lock_bytes = (state / "owner.lock").read_bytes()
    assert not state_is_owned(state)
    assert (state / "owner.lock").read_bytes() == lock_bytes
    with pytest.raises(PersistenceError, match="closed"):
        await store.claim_next(queue_id, 4)  # an old owner cannot continue dispatch


V1_ATTEMPTS = """CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
    backend_id TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL, submission_stage TEXT NOT NULL,
    remote_outcome TEXT NOT NULL, updated_at_utc TEXT NOT NULL, failure_json TEXT,
    artifacts_json TEXT NOT NULL DEFAULT '[]')"""


def v1_database(state: Path, *extra: str) -> Path:
    state.mkdir(parents=True)
    path = state / "attempts.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(V1_ATTEMPTS)
        legacy = dataclasses.asdict(dataclasses.replace(item(0), request_id="r1"))
        for name in ("schema_version", "strength", "image_inputs"):
            legacy.pop(name)
        db.execute(
            "INSERT INTO attempts VALUES ('a1','r1','fake',?,'succeeded','terminal_observed','succeeded','t',NULL,'[\"x\"]')",
            (json.dumps(legacy),),
        )
        for statement in extra:
            db.execute(statement)
        db.execute("PRAGMA user_version=1")
    return path


def snapshot(path: Path) -> tuple:
    with sqlite3.connect(path) as db:
        return (
            db.execute("PRAGMA user_version").fetchone()[0],
            sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")),
            db.execute("SELECT attempt_id, state, artifacts_json FROM attempts").fetchall(),
        )


@pytest.mark.asyncio
async def test_release_0_1_state_migrates_atomically_and_failures_keep_the_original(state):
    """3B-O10: a version 1 database upgrades with its attempts intact; a failed migration leaves it untouched."""
    path = v1_database(state / "good")
    store = SQLiteAttempts(state / "good")
    try:
        await store.open()
        assert (await store.inspect("a1"))[0]["artifacts"] == ["x"]
        queue_id = await store.create_queue(BACKEND, 1, [item(0)])
        assert (await store.inspect_queue(queue_id))["state"] == "paused"
    finally:
        await store.aclose()
    assert snapshot(path)[0] == SCHEMA_VERSION
    backup = next((state / "good").glob(f"attempts.pre-v{SCHEMA_VERSION}-from-v1-*.sqlite3"))
    assert snapshot(backup)[0] == 1  # recoverable pre-migration copy
    # A conflicting object makes the migration fail midway; the rollback must leave version 1 exactly as it was.
    bad = v1_database(state / "bad", "CREATE TABLE queue_items (unrelated TEXT)")
    before = snapshot(bad)
    broken = SQLiteAttempts(state / "bad")
    try:
        with pytest.raises(PersistenceError):
            await broken.open()
    finally:
        await broken.aclose()
    assert snapshot(bad) == before and before[0] == 1 and "queues" not in before[1]


@pytest.fixture
def invoke(tmp_path, capsys):
    from dtflow import cli

    def call(*args):
        code = cli.main(["-q", str(tmp_path / "logs"), "queue", *args, "-d", str(tmp_path / "cli-state")])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return call


def test_queue_cli_add_start_controls_and_exit_codes(invoke):
    """3B-O2 through the CLI: persisted paused queue, ordered results, controls, uncertainty exit and invalid input."""
    code, out, _ = invoke("add", "-b", "fake", "-p", "fixture", "-k", "3", "-s", "7")
    queue_id = json.loads(out)["queue_id"]
    assert code == 0 and json.loads(out)["state"] == "paused"
    listed = json.loads(invoke("list")[1])[0]
    assert listed["status"] == "paused" and listed["summary"]["pending"] == 3 and "items" not in listed
    code, out, err = invoke("start", queue_id)
    shown = json.loads(invoke("show", queue_id)[1])
    assert code == 0 and err.count(": succeeded") == 3
    assert [x["seed"] for x in shown["items"]] == [7, 8, 9] and shown["status"] == "finished"
    assert {x["state"] for x in shown["items"]} == {"succeeded"}
    assert Path(shown["items"][0]["artifacts"][0]).is_file()
    # Cancel before start leaves everything cancelled and dispatches nothing.
    cancelled = json.loads(invoke("add", "-b", "fake", "-p", "fixture", "-k", "2")[1])["queue_id"]
    assert invoke("cancel", cancelled)[0] == 0
    code, out, _ = invoke("start", cancelled)
    assert code == 3 and json.loads(out)["summary"]["cancelled"] == 2
    # An unknown outcome is settled as aborted: it fails the run (exit 3), blocks nothing and is not replayed.
    lost = json.loads(invoke("add", "-b", "fake", "-p", "fixture", "-k", "2", "-j", "1", "-f", "lost")[1])["queue_id"]
    code, out, err = invoke("start", lost)
    summary = json.loads(out)["summary"]
    assert code == 3 and summary["unknown"] == 2 and summary["pending"] == 0
    assert invoke("start", lost)[0] == 3
    assert json.loads(invoke("show", lost)[1])["summary"]["unknown"] == 2
    # Invalid input never creates or controls anything.
    for bad in (("add", "-b", "fake", "-k", "0"), ("add", "-b", "local"), ("show", "missing"), ("pause", "missing")):
        assert invoke(*bad)[0] == 2
    assert invoke("cap", lost, "-j", "9")[0] == 2
