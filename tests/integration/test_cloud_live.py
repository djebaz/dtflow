"""Explicit DT+ checks using the real adapter; generation runs only in the selected generation group."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from support.live_evidence import save_inventory
from support.network_guard import live_endpoints

from dtflow.backends.drawthings.cloud import DirectCloudBackend
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.core.models import ResolvedGenerationRequest, TimeoutPolicy
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.credentials import EnvironmentCredentials
from dtflow.services.generation import GenerationService


@asynccontextmanager
async def deployed_cloud():
    if not os.environ.get("DRAWTHINGS_API_KEY", "").strip():
        pytest.fail("DT+ checks require DRAWTHINGS_API_KEY in the environment.", pytrace=False)
    auth = CloudAuth(EnvironmentCredentials())
    adapter = DirectCloudBackend(auth, max_concurrency=1, timeouts=TimeoutPolicy(inventory=30.0, total=180.0))
    try:
        with live_endpoints(("api.drawthings.ai", 443), ("compute.drawthings.ai", 443)):
            await adapter.open()
            yield adapter
    finally:
        await adapter.aclose()


@pytest.mark.live
@pytest.mark.asyncio
async def test_cloud_token_connection_and_model_inventory(live_run):
    async with deployed_cloud() as adapter:
        status = await adapter.check_connection()
        assert (status.transport, status.authentication, status.inventory) == ("ok", "ok", "ok"), status.diagnostics
        inventory = await adapter.get_inventory(refresh=True)
        assert inventory.backend_id == "drawthings-cloud"
        save_inventory(live_run, "cloud", adapter.endpoint.label, inventory)


@pytest.mark.live_generation
@pytest.mark.asyncio
async def test_cloud_authorized_generation_and_saved_result(live_run):
    model = os.environ.get("DTFLOW_LIVE_MODEL", "").strip()
    if not model:
        pytest.fail("Set DTFLOW_LIVE_MODEL to an exact discovered model identifier before generation.", pytrace=False)
    # Small four-step workload based on the probe, using a wire-aligned size. One image, no LoRA or PAYG.
    request = ResolvedGenerationRequest(
        uuid4().hex,
        "A red apple on a white table, studio photograph",
        model,
        42,
        width_px=256,
        height_px=256,
        steps=4,
        guidance=1.0,
    )
    state = live_run / "state"
    store = SQLiteAttempts(state)
    await store.open()
    try:
        async with deployed_cloud() as adapter:
            service = GenerationService(adapter, store, live_run / "outputs", timeouts=adapter.timeouts)
            inventory = await service.get_inventory(refresh=True)
            save_inventory(live_run, "cloud", adapter.endpoint.label, inventory)
            saved = await service.generate(request)
            assert saved.backend_result.completion.terminal_status == "OK"
            assert saved.backend_result.completion.required_outputs_valid
            assert len(saved.output_paths) == 1 and Path(saved.metadata_path).is_file()
            assert Path(saved.output_paths[0]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            print(
                json.dumps(
                    {"attempt_id": saved.attempt_id, "outputs": saved.output_paths, "metadata": saved.metadata_path}
                )
            )
    finally:
        await store.aclose()
