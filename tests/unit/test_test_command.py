"""Test selection and pytest exit status belong to the thin development CLI wrapper."""

import pytest

from dtflow.cli import main


@pytest.mark.parametrize(
    "flags,mode,groups,scope",
    [
        ([], "offline", "(not live and not live_generation)", "both"),
        (["-all"], "offline", "(not live and not live_generation)", "both"),
        (["-live"], "live", "(live and not live_generation)", "both"),
        (["-live", "-local"], "live", "(live and not live_generation)", "local"),
        (["--cloud"], "live", "(live and not live_generation)", "cloud"),
        (["-local", "-cloud"], "live", "(live and not live_generation)", "both"),
        (["-gen", "-cloud"], "generation", "live_generation", "cloud"),
        (["-gen"], "generation", "live_generation", "both"),
        (["-all", "-live"], "live", "(not live and not live_generation) or (live and not live_generation)", "both"),
        (["-all", "-gen"], "generation", "(not live and not live_generation) or live_generation", "both"),
        (
            ["--all", "--live", "--generate"],
            "both",
            "(not live and not live_generation) or (live and not live_generation) or live_generation",
            "both",
        ),
    ],
)
def test_test_command_selects_only_requested_groups_and_preserves_exit_code(monkeypatch, flags, mode, groups, scope):
    calls = []

    def invoke(arguments):
        calls.append(arguments)
        return 7

    monkeypatch.setattr(pytest, "main", invoke)
    assert main(["test", *flags]) == 7
    (arguments,) = calls
    assert arguments[arguments.index("-m") + 1] == groups
    assert arguments[arguments.index("--dtflow-test-mode") + 1] == mode
    assert arguments[arguments.index("--dtflow-live-scope") + 1] == scope


def test_local_scope_cannot_select_cloud_generation(capsys):
    assert main(["test", "-local", "-gen"]) == 2
    assert "DT+ only" in capsys.readouterr().err
