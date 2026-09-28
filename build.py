"""Package the add-on as an installable zip.

    python build.py            # -> dist/camera_flight-<version>.zip

The zip works both as a Blender 4.2+ extension (Preferences > Get Extensions >
Install from Disk) and as a legacy add-on (Preferences > Add-ons > Install).
"""

import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "camera_flight"


def version():
    with open(os.path.join(ROOT, PACKAGE, "blender_manifest.toml"), encoding="utf-8") as fh:
        return re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.M).group(1)


def build(out_dir=None):
    out_dir = out_dir or os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s-%s.zip" % (PACKAGE, version()))
    src = os.path.join(ROOT, PACKAGE)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, dirs, files in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(base, name)
                zf.write(full, os.path.relpath(full, ROOT).replace(os.sep, "/"))
    return path


if __name__ == "__main__":
    print(build())
