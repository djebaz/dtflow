"""Shared guard initialization precedes test collection and async client imports."""

import asyncio
import subprocess
import sys
from pathlib import Path

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
    if mode in ("live", "both"):
        if not os.environ.get("DTFLOW_LIVE_HOST") or not os.environ.get("DTFLOW_LIVE_PORT"):
            raise pytest.UsageError("Live checks require DTFLOW_LIVE_HOST (IP literal) and DTFLOW_LIVE_PORT.")
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
