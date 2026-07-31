"""Allow running the factory as a module: ``python -m factory``."""

from factory.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
