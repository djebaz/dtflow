#@title Runtime implementation { display-mode: "form" }
# Requires: project source importable from src/.
# Produces: runtime implementation symbols for inspection.
# Safe to rerun: yes; does not prepare the runtime automatically.

from project_app.logging_utils import run_logged_command
from project_app.paths import ProjectPaths
from project_app.runtime import environment_fingerprint, prepare_runtime
