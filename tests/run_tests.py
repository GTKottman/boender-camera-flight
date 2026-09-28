"""Run the test-suite inside Blender's Python.

Either with the ``bpy`` module from PyPI::

    pip install bpy==4.2.*          # or 4.5.*, 5.0.*
    python tests/run_tests.py

or with a Blender binary::

    blender --background --factory-startup --python tests/run_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

import bpy  # noqa: E402  (must come first: it sets up Blender's module paths)
import addon_utils  # noqa: E402


def main():
    print("Blender", bpy.app.version_string)
    addon_utils.enable("camera_flight", default_set=True, handle_error=None)
    argv = [a for a in sys.argv[sys.argv.index("--") + 1 :]] if "--" in sys.argv else sys.argv[1:]
    pattern = argv[0] if argv else "test_*.py"
    suite = unittest.defaultTestLoader.discover(HERE, pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2 if "-v" in argv else 1).run(suite)
    addon_utils.disable("camera_flight", default_set=True)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
