"""Entry-point launcher so a freezing tool can discover the dtflow CLI. Packaging trials only."""

from dtflow.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
