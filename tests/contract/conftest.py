from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for service_root in (ROOT / "backend" / "platform", ROOT / "backend" / "relay"):
    path = str(service_root)
    if path not in sys.path:
        sys.path.insert(0, path)
