from __future__ import annotations

import asyncio
import json

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.decorators import classonlymethod
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .cursors import decode_feed_cursor, encode_feed_cursor
from .models import Event, FeedItem, IdempotencyKey, Notification
from .serializers import (
    EventIngestSerializer,
    EventOutSerializer,
    FeedQuerySerializer,
    NotificationOutSerializer,
    NotificationsQuerySerializer,
)
from .sse import broker, format_sse


def _get_header_user_id(request) -> int | None:
    """
    Mock auth: read user id from header. We accept common header names.
    """

    raw = (
        request.headers.get("X-User-Id")
        or request.headers.get("X-User-ID")
        or request.headers.get("X-USER-ID")
        or request.headers.get("user_id")
    )
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class EventIngestView(APIView):
    """
    POST /api/events

    Body:
      { actor_id, verb, object_type, object_id, target_user_ids[], created_at? }
    Returns:
      { event_id }
    """

    def post(self, request):
        header_user_id = _get_header_user_id(request)
        if header_user_id is None:
            return Response(
                {"detail": "Missing or invalid X-User-Id header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = EventIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Prevent spoofing in this mocked-auth setup.
        if int(data["actor_id"]) != int(header_user_id):
            return Response(
                {"detail": "actor_id must match X-User-Id header."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_user_ids = list(dict.fromkeys(data["target_user_ids"]))  # stable de-dupe
        created_at = data.get("created_at") or timezone.now()

        idem_key = request.headers.get("Idempotency-Key")
        with transaction.atomic():
            if idem_key:
                # IMPORTANT: A UNIQUE violation inside the outer atomic() would
                # mark the transaction as broken even if we catch the exception.
                # We isolate the possible IntegrityError in a savepoint so the
                # outer transaction remains usable.
                try:
                    with transaction.atomic():
                        idem = IdempotencyKey.objects.create(key=idem_key)
                except IntegrityError:
                    existing = IdempotencyKey.objects.select_for_update().get(key=idem_key)
                    if existing.event_id is not None:
                        return Response({"event_id": existing.event_id}, status=status.HTTP_200_OK)
                    # If it's not set yet (race), continue and set it below.
                    idem = existing
            else:
                idem = None

            event = Event.objects.create(
                actor_id=data["actor_id"],
                verb=data["verb"],
                object_type=data["object_type"],
                object_id=data["object_id"],
                created_at=created_at,
            )

            if target_user_ids:
                FeedItem.objects.bulk_create(
                    [
                        FeedItem(user_id=uid, event=event, created_at=created_at)
                        for uid in target_user_ids
                    ],
                    ignore_conflicts=True,
                )
                Notification.objects.bulk_create(
                    [
                        Notification(user_id=uid, event=event, created_at=created_at)
                        for uid in target_user_ids
                    ],
                    ignore_conflicts=True,
                )

            if idem is not None:
                idem.event = event
                idem.save(update_fields=["event"])

            # Publish to SSE subscribers only after the DB commit completes.
            # This keeps the stream consistent with polling/backfill.
            if target_user_ids:
                target_ids_for_publish = target_user_ids[:]

                def _publish_after_commit() -> None:
                    # NOTE: broker is in-memory; avoid extra DB work when nobody is listening.
                    # We check in the event loop since broker uses an asyncio lock.
                    async def _go() -> None:
                        if not await broker.any_subscribers(target_ids_for_publish):
                            return

                        rows = list(
                            Notification.objects.filter(
                                event_id=event.id,
                                user_id__in=target_ids_for_publish,
                            )
                            .select_related("event")
                            .values(
                                "id",
                                "user_id",
                                "created_at",
                                "read_at",
                                "delivered_at",
                                "event__id",
                                "event__actor_id",
                                "event__verb",
                                "event__object_type",
                                "event__object_id",
                                "event__created_at",
                            )
                        )
                        for r in rows:
                            msg = {
                                "notification_id": r["id"],
                                "user_id": r["user_id"],
                                "created_at": r["created_at"].isoformat(),
                                "read_at": r["read_at"].isoformat() if r["read_at"] else None,
                                "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
                                "event": {
                                    "event_id": r["event__id"],
                                    "actor_id": r["event__actor_id"],
                                    "verb": r["event__verb"],
                                    "object_type": r["event__object_type"],
                                    "object_id": r["event__object_id"],
                                    "created_at": r["event__created_at"].isoformat(),
                                },
                            }
                            await broker.publish(int(r["user_id"]), msg)

                    # Run in whichever loop Django/ASGI is using.
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        asyncio.run(_go())
                    else:
                        loop.create_task(_go())

                transaction.on_commit(_publish_after_commit)

        return Response({"event_id": event.id}, status=status.HTTP_201_CREATED)


class FeedView(APIView):
    """
    GET /api/feed?user_id=<id>&cursor=<optional>&limit=<optional>

    Returns:
      { items: [event...], next_cursor }
    """

    DEFAULT_LIMIT = 50
    MAX_LIMIT = 200

    def get(self, request):
        header_user_id = _get_header_user_id(request)
        if header_user_id is None:
            return Response(
                {"detail": "Missing or invalid X-User-Id header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        qs = FeedQuerySerializer(data=request.query_params)
        qs.is_valid(raise_exception=True)
        params = qs.validated_data

        user_id = int(params.get("user_id") or header_user_id)
        if user_id != int(header_user_id):
            return Response(
                {"detail": "user_id must match X-User-Id header."},
                status=status.HTTP_403_FORBIDDEN,
            )

        limit = int(params.get("limit") or self.DEFAULT_LIMIT)
        limit = max(1, min(limit, self.MAX_LIMIT))

        cursor_raw = params.get("cursor") or ""
        cursor = decode_feed_cursor(cursor_raw)
        if cursor_raw and cursor is None:
            return Response({"detail": "Invalid cursor."}, status=status.HTTP_400_BAD_REQUEST)

        feed_qs = (
            FeedItem.objects.filter(user_id=user_id)
            .select_related("event")
            .order_by("-created_at", "-id")
        )

        if cursor is not None:
            feed_qs = feed_qs.filter(
                Q(created_at__lt=cursor.created_at)
                | Q(created_at=cursor.created_at, id__lt=cursor.feed_item_id)
            )

        feed_items = list(feed_qs[:limit])
        events = [fi.event for fi in feed_items]

        next_cursor = None
        if len(feed_items) == limit:
            last = feed_items[-1]
            next_cursor = encode_feed_cursor(last.created_at, last.id)

        return Response(
            {
                "items": EventOutSerializer(events, many=True).data,
                "next_cursor": next_cursor,
            },
            status=status.HTTP_200_OK,
        )


class NotificationsView(APIView):
    """
    GET /api/notifications?user_id=<id>&since=<optional>&limit=<optional>

    Returns:
      { items: [notification...], next_since }
    """

    DEFAULT_LIMIT = 100
    MAX_LIMIT = 200

    def get(self, request):
        header_user_id = _get_header_user_id(request)
        if header_user_id is None:
            return Response(
                {"detail": "Missing or invalid X-User-Id header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        qs = NotificationsQuerySerializer(data=request.query_params)
        qs.is_valid(raise_exception=True)
        params = qs.validated_data

        user_id = int(params.get("user_id") or header_user_id)
        if user_id != int(header_user_id):
            return Response(
                {"detail": "user_id must match X-User-Id header."},
                status=status.HTTP_403_FORBIDDEN,
            )

        since = int(params.get("since") or 0)

        limit = int(params.get("limit") or self.DEFAULT_LIMIT)
        limit = max(1, min(limit, self.MAX_LIMIT))

        notif_qs = (
            Notification.objects.filter(user_id=user_id, id__gt=since)
            .select_related("event")
            .order_by("id")
        )
        notifications = list(notif_qs[:limit])

        next_since = notifications[-1].id if notifications else since
        return Response(
            {
                "items": NotificationOutSerializer(notifications, many=True).data,
                "next_since": next_since,
            },
            status=status.HTTP_200_OK,
        )


class NotificationsStreamView(APIView):
    """
    GET /api/notifications/stream?user_id=<id>

    Server-Sent Events stream of notifications.
    Supports resume via Last-Event-ID header (notification id).
    """

    # DRF's APIView is sync by default; we expose an async handler via `as_view`.
    @classonlymethod
    def as_view(cls, **initkwargs):  # type: ignore[override]
        view = super().as_view(**initkwargs)
        view._is_coroutine = asyncio.coroutines._is_coroutine  # type: ignore[attr-defined]
        return view

    async def get(self, request):
        header_user_id = _get_header_user_id(request)
        if header_user_id is None:
            return Response(
                {"detail": "Missing or invalid X-User-Id header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # user_id must match header
        try:
            user_id = int(request.GET.get("user_id") or header_user_id)
        except (TypeError, ValueError):
            return Response({"detail": "Invalid user_id."}, status=status.HTTP_400_BAD_REQUEST)
        if user_id != int(header_user_id):
            return Response(
                {"detail": "user_id must match X-User-Id header."},
                status=status.HTTP_403_FORBIDDEN,
            )

        last_event_id_raw = request.headers.get("Last-Event-ID")
        try:
            last_event_id = int(last_event_id_raw) if last_event_id_raw else 0
        except (TypeError, ValueError):
            last_event_id = 0

        sub = await broker.subscribe(user_id)

        async def stream():
            try:
                # Let browsers reconnect quickly.
                yield "retry: 3000\n\n"

                # Backfill from DB if the client is resuming.
                if last_event_id > 0:
                    rows = list(
                        Notification.objects.filter(user_id=user_id, id__gt=last_event_id)
                        .select_related("event")
                        .order_by("id")[:200]
                    )
                    for n in rows:
                        msg = NotificationOutSerializer(n).data
                        yield format_sse(data=msg, event_id=n.id)

                # Live stream
                while True:
                    try:
                        msg = await asyncio.wait_for(sub.queue.get(), timeout=15.0)
                        yield format_sse(data=msg, event_id=int(msg.get("notification_id") or 0))
                    except TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                await broker.unsubscribe(sub)

        resp = StreamingHttpResponse(stream(), content_type="text/event-stream")
        resp["Cache-Control"] = "no-cache"
        resp["X-Accel-Buffering"] = "no"
        return resp
