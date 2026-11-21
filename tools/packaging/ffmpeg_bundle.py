"""Stage pinned Windows FFmpeg distributions for the canonical PyInstaller build.

Selection is explicit at build time. Neither provider downloads at app startup,
changes DTFlow's encoder commands, or falls back to another provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
VERSION = "8.1.2"
TOOLS = ("ffmpeg.exe", "ffprobe.exe")
TAS_DLLS = (
    "avcodec-62.dll",
    "avdevice-62.dll",
    "avfilter-11.dll",
    "avformat-62.dll",
    "avutil-60.dll",
    "swresample-6.dll",
    "swscale-9.dll",
)
MAX_ARCHIVE_BYTES = 180 * 1024 * 1024
PROVIDERS = {
    "gyan": {
        "vendor": "Gyan Doshi Essentials, Windows x64 static build",
        "archive_name": f"ffmpeg-{VERSION}-essentials_build.zip",
        "archive_url": (
            f"https://github.com/GyanD/codexffmpeg/releases/download/{VERSION}/"
            f"ffmpeg-{VERSION}-essentials_build.zip"
        ),
        "archive_sha256": "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
        "license": "GPL-3.0",
        "stage": ROOT / "tmp" / "packaging" / f"ffmpeg-{VERSION}-essentials",
        "notice": "FFmpeg-NOTICE.txt",
    },
    "imageio": {
        "vendor": "imageio-ffmpeg 0.5.1 Windows x64 wheel (FFmpeg 4.2.2) + Gyan ffprobe 8.1.2",
        "archive_name": "imageio_ffmpeg-0.5.1-py3-none-win_amd64.whl",
        "archive_url": (
            "https://files.pythonhosted.org/packages/a9/1c/"
            "1b9c72bf839def47626436ea5ebaf643404f7850482c5fafd71a3deeaa94/"
            "imageio_ffmpeg-0.5.1-py3-none-win_amd64.whl"
        ),
        "archive_sha256": "1521e79e253bedbdd36a547e0cbd94a025ba0b558e17f08fea687d805a0e4698",
        "license": "Mixed upstream FFmpeg binaries; GPL source-delivery review pending",
        "stage": ROOT / "tmp" / "packaging" / "imageio-ffmpeg-0.5.1",
        "notice": "Imageio-FFmpeg-NOTICE.txt",
    },
    "imageio-mini": {
        "vendor": "imageio-ffmpeg 0.5.1 FFmpeg 4.2.2 + ffmpeg-mini-win64 1.0.1 FFprobe 7.1",
        "archive_name": "ffmpeg-mini-win64.zip",
        "archive_url": (
            "https://github.com/nichiken/ffmpeg-mini-win64/releases/download/v1.0.1/"
            "ffmpeg-mini-win64.zip"
        ),
        "archive_sha256": "f921d628254e824975ff9a60462cc1d48a5e627ed62c9a893c32ca36c55531cb",
        "license": "Mixed FFmpeg 4.2.2 + LGPL FFprobe 7.1; source review pending",
        "stage": ROOT / "tmp" / "packaging" / "imageio-mini-ffprobe-7.1",
        "notice": "Imageio-Mini-FFmpeg-NOTICE.txt",
    },
    "unpins": {
        "vendor": "unpins FFmpeg, Windows x64 headless multicall build (no GPU acceleration)",
        "archive_name": "ffmpeg-8.1.2-1-x86_64-windows.exe.zst",
        "archive_url": (
            "https://github.com/unpins/ffmpeg/releases/download/v8.1.2-1/"
            "ffmpeg-8.1.2-1-x86_64-windows.exe.zst"
        ),
        "archive_sha256": "abb78672e21721e7eadd84450616bd954f6b067d9355a27ef5d2b80f65a6f842",
        "license": "GPL-3.0-or-later",
        "stage": ROOT / "tmp" / "packaging" / "unpins-ffmpeg-8.1.2-1",
        "notice": "Unpins-FFmpeg-NOTICE.txt",
    },
    "tas": {
        "vendor": "NevermindNilas TAS-FFMPEG, Windows x64 shared build",
        "archive_name": f"tas-ffmpeg-{VERSION}-win64.zip",
        "archive_url": (
            f"https://github.com/NevermindNilas/TAS-FFMPEG/releases/download/v{VERSION}/"
            f"tas-ffmpeg-{VERSION}-win64.zip"
        ),
        "archive_sha256": "1d570273dc8be2b3f386467657591a22045d058a830b1eb1eaa881dafb689bfb",
        "license": "GPL-2.0-or-later",
        "stage": ROOT / "tmp" / "packaging" / f"tas-ffmpeg-{VERSION}",
        "notice": "TAS-FFmpeg-NOTICE.txt",
        "corresponding_source_url": (
            f"https://github.com/NevermindNilas/TAS-FFMPEG/releases/download/v{VERSION}/"
            f"tas-ffmpeg-{VERSION}-corresponding-source.tar.xz"
        ),
        "corresponding_source_sha256": (
            "74605032486afbaaf6a489420eb4eea870c20604aace2b993e850a3e7d338ebd"
        ),
    },
}


def sha256(path: Path) -> str:
    """Hash a file without reading the whole binary into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _spec(provider: str) -> dict:
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported FFmpeg provider: {provider!r}")
    return PROVIDERS[provider]


