# Build on Windows x64 using CPython 3.11. No operator workspace is bundled.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
release = root / "dist" / "windows"
datas = collect_data_files("music_mastering_tools")
datas += collect_data_files("resampy")
datas += copy_metadata("music-mastering-tools")
datas += copy_metadata("matchering", recursive=True)
# Editable installation metadata can contain the developer's absolute checkout
# path. It is not runtime metadata and must never enter the shared executable.
datas = [entry for entry in datas if Path(entry[0]).name != "direct_url.json"]
datas += [
    (str(release / "THIRD-PARTY-NOTICES"), "THIRD-PARTY-NOTICES"),
    (str(release / "dependency-inventory.json"), "."),
    (str(root / "LICENSE"), "."),
    (str(root / "NOTICE"), "."),
]

a = Analysis(
    [str(root / "packaging" / "desktop_entry.py")],
    pathex=[str(root / "src")],
    binaries=[], datas=datas,
    hiddenimports=collect_submodules("matchering"),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "pytest_cov", "coverage", "mypy", "ruff", "pygments", "colorama", "tkinter", "matplotlib", "IPython"],
    noarchive=False,
)
# Analysis hooks may collect installed metadata again after the explicit datas
# list above. Filter the completed graph too, before any archive is assembled.
a.datas = [entry for entry in a.datas if Path(entry[0]).name != "direct_url.json"]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="MusicMasteringTools", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    version=str(root / "packaging" / "windows_version.txt"),
)
