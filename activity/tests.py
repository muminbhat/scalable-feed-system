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
