"""Focused, offline progress-stage and shared animation lifecycle coverage (#170)."""

from types import SimpleNamespace

import pytest

from dtflow.core.models import BackendEvent
from dtflow.ui.progress_stages import event_stage


@pytest.mark.parametrize(
    ("kind", "stage", "label"),
    [
        ("preparing", "validating_request", "Validating generation request"),
        ("preparing", "opening_session", "Opening backend session"),
        ("preparing", "waiting_backend_response", "Request sent; waiting for backend response"),
        ("preparing", "backend_response_received", "Backend processing; awaiting generation progress"),
        ("preparing", "resolving_model", "Resolving model description"),
        ("preparing", "unknown_optional_stage", "Preparing request"),
        ("submission_possible", None, "Submitting request / waiting for backend"),
        ("progress", "sampling", "Generating"),
        ("progress", "text_encoded", "text encoded"),
        ("progress", None, "Backend progress"),
        ("terminal", None, "Backend finished; validating and saving output"),
    ],
)
def test_stage_uses_only_reported_event_kind_and_stage(kind, stage, label):
    event = BackendEvent("offline", 1, kind, "fake", stage=stage)
    assert event_stage(event) == label


def test_unobserved_event_does_not_create_stage():
    assert event_stage(SimpleNamespace(kind="unobserved", stage="sampling")) == ""


def test_shared_stripe_clock_stops_when_no_visible_indeterminate_widget(qtbot):
    from PySide6.QtWidgets import QApplication

    from dtflow.ui.progress_animation import AnimatedProgressBar, watch

    bar = AnimatedProgressBar()
    qtbot.addWidget(bar)
    bar.setRange(0, 0)
    bar.show()
    qtbot.waitUntil(lambda: bool(getattr(QApplication.instance(), "_dtflow_progress_clock", None)), timeout=1000)
    clock = QApplication.instance()._dtflow_progress_clock
    watch(bar)
    assert clock.timer.isActive()
    bar.setRange(0, 10)
    clock.tick()
    assert not clock.timer.isActive()
    bar.setRange(0, 0)
    assert clock.timer.isActive()
    bar.hide()
    clock.tick()
    assert not clock.timer.isActive()
