"""Build a stamped DTFlow desktop executable named dtflow-<version>-g<short-sha>-<platform>-<arch>.exe.

Run from the repository root: ``uv run --with pyinstaller python tools/packaging/build_exe.py``.
It records the full source commit and clean/dirty status in the ignored ``src/dtflow/_buildinfo.py``,
runs PyInstaller, renames the result in ``dist/`` and always removes the generated module afterwards.
Windows builds open Qt directly without a console; source CLI execution stays supported.
It builds only; it does not run the executable, generate images or touch the network.
"""

import argparse
import platform
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDINFO = ROOT / "src" / "dtflow" / "_buildinfo.py"
ARCHES = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def source_identity() -> tuple[str, str]:
    commit = git("rev-parse", "HEAD")
    status = "dirty" if git("status", "--porcelain", "--untracked-files=no") else "clean"
    return commit, status


def artifact_name(version: str, commit: str, system: str, machine: str) -> str:
    arch = ARCHES.get(machine.lower(), machine.lower())
    extension = ".exe" if system.lower() == "windows" else ""
    return f"dtflow-{version}-g{commit[:7]}-{system.lower()}-{arch}{extension}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out", default="dist", help="directory receiving the build (default: dist)")
    parser.add_argument(
        "--onedir", action="store_true", help="build a folder distribution instead of the default one-file executable"
    )
    args = parser.parse_args(argv)
    try:
        version("kmeans1d")
    except PackageNotFoundError:
        parser.error("Local q8p conversion requires: uv sync --group quantization.")
    # Derive all potential Component filters from source at packaging time, never at startup or Logs open.
    subprocess.run([sys.executable, str(ROOT / "tools" / "generate_diagnostic_components.py")], cwd=ROOT, check=True)
    commit, status = source_identity()
    if status == "dirty":
        print(
            "warning: working tree has local modifications; the build will report source status dirty", file=sys.stderr
        )
    name = artifact_name(version("dtflow"), commit, platform.system(), platform.machine())
    if args.onedir:
        name = name.removesuffix(".exe")
    out = (ROOT / args.out).resolve()
    BUILDINFO.write_text(f'SOURCE_COMMIT = "{commit}"\nSOURCE_STATUS = "{status}"\n', encoding="utf-8")
    try:
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir" if args.onedir else "--onefile", "--name", "dtflow",
            "--paths", "src", "--copy-metadata", "dtflow", "--collect-data", "drawthings_py",
            "--collect-data", "dtflow", "--copy-metadata", "keyring", "--collect-binaries", "onnxruntime",
            "--copy-metadata", "quickjs-ng", "--copy-metadata", "nodeeditor-qtpy", "--copy-metadata", "kmeans1d", "--hidden-import", "kmeans1d", "--hidden-import", "quickjs", "--hidden-import", "_quickjs", "--hidden-import", "dtflow.scripting.worker",
            "--distpath", str(out),
        ]  # fmt: skip
        if platform.system() == "Windows":
            command.extend(
                [
                    "--windowed",
                    "--icon",
                    str(ROOT / "src/dtflow/resources/images/dtflow.ico"),
                    "--hidden-import",
                    "keyring.backends.Windows",
                ]
            )
        command.append("tools/packaging/launch_gui.py")
        subprocess.run(command, cwd=ROOT, check=True)
    finally:
        BUILDINFO.unlink(missing_ok=True)
    built = out / ("dtflow" if args.onedir else ("dtflow.exe" if platform.system() == "Windows" else "dtflow"))
    target = out / name
    shutil.move(built, target)
    print(f"built {target}\ncommit {commit} ({status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
