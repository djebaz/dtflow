#@title Active status, result, and previous runs { display-mode: "form" }
# Requires: only the Python standard library; Drive is mounted on demand in Colab.
# Produces: a stable current/previous-result panel.
# Safe to rerun: yes.

import json
from pathlib import Path

_STATUS_PROJECT_ID = 'lora-converter'  # generated
_STATUS_DEFAULT_PERSISTENT_ROOT = '/content/drive/MyDrive/production_colab/lora-converter'  # generated


def _status_persistent_root():
    try:
        from google.colab import drive  # type: ignore[import-not-found]

        if not Path("/content/drive/MyDrive").is_dir():
            drive.mount("/content/drive")
    except Exception as error:
        print(f"Persistent storage could not be mounted: {error}")
    registry = Path("/content/drive/MyDrive/production_colab_registry") / (
        _STATUS_PROJECT_ID + ".json"
    )
    if registry.is_file():
        try:
            payload = json.loads(registry.read_text(encoding="utf-8"))
            if payload.get("project_id") == _STATUS_PROJECT_ID and payload.get("persistent_root"):
                return Path(payload["persistent_root"])
        except (OSError, ValueError, TypeError) as error:
            print(f"Persistent project registry is unreadable; using the default root: {error}")
    return Path(_STATUS_DEFAULT_PERSISTENT_ROOT)


def _show_file_link(path, label):
    print(f"{label}: {path}")
    if path.is_file():
        try:
            from IPython.display import FileLink, display

            display(FileLink(str(path), result_html_prefix=f"{label}: "))
        except ImportError:
            pass


if "RUN_RESULT" in globals():
    _show_file_link(Path(RUN_RESULT["output_path"]), "Current output")
    _show_file_link(Path(RUN_RESULT["run_log"]), "Current run log")

_persistent_root = _status_persistent_root()
_manifests = sorted((_persistent_root / "runs").glob("*/run_manifest.json"), reverse=True)
if not _manifests:
    print(f"No persistent runs found under {_persistent_root}")
else:
    print("Recent persistent runs")
    for _manifest_path in _manifests[:5]:
        try:
            _manifest = json.loads(_manifest_path.read_text(encoding="utf-8"))
            print(f"- {_manifest.get('run_id', _manifest_path.parent.name)}: {_manifest.get('status', 'UNKNOWN')}")
            _persistent_output = _manifest.get("persistent_output_path")
            if _persistent_output:
                _show_file_link(Path(_persistent_output), "  Persistent output")
            _show_file_link(Path(_manifest.get("run_log", "")), "  Full run log")
        except (OSError, ValueError, TypeError) as _error:
            print(f"- {_manifest_path.parent.name}: UNREADABLE ({_error})")
