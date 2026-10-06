"""Headless capture support for the exported DTFlow web reference (not runtime UI)."""

import argparse
import base64
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def parser(description):
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    result = argparse.ArgumentParser(description=description)
    result.add_argument("out", type=Path, help="Output directory (prefer ignored tmp/)")
    result.add_argument("-d", "--design-system", type=Path, default=ROOT / "devdocs/design-system")
    result.add_argument("-v", "--vendor", type=Path, default=ROOT / "tmp/design-system-vendor")
    result.add_argument(
        "-b", "--browser", type=Path, help="Explicit Chromium executable (optional; recorded in manifest)"
    )
    result.add_argument("-f", "--fetch", action="store_true", help="Fetch pinned CDN scripts; verify declared SRI")
    return result


def vendor_sources(root):
    sources = {}
    for html in (root / "ui_kits").rglob("index.html"):
        for tag in re.findall(r"<script\b[^>]*>", html.read_text(encoding="utf-8")):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
            url = attrs.get("src", "")
            if not url.startswith("https://"):
                continue
            if urlsplit(url).netloc != "unpkg.com" or not attrs.get("integrity", "").startswith("sha384-"):
                raise ValueError(f"Unapproved or unpinned script: {url}")
            if url in sources and sources[url] != attrs["integrity"]:
                raise ValueError(f"Conflicting integrity: {url}")
            sources[url] = attrs["integrity"]
    if not sources:
        raise ValueError("No pinned vendor scripts found in ui_kits/index.html files")
    return sources


def verified_vendor(root, cache, fetch=False):
    cache.mkdir(parents=True, exist_ok=True)
    result = {}
    for url, integrity in vendor_sources(root).items():
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".js")
        if not path.exists():
            if not fetch:
                raise ValueError("Vendor cache missing. Run once with --fetch (explicit CDN downloads).")
            with urlopen(url, timeout=60) as response:
                data = response.read()
        else:
            data = path.read_bytes()
        actual = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
        if actual != integrity:
            raise ValueError(f"Vendor integrity mismatch: {url}")
        if not path.exists():
            path.write_bytes(data)
        result[url] = data
    return result


class QuietHandler(SimpleHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # Browser navigation may abandon outstanding asset responses.

    def log_message(self, *_):
        pass


@contextmanager
def serve(root):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@contextmanager
def capture_session(args):
    from playwright.sync_api import sync_playwright

    root = args.design_system.resolve()
    if not (root / "_ds_bundle.js").is_file():
        raise ValueError(f"Not a design-system export: {root}")
    args.out.mkdir(parents=True, exist_ok=True)
    vendor = verified_vendor(root, args.vendor, args.fetch)
    with serve(root) as base, sync_playwright() as driver:
        browser = driver.chromium.launch(
            headless=True, **({"executable_path": str(args.browser)} if args.browser else {})
        )
        context = browser.new_context(device_scale_factor=1, reduced_motion="reduce", service_workers="block")
        blocked = []

        def route(request):
            url = request.request.url
            if url in vendor:
                request.fulfill(
                    body=vendor[url],
                    content_type="application/javascript",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            elif url.startswith(base + "/"):
                request.continue_()
            else:
                blocked.append(url)
                request.abort()

        context.route("**/*", route)
        page = context.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "response",
            lambda response: (
                errors.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None
            ),
        )
        try:
            yield (
                page,
                base,
                {
                    "browser": browser.version,
                    "executable_override": str(args.browser) if args.browser else None,
                    "device_scale_factor": 1,
                    "blocked_requests": blocked,
                    "errors": errors,
                },
            )
        finally:
            context.close()
            browser.close()


def ready(page):
    page.wait_for_function("window.React && Object.keys(window).some(k => k.startsWith('DTFlowDesignSystem_'))")
    page.evaluate("document.fonts.ready")
    failures = page.evaluate(
        "window[Object.keys(window).find(k => k.startsWith('DTFlowDesignSystem_'))].__errors || []"
    )
    if failures:
        raise ValueError(f"Export bundle errors: {failures}")


def manifest(args, data):
    data["blocked_request_count"] = len(data.get("blocked_requests", []))
    data["blocked_requests"] = list(dict.fromkeys(data.get("blocked_requests", [])))
    data["design_system"] = str(args.design_system.resolve())
    data["bundle_sha256"] = hashlib.sha256((args.design_system / "_ds_bundle.js").read_bytes()).hexdigest()
    data["limits"] = [
        "Browser versus Qt rendering; no pixel-parity assertion",
        "Remote fonts are blocked; native system font fallbacks apply",
        "Kit defaults/fixtures remain unchanged",
        "Matrix cells enlarged 2x from DPR 1, not native high-DPI renders",
    ]
    (args.out / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
