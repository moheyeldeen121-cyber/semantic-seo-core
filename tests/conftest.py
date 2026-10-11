"""Test bootstrap: make the project root importable in every pytest launcher."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests must never read or write the real AI response cache (it is ON by
# default for users): every test sees live mocked calls, and no test leaves
# cached answers behind in the project folder.
import os  # noqa: E402

os.environ["ENABLE_CACHE"] = "false"
