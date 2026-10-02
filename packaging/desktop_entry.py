"""PyInstaller entry point; multiprocessing support must precede app startup."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from music_mastering_tools.desktop import main

    raise SystemExit(main())
