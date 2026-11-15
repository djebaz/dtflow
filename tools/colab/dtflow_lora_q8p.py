# Auto-generated from notebook; keep markers for round-trip
# Markers + docstring headers are required for ipynb reconstruction
# NOTEBOOK_META_B64=eyJjb2xhYiI6eyJwcm92ZW5hbmNlIjpbXSwiY29sbGFwc2VkX3NlY3Rpb25zIjpbInE4cC1pbnRybyJdfSwia2VybmVsc3BlYyI6eyJsYW5ndWFnZSI6InB5dGhvbiIsIm5hbWUiOiJweXRob24zIiwiZGlzcGxheV9uYW1lIjoiUHl0aG9uIDMifSwibGFuZ3VhZ2VfaW5mbyI6eyJuYW1lIjoicHl0aG9uIn0sIm5iZm9ybWF0Ijo0LCJuYmZvcm1hdF9taW5vciI6NX0=

# %% [markdown] cell=0 id=q8p-intro
"""MARKDOWN
# DTFlow · F16 LoRA CKPT → q8p (CPU experiment)

Quantize an **already converted F16 Draw Things LoRA `.ckpt`**. No Swift build or GPU is needed. Safetensors must be converted to F16 first using the separate converter notebook or CLI.

1. Run Setup (downloads only the required DTFlow files, managed Python, NumPy and kmeans1d).
2. Upload one F16 CKPT or choose a path in this runtime/Google Drive.
3. Run Quantize; download the new CKPT and JSON report before the runtime expires.

Automatic CPU detection is the default; `workers=1` forces serial operation. A bounded queue schedules blocks across tensor boundaries, including one-block tensors. Progress includes elapsed time and an approximate encoding ETA. More workers use more memory; this is not a measured speedup guarantee.

**Experimental; end-to-end Colab conversion remains unverified.** The writer currently accepts structurally recognized Krea 2, Z Image and SDXL Base F16 LoRAs only. Originals and existing outputs are never overwritten. Nothing is sent to DT+, and no DT+ credentials or generation are involved. Cell 1 optionally reads a named Colab secret for GitHub source access; never paste a token into the form. Uploading a file here places it in your Google Colab runtime; optional Drive mounting is explicit. Structural checks and numerical errors do not establish DT+ acceptance or image quality.
"""ENDMARKDOWN

# %% [code] cell=1 id=q8p-setup
"""CELL: 1. Set up the DTFlow q8p CLI (minimal download, no Swift build)"""
# @title 1. Set up the DTFlow q8p CLI (minimal download, no Swift build)
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from google.colab import userdata

# @markdown Branch, tag or commit. Only the small q8p file manifest is downloaded, pinned to the resolved commit.
dtflow_revision = "feat/353-q8p-cli-experiment"  # @param {type:"string"}
# @markdown GitHub username for token authentication (x-access-token is also accepted).
github_username = "x-access-token"  # @param {type:"string"}
# @markdown Colab secret ID containing your token, NOT the token itself. Enable notebook access in Secrets. Leave empty for anonymous/public access.
github_secret_id = "GITHUB_TOKEN"  # @param {type:"string"}

if not Path("/content").is_dir():
    raise RuntimeError("Use a Google Colab Linux runtime.")
if not dtflow_revision.strip():
    raise ValueError("Choose a branch, tag or commit.")
UV = shutil.which("uv")
if UV is None:
    subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=True)
    UV = shutil.which("uv")
if UV is None:
    raise RuntimeError("uv was installed but its executable is not on PATH.")

CHECKOUT = Path(tempfile.mkdtemp(prefix="dtflow-q8p-src-", dir="/content"))
AUTH_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "DTFlow-q8p-notebook"}


def github_get(path, accept="application/vnd.github+json"):
    request = urllib.request.Request(
        "https://api.github.com/repos/djebaz/dtflow/" + path,
        headers={**AUTH_HEADERS, "Accept": accept},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(2_000_001)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub returned HTTP {error.code}. Check the revision, secret access and API rate limit."
        ) from None
    except urllib.error.URLError:
        raise RuntimeError("Could not reach GitHub. Check the connection and retry Setup.") from None
    if len(data) > 2_000_000:
        raise RuntimeError("A source response exceeded the small-file download limit.")
    return data


def source_file(path):
    entry = json.loads(github_get("contents/" + urllib.parse.quote(path, safe="/") + "?ref=" + REVISION))
    if entry.get("type") != "file" or entry.get("encoding") != "base64" or entry.get("path") != path:
        raise RuntimeError("GitHub did not return the expected source file.")
    data = base64.b64decode(entry["content"])
    # Verify Git's blob identity supplied with this commit-pinned content.
    blob_sha = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if blob_sha != entry["sha"]:
        raise RuntimeError("Source file identity verification failed.")
    return data


