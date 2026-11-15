# Auto-generated from notebook; keep markers for round-trip
# Markers + docstring headers are required for ipynb reconstruction
# NOTEBOOK_META_B64=eyJjb2xhYiI6eyJwcm92ZW5hbmNlIjpbXSwiY29sbGFwc2VkX3NlY3Rpb25zIjpbInE4cC1pbnRybyJdfSwia2VybmVsc3BlYyI6eyJuYW1lIjoicHl0aG9uMyIsImRpc3BsYXlfbmFtZSI6IlB5dGhvbiAzIiwibGFuZ3VhZ2UiOiJweXRob24ifSwibGFuZ3VhZ2VfaW5mbyI6eyJuYW1lIjoicHl0aG9uIn0sIm5iZm9ybWF0Ijo0LCJuYmZvcm1hdF9taW5vciI6NX0=

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

# %% [code] cell=4 id=q8p-gpu-samples
"""CELL: 4. Optional GPU sample benchmark (installed PyTorch only; no CKPT written)"""
# @title 4. Optional GPU sample benchmark (installed PyTorch only; no CKPT written)
# @markdown Run cells 1–2 first. Use a CUDA GPU runtime. This is approximate Lloyd clustering, NOT optimal 1D clustering. No packages are installed or upgraded.
gpu_sample_blocks = 32  # @param {type:"integer"}
gpu_batch_blocks = 8  # @param {type:"integer"}
gpu_max_iterations = 100  # @param {type:"integer"}
gpu_tolerance = 0.000001  # @param {type:"number"}
# @markdown Optional CPU/iOS q8p reference path in this runtime. Empty uses cell 3's output only if its report matches the selected input SHA; otherwise benchmarks against F16 only.
gpu_reference_path = ""  # @param {type:"string"}
gpu_download_report = True  # @param {type:"boolean"}

import struct
import time
from contextlib import ExitStack

import numpy as np
import torch

if not torch.cuda.is_available():
    raise RuntimeError("CUDA PyTorch is unavailable. Select a GPU runtime with CUDA-enabled PyTorch; this cell will not install or replace it.")
for value, maximum, label in ((gpu_sample_blocks, 256, "Sample blocks"), (gpu_batch_blocks, 16, "Batch blocks"), (gpu_max_iterations, 200, "Iterations")):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be 1..{maximum}.")
if not np.isfinite(gpu_tolerance) or not 0 < gpu_tolerance <= 0.01:
    raise ValueError("Tolerance must be finite and in (0, 0.01].")
if "dtflow" in sys.modules and not Path(sys.modules["dtflow"].__file__).resolve().is_relative_to(CHECKOUT):
    raise RuntimeError("A different DTFlow source bundle is already imported. Restart the runtime before comparing another revision.")
sys.path.insert(0, str(CHECKOUT / "src"))
from dtflow.services.lora_convert.numeric import f32_to_f16_bits
from dtflow.services.lora_q8p_codec import BLOCK, Q8P, decode
from dtflow.services.lora_quantization import Differences, Input, WRITER_FAMILIES


@torch.inference_mode()
def gpu_lloyd(values, iterations):
    # In 1D, sorted-center midpoints allow nearest-center assignment without a B×N×256 distance matrix.
    ordered = values.sort(dim=1).values
    positions = torch.linspace(0, values.shape[1] - 1, 256, device=values.device).long()
    centers = ordered[:, positions].contiguous()
    converged = False
    for step in range(iterations):
        boundaries = ((centers[:, :-1] + centers[:, 1:]) * 0.5).contiguous()
        labels = torch.searchsorted(boundaries, values)
        sums = torch.zeros_like(centers).scatter_add_(1, labels, values)
        counts = torch.zeros_like(centers).scatter_add_(1, labels, torch.ones_like(values))
        updated = torch.where(counts > 0, sums / counts.clamp_min(1), centers).sort(dim=1).values
        shift = (updated - centers).abs().max().item()
        centers = updated
        if shift <= gpu_tolerance:
            converged = True
            break
    labels = torch.searchsorted(((centers[:, :-1] + centers[:, 1:]) * 0.5).contiguous(), values)
    return centers, labels, step + 1, converged


