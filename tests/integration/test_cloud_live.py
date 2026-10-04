"""Explicit DT+ checks using the real adapter; generation runs only in the selected generation group."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from support.live_evidence import save_inventory, save_lora_inventory
from support.live_local import (
    CONNECT_FIX,
    UNREACHABLE_FIX,
    local_connect_problem,
    unreachable_reason,
    warn_if_model_browsing_off,
    warn_live,
)
from support.network_guard import endpoint, live_endpoints
from test_local_grpc_live import configured_local_adapter

from dtflow.backends.drawthings.cloud import DirectCloudBackend
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.core.models import BackendError, ResolvedGenerationRequest, TimeoutPolicy
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.credentials import EnvironmentCredentials
from dtflow.services.generation import GenerationService
from dtflow.services.lora_inventory import resolve_lora_inventory


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
@pytest.mark.cloud
@pytest.mark.asyncio
async def test_cloud_token_model_and_lora_inventory(live_run, request, live_scope):
    async with deployed_cloud() as cloud:
        status = await cloud.check_connection()
        assert (status.transport, status.authentication, status.inventory) == ("ok", "ok", "ok"), status.diagnostics
        cloud_inventory = await cloud.get_inventory(refresh=True)
        assert cloud_inventory.backend_id == "drawthings-cloud"
        save_inventory(live_run, "cloud", cloud.endpoint.label, cloud_inventory)

        if live_scope == "cloud":
            print("Cloud-only run: local LoRA inventory comparison omitted.")
            return
        local, host, port = configured_local_adapter()
        await local.open()
        try:
            with endpoint(host, port):
                try:
                    local_inventory = await local.get_inventory(refresh=True)
                except BackendError as exc:
                    failure = exc.failure
                    reason = unreachable_reason(failure.code, dict(failure.diagnostics).get("kind"), f"{host}:{port}")
                    kind = dict(failure.diagnostics).get("kind")
                    if reason is None and failure.code == "connect_failed":
                        # A local setup problem is reported by the local live check; it must not fail the cloud check.
                        reason, fix = local_connect_problem(f"{host}:{port}", failure.code, kind), CONNECT_FIX
                    elif reason is None:
                        raise
                    else:
                        fix = UNREACHABLE_FIX
                    # The cloud checks above already passed and stay retained; only local LoRA enrichment is omitted.
                    warn_live(request, reason, fix)
                    warn_live(request, "Cloud checks passed; local LoRA inventory was skipped.")
                    return
                save_inventory(live_run, "local", local.endpoint.label, local_inventory)
                warn_if_model_browsing_off(request, local_inventory)
                local_loras = tuple(entry for entry in local_inventory.entries if entry.kind == "lora")
                requested = os.environ.get("DTFLOW_LIVE_LORA", "").strip()
                if not local_loras and not requested:
                    print("LoRA enrichment omitted: local inventory has no structured LoRA entries.")
                    return
                if requested:
                    selected = next((entry for entry in local_loras if entry.identifier == requested), None)
                    assert selected is not None, "DTFLOW_LIVE_LORA is not present in the local LoRA inventory."
                else:
                    selected = max(local_loras, key=lambda entry: len(entry.identifier))

                lora_inventory = await resolve_lora_inventory(
                    local,
                    cloud,
                    local_snapshot=local_inventory,
                    cloud_snapshot=cloud_inventory,
                    lookup_identifiers=(selected.identifier,),
                )
                lora_path = save_lora_inventory(
                    live_run,
                    lora_inventory,
                    lookup_identifier=selected.identifier,
                )
                resolved = next(entry for entry in lora_inventory.entries if entry.identifier == selected.identifier)
                if requested:
                    # An explicit fixture is expected to be already available in the selected cloud account.
                    assert resolved.dt_cloud.availability in {"listed_in_server_inventory", "byom_reusable"}, (
                        f"Configured LoRA fixture is not currently reusable in DT+: "
                        f"{resolved.identifier} -> {resolved.dt_cloud.availability}"
                    )
                # Automatic selection is discovery: upload_required/unknown are legitimate observations.
                print(
                    json.dumps(
                        {
                            "lora_inventory": str(lora_path),
                            "dt_cloud_lookup_identifier": selected.identifier,
                            "dt_cloud_availability": resolved.dt_cloud.availability,
                        }
                    )
                )
        finally:
            await local.aclose()


@pytest.mark.live_generation
@pytest.mark.cloud
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