token = None
try:
    if github_secret_id.strip():
        username = github_username.strip()
        if not username or any(char in username for char in ":\r\n"):
            raise ValueError("Set a valid GitHub username, or clear the secret ID for anonymous access.")
        try:
            token = userdata.get(github_secret_id.strip())
        except Exception:
            raise RuntimeError(
                "Could not read the named Colab secret. Check its ID and enable notebook access."
            ) from None
        if not token:
            raise RuntimeError("The selected Colab secret is empty.")
        AUTH_HEADERS["Authorization"] = "Basic " + base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
    REVISION = (
        github_get("commits/" + urllib.parse.quote(dtflow_revision.strip(), safe=""), "application/vnd.github.sha")
        .decode("ascii")
        .strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}", REVISION):
        raise RuntimeError("GitHub did not return a full commit identity.")
    paths = json.loads(source_file("tools/colab/q8p_files.json"))
    if not isinstance(paths, list) or not 1 <= len(paths) <= 40 or len(set(paths)) != len(paths):
        raise RuntimeError("Unsupported q8p source manifest.")
    total_bytes = 0
    for index, path in enumerate(paths, 1):
        relative = PurePosixPath(path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in path
            or not (
                (path.startswith("src/dtflow/") and path.endswith(".py"))
                or path in ("tools/colab/q8p_cli.py", "tools/colab/q8p_cli.py.lock")
            )
        ):
            raise RuntimeError("Unsupported path in the q8p source manifest.")
        data = source_file(path)
        destination = CHECKOUT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(data)
        total_bytes += len(data)
        print(f"Source files: {index}/{len(paths)}", flush=True)
finally:
    token = None
    AUTH_HEADERS.pop("Authorization", None)

ENV = {
    **os.environ,
    "UV_LINK_MODE": "copy",
    "UV_SYSTEM_CERTS": "true",
    "UV_PYTHON_PREFERENCE": "only-managed",
    "QT_QPA_PLATFORM": "offscreen",
}
SCRIPT = CHECKOUT / "tools/colab/q8p_cli.py"
CLI = [UV, "run", "--locked", "--script", str(SCRIPT), "lora"]
# Install only the two script-locked dependencies and validate the lightweight entry point.
with subprocess.Popen(
    CLI + ["--help"],
    cwd=CHECKOUT,
    env=ENV,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
) as setup_process:
    for line in setup_process.stdout:
        print(line.rstrip(), flush=True)
    setup_code = setup_process.wait()
if setup_code != 0:
    raise RuntimeError(f"uv/launcher setup failed (exit {setup_code}); see its output above.")
print("DTFlow source revision:", REVISION)
print(f"Downloaded {len(paths)} source/launcher files, {total_bytes / 1024:.1f} KiB; no repository checkout.")
print("Ready. Auto-detection uses the CPUs available to the CLI's managed Python.")

# %% [code] cell=2 id=q8p-input
"""CELL: 2. Choose an F16 CKPT"""
# @title 2. Choose an F16 CKPT
# @markdown Leave empty to upload one CKPT. Or use a runtime/Drive path.
source_path = ""  # @param {type:"string"}
mount_google_drive = False  # @param {type:"boolean"}

if mount_google_drive:
    from google.colab import drive

    drive.mount("/content/drive")
if source_path.strip():
    SOURCE = Path(source_path.strip()).expanduser().resolve(strict=True)
else:
    from google.colab import files

    # A fresh upload directory prevents overwriting a previous upload.
    upload_dir = Path(tempfile.mkdtemp(prefix="q8p-input-", dir="/content"))
    previous_directory = Path.cwd()
    try:
        os.chdir(upload_dir)
        uploaded = files.upload()
    finally:
        os.chdir(previous_directory)
    if len(uploaded) != 1:
        raise ValueError("Upload exactly one F16 .ckpt file.")
    SOURCE = upload_dir / next(iter(uploaded))
    del uploaded
if not SOURCE.is_file() or SOURCE.suffix.lower() != ".ckpt":
    raise ValueError("Choose an existing F16 LoRA .ckpt file.")
print("Input:", SOURCE.name, f"({SOURCE.stat().st_size / 1_000_000:.1f} MB)")

# %% [code] cell=3 id=q8p-quantize
"""CELL: 3. Quantize and download the new CKPT + report"""
# @title 3. Quantize and download the new CKPT + report
# @markdown 0 = automatically use available CPUs; 1 = serial; another positive integer = explicit worker count.
workers = 0  # @param {type:"integer"}
download_result = True  # @param {type:"boolean"}

if type(workers) is not int or workers < 0:
    raise ValueError("Workers must be a non-negative integer.")
# Keep SQLite staging/publication on the runtime's local filesystem, not mounted Drive.
OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="q8p-output-", dir="/content"))
OUTPUT = OUTPUT_DIR / (SOURCE.stem.removesuffix("_f16") + "_q8p.ckpt")
REPORT = OUTPUT.with_suffix(".report.json")
command = CLI + ["quantize", str(SOURCE), "-o", str(OUTPUT), "-e", "-j", str(workers), "-r", REVISION]
with REPORT.open("x", encoding="utf-8", newline="\n") as report_file:
    process = subprocess.Popen(
        command, cwd=CHECKOUT, env=ENV, stdout=report_file, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    try:
        for line in process.stderr:
            print(line.rstrip(), flush=True)
        code = process.wait()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        process.wait()
        raise
    finally:
        process.stderr.close()
if code != 0:
    raise RuntimeError(f"Quantization exited with code {code}; inspect the message above. No provider work started.")
result = json.loads(REPORT.read_text(encoding="utf-8"))
print("Output:", OUTPUT)
print(f"Size: {result['input']['file_bytes'] / 1_000_000:.2f} → {result['output']['file_bytes'] / 1_000_000:.2f} MB")
print(
    f"Reduction: {result['size_reduction_percent']}%; elapsed: {result['seconds']} s; workers: {result['clustering']['workers']}"
)
print("Report:", REPORT)
print("Output was reopened and decoded by the CLI before publication. DT+ compatibility/quality remain unverified.")
if download_result:
    from google.colab import files

    files.download(str(OUTPUT))
    files.download(str(REPORT))
