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

