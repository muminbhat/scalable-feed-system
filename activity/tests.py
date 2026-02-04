from __future__ import annotations

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Event, FeedItem


class FeedTests(TestCase):
    def test_feed_paginates_with_stable_cursor(self):
        now = timezone.now()
        events = [
            Event.objects.create(
                actor_id=1,
                verb="like",
                object_type="post",
                object_id=str(i),
                created_at=now,
            )
            for i in range(1, 6)
        ]
        # Create feed items with identical timestamps to exercise the id tie-break.
        for ev in events:
            FeedItem.objects.create(user_id=2, event=ev, created_at=now)

        client = APIClient()
        client.credentials(HTTP_X_USER_ID="2")

        r1 = client.get("/api/feed", {"limit": 2, "user_id": 2})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(len(r1.data["items"]), 2)
        self.assertIsNotNone(r1.data["next_cursor"])

        r2 = client.get("/api/feed", {"limit": 2, "user_id": 2, "cursor": r1.data["next_cursor"]})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.data["items"]), 2)
        self.assertNotEqual(r1.data["items"][0]["event_id"], r2.data["items"][0]["event_id"])


class NotificationsTests(TestCase):
    def test_notifications_since_filters_by_id(self):
        now = timezone.now()
        ev1 = Event.objects.create(
            actor_id=1,
            verb="comment",
            object_type="post",
            object_id="1",
            created_at=now,
        )
        ev2 = Event.objects.create(
            actor_id=1,
            verb="comment",
            object_type="post",
            object_id="2",
            created_at=now,
        )
        from .models import Notification

        n1 = Notification.objects.create(user_id=2, event=ev1, created_at=now)
        Notification.objects.create(user_id=2, event=ev2, created_at=now)

        client = APIClient()
        client.credentials(HTTP_X_USER_ID="2")

        r = client.get("/api/notifications", {"user_id": 2, "since": n1.id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["items"]), 1)
        self.assertEqual(r.data["items"][0]["event"]["object_id"], "2")


class AnalyticsTests(TestCase):
    def test_top_counts_object_ids(self):
        from .analytics import SlidingWindowTop

        sw = SlidingWindowTop(window_seconds=60, bucket_size_seconds=5)
        sw.add("a", ts=100.0)
        sw.add("a", ts=101.0)
        sw.add("b", ts=101.0)

        top = sw.top(now_ts=110.0)
        self.assertEqual(top[0], ("a", 2))
        self.assertEqual(top[1], ("b", 1))