def stage_dir(provider: str) -> Path:
    """Return the provider's separate ignored staging directory."""
    return _spec(provider)["stage"]


def _archive(provider: str) -> Path:
    spec = _spec(provider)
    stage = stage_dir(provider)
    archive = stage / spec["archive_name"]
    stage.mkdir(parents=True, exist_ok=True)
    if archive.is_file():
        if sha256(archive) != spec["archive_sha256"]:
            raise ValueError(f"FFmpeg archive checksum mismatch: remove {archive} before rebuilding.")
        return archive

    partial = stage / (spec["archive_name"] + ".partial")
    try:
        with urllib.request.urlopen(spec["archive_url"], timeout=90) as response, partial.open("wb") as out:
            total = 0
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError("FFmpeg download exceeded its expected size.")
                out.write(chunk)
        if sha256(partial) != spec["archive_sha256"]:
            raise ValueError("Downloaded FFmpeg archive failed the pinned SHA-256 check.")
        os.replace(partial, archive)
    finally:
        partial.unlink(missing_ok=True)
    return archive


def _stage_unpins(archive: Path, tools_dir: Path) -> None:
    """Unpack Unpins' verified multicall Windows binary under both CLI names.

    FFmpeg's executable dispatches by argv[0]. Keep two names for DTFlow's
    existing subprocess calls; this duplicates the file in a one-file archive.
    """

    extractor = shutil.which("7z")
    if not extractor:
        seven_zip = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
        if seven_zip.is_file():
            extractor = str(seven_zip)
    if not extractor:
        raise ValueError("Unpins FFmpeg requires 7-Zip 24.03+ (7z.exe) to unpack its .zst build archive.")

    with tempfile.TemporaryDirectory(dir=archive.parent) as temp:
        destination = Path(temp)
        completed = subprocess.run(
            [extractor, "e", "-y", f"-o{destination}", str(archive)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise ValueError(f"7-Zip could not extract Unpins FFmpeg (exit {completed.returncode}).")
        unpacked = destination / archive.name.removesuffix(".zst")
        if not unpacked.is_file() or not 0 < unpacked.stat().st_size <= 256 * 1024 * 1024:
            raise ValueError("Unpins archive did not contain the expected standalone Windows executable.")
        for tool in TOOLS:
            shutil.copyfile(unpacked, tools_dir / tool)


def _stage_imageio(
    archive: Path, tools_dir: Path, notices_dir: Path, *, mini_probe_archive: Path | None = None
) -> None:
    """Stage the pinned imageio encoder with a separately pinned FFprobe executable."""

    with zipfile.ZipFile(archive) as z:
        ffmpeg_member = "imageio_ffmpeg/binaries/ffmpeg-win64-v4.2.2.exe"
        metadata_member = "imageio_ffmpeg-0.5.1.dist-info/METADATA"
        if ffmpeg_member not in z.namelist() or metadata_member not in z.namelist():
            raise ValueError("Pinned imageio-ffmpeg wheel lacks its expected FFmpeg binary or metadata.")
        with z.open(ffmpeg_member) as src, (tools_dir / "ffmpeg.exe").open("wb") as dst:
            shutil.copyfileobj(src, dst)
        upstream = notices_dir / "IMAGEIO-WHEEL"
        upstream.mkdir()
        for name in z.namelist():
            if not name.startswith("imageio_ffmpeg-0.5.1.dist-info/") or name.endswith("/"):
                continue
            relative = name.rsplit("/", 1)[-1]
            if relative == "METADATA" or "LICEN" in relative.upper():
                with z.open(name) as src, (upstream / relative).open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    if mini_probe_archive is not None:
        with zipfile.ZipFile(mini_probe_archive) as z:
            for member, destination in (
                ("ffprobe.exe", tools_dir / "ffprobe.exe"),
                ("LICENSE.LGPL.txt", notices_dir / "MINI-FFPROBE-LICENSE.LGPL.txt"),
                ("FFMPEG-README.md", notices_dir / "MINI-FFPROBE-README.md"),
            ):
                if member not in z.namelist():
                    raise ValueError(f"Pinned mini FFprobe archive is missing {member}.")
                with z.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        return

    gyan_archive = _archive("gyan")
    with zipfile.ZipFile(gyan_archive) as z:
        entries = {
            name: name[: -len("bin/ffprobe.exe")]
            for name in z.namelist()
            if name.endswith("/bin/ffprobe.exe")
        }
        if len(entries) != 1:
            raise ValueError("Pinned Gyan archive has no unique ffprobe.exe.")
        root = next(iter(entries.values()))
        gyan_files = {
            root + "bin/ffprobe.exe": tools_dir / "ffprobe.exe",
            root + "LICENSE": notices_dir / "GYAN-LICENSE.txt",
            root + "README.txt": notices_dir / "GYAN-README.txt",
        }
        for name, target in gyan_files.items():
            if name not in z.namelist():
                raise ValueError(f"Pinned Gyan archive is missing {name}.")
            with z.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def prepare(provider: str = "gyan") -> dict:
    """Stage verified binaries and license files for one explicitly selected provider."""
    spec = _spec(provider)
    stage = stage_dir(provider)
    archive = _archive(provider)
    tools_dir, notices_dir = stage / "bin", stage / "licenses"
    for directory in (tools_dir, notices_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    binaries = TOOLS + (TAS_DLLS if provider == "tas" else ())
    if provider == "unpins":
        _stage_unpins(archive, tools_dir)
    elif provider == "imageio":
        _stage_imageio(archive, tools_dir, notices_dir)
    elif provider == "imageio-mini":
        _stage_imageio(_archive("imageio"), tools_dir, notices_dir, mini_probe_archive=archive)
    else:
        with zipfile.ZipFile(archive) as z:
            members = {info.filename.replace("\\", "/"): info for info in z.infolist() if not info.is_dir()}
            roots = [name[: -len("bin/ffmpeg.exe")] for name in members if name.endswith("/bin/ffmpeg.exe")]
            if len(roots) != 1:
                raise ValueError("Pinned FFmpeg archive has no unique bin/ffmpeg.exe.")
            root = roots[0]
    
            required = {name: root + "bin/" + name for name in binaries}
            if provider == "gyan":
                required.update({
                    "UPSTREAM-LICENSE.txt": root + "LICENSE",
                    "UPSTREAM-README.txt": root + "README.txt",
                })
            else:
                # Retain the publisher's full notice tree, not a hand-selected subset.
                license_root = root + "licenses/"
                for name in members:
                    if not name.startswith(license_root):
                        continue
                    relative = name[len(license_root):]
                    if not relative or ".." in PurePosixPath(relative).parts:
                        raise ValueError("Unsafe TAS license archive member.")
                    required["UPSTREAM/" + relative] = name
                required["UPSTREAM-MANIFEST.json"] = root + "manifest.json"
                if "UPSTREAM/LICENSING.md" not in required or "UPSTREAM/README.txt" not in required:
                    raise ValueError("TAS archive lacks its required license and corresponding-source notices.")
    
            missing = [name for name, member in required.items() if member not in members]
            if missing:
                raise ValueError(f"FFmpeg archive missing required members: {', '.join(missing)}.")
            for name, member in required.items():
                dest = (tools_dir if name in binaries else notices_dir) / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(members[member]) as source, dest.open("wb") as output:
                    shutil.copyfileobj(source, output)

    shutil.copyfile(ROOT / "LICENSE", notices_dir / "GPL-3.0.txt")
    shutil.copyfile(
        ROOT / "tools" / "packaging" / spec["notice"], notices_dir / "DTFlow-FFmpeg-NOTICE.txt"
    )
    record = {
        "provider": provider,
        "multicall": provider == "unpins",
        "vendor": spec["vendor"],
        "version": "4.2.2" if provider in ("imageio", "imageio-mini") else VERSION,
        "ffprobe_version": "7.1" if provider == "imageio-mini" else VERSION,
        "archive_url": spec["archive_url"],
        "archive_sha256": spec["archive_sha256"],
        "license": spec["license"],
        "tools": {name: sha256(tools_dir / name) for name in TOOLS},
        "dlls": {name: sha256(tools_dir / name) for name in TAS_DLLS} if provider == "tas" else {},
        "notices": {
            path.relative_to(notices_dir).as_posix(): sha256(path)
            for path in sorted(notices_dir.rglob("*"))
            if path.is_file()
        },
    }
    if provider == "imageio":
        gyan = _spec("gyan")
        record["ffprobe_archive_url"] = gyan["archive_url"]
        record["ffprobe_archive_sha256"] = gyan["archive_sha256"]
    if provider == "imageio-mini":
        imageio = _spec("imageio")
        record["ffmpeg_archive_url"] = imageio["archive_url"]
        record["ffmpeg_archive_sha256"] = imageio["archive_sha256"]
        record["ffprobe_archive_url"] = spec["archive_url"]
        record["ffprobe_archive_sha256"] = spec["archive_sha256"]
    if provider == "tas":
        record["corresponding_source_url"] = spec["corresponding_source_url"]
        record["corresponding_source_sha256"] = spec["corresponding_source_sha256"]
    (stage / "bundle.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {
        "tools_dir": tools_dir,
        "notices_dir": notices_dir,
        "binary_names": binaries,
        "record": record,
    }


def staged_record(provider: str = "gyan") -> dict:
    """Verify staged contents and provenance without a second network request."""
    spec = _spec(provider)
    stage = stage_dir(provider)
    manifest = stage / "bundle.json"
    if not manifest.is_file():
        raise ValueError(f"FFmpeg {provider} staging manifest is missing; rerun the canonical builder.")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        record.get("provider") != provider
        or record.get("version") != ("4.2.2" if provider in ("imageio", "imageio-mini") else VERSION)
        or record.get("archive_sha256") != spec["archive_sha256"]
        or record.get("archive_url") != spec["archive_url"]
        or (provider == "imageio" and (
            record.get("ffprobe_version") != VERSION
            or record.get("ffprobe_archive_sha256") != _spec("gyan")["archive_sha256"]
            or record.get("ffprobe_archive_url") != _spec("gyan")["archive_url"]
        ))
        or (provider == "imageio-mini" and (
            record.get("ffprobe_version") != "7.1"
            or record.get("ffmpeg_archive_sha256") != _spec("imageio")["archive_sha256"]
            or record.get("ffmpeg_archive_url") != _spec("imageio")["archive_url"]
            or record.get("ffprobe_archive_sha256") != spec["archive_sha256"]
            or record.get("ffprobe_archive_url") != spec["archive_url"]
        ))
    ):
        raise ValueError("Staged FFmpeg provider or pinned archive does not match the requested build.")
    expected_dlls = set(TAS_DLLS) if provider == "tas" else set()
    if set(record["tools"]) != set(TOOLS) or set(record["dlls"]) != expected_dlls:
        raise ValueError("Staged FFmpeg executable/DLL inventory is incomplete.")
    for name, digest in (record["tools"] | record["dlls"]).items():
        if sha256(stage / "bin" / name) != digest:
            raise ValueError(f"Staged {name} differs from the verified upstream archive.")
    for relative, digest in record["notices"].items():
        if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError("Invalid staged FFmpeg license path.")
        if sha256(stage / "licenses" / relative) != digest:
            raise ValueError(f"Staged license file {relative} differs from the recorded bundle.")
    return record
