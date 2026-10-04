"""Shared guard initialization precedes test collection and async client imports."""

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from support.network_guard import child_command, endpoint, install, live_allowed, reset_registry

install()


def pytest_addoption(parser):
    parser.addoption(
        "--dtflow-test-mode",
        choices=("offline", "live", "generation", "both"),
        default=None,
        help="Internal selection used by dtflow test.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: explicitly selected non-generative live checks")
    config.addinivalue_line("markers", "live_generation: separately authorized named live generation workload")


def pytest_collection_modifyitems(config, items):
    import os

    mode = config.getoption("--dtflow-test-mode")
    if mode in ("generation", "both") and not any(item.get_closest_marker("live_generation") for item in items):
        raise pytest.UsageError("No live generation tests are implemented yet.")
    if mode in ("live", "generation", "both"):
        if not os.environ.get("DRAWTHINGS_API_KEY", "").strip():
            raise pytest.UsageError("DT+ live checks require DRAWTHINGS_API_KEY in the environment.")
    if mode in ("live", "both"):
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
def guarded_python():
    """Run a fixed Python child that installs the same guard before application imports."""
    support = Path(__file__).parent / "support"

    def run(code: str, *, env=None, timeout=10):
        bootstrap = f"import sys; sys.path.insert(0, {str(support)!r}); from network_guard import install; install(); "
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
                **({"warnings": notes} if (notes := [v for k, v in report.user_properties if k == "warning"]) else {}),
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
