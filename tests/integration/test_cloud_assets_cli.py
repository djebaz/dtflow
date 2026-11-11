"""CLI help discloses cloud asset preparation and submission boundaries."""

from dtflow import cli


def test_help_discloses_that_only_prepare_may_upload():
    text = cli.parser().format_help()
    assert "assets" in text
    choices = cli.parser()._subparsers._group_actions[0].choices["assets"]._subparsers._group_actions[0].choices
    assert "UPLOAD" in choices["prepare"].format_help() and "Never uploads" in choices["reconcile"].format_help()
    assert "-z, --asset" in cli.parser()._subparsers._group_actions[0].choices["generate"].format_help()
