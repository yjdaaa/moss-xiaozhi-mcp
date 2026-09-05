from __future__ import annotations

import sys
from pathlib import Path

PREVIEW_DIR = Path(__file__).resolve().parent
ROOT_DIR = PREVIEW_DIR.parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from apps.excalidraw_lab import server as lab_server

# Only redirect the static build directory. API handlers and safety gates remain the originals.
lab_server.STATIC_DIR = PREVIEW_DIR / "dist"


if __name__ == "__main__":
    lab_server.main()
