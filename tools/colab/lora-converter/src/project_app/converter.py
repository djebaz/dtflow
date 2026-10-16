"""Build and invoke the official Draw Things LoRAConverter on CPU Linux.

The upstream CLI and arguments are source-verified. This Linux/Colab build
integration is experimental until an actual Colab conversion succeeds.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen

from .cache import content_fingerprint
from .config import atomic_write_json
from .logging_utils import run_logged_command

UPSTREAM_REVISION = "8d9c62ad38539ef8bb123f8b8e78cab76caafd0c"
SWIFT_VERSION = "6.0.3"
BAZEL_VERSION = "7.4.1"
BUILD_REVISION = "cpu-linux-v2"
SOURCE_PATCH_REVISION = "zipfoundation-optional-file-v1"
ZIPFOUNDATION_ORIGINAL_SHA256 = '3bb6447dff9dd52bd8da73111c592f0a2171f06750865dc795aa53455beb3c01'
ZIPFOUNDATION_PATCHED_SHA256 = 'fc095674bf385dbfe3d2ae31623a9acb183edb05616a3e9fa23eb13f44812c50'


def _download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    with urlopen(url, timeout=90) as source, partial.open("wb") as output:
        shutil.copyfileobj(source, output, length=4 * 1024 * 1024)
    partial.replace(target)



def swift_distribution(ubuntu):
    """Return the pinned URL and extracted folder for a supported Ubuntu release."""
    if ubuntu not in {"22.04", "24.04"}:
        raise ValueError("Automatic Swift setup supports Ubuntu 22.04 and 24.04 only")
    directory = "ubuntu" + ubuntu.replace(".", "")
    folder = f"swift-{SWIFT_VERSION}-RELEASE-ubuntu{ubuntu}"
    url = (
        f"https://download.swift.org/swift-{SWIFT_VERSION}-release/"
        f"{directory}/swift-{SWIFT_VERSION}-RELEASE/{folder}.tar.gz"
    )
    return url, folder



def patch_zipfoundation(checkout, logger):
    """Unwrap the optional FILE pointer in the pinned vendored ZIPFoundation source."""
    source = checkout / "Vendors/ZIPFoundation/Sources/ZIPFoundation/Archive+MemoryFile.swift"
    digest = content_fingerprint(source)
    if digest == ZIPFOUNDATION_PATCHED_SHA256:
        logger.info("SOURCE PATCH REUSED: %s", SOURCE_PATCH_REVISION)
        return
    if digest != ZIPFOUNDATION_ORIGINAL_SHA256:
        raise RuntimeError("ZIPFoundation source does not match the pinned compatibility patch")
    original = source.read_text()
    before = "      if append {\n        fseeko(result, 0, SEEK_END)"
    after = "      if append, let result = result {\n        fseeko(result, 0, SEEK_END)"
    if original.count(before) != 1:
        raise RuntimeError("ZIPFoundation optional FILE patch no longer matches")
    source.write_text(original.replace(before, after, 1))
    if content_fingerprint(source) != ZIPFOUNDATION_PATCHED_SHA256:
        raise RuntimeError("ZIPFoundation compatibility patch checksum mismatch")
    logger.info("SOURCE PATCH APPLIED: %s", SOURCE_PATCH_REVISION)


def prepare_converter(root, jobs, logger):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("The automatic converter build targets x86_64 Linux only")
    root.mkdir(parents=True, exist_ok=True)
    binary = root / "LoRAConverter"
    receipt = root / "build_receipt.json"
    identity = {"upstream": UPSTREAM_REVISION, "swift": SWIFT_VERSION, "bazel": BAZEL_VERSION, "build": BUILD_REVISION, "platform": platform.platform(), "c_toolchain": "system-clang-openmp-v1", "source_patch": SOURCE_PATCH_REVISION}
    if binary.is_file() and receipt.is_file():
        previous = json.loads(receipt.read_text())
        if previous.get("identity") == identity and previous.get("sha256") == content_fingerprint(binary):
            run_logged_command([str(binary), "--help"], logger, env=converter_env(root))
            return binary
    logger.info("BUILD START: official LoRAConverter; experimental CPU Linux build; no model weights are needed")
    run_logged_command(["apt-get", "install", "-y", "git", "clang", "llvm", "libomp-dev", "cmake", "pkg-config", "libpng-dev", "libjpeg-dev", "libopenblas-dev", "libedit2", "libxml2", "libsqlite3-0", "libcurl4-openssl-dev", "libssl-dev", "zlib1g-dev", "libncurses6", "libatomic1", "libpython3-dev"], logger)
    # Use Ubuntu's compiler and its matching OpenMP development package.
    # Swift's bin directory also contains clang, but not Ubuntu's omp.h.
    cc = Path("/usr/bin/clang")
    cxx = Path("/usr/bin/clang++")
    if not cc.is_file() or not cxx.is_file():
        raise RuntimeError("System Clang was not installed successfully")
    with tempfile.TemporaryDirectory(prefix="openmp-check-", dir=root) as check:
        source = Path(check) / "openmp.c"
        executable = Path(check) / "openmp-check"
        source.write_text("#include <omp.h>\nint main(void) { return omp_get_max_threads() < 1; }\n")
        logger.info("TOOLCHAIN CHECK: system Clang OpenMP compile, link and execution")
        run_logged_command([str(cc), "-fopenmp", str(source), "-o", str(executable)], logger)
        run_logged_command([str(executable)], logger)
    release = platform.freedesktop_os_release()
    ubuntu = release.get("VERSION_ID")
    if release.get("ID") != "ubuntu" or ubuntu not in {"22.04", "24.04"}:
        raise RuntimeError("Automatic Swift setup supports Ubuntu 22.04 and 24.04 only")
    swift_url, swift_folder = swift_distribution(ubuntu)
    tools = root / "toolchain"
    swift_root = tools / swift_folder
    if not (swift_root / "usr/bin/swiftc").is_file():
        archive = tools / "swift.tar.gz"
        logger.info("TOOLCHAIN DOWNLOAD: Swift %s for Ubuntu %s", SWIFT_VERSION, ubuntu)
        _download(swift_url, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(tools, filter="data")
        if not (swift_root / "usr/bin/swiftc").is_file():
            raise RuntimeError(f"Swift archive did not contain {swift_folder}/usr/bin/swiftc")
    bazel = tools / "bazel"
    if not bazel.is_file():
        _download(f"https://github.com/bazelbuild/bazel/releases/download/{BAZEL_VERSION}/bazel-{BAZEL_VERSION}-linux-x86_64", bazel)
        bazel.chmod(0o755)
    checkout = root / "source"
    if not (checkout / ".git").is_dir():
        run_logged_command(["git", "init", str(checkout)], logger)
        run_logged_command(["git", "-C", str(checkout), "remote", "add", "origin", "https://github.com/drawthingsai/draw-things-community.git"], logger)
    run_logged_command(["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", UPSTREAM_REVISION], logger)
    run_logged_command(["git", "-C", str(checkout), "checkout", "--detach", UPSTREAM_REVISION], logger)
    actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if actual != UPSTREAM_REVISION:
        raise RuntimeError("Upstream source revision mismatch")
    patch_zipfoundation(checkout, logger)
    # The official Linux workspace defaults to cuDNN. Conversion uses CPU;
    # change only this generated workspace copy and preserve provenance.
    workspace = (checkout / "WORKSPACE.linux").read_text()
    if workspace.count("have_cudnn = True") != 1:
        raise RuntimeError("Upstream CPU build adaptation no longer matches")
    (checkout / "WORKSPACE").write_text(workspace.replace("have_cudnn = True", "have_cudnn = False"))
    (checkout / ".bazelrc").write_text("try-import %workspace%/.bazelrc.linux\n")
    env = dict(os.environ, PATH=str(swift_root / "usr/bin") + os.pathsep + os.environ.get("PATH", ""), CC=str(cc), CXX=str(cxx))
    run_logged_command([str(bazel), "build", "Apps:LoRAConverter", "--compilation_mode=opt", f"--jobs={jobs}", "--spawn_strategy=local", f"--repo_env=CC={cc}", f"--repo_env=CXX={cxx}", "--verbose_failures"], logger, cwd=checkout, env=env)
    built = checkout / "bazel-bin/Apps/LoRAConverter"
    run_logged_command([str(built), "--help"], logger, env=env)
    shutil.copy2(built, binary)
    # Copy the Linux runtime libraries needed by Swift's dynamically-linked binary.
    libraries = swift_root / "usr/lib/swift/linux"
    target_lib = root / "swift-libs"
    shutil.copytree(libraries, target_lib, dirs_exist_ok=True)
    run_logged_command([str(binary), "--help"], logger, env=converter_env(root))
    atomic_write_json(receipt, {"identity": identity, "sha256": content_fingerprint(binary), "source_patch": SOURCE_PATCH_REVISION, "adaptation": "WORKSPACE: have_cudnn=False; system Clang with libomp-dev; ZIPFoundation optional FILE unwrap; no CUDA build config"})
    return binary


def converter_env(root):
    return dict(os.environ, LD_LIBRARY_PATH=str(root / "swift-libs") + os.pathsep + os.environ.get("LD_LIBRARY_PATH", ""))


def validate_checkpoint(path):
    """Reject pickle/renamed safetensors; verify the Draw Things SQLite store."""
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("Converter output is not a Draw Things SQLite checkpoint")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Converted checkpoint failed SQLite integrity validation")
        if not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            raise ValueError("Converted checkpoint contains no tables")
    if path.with_name(path.name + "-tensordata").exists():
        raise ValueError("Unexpected external tensor sidecar; this workflow only delivers self-contained LoRA checkpoints")


def convert(binary, source, target, config, logger):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lora-convert-", dir=Path(config.runtime_root)) as temporary:
        output = Path(temporary)
        command = [str(binary), "--file", str(source), "--name", "converted", "--output-directory", str(output), "--scale-factor", str(config.scale_factor)]
        if config.model_version:
            command += ["--version", config.model_version]
        run_logged_command(command, logger, env=converter_env(binary.parent))
        checkpoint = output / "converted_lora_f16.ckpt"
        if not checkpoint.is_file():
            raise RuntimeError("Official converter did not create the expected checkpoint")
        validate_checkpoint(checkpoint)
        shutil.copy2(checkpoint, target)
        validate_checkpoint(target)
