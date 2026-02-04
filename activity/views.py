from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .cursors import decode_feed_cursor, encode_feed_cursor
from .models import Event, FeedItem, IdempotencyKey, Notification
from .serializers import EventIngestSerializer, EventOutSerializer, FeedQuerySerializer


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
