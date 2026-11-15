# Auto-generated from notebook; keep markers for round-trip
# Markers + docstring headers are required for ipynb reconstruction
# NOTEBOOK_META_B64=eyJjb2xhYiI6eyJwcm92ZW5hbmNlIjpbXSwiY29sbGFwc2VkX3NlY3Rpb25zIjpbInE4cC1pbnRybyJdfSwia2VybmVsc3BlYyI6eyJuYW1lIjoicHl0aG9uMyIsImxhbmd1YWdlIjoicHl0aG9uIiwiZGlzcGxheV9uYW1lIjoiUHl0aG9uIDMifSwibGFuZ3VhZ2VfaW5mbyI6eyJuYW1lIjoicHl0aG9uIn0sIm5iZm9ybWF0Ijo0LCJuYmZvcm1hdF9taW5vciI6NX0=

# %% [markdown] cell=0 id=q8p-intro
"""MARKDOWN
# DTFlow · F16 LoRA CKPT → q8p (CPU experiment)

Quantize an **already converted F16 Draw Things LoRA `.ckpt`**. No Swift build or GPU is needed. Safetensors must be converted to F16 first using the separate converter notebook or CLI.

1. Run Setup (downloads DTFlow, managed Python and its locked dependencies).
2. Upload one F16 CKPT or choose a path in this runtime/Google Drive.
3. Run Quantize; download the new CKPT and JSON report before the runtime expires.

Automatic CPU detection is the default; `workers=1` forces serial operation. Work is parallel within each tensor, so small tensors may not fill every CPU. Progress includes elapsed time and an approximate encoding ETA. More workers use more memory; this is not a measured speedup guarantee.

**Experimental, not yet executed on Colab.** The writer currently accepts structurally recognized Krea 2, Z Image and SDXL Base F16 LoRAs only. Originals and existing outputs are never overwritten. Nothing is sent to DT+, and no credentials or generation are involved. Uploading a file here places it in your Google Colab runtime; optional Drive mounting is explicit. Structural checks and numerical errors do not establish DT+ acceptance or image quality.
"""ENDMARKDOWN

# %% [code] cell=1 id=q8p-setup
"""CELL: 1. Set up the DTFlow q8p CLI (no Swift build)"""
# @title 1. Set up the DTFlow q8p CLI (no Swift build)
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

# @markdown Branch, tag or commit to fetch. The resolved commit is saved in the report.
dtflow_revision = "feat/353-q8p-cli-experiment"  # @param {type:"string"}

if not Path("/content").is_dir():
    raise RuntimeError("Use a Google Colab Linux runtime.")
if not dtflow_revision.strip() or dtflow_revision.startswith("-"):
    raise ValueError("Choose a branch, tag or commit.")

UV = shutil.which("uv")
if UV is None:
    # Bootstrap only uv with the notebook interpreter; uv owns the project environment.
    subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=True)
    UV = shutil.which("uv")
if UV is None:
    raise RuntimeError("uv was installed but its executable is not on PATH.")

CHECKOUT = Path(tempfile.mkdtemp(prefix="dtflow-q8p-src-", dir="/content"))


def git(*args):
    return subprocess.check_output(["git", "-C", str(CHECKOUT), *args], text=True).strip()


git("init", "-q")
git("remote", "add", "origin", "https://github.com/djebaz/dtflow.git")
git("fetch", "--depth", "1", "origin", dtflow_revision.strip())
git("checkout", "-q", "--detach", "FETCH_HEAD")
REVISION = git("rev-parse", "HEAD")
ENV = {
    **os.environ,
    "UV_LINK_MODE": "copy",
    "UV_SYSTEM_CERTS": "true",
    "UV_PYTHON_PREFERENCE": "only-managed",
    "QT_QPA_PLATFORM": "offscreen",
}
subprocess.run([UV, "sync", "--frozen", "--no-dev", "--group", "quantization"], cwd=CHECKOUT, env=ENV, check=True)
CLI = [UV, "run", "--frozen", "--no-dev", "--group", "quantization", "dtflow", "lora"]
print("DTFlow revision:", REVISION)
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
