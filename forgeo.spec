# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds the ``forgeo`` CLI as a single-file executable.

Used by the tag-triggered CI release job. Run from the repository root:

    pyinstaller forgeo.spec

The web frontend under ``src/forgeo/web`` is bundled next to the package, so
``Path(__file__).parent / "web"`` resolves to it inside the one-file bundle at
runtime.
"""

a = Analysis(
    ["src/forgeo/cli.py"],
    pathex=["src"],
    binaries=[],
    datas=[("src/forgeo/web", "forgeo/web")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="forgeo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
