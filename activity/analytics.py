from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class _Bucket:
    bucket_id: int | None = None
    counts: Counter[str] = field(default_factory=Counter)


class SlidingWindowTop:
    """
    Sliding window frequency counter using a ring of time buckets.

    - bucket_size_seconds controls granularity (smaller => more accurate, more buckets).
    - window_seconds controls the window length.
    - Keys are arbitrary strings (object_id, verb, etc.).
    """

    def __init__(self, *, window_seconds: int, bucket_size_seconds: int = 5) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if bucket_size_seconds <= 0:
            raise ValueError("bucket_size_seconds must be > 0")

        self.window_seconds = int(window_seconds)
        self.bucket_size_seconds = int(bucket_size_seconds)
        self.num_buckets = max(1, (self.window_seconds + self.bucket_size_seconds - 1) // self.bucket_size_seconds)

        self._buckets: list[_Bucket] = [_Bucket() for _ in range(self.num_buckets)]
        self._total: Counter[str] = Counter()
        self._lock = threading.Lock()

    def _current_bucket_id(self, ts: float) -> int:
        return int(ts // self.bucket_size_seconds)

    def _expire_old(self, now_ts: float) -> None:
        """
        Drop buckets that are older than the window relative to now_ts.
        """

        current_bucket = self._current_bucket_id(now_ts)
        min_valid = current_bucket - (self.num_buckets - 1)
        for b in self._buckets:
            if b.bucket_id is None:
                continue
            if b.bucket_id < min_valid:
                if b.counts:
                    self._total.subtract(b.counts)
                b.counts.clear()
                b.bucket_id = None

        # Counter can keep zero/negative entries; clean occasionally.
        if len(self._total) > 0:
            for k in [k for k, v in self._total.items() if v <= 0]:
                del self._total[k]

    def add(self, key: str, *, ts: float | None = None, n: int = 1) -> None:
        if not key or n <= 0:
            return
        ts = time.time() if ts is None else float(ts)
        bucket_id = self._current_bucket_id(ts)
        idx = bucket_id % self.num_buckets

        with self._lock:
            self._expire_old(ts)
            bucket = self._buckets[idx]
            if bucket.bucket_id != bucket_id:
                # The ring slot is being reused; remove its old contribution.
                if bucket.counts:
                    self._total.subtract(bucket.counts)
                bucket.counts.clear()
                bucket.bucket_id = bucket_id

            bucket.counts[key] += n
            self._total[key] += n

    def top(self, *, k: int = 100, now_ts: float | None = None) -> list[tuple[str, int]]:
        now_ts = time.time() if now_ts is None else float(now_ts)
        with self._lock:
            self._expire_old(now_ts)
            return [(key, int(count)) for key, count in self._total.most_common(k)]


class ActivityAnalytics:
    """
    Tracks top keys for multiple windows.

    Assignment requirement: top-100 by object_id (or verbs) over 1m/5m/1h.
    """

    def __init__(self) -> None:
        self._by_object_id = {
            "1m": SlidingWindowTop(window_seconds=60, bucket_size_seconds=5),
            "5m": SlidingWindowTop(window_seconds=300, bucket_size_seconds=5),
            "1h": SlidingWindowTop(window_seconds=3600, bucket_size_seconds=5),
        }
        self._by_verb = {
            "1m": SlidingWindowTop(window_seconds=60, bucket_size_seconds=5),
            "5m": SlidingWindowTop(window_seconds=300, bucket_size_seconds=5),
            "1h": SlidingWindowTop(window_seconds=3600, bucket_size_seconds=5),
        }

    def record(self, *, object_id: str, verb: str, ts: float | None = None) -> None:
        for w in self._by_object_id.values():
            w.add(object_id, ts=ts)
        for w in self._by_verb.values():
            w.add(verb, ts=ts)

    def top(self, *, window: str, by: str = "object_id", k: int = 100) -> list[tuple[str, int]]:
        if by == "object_id":
            counter = self._by_object_id.get(window)
        elif by == "verb":
            counter = self._by_verb.get(window)
        else:
            raise ValueError("by must be 'object_id' or 'verb'")

        if counter is None:
            raise ValueError("window must be one of: 1m, 5m, 1h")
        return counter.top(k=k)


analytics = ActivityAnalytics()

