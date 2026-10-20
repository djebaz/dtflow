#!/usr/bin/env python3
"""Upload DTFlow UI render files to a personal Google Drive folder."""

from __future__ import annotations

import argparse
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 30,
) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google API request failed ({exc.code}): {detail}") from exc
    return json.loads(payload.decode("utf-8")) if payload else {}


def get_access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    response = _request_json(
        TOKEN_URL,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    token = response.get("access_token")
    if not token:
        raise RuntimeError("Google OAuth token response did not contain an access token.")
    return str(token)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _quote_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_or_create_folder(
    access_token: str,
    name: str,
    parent_id: str | None = None,
) -> tuple[str, str]:
    clauses = [
        f"name='{_quote_query(name)}'",
        "mimeType='application/vnd.google-apps.folder'",
        "trashed=false",
    ]
    if parent_id:
        clauses.append(f"'{_quote_query(parent_id)}' in parents")
    params = urllib.parse.urlencode(
        {"q": " and ".join(clauses), "spaces": "drive", "pageSize": 1, "fields": "files(id,webViewLink)"}
    )
    response = _request_json(f"{DRIVE_API}/files?{params}", headers=_auth(access_token))
    files = response.get("files", [])
    if files:
        item = files[0]
        return str(item["id"]), str(item.get("webViewLink") or "")

    metadata: dict[str, object] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]
    response = _request_json(
        f"{DRIVE_API}/files?fields=id,webViewLink",
        method="POST",
        headers={**_auth(access_token), "Content-Type": "application/json"},
        data=json.dumps(metadata).encode(),
    )
    return str(response["id"]), str(response.get("webViewLink") or "")


def _delete_existing(access_token: str, folder_id: str, name: str) -> None:
    query = f"name='{_quote_query(name)}' and '{_quote_query(folder_id)}' in parents and trashed=false"
    params = urllib.parse.urlencode({"q": query, "spaces": "drive", "fields": "files(id)"})
    response = _request_json(f"{DRIVE_API}/files?{params}", headers=_auth(access_token))
    for item in response.get("files", []):
        _request_json(
            f"{DRIVE_API}/files/{item['id']}",
            method="DELETE",
            headers=_auth(access_token),
        )


def upload_file(access_token: str, path: Path, folder_id: str, remote_name: str) -> dict[str, str]:
    _delete_existing(access_token, folder_id, remote_name)
    mime_type = mimetypes.guess_type(remote_name)[0] or "application/octet-stream"
    metadata = {"name": remote_name, "parents": [folder_id], "mimeType": mime_type}
    created = _request_json(
        f"{DRIVE_API}/files?fields=id,webViewLink",
        method="POST",
        headers={**_auth(access_token), "Content-Type": "application/json"},
        data=json.dumps(metadata).encode(),
    )
    file_id = str(created["id"])
    request = urllib.request.Request(
        f"{DRIVE_UPLOAD_API}/files/{file_id}?uploadType=media",
        data=path.read_bytes(),
        headers={**_auth(access_token), "Content-Type": mime_type},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=300):
            pass
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Drive upload failed for {remote_name} ({exc.code}): {detail}") from exc

    # GitHub cannot display authenticated Drive images. Render files are deliberately
    # link-readable so their stable content URLs can be embedded in summaries/comments.
    _request_json(
        f"{DRIVE_API}/files/{file_id}/permissions",
        method="POST",
        headers={**_auth(access_token), "Content-Type": "application/json"},
        data=json.dumps({"type": "anyone", "role": "reader"}).encode(),
    )
    return {
        "id": file_id,
        "name": remote_name,
        "view_url": str(created.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"),
        "content_url": f"https://drive.google.com/uc?export=view&id={file_id}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Directory containing render PNGs")
    parser.add_argument("--archive", type=Path, help="Optional ZIP archive to upload beside the PNGs")
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Upload only the first PNG as a visual preview, plus the optional archive.",
    )
    parser.add_argument("--refresh-token", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--root-folder", default="dtflow_ui_renders")
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    pngs = sorted(args.source.rglob("*.png")) if args.source.exists() else []
    if not pngs and not (args.archive and args.archive.is_file()):
        raise SystemExit("No render files were produced; nothing to upload.")

    token = get_access_token(args.refresh_token, args.client_id, args.client_secret)
    root_id, _ = find_or_create_folder(token, args.root_folder)
    run_id, run_url = find_or_create_folder(token, args.run_folder, root_id)

    uploaded: list[dict[str, str]] = []
    upload_pngs = pngs[:1] if args.preview_only else pngs
    for path in upload_pngs:
        relative = path.relative_to(args.source).as_posix()
        # Preserve the useful relative path in the Drive filename without creating
        # hundreds of nested API folders.
        remote_name = relative.replace("/", "__")
        item = upload_file(token, path, run_id, remote_name)
        item["relative_path"] = relative
        uploaded.append(item)
        print(f"Uploaded {relative}")

    archive_item = None
    if args.archive and args.archive.is_file():
        archive_item = upload_file(token, args.archive, run_id, args.archive.name)
        print(f"Uploaded {args.archive.name}")

    manifest = {
        "folder_id": run_id,
        "folder_url": run_url or f"https://drive.google.com/drive/folders/{run_id}",
        "files": uploaded,
        "archive": archive_item,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Uploaded {len(uploaded)} render PNGs to Google Drive.")


if __name__ == "__main__":
    main()
