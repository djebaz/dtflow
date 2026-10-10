"""Phase 3A CLI surface and composition: help/identity, exit mapping, config/settings, backend selection.

The fake backend stands in for every real adapter here; adapter wire/transport behavior has its own suites. Every
call passes an isolated log directory so tests never write to the repository's logs/.
"""

import json
import sqlite3

import pytest

from dtflow import cli
from dtflow.backends.drawthings.backend import DrawThingsBackend
from dtflow.backends.drawthings.cloud import DirectCloudBackend
from dtflow.backends.fake import FakeBackend
from dtflow.build import display_identity
from dtflow.composition import BackendSelection, create_backend
from dtflow.core.models import ValidationError


@pytest.fixture
def invoke(tmp_path, capsys):
    def call(*args, state=True):
        argv = ["-q", str(tmp_path / "logs"), *args]
        if state and args[0] != "test":
            argv += ["-d", str(tmp_path / "state")]
        code = cli.main(argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return call


GENERATE = ("generate", "-b", "fake", "-p", "fixture")


def test_edit_without_probe_refuses_before_selection_preparation_or_backend(invoke, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unconsented edit must not select/open/authenticate a backend or prepare files.")

    monkeypatch.setattr(cli, "selection_for", forbidden)
    monkeypatch.setattr(cli, "open_session", forbidden)
    monkeypatch.setattr(cli, "generate_image_files_once", forbidden)
    code, out, err = invoke(
        "edit",
        "--backend",
        "cloud",
        "--model",
        "exact-model",
        "--prompt",
        "fixture",
        "--source",
        str(tmp_path / "missing.png"),
        "--evidence",
        "first-probe",
    )
    assert code == 2 and out == "" and "requires explicit --probe" in err
    assert not (tmp_path / "state").exists()


def test_help_version_and_documented_options(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["-v"])
    assert exit_info.value.code == 0 and display_identity() in capsys.readouterr().out
    help_text = cli.parser().format_help()
    for command in ("generate", "check", "models", "attempts", "settings", "test"):
        assert command in help_text
    choices = cli.parser()._subparsers._group_actions[0].choices
    generate = choices["generate"].format_help()
    for pair in ("-b, --backend", "-e, --endpoint", "-c, --config", "-u, --export", "-l, --lora", "-r, --prefix"):
        assert pair in generate
    assert "-j, --jobs" in choices["settings"].format_help()
    assert "-nc, --no-cache" in choices["gui"].format_help() and cli.parser().parse_args(["gui", "-nc"]).no_cache


@pytest.mark.parametrize(
    "extra,code,remote",
    [
        ((), 0, None),
        (("-f", "reject"), 3, "not_started"),
        (("-f", "terminal_error"), 3, "failed"),
        (("-f", "lost"), 4, "unknown"),
    ],
)
def test_exit_codes_and_stream_separation(invoke, extra, code, remote):
    exit_code, out, err = invoke(*GENERATE, *extra)
    assert exit_code == code
    if remote is None:
        assert json.loads(out)["fake"] is True and "[fake] terminal: OK" in err  # JSON only on stdout
    else:
        assert out == "" and json.loads(err.splitlines()[-1])["remote_outcome"] == remote


@pytest.mark.parametrize(
    "args",
    [
        ("-w", "300"),  # not a multiple of 64
        ("-l", "noweight"),  # explicit weight required
        ("-l", "x:heavy"),
        ("-m", "other-model"),  # unsupported by the fake: rejected before any attempt
        ("-f", "lost", "-b", "local", "-e", "127.0.0.1:1", "-m", "m"),  # fake scenarios are fake-only
    ],
)
def test_invalid_input_exits_2_and_never_dispatches(invoke, tmp_path, args):
    code, out, err = invoke(*GENERATE, *args)
    assert code == 2 and out == ""
    state = tmp_path / "state"
    if (state / "attempts.sqlite3").exists():
        assert sqlite3.connect(state / "attempts.sqlite3").execute("select count(*) from attempts").fetchone()[0] == 0


def test_config_import_overrides_export_and_no_cache_bypass(invoke, tmp_path):
    config = tmp_path / "in.json"
    config.write_text(
        json.dumps(
            {
                "schema": "dtflow.generation-config",
                "version": 1,
                "generation": {"prompt": "from file", "seed": 0, "width": 192, "steps": 7},
            }
        )
    )
    exported = tmp_path / "out.json"
    code, out, _ = invoke("generate", "-b", "fake", "-c", str(config), "-s", "5", "-nc", "-u", str(exported))
    assert code == 0
    metadata = json.loads(open(json.loads(out)["metadata"]).read())
    request = metadata["request"]
    assert (request["prompt"], request["seed"], request["width_px"], request["steps"]) == ("from file", 5, 192, 7)
    assert metadata["build"]["version"] and "source_commit" in metadata["build"]
    evidence = dict(map(tuple, request["resolution_evidence"]))
    assert evidence["field.seed"] == "override" and evidence["field.width"] == "config"
    reused = json.loads(exported.read_text())["generation"]
    assert reused["seed"] == 5 and reused["width"] == 192 and "request_id" not in reused
    assert not (tmp_path / "state" / "cache").exists()  # no-cache run creates no cache
    # Prior metadata is reusable as the configuration, and zero stays zero.
    code, out, _ = invoke(
        "generate",
        "-b",
        "fake",
        "-c",
        metadata["artifacts"][0]["path"].replace("final-1.png", "metadata.json"),
        "-s",
        "0",
    )
    assert code == 0 and json.loads(open(json.loads(out)["metadata"]).read())["request"]["seed"] == 0


def test_unsupported_config_exits_2(invoke, tmp_path):
    bad = tmp_path / "new.json"
    bad.write_text(json.dumps({"schema": "dtflow.generation-config", "version": 99, "generation": {}}))
    code, out, err = invoke("generate", "-b", "fake", "-c", str(bad))
    assert code == 2 and "Unsupported configuration version" in err and out == ""


def test_models_filter_keeps_provenance_and_unknown_entries(invoke):
    code, out, _ = invoke("models", "-b", "fake", "-r", "-k", "lora")
    data = json.loads(out)
    assert code == 0 and data["entries_total"] == 5 and data["kind"] == "lora"
    assert {entry["identifier"] for entry in data["entries"]} == {"fake-soft", "fake-mismatch", "fake-unknown"}
    assert all(entry["kind"] == "lora" and entry["metadata_source"] == "bundled-fixture" for entry in data["entries"])
    assert data["backend_id"] == "fake" and data["observed_at_utc"]
    assert json.loads(invoke("models", "-b", "fake")[1])["entries"][0]["kind"] == "model"


def test_cloud_concurrency_setting_is_validated_persisted_and_versioned(invoke, tmp_path):
    state = tmp_path / "state"
    assert json.loads(invoke("settings")[1]) == {"cloud_concurrency": 4, "entitlement_verified": False}
    assert json.loads(invoke("settings", "-j", "2")[1])["cloud_concurrency"] == 2
    assert json.loads(invoke("settings")[1])["cloud_concurrency"] == 2
    for bad in ("0", "5"):
        assert invoke("settings", "-j", bad)[0] == 2
    assert json.loads(invoke("settings")[1])["cloud_concurrency"] == 2
    newer = '{"schema_version": 2, "cloud_concurrency": 3}\n'
    (state / "settings.json").write_text(newer)
    assert invoke("settings", "-j", "1")[0] == 2 and (state / "settings.json").read_text() == newer


def test_logging_destination_failure_does_not_change_the_command(tmp_path, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    code = cli.main(["-q", str(blocked / "logs"), *GENERATE, "-d", str(tmp_path / "state")])
    captured = capsys.readouterr()
    assert code == 0 and "Logging unavailable" in captured.err and json.loads(captured.out)["fake"] is True


def test_log_file_records_lifecycle_without_prompt_or_secret(invoke, tmp_path, monkeypatch):
    monkeypatch.setenv("DRAWTHINGS_API_KEY", "FAKE-API-KEY-SENTINEL")
    code, out, err = invoke("generate", "-b", "fake", "-p", "SECRET PROMPT TEXT")
    (log_file,) = (tmp_path / "logs").glob("dtflow-*.log")
    text = log_file.read_text()
    assert code == 0 and "attempt succeeded" in text and "backend=fake" in text
    assert "SECRET PROMPT TEXT" not in text + out + err and "FAKE-API-KEY-SENTINEL" not in text + out + err
    attempt = json.loads(out)["attempt_id"]
    assert f"attempt={attempt}" in text  # correlated to the durable attempt


# -- composition: explicit selection, no silent fallback ------------------------------------------------------------


def test_backend_selection_reaches_the_intended_adapter(monkeypatch):
    env = {"DRAWTHINGS_API_KEY": "FAKE-KEY"}
    assert isinstance(create_backend(BackendSelection("fake")), FakeBackend)
    local = create_backend(BackendSelection("local", "192.0.2.7:7859"), environ={})
    assert isinstance(local, DrawThingsBackend) and local.endpoint.label == "grpcs://192.0.2.7:7859"
    assert not isinstance(local, DirectCloudBackend) and local.max_concurrency == 1
    cloud = create_backend(BackendSelection("cloud", cloud_concurrency=2), environ=env)
    assert isinstance(cloud, DirectCloudBackend) and cloud.max_concurrency == 2
    assert "FAKE-KEY" not in repr(vars(cloud))


@pytest.mark.parametrize(
    "selection,environ,message",
    [
        (BackendSelection("local"), {}, "requires --endpoint"),
        (BackendSelection("local", "no-port"), {}, "HOST:PORT"),
        (BackendSelection("local", "host:abc"), {}, "HOST:PORT"),
        (BackendSelection("cloud"), {}, "DRAWTHINGS_API_KEY"),
        (BackendSelection("cloud", cloud_concurrency=9), {"DRAWTHINGS_API_KEY": "k"}, "concurrency"),
        (BackendSelection("other"), {}, "Unknown backend"),
    ],
)
def test_selection_errors_are_validation_failures_with_no_fallback(selection, environ, message):
    with pytest.raises(ValidationError, match=message):
        create_backend(selection, environ=environ)


def test_cli_missing_selection_inputs_exit_2(invoke, monkeypatch):
    monkeypatch.delenv("DRAWTHINGS_API_KEY", raising=False)
    assert invoke("check", "-b", "local")[0] == 2
    assert invoke("check", "-b", "cloud")[0] == 2
    assert invoke("generate", "-b", "cloud", "-p", "x", "-m", "m")[0] == 2


def test_queue_add_plan_dry_run_prints_the_exact_items_and_creates_nothing(invoke, tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema": "dtflow.queue-plan",
                "version": 1,
                "mode": "cross",
                "dimensions": {"prompt": ["a", "b"], "steps": [4, 8]},
            }
        ),
        encoding="utf-8",
    )
    add = ("queue", "add", "-b", "fake", "-p", "fixture")
    code, out, _ = invoke(*add, "-r", str(plan), "-u")
    table = json.loads(out)
    assert code == 0 and table["total"] == 4 and table["seed_policy"] == "fixed"
    assert table["rows"][1] == ["2", "a", "8", "0"] and not (tmp_path / "state").exists()  # dry run persists nothing
    assert invoke(*add, "-k", "2", "-u")[0] == 0 and json.loads(invoke(*add, "-k", "2", "-u")[1])["total"] == 2
    code, _, err = invoke(*add, "-r", str(plan), "-k", "2")
    assert code == 2 and "mutually exclusive" in err and not (tmp_path / "state").exists()
    choices = cli.parser()._subparsers._group_actions[0].choices["queue"]._subparsers._group_actions[0].choices
    assert "-r, --plan" in choices["add"].format_help() and "-u, --dry" in choices["add"].format_help()
