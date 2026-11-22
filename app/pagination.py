"""Opaque keyset cursors for stable, duplicate-free pagination."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime

from app.errors import ValidationFailed


def encode_cursor(created_at: datetime, campaign_id: int) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": campaign_id},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw)
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        campaign_id = int(payload["id"])
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationFailed(
            "Invalid pagination cursor.",
            details=[
                {
                    "field": "cursor",
                    "message": "Cursor is malformed or from a different query; "
                    "start again without a cursor.",
                }
            ],
        ) from exc
    return created_at, campaign_id
