"""Template recipes are authoring state, never queue execution or provider authorization."""

import json
import sqlite3
from dataclasses import asdict, replace

import pytest

from dtflow.core.models import LoRASelection, ResolvedGenerationRequest, ValidationError
from dtflow.core.queue_authoring import PlanForm
from dtflow.services import queue_templates as templates


def recipe():
    return templates.Recipe(
        ResolvedGenerationRequest(
            "old-request", "a {cat|dog}", "fake-fixture", 12, loras=(LoRASelection("style", 0.8),)
        ),
        PlanForm("Cross", models="first; second", variation="Random sample", rng_seed="42"),
        5,
        "fake",
        3,
    )


def test_library_roundtrip_update_rename_delete_and_stale_edit(tmp_path):
    assert templates.list_templates(tmp_path) == ()
    original = recipe()
    saved = templates.save_template(tmp_path, "My recipe", original)
    (loaded,) = templates.list_templates(tmp_path)
    assert loaded == saved
    assert loaded.recipe.form == original.form and loaded.recipe.request.prompt == original.request.prompt
    assert loaded.recipe.request.request_id != "old-request"
    changed = replace(original, count=7)
    updated = templates.save_template(tmp_path, saved.name, changed, previous=saved)
    assert updated.identity == saved.identity and updated.revision == 2
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, saved.name, original, previous=saved)
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, "my RECIPE", original)
    renamed = templates.rename_template(tmp_path, updated, "Renamed")
    assert renamed.recipe.count == 7 and renamed.name == "Renamed"
    with pytest.raises(ValidationError):
        templates.delete_template(tmp_path, updated)
    templates.delete_template(tmp_path, renamed)
    assert templates.list_templates(tmp_path) == ()
    assert not (tmp_path / "jobs.sqlite3").exists()  # no execution store or cache needed


@pytest.mark.parametrize("field,value", [("count", True), ("count", 1001), ("cap", 11), ("backend", "unknown")])
def test_recipe_validation_rejects_unsupported_without_overwriting(tmp_path, field, value):
    saved = templates.save_template(tmp_path, "Good", recipe())
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, "Bad", replace(recipe(), **{field: value}))
    assert templates.list_templates(tmp_path) == (saved,)


def test_recipe_excludes_execution_and_private_evidence_and_rejects_new_fields():
    request = replace(recipe().request, resolution_evidence=(("private", "secret-path"),))
    record = templates.recipe_record(replace(recipe(), request=request))
    text = json.dumps(record)
    assert "old-request" not in text and "secret-path" not in text
    assert "request_id" not in record["source"]["base"]
    assert "prepared_assets" not in record["source"]["base"]
    for key in ("endpoint", "asset_ids", "outputs"):
        with pytest.raises(ValidationError):
            templates.recipe_from_record({**record, key: "not allowed"})
    invalid = {**record, "source": {**record["source"], "form": {**asdict(PlanForm()), "variation_count": 1001}}}
    with pytest.raises(ValidationError):
        templates.recipe_from_record(invalid)
    for missing in (
        {**record, "source": {**record["source"], "form": {}}},
        {
            **record,
            "source": {**record["source"], "base": {k: v for k, v in record["source"]["base"].items() if k != "seed"}},
        },
    ):
        with pytest.raises(ValidationError):
            templates.recipe_from_record(missing)


def test_deterministic_enumeration_needs_no_seed_but_random_edits_do(tmp_path):
    form = PlanForm("Cross", variation="Every combination")
    saved = templates.save_template(tmp_path, "Deterministic", replace(recipe(), form=form))
    assert saved.recipe.form.rng_seed == "" and not saved.recipe.form.uses_randomness
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, "Shuffled", replace(recipe(), form=replace(form, shuffle="Shuffle parts")))


def test_newer_or_corrupt_library_is_left_unchanged(tmp_path):
    saved = templates.save_template(tmp_path, "Good", recipe())
    path = tmp_path / "queue_templates.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    for action in (
        lambda: templates.list_templates(tmp_path),
        lambda: templates.save_template(tmp_path, "Another", recipe()),
        lambda: templates.delete_template(tmp_path, saved),
    ):
        with pytest.raises(ValidationError):
            action()
        assert path.read_bytes() == before
    path.write_bytes(b"corrupt library")
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, "Another", recipe())
    assert path.read_bytes() == b"corrupt library"


def test_library_limits_and_recipe_corruption_fail_closed(tmp_path, monkeypatch):
    from dtflow.persistence import queue_templates as storage

    saved = templates.save_template(tmp_path, "Good", recipe())
    monkeypatch.setattr(storage, "MAX_TEMPLATES", 1)
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, "Full", recipe())
    with pytest.raises(ValidationError):
        templates.save_template(
            tmp_path, "Oversized", replace(recipe(), request=replace(recipe().request, prompt="x" * 300_000))
        )
    assert templates.list_templates(tmp_path) == (saved,)
    monkeypatch.setattr(storage, "MAX_LIBRARY_BYTES", 1)
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, saved.name, recipe(), previous=saved)
    monkeypatch.setattr(storage, "MAX_LIBRARY_BYTES", 4 * 1024 * 1024)
    path = tmp_path / storage.FILENAME
    with sqlite3.connect(path) as db:
        db.execute("UPDATE templates SET recipe=?", ('{"version":99}',))
    before = path.read_bytes()
    for action in (
        lambda: templates.list_templates(tmp_path),
        lambda: templates.save_template(tmp_path, "Other", recipe()),
        lambda: templates.rename_template(tmp_path, saved, "Other"),
        lambda: templates.delete_template(tmp_path, saved),
    ):
        with pytest.raises(ValidationError):
            action()
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    "form",
    [
        PlanForm(mode="Unknown"),
        PlanForm(draws=(("seed", True),)),
        PlanForm(drop=51),
        PlanForm(variation="Random sample", rng_seed=""),
        PlanForm(strengths="0.5"),
    ],
)
def test_invalid_or_clamping_form_values_are_not_saved(tmp_path, form):
    with pytest.raises(ValidationError):
        templates.save_template(tmp_path, "Invalid", replace(recipe(), form=form))
    assert templates.list_templates(tmp_path) == ()
