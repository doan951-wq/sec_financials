from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files


project_dir = Path(SPECPATH)

a = Analysis(
    [str(project_dir / "gui.py")],
    pathex=[str(project_dir.parent)],
    binaries=[],
    datas=collect_data_files("customtkinter"),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SEC Financials",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SEC Financials",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SEC Financials.app",
        icon=None,
        bundle_identifier="com.secfinancials.app",
        info_plist={
            "CFBundleDisplayName": "SEC Financials",
            "NSHighResolutionCapable": True,
        },
    )
