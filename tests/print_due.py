"""Print every fixture task's computed due date / pause / turn. Run: uv run python tests/print_due.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_helpers import due_table  # noqa: E402

if __name__ == "__main__":
    print(due_table())
