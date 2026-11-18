"""Owner smoke for the DT+ pose experiment: extract once, run baseline and pose jobs in parallel, build a sheet.

Usage (from the repository root):
    uv run python tools/pose_smoke.py -ph photo.png -p "a woman in a red coat" -o out_dir
    uv run python tools/pose_smoke.py -ph photo.png -p "..." -o out_dir --submit -cs 0.7 1.0

Without --submit nothing is sent: the script only extracts the map locally and prints the planned commands.
With --submit it starts PAID DT+ generations (one baseline without control plus one per strength) and writes
sheet.png (map | baseline | pose runs) in the output directory. Each job uses its own state directory
(durable state is SQLite), and stderr of every job is shown on failure. No credentials are read or printed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CONTROL = "controlnet_union_pro_1.0_flux1_q8p.ckpt"
LAUNCH = "from dtflow.cli import main; raise SystemExit(main())"


def cli(*args: str) -> list[str]:
    """Command line for the dtflow CLI of this checkout (there is no __main__ module)."""
    return [sys.executable, "-c", LAUNCH, *args]


def run(label: str, command: list[str]) -> dict | None:
    """Run one CLI command and return its JSON stdout, or None after printing stderr."""
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"[{label}] FAILED (exit {result.returncode})\n{result.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[{label}] unreadable output:\n{result.stdout}\n{result.stderr}", file=sys.stderr)
        return None


def generation_args(args, job_state: Path, out: Path) -> list[str]:
    common = [
        "-b",
        "cloud",
        "-d",
        str(job_state),
        "-o",
        str(out),
        "-p",
        args.prompt,
        "-w",
        str(args.width),
        "-y",
        str(args.height),
        "-s",
        str(args.seed),
        "-t",
        str(args.timeout),
    ]
    if args.model:
        common += ["-m", args.model]
    if args.steps:
        common += ["-i", str(args.steps)]
    return common


def sheet(panels: list[tuple[str, Path]], target: Path) -> None:
    images = [(name, Image.open(path).convert("RGB")) for name, path in panels]
    height = max(image.height for _, image in images)
    label_height = 28
    width = sum(image.width for _, image in images)
    canvas = Image.new("RGB", (width, height + label_height), "white")
    draw = ImageDraw.Draw(canvas)
    x = 0
    for name, image in images:
        canvas.paste(image, (x, label_height))
        draw.text((x + 6, 8), name, fill="black")
        x += image.width
    canvas.save(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-ph", "--photo", type=Path, help="Photograph; the map is extracted locally.")
    source.add_argument("-im", "--image", type=Path, help="Prepared OpenPose RGB body map.")
    parser.add_argument("-p", "--prompt", required=True)
    parser.add_argument("-o", "--out", type=Path, required=True, help="Output directory (created).")
    parser.add_argument("-cs", "--strengths", type=float, nargs="+", default=[1.0], help="Control weights (0,2].")
    parser.add_argument("-co", "--control", default=CONTROL, help="Pinned control checkpoint name.")
    parser.add_argument("-m", "--model", help="FLUX.1 Dev checkpoint; default is the CLI default.")
    parser.add_argument("-w", "--width", type=int, default=1024)
    parser.add_argument("-y", "--height", type=int, default=1024)
    parser.add_argument("-s", "--seed", type=int, default=12345)
    parser.add_argument("-i", "--steps", type=int)
    parser.add_argument("-t", "--timeout", type=float, default=600)
    parser.add_argument("--submit", action="store_true", help="Send PAID DT+ generations (default: extract only).")
    parser.add_argument("--state", type=Path, default=Path(".dtflow"), help="State holding the installed detector.")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    pose_map = args.image
    if args.photo:
        pose_map = args.out / "map.png"
        if pose_map.exists():
            pose_map.unlink()
            pose_map.with_suffix(".pose.json").unlink(missing_ok=True)
        report = run(
            "extract", cli("pose", "extract", "-d", str(args.state), "-i", str(args.photo), "-o", str(pose_map))
        )
        if report is None:
            return 1
        print(f"map: {pose_map} (sha256 {report['map_sha256'][:12]})")

    jobs = {"baseline": cli("generate", *generation_args(args, args.out / "state_baseline", args.out / "baseline.png"))}
    for strength in args.strengths:
        name = f"pose_{strength:g}"
        jobs[name] = cli(
            "pose",
            "generate",
            *generation_args(args, args.out / f"state_{name}", args.out / f"{name}.png"),
            "-im",
            str(pose_map),
            "-co",
            args.control,
            "-cs",
            str(strength),
        )
    for label, command in jobs.items():
        print(f"{label}: uv run dtflow {' '.join(command[3:])}")
    if not args.submit:
        print("\nNothing submitted. Add --submit to run these (paid DT+ generations).")
        return 0

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {label: pool.submit(run, label, command) for label, command in jobs.items()}
        results = {label: future.result() for label, future in futures.items()}
    panels = [("map", pose_map)]
    for label, result in results.items():
        if result and result.get("outputs"):
            panels.append((label, Path(result["outputs"][0])))
            print(f"{label}: {result['outputs'][0]} {result.get('warnings') or ''}")
    if len(panels) < 2:
        return 1
    sheet(panels, args.out / "sheet.png")
    print(f"sheet: {args.out / 'sheet.png'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