gpu_device = torch.device("cuda:0")
print("GPU:", torch.cuda.get_device_name(gpu_device), "| PyTorch:", torch.__version__, "| NumPy:", np.__version__, flush=True)
print("Approximate FP32 Lloyd clustering; fixed quantile initialization, but CUDA reductions can vary. No full-file conversion or CPU speedup claim.", flush=True)
gpu_wall_started = time.perf_counter()
with ExitStack() as gpu_stack:
    gpu_source = gpu_stack.enter_context(Input(SOURCE))
    if gpu_source.family not in WRITER_FAMILIES or any(t.codec != 0 for t in gpu_source.tensors):
        raise ValueError("Use a supported raw F16 LoRA, not an already encoded file.")
    reference_path = gpu_reference_path.strip()
    reference_origin = "owner-supplied; matching inventory alone does not prove the same original weights"
    if not reference_path and "OUTPUT" in globals() and "REPORT" in globals() and OUTPUT.is_file() and REPORT.is_file():
        cpu_report = json.loads(REPORT.read_text(encoding="utf-8"))
        if cpu_report.get("input", {}).get("sha256") == gpu_source.sha256:
            reference_path = str(OUTPUT)
            reference_origin = "cell-3 report matches input SHA; output SHA verified"
    gpu_reference = gpu_stack.enter_context(Input(Path(reference_path))) if reference_path else None
    if gpu_reference is not None:
        if [(t.name, t.dim, t.format) for t in gpu_reference.tensors] != [(t.name, t.dim, t.format) for t in gpu_source.tensors]:
            raise ValueError("Reference tensor inventory does not match the F16 input.")
        if reference_origin.startswith("cell-3") and gpu_reference.sha256 != cpu_report.get("output", {}).get("sha256"):
            raise ValueError("Cell-3 output does not match its recorded SHA.")
    candidates = [(i, offset) for i, tensor in enumerate(gpu_source.tensors) for offset in range(0, tensor.count - BLOCK + 1, BLOCK)]
    if not candidates:
        raise ValueError("No full 16,384-value block is available; partial blocks and ezm7 are outside this GPU sample experiment.")
    selected = [candidates[int(i)] for i in np.linspace(0, len(candidates) - 1, min(gpu_sample_blocks, len(candidates)), dtype=int)]
    samples, reference_samples = [], []
    for index in sorted({i for i, _ in selected}):
        tensor = gpu_source.tensors[index]
        values = decode(gpu_source.payload(tensor), tensor.count, tensor.codec)
        ref = gpu_reference.tensors[index] if gpu_reference is not None else None
        ref_values = decode(gpu_reference.payload(ref), ref.count, ref.codec) if ref is not None else None
        for _, offset in (position for position in selected if position[0] == index):
            samples.append(values[offset : offset + BLOCK].copy())
            if ref_values is not None:
                reference_samples.append(ref_values[offset : offset + BLOCK].copy())
    gpu_samples = np.stack(samples)
    gpu_loading_seconds = time.perf_counter() - gpu_wall_started
    # Separate CUDA/kernel warmup from the measured batches; no exact CPU clustering is run here.
    warm_values = torch.from_numpy(gpu_samples[:1].astype(np.float32)).to(gpu_device)
    gpu_lloyd(warm_values, min(3, gpu_max_iterations))
    torch.cuda.synchronize(gpu_device)
    del warm_values
    gpu_difference, gpu_reference_difference, cpu_reference_difference = Differences(), Differences(), Differences()
    gpu_seconds = 0.0
    gpu_batches = []
    for start in range(0, len(gpu_samples), gpu_batch_blocks):
        original = gpu_samples[start : start + gpu_batch_blocks]
        torch.cuda.synchronize(gpu_device)
        batch_started = time.perf_counter()
        try:
            batch_values = torch.from_numpy(original.astype(np.float32)).to(gpu_device)
            centers, labels, iterations, converged = gpu_lloyd(batch_values, gpu_max_iterations)
            center_values = centers.cpu().numpy()
            label_values = labels.cpu().numpy().astype("u1")
            decoded = []
            for palette, indices in zip(center_values, label_values, strict=True):
                payload = struct.pack("<I", BLOCK) + f32_to_f16_bits(palette).tobytes() + indices.tobytes()
                decoded.append(decode(payload, BLOCK, Q8P))
            torch.cuda.synchronize(gpu_device)
        except torch.cuda.OutOfMemoryError:
            raise RuntimeError("GPU memory exhausted. Lower gpu_batch_blocks and rerun this optional cell.") from None
        gpu_seconds += time.perf_counter() - batch_started
        gpu_batches.append({"blocks": len(original), "iterations": iterations, "converged": converged})
        for offset, reconstructed in enumerate(decoded):
            gpu_difference.add(original[offset], reconstructed)
            if gpu_reference is not None:
                reference = reference_samples[start + offset]
                gpu_reference_difference.add(reference, reconstructed)
                cpu_reference_difference.add(original[offset], reference)
        del batch_values, centers, labels
        print(f"GPU samples: {start + len(original)}/{len(gpu_samples)} blocks | measured pipeline {gpu_seconds:.3f} s", flush=True)
    gpu_source.verify()
    if gpu_reference is not None:
        gpu_reference.verify()
    gpu_report = {
        "schema": 1, "experiment": "q8p-gpu-samples-v1", "recorded_source_revision": REVISION,
        "input": gpu_source.summary(), "sample_positions": selected,
        "algorithm": "approximate 1D Lloyd; FP32; quantile initialization; no normalization; CUDA atomic reductions",
        "torch": str(torch.__version__), "numpy": np.__version__, "cuda": torch.version.cuda,
        "device": torch.cuda.get_device_name(gpu_device), "sample_blocks": len(gpu_samples),
        "batch_blocks": gpu_batch_blocks, "max_iterations": gpu_max_iterations, "tolerance": gpu_tolerance,
        "source_loading_seconds": gpu_loading_seconds, "measured_pipeline_seconds": gpu_seconds,
        "pipeline_seconds_per_block": gpu_seconds / len(gpu_samples), "batches": gpu_batches,
        "timing_scope": "warmed; includes host/device copies, clustering, palette truncation, packing and decode; excludes source I/O, warmup, comparisons, final verification, SQLite writing",
        "gpu_vs_f16": gpu_difference.summary(),
        "reference": gpu_reference.summary() if gpu_reference is not None else None,
        "reference_origin": reference_origin if gpu_reference is not None else "none",
        "gpu_vs_reference": gpu_reference_difference.summary() if gpu_reference is not None else None,
        "reference_vs_f16": cpu_reference_difference.summary() if gpu_reference is not None else None,
        "whole_file_output_written": False, "quality_or_provider_acceptance": "not_established",
    }
gpu_report_dir = Path(tempfile.mkdtemp(prefix="q8p-gpu-samples-", dir="/content"))
gpu_report_path = gpu_report_dir / "gpu-sample-report.json"
gpu_report_path.write_text(json.dumps(gpu_report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps(gpu_report, indent=2, allow_nan=False))
print("Report:", gpu_report_path, "| No CKPT was written; do not compare sampled pipeline time directly to a full CPU conversion.")
if gpu_download_report:
    from google.colab import files
    files.download(str(gpu_report_path))
