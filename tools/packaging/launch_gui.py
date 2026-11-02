"""Frozen desktop entry point; the source CLI remains dtflow.cli:main."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # script child processes re-enter this executable (spawn start method)
    if len(sys.argv) > 1 and sys.argv[1] == "--dtflow-script-selfcheck":
        from pathlib import Path

        from dtflow.scripting.selfcheck import main as selfcheck

        raise SystemExit(selfcheck(Path(sys.argv[2]) if len(sys.argv) > 2 else None))
    from dtflow.ui.desktop import main

    raise SystemExit(main())
