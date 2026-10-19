"""Focused regressions for Logs Refresh evidence and export selection admission."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from dtflow import _diagnostic_export as diagnostic_export
from dtflow import _diagnostic_reader as reader
from dtflow.ui import logs_workspace as logs_workspace


def _record(sequence: int, *, component: str = "gallery") -> dict:
    """A minimal record for exercising reader callbacks without provider I/O."""
    return {
        "seq": sequence,
        "ts": f"2026-10-19T12:00:{sequence:02d}.000Z",
        "component": component,
        "event": "gallery.changed",
        "level": "INFO",
    }


def _reference(sequence: int, *, offset: int = 0) -> reader.RecordRef:
    """An immutable identity and offset in the synthetic diagnostic segment."""
    return reader.RecordRef("test", sequence, 7, 8, offset, 60, "dtflow-test.jsonl")


def test_refresh_discloses_incomplete_evidence_with_zero_matching_arrivals(qtbot):
    """Zero matching arrivals must not disguise an incomplete source as a clean Refresh."""
    from PySide6.QtWidgets import QToolButton

    button = QToolButton()
    qtbot.addWidget(button)
    controller = SimpleNamespace(form=SimpleNamespace(logsRefreshButton=button))
    logs_workspace.LogsWorkspace._set_pending(controller, True, 0, incomplete=True, detail="Source has a sequence gap")
    assert button.text().strip() == "Refresh (0 new · incomplete)"
    assert button.property("pending") is True
    assert "sequence gap" in button.toolTip()

    logs_workspace.LogsWorkspace._set_pending(controller, True, 0, incomplete=False)
    assert button.text().strip() == "Refresh"
    assert button.property("pending") is False


def test_arrival_count_is_filtered_against_shown_bytes_and_reports_incomplete(monkeypatch, tmp_path):
    """Count only matching records beyond the displayed file boundary."""
    rows = [
        (_record(1), _reference(1, offset=0)),
        (_record(2), _reference(2, offset=130)),
        (_record(3, component="worker"), _reference(3, offset=210)),
    ]
    status = SimpleNamespace(state="incomplete", reasons=("sequence_gap",))

    def fake_scan(_directory, _session, *, on_record, on_reset, cancelled):
        on_reset()
        for record, ref in rows:
            on_record(record, ref)
        return status

    monkeypatch.setattr(logs_workspace, "scan", fake_scan)
    counted, observed = logs_workspace.count_arrivals(
        tmp_path, "test", {(7, 8): (100, 0)}, {"component": "gallery"}
    )
    assert counted == 1
    assert observed.state == "incomplete"
    assert observed.reasons == ("sequence_gap",)


def test_export_selection_rejects_budget_overrun_without_truncating(monkeypatch, tmp_path):
    """Candidate admission must fail explicitly instead of returning a partial selection."""
    monkeypatch.setattr(reader, "MAX_WORKER_BYTES", 3072)

    def fake_scan(_directory, _session, *, cancelled, on_record, on_reset):
        on_reset()
        for sequence in range(1, 8):
            on_record(_record(sequence), _reference(sequence))
        return SimpleNamespace(state="pending")

    monkeypatch.setattr(reader, "scan", fake_scan)
    with pytest.raises(reader.EvidenceError, match="consumer_memory_limit"):
        reader.select(tmp_path, "test")


def test_export_selection_resets_budget_on_scan_retry(monkeypatch, tmp_path):
    """A rotation retry must not inherit memory reservations from the abandoned scan."""
    monkeypatch.setattr(reader, "MAX_WORKER_BYTES", 3072)

    def fake_scan(_directory, _session, *, cancelled, on_record, on_reset):
        on_reset()
        for sequence in range(1, 3):
            on_record(_record(sequence), _reference(sequence))
        on_reset()  # simulate snapshot_changed followed by a clean retry
        on_record(_record(3), _reference(3))
        return SimpleNamespace(state="pending")

    monkeypatch.setattr(reader, "scan", fake_scan)
    _status, selected = reader.select(tmp_path, "test")
    assert [ref.seq for ref in selected] == [3]


def test_export_budget_failure_never_publishes_a_completed_pair(monkeypatch, tmp_path):
    """Failed selection may not leave a destination or a successful manifest."""
    source = tmp_path / "dtflow-test.jsonl"
    source.write_text("", encoding="utf-8")
    destination = tmp_path / "diagnostics.csv"

    def reject(*_args, **_kwargs):
        raise reader.EvidenceError("consumer_memory_limit")

    monkeypatch.setattr(diagnostic_export, "select", reject)
    with pytest.raises(reader.EvidenceError, match="consumer_memory_limit"):
        diagnostic_export.export_matching(tmp_path, "test", destination, fmt="csv")
    assert not destination.exists()
    assert not Path(str(destination) + ".manifest.json").exists()
    assert list(tmp_path.glob(".diagnostic-export-*")) == []
