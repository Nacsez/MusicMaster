"""Allow ``python -m music_mastering_tools`` to behave like ``mmt``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
