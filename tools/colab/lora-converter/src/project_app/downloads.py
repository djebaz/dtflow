"""Civitai version resolution and checksum-verified, resumable LoRA downloads."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import struct
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cache import content_fingerprint
from .config import CIVITAI_HOSTS, civitai_origin


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise RuntimeError("Refusing an unsafe download redirect")
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None and parsed.hostname != urlsplit(req.full_url).hostname:
            result.remove_header("Authorization")
        return result


def open_url(url, token="", *, headers=None):
    """Authorize only Civitai; never forward credentials to its download CDN."""
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("HTTPS is required")
    values = {"User-Agent": "DrawThings-LoRA-Colab/1.0", **(headers or {})}
    if token and parsed.hostname in CIVITAI_HOSTS:
        values["Authorization"] = "Bearer " + token
    try:
        return build_opener(SafeRedirect()).open(Request(url, headers=values), timeout=90)
    except HTTPError as exc:
        raise RuntimeError(f"Civitai/download HTTP {exc.code}; check the URL, access rights and Colab secret") from None
    except URLError:
        raise RuntimeError("Network request failed; retry the same submitted job") from None


def api_json(path, token, *, origin="https://civitai.com"):
    with open_url(origin + "/api/v1/" + path, token) as response:
        data = response.read(8_000_001)
        if len(data) > 8_000_000:
            raise ValueError("Civitai metadata response is unexpectedly large")
        return json.loads(data)


def resolve_lora(url, token=""):
    origin = civitai_origin(url)
    parsed = urlsplit(url.strip())
    query = parse_qs(parsed.query)
    match = re.fullmatch(r"/api/download/models/(\d+)/?", parsed.path)
    file_ids = query.get("fileId", [])
    if file_ids and (len(file_ids) != 1 or not file_ids[0].isdigit()):
        raise ValueError("fileId must be one numeric Civitai file ID")
    file_id = file_ids[0] if file_ids else None
    if match:
        version_id = match[1]
    else:
        match = re.match(r"^/models/(\d+)(?:/[^/]*)?/?$", parsed.path)
        if not match:
            raise ValueError("Use a Civitai model page or /api/download/models/VERSION_ID URL")
        if query.get("modelVersionId"):
            version_id = query["modelVersionId"][0]
        else:
            model = api_json("models/" + match[1], token, origin=origin)
            versions = model.get("modelVersions", [])
            if not versions:
                raise ValueError("Civitai model has no accessible versions")
            version_id = str(versions[0]["id"])
    if not str(version_id).isdigit():
        raise ValueError("Invalid model version ID")
    version = api_json("model-versions/" + str(version_id), token, origin=origin)
    if version.get("model", {}).get("type", "").upper() not in {"LORA", "LOCON"}:
        raise ValueError("This workflow accepts LoRA/LoCon assets, not full checkpoints")
    files = [f for f in version.get("files", []) if f.get("name", "").lower().endswith(".safetensors") and f.get("type") == "Model"]
    for key in ("size", "fp", "format"):
        if query.get(key):
            files = [f for f in files if str(f.get("metadata", {}).get(key, "")).lower() == query[key][0].lower()]
    if file_id is not None:
        files = [f for f in files if str(f.get("id")) == file_id]
    primary = [f for f in files if f.get("primary")]
    files = primary or files
    if len(files) != 1:
        raise ValueError("No unique safetensors file was found; use a version-specific download URL with file variant parameters")
    selected = files[0]
    digest = selected.get("hashes", {}).get("SHA256", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("A Civitai SHA-256 is required to verify and safely resume this download")
    download_url = selected["downloadUrl"]
    if urlsplit(download_url).hostname not in CIVITAI_HOSTS:
        raise ValueError("Unexpected Civitai API download URL")
    civitai_origin(download_url)  # Validate scheme, host and credentials before authorization.
    return {
        "version_id": str(version_id), "file_id": selected["id"],
        "source_name": selected["name"], "source_sha256": digest,
        "base_model": version.get("baseModel"), "download_url": download_url,
    }


def validate_safetensors(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        raw = stream.read(8)
        if len(raw) != 8:
            raise ValueError("Not a safetensors file")
        length = struct.unpack("<Q", raw)[0]
        if not 2 <= length <= min(16_000_000, size - 8):
            raise ValueError("Invalid safetensors header length")
        header = json.loads(stream.read(length))
    if not isinstance(header, dict):
        raise ValueError("Invalid safetensors metadata")
    tensors = {k: v for k, v in header.items() if k != "__metadata__"}
    if not tensors:
        raise ValueError("Safetensors file contains no tensors")
    for tensor in tensors.values():
        start, end = tensor["data_offsets"]
        if not 0 <= start <= end <= size - 8 - length:
            raise ValueError("Safetensors data offsets exceed the file")
    return len(tensors)


def download_lora(metadata, root, token, logger):
    root.mkdir(parents=True, exist_ok=True)
    destination = root / (metadata["source_sha256"] + ".safetensors")
    if destination.is_file() and content_fingerprint(destination) == metadata["source_sha256"]:
        validate_safetensors(destination)
        logger.info("DOWNLOAD REUSED: checksum verified")
        return destination
    partial = destination.with_suffix(".part")
    offset = partial.stat().st_size if partial.exists() else 0
    # Full expected content hash is authoritative even if the CDN does not supply ETag.
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with open_url(metadata["download_url"], token, headers=headers) as response:
        if response.status == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {offset}-"):
                raise RuntimeError("Download server returned an unexpected range")
            mode = "ab"
        else:
            offset = 0
            mode = "wb"
        logger.info("DOWNLOAD START: version=%s file=%s offset=%s", metadata["version_id"], metadata["file_id"], offset)
        total = offset
        next_log = total + 128 * 1024 * 1024
        with partial.open(mode) as stream:
            while chunk := response.read(4 * 1024 * 1024):
                stream.write(chunk)
                total += len(chunk)
                if total >= next_log:
                    stream.flush()
                    logger.info("DOWNLOAD: %s MiB saved", total // (1024 * 1024))
                    next_log = total + 128 * 1024 * 1024
    if content_fingerprint(partial) != metadata["source_sha256"]:
        partial.rename(partial.with_suffix(".sha256-mismatch-" + str(__import__('time').time_ns())))
        raise ValueError("Download checksum mismatch; the invalid partial was preserved separately. Rerun to download again.")
    validate_safetensors(partial)
    partial.replace(destination)
    logger.info("DOWNLOAD COMPLETE: verified SHA-256")
    return destination
