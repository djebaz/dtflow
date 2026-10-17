from dtflow.core.export import ExportResult
from dtflow.services.image_export import CREATION_TIME_NOTICE, INFORMATIONAL_NOTES
from dtflow.ui.image_export_panel import completion_state


def _result(**values) -> ExportResult:
    return ExportResult("out", 2, 2, 0, False, **values)


def test_platform_notice_is_informational_but_real_problems_stay_partial():
    assert CREATION_TIME_NOTICE in INFORMATIONAL_NOTES
    assert completion_state(_result(notices=(CREATION_TIME_NOTICE,))) == "success"
    assert completion_state(_result(warnings=("Persistent cache unavailable/busy.",))) == "partial"
    assert completion_state(ExportResult("out", 2, 1, 1, False)) == "partial"
    assert completion_state(ExportResult("out", 2, 1, 0, True, notices=(CREATION_TIME_NOTICE,))) == "stopped"
