"""Test selection and pytest exit status belong to the thin development CLI wrapper."""

import pytest

from dtflow.cli import main


@pytest.mark.parametrize(
    "flags,mode,groups",
    [
        ([], "offline", "(not live and not live_generation)"),
        (["-all"], "offline", "(not live and not live_generation)"),
        (["-live"], "live", "(live and not live_generation)"),
        (["-gen"], "generation", "live_generation"),
        (["-all", "-live"], "live", "(not live and not live_generation) or (live and not live_generation)"),
        (["-all", "-gen"], "generation", "(not live and not live_generation) or live_generation"),
        (
            ["--all", "--live", "--generate"],
            "both",
            "(not live and not live_generation) or (live and not live_generation) or live_generation",
        ),
    ],
)
def test_test_command_selects_only_requested_groups_and_preserves_exit_code(monkeypatch, flags, mode, groups):
    calls = []

    def invoke(arguments):
        calls.append(arguments)
        return 7

    monkeypatch.setattr(pytest, "main", invoke)
    assert main(["test", *flags]) == 7
    (arguments,) = calls
    assert arguments[arguments.index("-m") + 1] == groups
    assert arguments[arguments.index("--dtflow-test-mode") + 1] == mode
