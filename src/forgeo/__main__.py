"""Allow running Forgeo as a module: ``python -m forgeo``."""

from forgeo.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
