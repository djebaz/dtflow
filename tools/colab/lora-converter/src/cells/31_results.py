#@title Result delivery { display-mode: "form" }
# Requires: optional RUN_RESULT.
# Produces: resolved output path for downstream preview/download UI.
# Safe to rerun: yes.

from project_app.results import show_result_panel

RESULT_PANEL = show_result_panel(RUN_RESULT, auto_download=False) if "RUN_RESULT" in globals() else None
