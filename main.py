"""Convenience launcher: ``python main.py`` starts the coaching server.

Equivalent to the ``coach`` console script or ``uvicorn --factory coach.app:app``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coach.app import run  # noqa: E402


if __name__ == "__main__":
    run()
