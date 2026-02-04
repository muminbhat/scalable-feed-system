from __future__ import annotations

from rest_framework import serializers


class EventIngestSerializer(serializers.Serializer):
    actor_id = serializers.IntegerField(min_value=1)
    verb = serializers.CharField(max_length=64)
    object_type = serializers.CharField(max_length=64)
    object_id = serializers.CharField(max_length=128)
    target_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
    )
    created_at = serializers.DateTimeField(required=False)


class FeedQuerySerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1, required=False)
    cursor = serializers.CharField(required=False, allow_blank=True)
    limit = serializers.IntegerField(min_value=1, max_value=200, required=False)


class EventOutSerializer(serializers.Serializer):
    event_id = serializers.IntegerField(source="id")
    actor_id = serializers.IntegerField()
    verb = serializers.CharField()
    object_type = serializers.CharField()
    object_id = serializers.CharField()
    created_at = serializers.DateTimeField()


class NotificationsQuerySerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1, required=False)
    since = serializers.IntegerField(min_value=0, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=200, required=False)


class NotificationOutSerializer(serializers.Serializer):
    notification_id = serializers.IntegerField(source="id")
    user_id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    read_at = serializers.DateTimeField(allow_null=True)
    delivered_at = serializers.DateTimeField(allow_null=True)

    event = EventOutSerializer()

