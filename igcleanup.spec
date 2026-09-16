# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright", "igcleanup"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# The app only ever runs a headed browser; the headless shell just adds ~100 MB and
# very long paths that can break unzipping on a deep Desktop folder.
datas = [x for x in datas if "chromium_headless_shell" not in str(x[0])]
binaries = [x for x in binaries if "chromium_headless_shell" not in str(x[0])]

a = Analysis(
    ["src/igcleanup/main.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                                   "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"],
    noarchive=False,
)
# PyInstaller's own playwright hook re-adds the browsers, so filter the analysis result too.
a.datas = [x for x in a.datas if "chromium_headless_shell" not in str(x[0]) and "chromium_headless_shell" not in str(x[1])]
a.binaries = [x for x in a.binaries if "chromium_headless_shell" not in str(x[0]) and "chromium_headless_shell" not in str(x[1])]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Instagram Cleanup",
          console=False, icon="assets/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="Instagram Cleanup")
