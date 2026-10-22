"""DT+ LoRA availability lookup is metadata-only and never follows signed upload URLs."""

import httpx
import pytest

from dtflow.backends.drawthings.cloud_assets import lookup_lora_assets
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.core.models import Credential, FileIdentity


class Credentials:
    async def get(self):
        return Credential("test", "FAKE-API-KEY")


@pytest.mark.asyncio
async def test_byom_lookup_classifies_reuse_and_upload_required_without_put():
    calls = []
    response_upload = [None]

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/sdk/token":
            return httpx.Response(200, json={"shortTermToken": "TOKEN", "expiresIn": 3600})
        assert request.url.path == "/get_byom_urls"
        return httpx.Response(
            200,
            json={
                "code": 208,
                "data": {
                    "alreadyExists": {"aa" * 32: ("aa" * 32) + "_remote"},
                    "signedUrls": {
                        "urls": {
                            "upload.ckpt": response_upload[0]
                            or {
                                "sha256": "cc" * 32,  # server-returned value need not echo the local digest
                                "url": "https://storage.invalid/SECRET-SIGNED-URL",
                            }
                        }
                    },
                },
            },
        )

    auth = CloudAuth(Credentials(), transport=httpx.MockTransport(respond))
    try:
        result = await lookup_lora_assets(
            auth,
            (
                FileIdentity("reuse.ckpt", True, "aa" * 32),
                FileIdentity("upload.ckpt", True, "bb" * 32),
            ),
        )
        for invalid_upload in (
            {"sha256": "bb" * 32, "url": None},
            {"sha256": "bb" * 32},
            "SECRET-SIGNED-URL",
        ):
            response_upload[0] = invalid_upload
            unknown = await lookup_lora_assets(auth, (FileIdentity("upload.ckpt", True, "bb" * 32),))
            assert unknown["upload.ckpt"].availability == "unknown"
            assert unknown["upload.ckpt"].reference is None
            assert "SECRET-SIGNED-URL" not in repr(unknown)
    finally:
        await auth.aclose()

    assert result["reuse.ckpt"].availability == "byom_reusable"
    assert result["reuse.ckpt"].reference == ("aa" * 32) + "_remote"
    assert result["upload.ckpt"].availability == "upload_required"
    assert calls == [("POST", "/sdk/token")] + [("POST", "/get_byom_urls")] * 4
