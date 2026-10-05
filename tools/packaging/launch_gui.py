"""Frozen desktop entry point; the source CLI remains dtflow.cli:main."""

from dtflow.ui.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
