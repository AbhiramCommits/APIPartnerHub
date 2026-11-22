"""Shared credential loading for runnable examples.

Uses environment variables when provided, otherwise the local seed file written by
``python -m scripts.seed``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def base_url() -> str:
    return (
        os.environ.get("BASE_URL")
        or os.environ.get("API_BASE_URL")
        or f"http://localhost:{os.environ.get('API_PORT', '8000')}"
    )


def load_partners() -> list[dict[str, Any]]:
    path = ROOT / ".seed_credentials.json"
    if not path.exists():
        raise SystemExit("Missing .seed_credentials.json - run `python -m scripts.seed` first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    partners = payload["partners"]
    assert isinstance(partners, list)
    return partners


def partner(tier: str) -> dict[str, Any]:
    for entry in load_partners():
        if entry["tier"] == tier:
            return entry
    raise SystemExit(f"No seeded partner with tier={tier}.")
