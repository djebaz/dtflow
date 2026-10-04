"""Read-only DT+ LoRA storage resolution through the verified BYOM metadata endpoint."""

from __future__ import annotations

from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.backends.drawthings.errors import error
from dtflow.core.models import ErrorCategory, FileIdentity, LoRABackendAvailability


async def lookup_lora_assets(
    auth: CloudAuth, identities: tuple[FileIdentity, ...]
) -> dict[str, LoRABackendAvailability]:
    """Resolve known converted LoRA fingerprints without uploading bytes."""
    usable = tuple(item for item in identities if item.exists and item.sha256 is not None)
    if not usable:
        return {}
    response = await auth.authenticated_json(
        "/get_byom_urls",
        {"files": [{"file": item.identifier, "sha256": item.sha256} for item in usable]},
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise error(ErrorCategory.PROTOCOL, "cloud_byom_response_invalid", "DT+ LoRA lookup response is invalid.")

    already = data.get("alreadyExists")
    already = already if isinstance(already, dict) else {}
    signed = data.get("signedUrls")
    urls = signed.get("urls") if isinstance(signed, dict) else {}
    urls = urls if isinstance(urls, dict) else {}

    results: dict[str, LoRABackendAvailability] = {}
    for item in usable:
        reference = already.get(item.sha256)
        if isinstance(reference, str) and reference:
            results[item.identifier] = LoRABackendAvailability(
                "byom_reusable", "dt_cloud.get_byom_urls", sha256=item.sha256, reference=reference
            )
        elif item.identifier in urls:
            results[item.identifier] = LoRABackendAvailability(
                "upload_required", "dt_cloud.get_byom_urls", sha256=item.sha256
            )
        else:
            results[item.identifier] = LoRABackendAvailability(
                "unknown", "dt_cloud.get_byom_urls", sha256=item.sha256
            )
    return results
