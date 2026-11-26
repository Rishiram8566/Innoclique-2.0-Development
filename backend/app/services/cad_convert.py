"""
DWG to DXF through the free ODA File Converter.

The converter installs in a versioned folder (C:\\Program Files\\ODA\\ODAFileConverter 26.4.0\\),
which ezdxf's default path does not cover, so the newest installed copy is looked up here
and handed to ezdxf.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path


def find_odafc() -> str | None:
    """Path of the newest ODAFileConverter.exe on Windows (or the one on PATH elsewhere), or None."""
    import ezdxf

    candidates = []
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidates += glob.glob(os.path.join(root, "ODA", "*", "ODAFileConverter.exe"))
    if candidates:
        path = sorted(candidates)[-1]
        ezdxf.options.set("odafc-addon", "win_exec_path", f'"{path}"')
        return path
    from ezdxf.addons import odafc

    return "installed" if odafc.is_installed() else None


def dwg_to_dxf(source: Path, target: Path) -> Path:
    """Convert one DWG to DXF (AutoCAD 2018 format); raises if the converter is missing or fails."""
    if not find_odafc():
        raise RuntimeError("The ODA File Converter is not installed.")
    from ezdxf.addons import odafc

    odafc.convert(str(source), str(target), version="R2018", replace=True)
    return target
