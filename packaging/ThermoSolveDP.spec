# PyInstaller recipe for ThermoSolveDP. Build with:
#     python -m PyInstaller --noconfirm --clean packaging/ThermoSolveDP.spec
# macOS -> dist/ThermoSolveDP.app     Windows -> dist/ThermoSolveDP/ThermoSolveDP.exe
import os
import sys

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from thermosolvedp import __version__  # noqa: E402

datas, binaries, hidden = collect_all("CoolProp")

# Windows wheels of CoolProp (made with "delvewheel") keep the DLLs they need, e.g. the Microsoft
# C++ runtime msvcp140-<hash>.dll, in a folder NEXT TO the package: site-packages/coolprop.libs.
# CoolProp's __init__ adds "<package>/../coolprop.libs" to the DLL search path at import time,
# so the folder must be bundled under the same name beside the CoolProp package.
import importlib.util
from pathlib import Path

_cp_dir = Path(importlib.util.find_spec("CoolProp").origin).parent
for _libs in sorted(_cp_dir.parent.glob("*.libs")):
    if _libs.name.lower() == "coolprop.libs":
        binaries += [(str(f), _libs.name) for f in _libs.iterdir() if f.is_file()]
datas += [(os.path.join(ROOT, "examples"), "examples")]
datas += [(os.path.join(ROOT, f), ".") for f in ("README.txt", "LICENSE", "THIRD_PARTY_NOTICES.txt",
                                                 "INSTALLATION_INSTRUCTIONS.txt")
          if os.path.exists(os.path.join(ROOT, f))]
hidden += ["matplotlib.backends.backend_qtagg"]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib.backends.backend_tkagg", "PyQt5", "PyQt6", "PySide2", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon_mac = os.path.join(SPECPATH, "ThermoSolveDP.icns")
icon_win = os.path.join(SPECPATH, "ThermoSolveDP.ico")
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ThermoSolveDP",
    console=False,                 # no Terminal / console window
    icon=icon_mac if sys.platform == "darwin" else icon_win,
    argv_emulation=False,
    codesign_identity=None,        # ad-hoc signed by PyInstaller on macOS
)
coll = COLLECT(exe, a.binaries, a.datas, name="ThermoSolveDP")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ThermoSolveDP.app",
        icon=icon_mac,
        bundle_identifier="com.deoras.thermosolvedp",
        version=__version__,
        info_plist={
            "CFBundleName": "ThermoSolveDP",
            "CFBundleDisplayName": "ThermoSolveDP",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHumanReadableCopyright": "Created by Deoras Prabhudharwadkar",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,      # follow light/dark mode
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            # double-clicking a .tpf flowsheet opens it in ThermoSolveDP
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "ThermoSolveDP flowsheet",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "CFBundleTypeIconFile": "ThermoSolveDP.icns",
                "LSItemContentTypes": ["com.deoras.thermosolvedp.flowsheet"],
            }],
            "UTExportedTypeDeclarations": [{
                "UTTypeIdentifier": "com.deoras.thermosolvedp.flowsheet",
                "UTTypeDescription": "ThermoSolveDP flowsheet",
                "UTTypeConformsTo": ["public.json", "public.data"],
                "UTTypeIconFile": "ThermoSolveDP.icns",
                "UTTypeTagSpecification": {"public.filename-extension": ["tpf"]},
            }],
        },
    )
