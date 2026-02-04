from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime

from django.utils.dateparse import parse_datetime


@dataclass(frozen=True)
class FeedCursor:
    created_at: datetime
    feed_item_id: int


def encode_feed_cursor(created_at: datetime, feed_item_id: int) -> str:
    payload = {
        "created_at": created_at.isoformat(),
        "feed_item_id": int(feed_item_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_feed_cursor(cursor: str) -> FeedCursor | None:
    if not cursor:
        return None

    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None

    created_at_raw = payload.get("created_at")
    feed_item_id_raw = payload.get("feed_item_id")
    if created_at_raw is None or feed_item_id_raw is None:
        return None

    dt = parse_datetime(created_at_raw)
    if dt is None:
        return None

    try:
        fid = int(feed_item_id_raw)
    except (TypeError, ValueError):
        return None

    if fid <= 0:
        return None

    return FeedCursor(created_at=dt, feed_item_id=fid)

