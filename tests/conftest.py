from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MARKO_CONFIG", str(REPO_ROOT / "config" / "assistant.example.yaml"))
os.environ.setdefault("MARKO_INBOX_SYNC_TOKEN", "test-only-inbox-token-with-32-characters")
