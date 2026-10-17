from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


# This is a test inside the Python fixture repository, not a repo-ready test.
collect_ignore = [str(PROJECT_ROOT / "tests" / "fixtures" / "python-complete" / "tests" / "test_example.py")]
