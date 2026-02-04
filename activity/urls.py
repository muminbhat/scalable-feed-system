from django.urls import path

from .views import EventIngestView, FeedView, NotificationsView

urlpatterns = [
    # Support both with and without trailing slash (POST redirects are painful).
    path("events", EventIngestView.as_view(), name="events-ingest"),
    path("events/", EventIngestView.as_view(), name="events-ingest-slash"),

    path("feed", FeedView.as_view(), name="feed"),
    path("feed/", FeedView.as_view(), name="feed-slash"),

    path("notifications", NotificationsView.as_view(), name="notifications"),
    path("notifications/", NotificationsView.as_view(), name="notifications-slash"),
]
