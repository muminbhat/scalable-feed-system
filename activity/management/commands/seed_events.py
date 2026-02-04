from __future__ import annotations

import random
import time
from typing import Iterable

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from activity.models import Event, FeedItem, IdempotencyKey, Notification


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


class Command(BaseCommand):
    help = "Seed the database with events + feed_items + notifications for load testing."

    def _reset_tables(self) -> None:
        """
        Reset activity tables.

        On SQLite, large ORM deletes can fail with "too many SQL variables" due to
        Django's deletion collector emitting big IN(...) statements for SET_NULL relations.
        Use raw SQL deletes instead.
        """

        if connection.vendor == "sqlite":
            tables = [
                "activity_idempotencykey",
                "activity_notification",
                "activity_feeditem",
                "activity_event",
            ]
            with connection.cursor() as cur:
                cur.execute("PRAGMA foreign_keys=OFF;")
                for t in tables:
                    cur.execute(f"DELETE FROM {t};")
                # Best-effort reset of AUTOINCREMENT counters (ok if sqlite_sequence missing).
                try:
                    placeholders = ",".join(["?"] * len(tables))
                    cur.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders});", tables)
                except Exception:
                    pass
                cur.execute("PRAGMA foreign_keys=ON;")
            return

        # Non-SQLite: regular ORM deletes are fine.
        Notification.objects.all().delete()
        FeedItem.objects.all().delete()
        IdempotencyKey.objects.all().delete()
        Event.objects.all().delete()

    def add_arguments(self, parser):
        parser.add_argument("--events", type=int, required=True, help="Number of events to create.")
        parser.add_argument(
            "--hot-user-id",
            type=int,
            default=2,
            help="User id that receives all events (worst-case feed history).",
        )
        parser.add_argument(
            "--actor-id",
            type=int,
            default=1,
            help="Actor id to use for seeded events.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10_000,
            help="Bulk insert batch size.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing activity tables before seeding.",
        )

    def handle(self, *args, **opts):
        total_events: int = int(opts["events"])
        hot_user_id: int = int(opts["hot_user_id"])
        actor_id: int = int(opts["actor_id"])
        batch_size: int = int(opts["batch_size"])
        reset: bool = bool(opts["reset"])

        if total_events <= 0:
            self.stderr.write("events must be > 0")
            return

        if reset:
            # Order matters due to FK constraints.
            self._reset_tables()

        verbs = ["like", "comment", "follow", "purchase", "share"]
        now = timezone.now()

        self.stdout.write(
            f"Seeding {total_events:,} events (hot_user_id={hot_user_id}) in batches of {batch_size:,}..."
        )
        start = time.time()

        created = 0
        object_id_counter = 1
        while created < total_events:
            n = min(batch_size, total_events - created)
            batch_events = [
                Event(
                    actor_id=actor_id,
                    verb=random.choice(verbs),
                    object_type="post",
                    object_id=str(object_id_counter + i),
                    created_at=now,
                )
                for i in range(n)
            ]

            with transaction.atomic():
                Event.objects.bulk_create(batch_events, batch_size=min(5000, n))

                feed_items = [
                    FeedItem(user_id=hot_user_id, event=e, created_at=now) for e in batch_events
                ]
                notifications = [
                    Notification(user_id=hot_user_id, event=e, created_at=now) for e in batch_events
                ]
                FeedItem.objects.bulk_create(feed_items, batch_size=min(5000, n), ignore_conflicts=True)
                Notification.objects.bulk_create(notifications, batch_size=min(5000, n), ignore_conflicts=True)

            created += n
            object_id_counter += n
            if created % (batch_size * 5) == 0 or created == total_events:
                elapsed = time.time() - start
                self.stdout.write(f"  - created {created:,}/{total_events:,} (elapsed {elapsed:.1f}s)")

        elapsed = time.time() - start
        self.stdout.write(self.style.SUCCESS(f"Done. Seeded {total_events:,} events in {elapsed:.1f}s."))

