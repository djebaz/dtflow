"""Build and measure the node-editor POC executables (#223) with identical PyInstaller settings.

Run from the repository root inside an isolated uv environment that holds only the candidate's runtime
(see README.md for the exact pwsh commands):

    python tools/node_editor_poc/build.py --candidate baseline --measure 5
    python tools/node_editor_poc/build.py --candidate nodeeditor --measure 5

Each run builds the one-file EXE and the one-folder bundle (the folder size is the extracted size), then
writes ``dist/node-editor-poc/<candidate>-manifest.json`` with sizes, the build environment's packages and,
with ``--measure``, start-up timings. Timing starts the packaged app with ``--smoke-report``: the app opens the
fixture, shows the window and records the first event-loop turn, then quits. Nothing is downloaded or generated.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POC = ROOT / "tools" / "node_editor_poc"
RESOURCES = ROOT / "src" / "dtflow" / "resources"
ENTRY = {"baseline": POC / "baseline_app.py", "nodeeditor": POC / "nodeeditor_app.py"}
BUILD_TOOLS = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "setuptools", "pip"}


def pyinstaller(candidate: str, onefile: bool, dist: Path, work: Path) -> None:
    name = f"dtflow-node-editor-poc-{candidate}"
    sep = os.pathsep
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile" if onefile else "--onedir", "--name", name,
        "--paths", str(POC),
        "--add-data", f"{RESOURCES / 'styles'}{sep}dtflow_resources/styles",
        "--add-data", f"{RESOURCES / 'images' / 'controls'}{sep}dtflow_resources/images/controls",
        "--add-data", f"{RESOURCES / 'images' / 'gallery'}{sep}dtflow_resources/images/gallery",
        "--add-data", f"{POC / 'reference_workflow_v1.json'}{sep}.",
        "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work),
    ]  # fmt: skip
    if platform.system() == "Windows":
        command += ["--windowed", "--icon", str(RESOURCES / "images" / "dtflow.ico")]
    command.append(str(ENTRY[candidate]))
    subprocess.run(command, cwd=ROOT, check=True)


def folder_size(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return sum(item.stat().st_size for item in files), len(files)


def measure(executable: Path, runs: int) -> dict:
    """Spawn-to-first-frame and spawn-to-exit wall times, in seconds, for ``runs`` consecutive launches."""
    first_frame, exit_times, reports = [], [], []
    for _ in range(runs):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            start_wall, start = time.time(), time.perf_counter()
            subprocess.run([str(executable), "--smoke-report", str(report_path)], check=True, timeout=180)
            exit_times.append(round(time.perf_counter() - start, 3))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            first_frame.append(round(report["wall_time_first_frame"] - start_wall, 3))
            reports.append(report)
    return {
        "runs": runs,
        "spawn_to_first_frame_s": first_frame,
        "spawn_to_exit_s": exit_times,
        "first_run_first_frame_s": first_frame[0],
        "median_later_first_frame_s": statistics.median(first_frame[1:]) if runs > 1 else None,
        "app_report": reports[-1],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", choices=sorted(ENTRY), required=True)
    parser.add_argument("--out", default="dist/node-editor-poc")
    parser.add_argument("--measure", type=int, default=0, help="launch each build N times and record timings")
    args = parser.parse_args(argv)
    out = (ROOT / args.out).resolve()
    work = ROOT / "build" / "node-editor-poc" / args.candidate
    name = f"dtflow-node-editor-poc-{args.candidate}"
    exe_suffix = ".exe" if platform.system() == "Windows" else ""

    pyinstaller(args.candidate, True, out / "onefile", work / "onefile")
    pyinstaller(args.candidate, False, out / "onedir", work / "onedir")
    onefile = out / "onefile" / f"{name}{exe_suffix}"
    onedir = out / "onedir" / name
    extracted, file_count = folder_size(onedir)
    packages = sorted(
        f"{dist.metadata['Name']}=={dist.version}"
        for dist in distributions()
        if dist.metadata["Name"] and dist.metadata["Name"].lower() not in BUILD_TOOLS
    )
    manifest = {
        "candidate": args.candidate,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": sys.version.split()[0],
        "onefile_exe": onefile.name,
        "onefile_exe_bytes": onefile.stat().st_size,
        "onedir_bytes": extracted,
        "onedir_files": file_count,
        "runtime_packages_in_build_env": packages,
    }
    if args.measure:
        manifest["onefile_timing"] = measure(onefile, args.measure)
        manifest["onedir_timing"] = measure(onedir / f"{name}{exe_suffix}", args.measure)
    baseline = out / "baseline-manifest.json"
    if args.candidate != "baseline" and baseline.is_file():
        base = json.loads(baseline.read_text(encoding="utf-8"))
        manifest["added_vs_baseline"] = {
            "packages": sorted(set(packages) - set(base["runtime_packages_in_build_env"])),
            "onefile_exe_bytes": manifest["onefile_exe_bytes"] - base["onefile_exe_bytes"],
            "onedir_bytes": extracted - base["onedir_bytes"],
        }
    target = out / f"{args.candidate}-manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if not k.endswith("_timing")}, indent=2))
    for key in ("onefile_timing", "onedir_timing"):
        if key in manifest:
            timing = manifest[key]
            print(f"{key}: first-frame {timing['spawn_to_first_frame_s']} s; exit {timing['spawn_to_exit_s']} s")
    print(f"Manifest: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
