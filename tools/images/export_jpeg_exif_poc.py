"""Local JPEG/EXIF export PoC; complete private metadata is retained.

No provider calls or dependency installation. Uses already available Pillow.
Explicit source/output arguments are required; original PNGs are untouched.
WARNING: output EXIF and JSON retain full private source metadata. Do not share blindly.
"""

import argparse
import hashlib
import io
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import PIL
from PIL import Image, ImageChops, ImageStat

XP_TAGS = {"Title": 40091, "Subject": 40095, "Comments": 40092, "Tags": 40094}


def slug(value):
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    return "-".join(words)[:40].rstrip("-") or "image"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    with path.open("xb") as handle:
        handle.write(data)


def xp(value):
    return value.encode("utf-16-le") + b"\0\0"


def export(source, destination):
    source = source.resolve(strict=True)
    destination = destination.resolve()

    if destination == source or destination.is_relative_to(source):
        raise ValueError("Destination cannot be within the source")
    raw_meta = (source / "metadata.json").read_bytes()
    meta = json.loads(raw_meta)
    req = meta["request"]
    destination.mkdir(parents=True, exist_ok=True)
    for index, artifact in enumerate(meta["artifacts"], 1):
        image_path = (source / Path(artifact["path"]).name).resolve(strict=True)
        if image_path.parent != source:
            raise ValueError("Artifact must be directly inside source")
        original = image_path.read_bytes()
        timestamp = datetime.fromtimestamp(image_path.stat().st_mtime, UTC)
        base = f"{timestamp:%Y%m%d-%H%M%SZ}_{slug(req.get('prompt', ''))}_s{req['seed']}_{meta['attempt_id'][:8]}_{index:02d}"
        suffix = 0
        while True:
            stem = base if suffix == 0 else f"{base}_{suffix:02d}"
            target = destination / f"{stem}.jpg"
            sidecar = target.with_suffix(".json")
            report_path = target.with_suffix(".verification.json")
            if not any(p.exists() for p in (target, sidecar, report_path)):
                break
            suffix += 1
        loras = "; ".join(f"{item['identifier']} @ {item['weight']}" for item in req.get("loras", [])) or "none"
        fields = {
            "Title": req.get("prompt", "")[:160] or "DTFlow image",
            "Subject": f"DTFlow | {req['operation']} | {req['model_id']}",
            "Comments": (
                f"Prompt: {req.get('prompt', '')}\nNegative prompt: {req.get('negative_prompt', '')}\n"
                f"Requested seed: {req['seed']} | Steps: {req['steps']} | Guidance: {req['guidance']}\n"
                f"Size: {artifact['width_px']}x{artifact['height_px']} | Sampler: {req['sampler_id']}\n"
                f"Model: {req['model_id']}\nLoRAs: {loras}\n"
                f"Provider: {meta['backend_id']} | Attempt: {meta['attempt_id']} | Output: {index}\n"
                "Full recorded metadata is stored as JSON in EXIF UserComment."
            ),
            "Tags": f"DTFlow;{req['model_id']};Seed-{req['seed']};Steps-{req['steps']};{req['sampler_id']};{loras}",
        }
        exif = Image.Exif()
        for label, tag in XP_TAGS.items():
            exif[tag] = xp(fields[label])
        # Do not misuse camera Make/Model, copyright or authorship tags.
        exif[270] = fields["Title"].encode("ascii", "replace").decode("ascii")
        exif[305] = f"DTFlow {meta.get('build', {}).get('version', 'unknown')}"
        exif[34665] = {
            37510: b"UNICODE\0" + json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-16"),
            40962: artifact["width_px"],
            40963: artifact["height_px"],
        }
        exif_bytes = exif.tobytes()
        if len(exif_bytes) > 65533:
            raise ValueError("Complete EXIF exceeds JPEG APP1 limit; use a sidecar instead")
        with Image.open(io.BytesIO(original)) as source_image:
            source_image.load()
            if source_image.mode != "RGB":
                raise ValueError("This PoC only handles RGB PNGs; no silent alpha/color conversion")
            encoded = io.BytesIO()
            source_image.save(
                encoded,
                format="JPEG",
                quality=95,
                subsampling=0,
                exif=exif_bytes,
                icc_profile=source_image.info.get("icc_profile"),
            )
            write_new(target, encoded.getvalue())
            with Image.open(target) as exported:
                exported.load()
                actual_exif = exported.getexif()
                actual_fields = {
                    label: bytes(actual_exif[tag]).decode("utf-16-le").rstrip("\0") for label, tag in XP_TAGS.items()
                }
                user_comment = actual_exif.get_ifd(34665)[37510]
                restored_meta = json.loads(user_comment[8:].decode("utf-16"))
                checks = {
                    "jpeg_decodes": exported.format == "JPEG",
                    "dimensions_equal": exported.size == source_image.size,
                    "windows_exif_fields_round_trip": actual_fields == fields,
                    "complete_metadata_round_trip": restored_meta == meta,
                    "original_png_unchanged": image_path.read_bytes() == original,
                    "original_metadata_unchanged": (source / "metadata.json").read_bytes() == raw_meta,
                }
                pixel_error = ImageStat.Stat(ImageChops.difference(source_image, exported)).mean
        if not all(checks.values()):
            raise ValueError("JPEG verification failed")
        write_new(sidecar, raw_meta)
        report = {
            "exported_file": target.name,
            "pillow_version": PIL.__version__,
            "jpeg_quality": 95,
            "subsampling": "4:4:4",
            "lossy": True,
            "mean_absolute_pixel_error_rgb": pixel_error,
            "filename_timestamp_source": "source_file_mtime_UTC_not_verified_generation_time",
            "source_sha256": digest(original),
            "export_sha256": digest(target.read_bytes()),
            "exif_bytes": len(exif_bytes),
            "checks": checks,
            "privacy": "Full private metadata includes prompts, local paths and asset references.",
            "windows_property_api_verification": "separate Windows Shell check required",
        }
        write_new(report_path, (json.dumps(report, indent=2) + "\n").encode())
        print(f"Exported JPEG {index}: EXIF and metadata round-trip passed; originals unchanged.")
    print(f"Destination: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-s", "--source", type=Path, required=True, help="Saved attempt folder containing metadata.json and final PNGs"
    )
    parser.add_argument(
        "-o", "--out", type=Path, required=True, help="New export folder outside the source; metadata is private"
    )
    args = parser.parse_args()
    export(args.source, args.out)


if __name__ == "__main__":
    main()
