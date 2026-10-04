"""Echo reply to backend-neutral inventory.

Absence from this list is evidence, not proof of failure: Draw Things can render models it does not list. The
service still gates requests on the listed model identifiers (a deliberate 2B choice recorded in the integration doc).
"""

from __future__ import annotations

from drawthings_py.generated.dt_grpc import image_service
from drawthings_py.models.types import ModelsInfo

from dtflow.backends.drawthings.errors import diagnostics, error
from dtflow.core.models import ErrorCategory, InventoryEntry, InventorySnapshot

LISTED = "listed_in_server_inventory"


def _default_weight(item: dict) -> float | None:
    value = item.get("weight")
    if isinstance(value, dict):
        value = value.get("value")
    if type(value) in (int, float):
        return float(value)
    return None


def build_snapshot(
    backend_id: str,
    reply: image_service.EchoReply,
    *,
    observed_at_utc: str,
    refreshed: bool,
    secret_sent: bool,
    endpoint: str | None = None,
) -> InventorySnapshot:
    try:
        info = ModelsInfo.from_echo_reply(reply)
        groups = (
            ("model", info.models),
            ("lora", info.loras),
            ("control", info.controlNets),
            ("other", info.upscalers),
            ("other", info.textualInversions),
        )
        entries: dict[str, InventoryEntry] = {}
        for kind, items in groups:
            for item in items:
                identifier = str(item["file"])
                entries.setdefault(
                    identifier,
                    InventoryEntry(
                        identifier,
                        kind,
                        str(item.get("name") or identifier),
                        str(item["version"]) if item.get("version") else None,
                        LISTED,
                        "echo.override",
                        trigger_prefix=str(item["prefix"]) if item.get("prefix") else None,
                        default_weight=_default_weight(item) if kind == "lora" else None,
                    ),
                )
        files = [str(name) for name in info.files]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise error(
            ErrorCategory.PROTOCOL, "inventory_decode_failed", "Inventory metadata could not be decoded."
        ) from exc
    for name in files:
        entries.setdefault(name, InventoryEntry(name, "unknown", name, None, LISTED, "echo.files"))
    identity = str(reply.server_identifier) if reply.server_identifier else "unknown"
    return InventorySnapshot(
        backend_id,
        identity,
        observed_at_utc,
        refreshed,
        tuple(entries.values()),
        diagnostics(
            entries=len(entries),
            files=len(files),
            model_metadata="present" if info.models else "absent",
            secret="sent" if secret_sent else "not_sent",
        ),
        endpoint=endpoint,
    )


def eligible_model_ids(snapshot: InventorySnapshot) -> tuple[str, ...]:
    """Identifiers the service may accept as base models.

    Unclassified files qualify only when the server supplied no model metadata at all; otherwise a classified
    inventory excludes LoRAs, controls and other files from the base-model gate.
    """
    metadata = dict(snapshot.diagnostics).get("model_metadata") == "present"
    return tuple(
        entry.identifier
        for entry in snapshot.entries
        if entry.kind == "model" or (entry.kind == "unknown" and not metadata)
    )
