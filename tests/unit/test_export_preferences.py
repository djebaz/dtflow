import json

import pytest

from dtflow.core.models import ValidationError
from dtflow.persistence.export_preferences import FILENAME, load_export_preferences, save_export_preferences

WINDOWS_FOLDER = "C:\\Exports"
WSL_FOLDER = "/mnt/c/Exports"


def test_runtimes_keep_separate_destinations_and_share_options(tmp_path):
    assert load_export_preferences(tmp_path, "windows") == {}
    save_export_preferences(tmp_path, WINDOWS_FOLDER, True, False, runtime="windows")
    first = load_export_preferences(tmp_path, "wsl")
    assert first["destination"] == "" and first["include_prompts"] is True
    save_export_preferences(tmp_path, WSL_FOLDER, False, True, True, runtime="wsl")
    save_export_preferences(tmp_path, WINDOWS_FOLDER, False, True, False, runtime="windows")
    windows, wsl = load_export_preferences(tmp_path, "windows"), load_export_preferences(tmp_path, "wsl")
    assert (windows["destination"], wsl["destination"]) == (WINDOWS_FOLDER, WSL_FOLDER)
    assert windows["json_sidecars"] is True and wsl["separate_noise_blank"] is False


@pytest.mark.parametrize(
    ("legacy", "owner", "other", "saved_by"),
    [(WINDOWS_FOLDER, "windows", "wsl", "wsl"), (WSL_FOLDER, "wsl", "windows", "windows")],
)
def test_schema_one_destination_is_attributed_by_form_and_never_converted(tmp_path, legacy, owner, other, saved_by):
    document = {"schema_version": 1, "destination": legacy, "include_prompts": True, "json_sidecars": False}
    (tmp_path / FILENAME).write_text(json.dumps(document), encoding="utf-8")
    assert load_export_preferences(tmp_path, owner)["destination"] == legacy
    assert load_export_preferences(tmp_path, other)["destination"] == ""
    save_export_preferences(tmp_path, "/elsewhere" if saved_by == "wsl" else "D:\\Out", True, False, runtime=saved_by)
    saved = json.loads((tmp_path / FILENAME).read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2 and saved["destinations"][owner] == legacy


def test_unsupported_or_malformed_preferences_are_refused_and_left_unchanged(tmp_path):
    for document in (
        {"schema_version": 3, "destinations": {}},
        {"schema_version": 2, "destinations": {"windows": 5}, "include_prompts": True, "json_sidecars": True},
    ):
        (tmp_path / FILENAME).write_text(json.dumps(document), encoding="utf-8")
        before = (tmp_path / FILENAME).read_bytes()
        with pytest.raises(ValidationError):
            load_export_preferences(tmp_path, "windows")
        with pytest.raises(ValidationError):
            save_export_preferences(tmp_path, WINDOWS_FOLDER, True, True, runtime="windows")
        assert (tmp_path / FILENAME).read_bytes() == before


def test_destination_length_is_bounded(tmp_path):
    with pytest.raises(ValidationError):
        save_export_preferences(tmp_path, "C:\\" + "a" * 4096, True, True, runtime="windows")
