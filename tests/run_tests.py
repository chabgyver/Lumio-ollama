"""Lance toute la suite de tests de Lumio : python tests\\run_tests.py"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent
for path in (str(APP_DIR), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
