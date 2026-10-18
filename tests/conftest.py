"""Shared guard initialization precedes test collection and async client imports."""

import asyncio
import json
import subprocess
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from textwrap import indent
from uuid import uuid4

import pytest
import pytest_asyncio
from support.fake_image import use_fake_image
from support.network_guard import child_command, endpoint, install, live_allowed, reset_registry

install()


def pytest_addoption(parser):
    parser.addoption(
        "--dtflow-test-mode",
        choices=("offline", "live", "generation", "both"),
        default=None,
        help="Internal selection used by dtflow test.",
    )
    parser.addoption(
        "--dtflow-live-scope",
        choices=("local", "cloud", "both"),
        default="both",
        help="Internal live-check scope used by dtflow test.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: explicitly selected non-generative live checks")
    config.addinivalue_line("markers", "live_generation: separately authorized named live generation workload")
    config.addinivalue_line("markers", "local: live check against the local Draw Things server")
    config.addinivalue_line("markers", "cloud: live check against DT+ cloud")


def pytest_collection_modifyitems(config, items):
    import os

    mode = config.getoption("--dtflow-test-mode")
    scope = config.getoption("--dtflow-live-scope")
    if scope != "both":
        dropped = [item for item in items if item.get_closest_marker("local" if scope == "cloud" else "cloud")]
        items[:] = [item for item in items if item not in dropped]
        config.hook.pytest_deselected(items=dropped)
    if mode in ("generation", "both") and not any(item.get_closest_marker("live_generation") for item in items):
        raise pytest.UsageError("No live generation tests are implemented yet.")
    cloud_live = mode in ("live", "both") and scope != "local"
    if cloud_live or mode in ("generation", "both"):
        if not os.environ.get("DRAWTHINGS_API_KEY", "").strip():
            raise pytest.UsageError("DT+ live checks require DRAWTHINGS_API_KEY in the environment.")
    if mode in ("live", "both") and scope != "cloud":
        if not os.environ.get("DTFLOW_LIVE_HOST") or not os.environ.get("DTFLOW_LIVE_PORT"):
            raise pytest.UsageError("Local live checks require DTFLOW_LIVE_HOST (IP literal) and DTFLOW_LIVE_PORT.")
    if mode in ("generation", "both") and not os.environ.get("DTFLOW_LIVE_MODEL", "").strip():
        raise pytest.UsageError("DT+ generation requires an exact DTFLOW_LIVE_MODEL identifier.")
    for item in items:
        generation = item.get_closest_marker("live_generation") is not None
        if generation or item.get_closest_marker("live") is not None:
            allowed = (
                (mode in ("generation", "both") if generation else mode in ("live", "both"))
                if mode
                else live_allowed(config.option.markexpr, os.environ, generation)
            )
            if not allowed:
                item.add_marker(
                    pytest.mark.skip(reason="Explicit live selection, opt-ins and workload authorization required.")
                )


@pytest.fixture
def live_scope(request):
    return request.config.getoption("--dtflow-live-scope")


@pytest.fixture(scope="session")
def fake_fixture_image(tmp_path_factory):
    """Generate a small RGB PNG for fake outputs without requiring bundled artwork."""
    from dtflow.core.png import fixture_png

    path = tmp_path_factory.mktemp("fake-image") / "fixture.png"
    path.write_bytes(fixture_png(96, 64, 42, grid=True))
    return path


@pytest.fixture(autouse=True)
def fake_image_source(fake_fixture_image):
    with use_fake_image(fake_fixture_image):
        yield


@pytest_asyncio.fixture
async def owning_loop():
    """Resolve the pytest-asyncio runner before installing per-test client state."""
    return asyncio.get_running_loop()


@pytest.fixture(autouse=True)
def offline_guard(owning_loop):
    # Dependency is explicit: Windows runner startup precedes test-body admission.
    assert not owning_loop.is_closed()
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def fixture_endpoint():
    return endpoint


@pytest.fixture
def guarded_python(fake_fixture_image):
    """Install the child guard first; fake image substitution is explicitly selected."""
    support = Path(__file__).parent / "support"

    def run(code: str, *, env=None, timeout=10, fake_image=False):
        bootstrap = (
            f"import sys; sys.path.insert(0, {str(support)!r}); from network_guard import install; install(); \n"
        )
        if fake_image:
            bootstrap += (
                "from pathlib import Path\nfrom fake_image import use_fake_image\n"
                f"with use_fake_image(Path({str(fake_fixture_image)!r})):\n"
            )
            # Indenting also changes multiline string contents; use this opt-in only for indentation-safe code.
            code = indent(code, "    ")
        command = [sys.executable, "-c", bootstrap + code]
        with child_command(command):
            return subprocess.run(command, text=True, capture_output=True, env=env, timeout=timeout)

    return run


@pytest.fixture(scope="session")
def live_run(request):
    """Retain one isolated evidence directory per explicitly executed live-test invocation."""
    from dtflow.build import identity

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    root = Path(__file__).resolve().parents[1] / "test-runs" / run_id
    root.mkdir(parents=True)
    request.config._dtflow_live_root = root
    request.config._dtflow_live_reports = []
    request.config._dtflow_live_build = identity()
    return root


def _live_warnings(properties) -> list[dict]:
    """Pair each ("warning", text) with the ("fix", text) recorded right after it, if any."""
    notes: list[dict] = []
    for key, value in properties:
        if key == "warning":
            notes.append({"message": value, "fix": None})
        elif key == "fix" and notes:
            notes[-1]["fix"] = value
    return notes


@pytest.hookimpl(hookwrapper=True)
def pytest_sessionstart(session):
    """Let the xdist controller spawn only its exact execnet worker bootstraps; workers re-install the guard."""
    config = session.config
    if hasattr(config, "workerinput") or not config.pluginmanager.has_plugin("dsession"):
        yield
        return
    from execnet.gateway_io import popen_bootstrapline

    with ExitStack() as stack:
        for flags in (["-u"], ["-u", "-B"]):
            stack.enter_context(child_command([sys.executable, *flags, "-c", popen_bootstrapline]))
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    if hasattr(item.config, "_dtflow_live_reports") and (
        item.get_closest_marker("live") or item.get_closest_marker("live_generation")
    ):
        item.config._dtflow_live_reports.append(
            {
                "test": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "seconds": report.duration,
                **({"warnings": notes} if (notes := _live_warnings(report.user_properties)) else {}),
            }
        )


def pytest_terminal_summary(terminalreporter):
    root = getattr(terminalreporter.config, "_dtflow_live_root", None)
    if root is not None:
        evidence = {
            "run_id": root.name,
            "build": terminalreporter.config._dtflow_live_build,
            "checks": terminalreporter.config._dtflow_live_reports,
        }
        (root / "results.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        terminalreporter.write_line(f"Live-test evidence: {root}")
        _write_live_warnings(terminalreporter, terminalreporter.config._dtflow_live_reports)


def _write_live_warnings(terminalreporter, checks) -> None:
    """Print each distinct live-check warning with how to resolve it, instead of raw pytest warning text."""
    seen: dict[str, str | None] = {}
    for check in checks:
        for note in check.get("warnings", []):
            seen.setdefault(note["message"], note["fix"])
    if not seen:
        return
    terminalreporter.write_sep("-", "DTFlow live-check warnings (not failures)", yellow=True, bold=True)
    for message, fix in seen.items():
        terminalreporter.write_line(f"WARNING: {message}", yellow=True)
        if fix:
            terminalreporter.write_line(f"  To fix: {fix}", cyan=True, bold=True)
